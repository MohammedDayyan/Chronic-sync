import os
import shutil
import tempfile
from pathlib import Path
import pytest
import yaml

from src.engine.graph import ArchitectureGraphManager, match_glob_pattern
from src.agents.parser import parse_git_diff_ast
from src.agents.auditor import find_relevant_doc_section, audit_specification_drift
from src.agents.patcher import generate_documentation_patches
from src.cli import apply_patch_to_file
from src.engine.model import LocalHFModel
from src.state import SyncState


@pytest.fixture(autouse=True)
def setup_mock_model(monkeypatch):
    """Ensure tests run in mock LLM mode without downloading weights."""
    monkeypatch.setenv("CHRONICLE_MOCK_LLM", "true")
    LocalHFModel.reset_instance(mock=True)


def test_match_glob_pattern():
    assert match_glob_pattern("backend/auth.py", "backend/**/*.py") is True
    assert match_glob_pattern("backend/api/users.py", "backend/**/*.py") is True
    assert match_glob_pattern("src/engine/graph.py", "src/**/*.py") is True
    assert match_glob_pattern("tests/test.py", "src/**/*.py") is False


def test_graph_manager_dynamic_linking():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        
        # Create directory structure
        (root / "src" / "billing").mkdir(parents=True)
        (root / "docs").mkdir(parents=True)
        (root / "config").mkdir(parents=True)

        # Create mapping configuration
        config_data = {
            "mappings": [
                {"code_glob": "src/billing/**/*.py", "doc_path": "docs/billing_guide.md"}
            ]
        }
        (root / "config" / "mapping.yaml").write_text(yaml.dump(config_data), encoding="utf-8")

        # Create documentation files
        (root / "README.md").write_text("# Project Overview\n\nMentions ProcessPaymentService.", encoding="utf-8")
        (root / "docs" / "billing_guide.md").write_text("# Billing\n\nHandles subscription billing.", encoding="utf-8")
        (root / "docs" / "api.rst").write_text("API Reference\n=============\n\nDescribes endpoints.", encoding="utf-8")

        gm = ArchitectureGraphManager(root_dir=root)

        # 1. Test doc discovery finds .md, .rst
        discovered = gm.discover_docs()
        assert "README.md" in discovered
        assert "docs/billing_guide.md" in discovered
        assert "docs/api.rst" in discovered

        # 2. Test config mapping link
        linked_config = gm.get_linked_docs("src/billing/stripe.py")
        assert "docs/billing_guide.md" in linked_config

        # 3. Test symbol mention link using structural deltas
        linked_symbol = gm.get_linked_docs(
            "unknown/service.py",
            structural_deltas=[{"target": "ProcessPaymentService", "signature_delta": {"parameters": ["amount"]}}]
        )
        assert "README.md" in linked_symbol

        # 4. Verify NetworkX graph edges exist
        assert gm.graph.number_of_nodes() > 0


def test_parser_extracts_rich_structural_deltas():
    diff = """diff --git a/src/api/auth.py b/src/api/auth.py
--- a/src/api/auth.py
+++ b/src/api/auth.py
@@ -10,4 +10,6 @@
+@router.post("/login")
+async def authenticate_user(username: str, password: str, remember: bool = False) -> TokenResponse:
+    return TokenResponse()
+class SessionManager(BaseSession):
+    pass
-def legacy_auth(token: str):
-    pass
"""
    state: SyncState = {"git_diff": diff}
    result = parse_git_diff_ast(state)
    deltas = result["ast_deltas"]

    assert len(deltas) >= 3

    # Check function delta
    auth_delta = next((d for d in deltas if d["target"] == "authenticate_user"), None)
    assert auth_delta is not None
    assert auth_delta["change_type"] == "API_ROUTE"
    assert "username" in auth_delta["signature_delta"]["parameters"]
    assert "password" in auth_delta["signature_delta"]["parameters"]
    assert auth_delta["signature_delta"]["return_type"] == "TokenResponse:" or "TokenResponse" in auth_delta["signature_delta"]["return_type"]

    # Check class delta
    class_delta = next((d for d in deltas if d["target"] == "SessionManager"), None)
    assert class_delta is not None
    assert class_delta["change_type"] == "MODIFIED_CLASS"

    # Check removed function delta
    rem_delta = next((d for d in deltas if d["target"] == "legacy_auth"), None)
    assert rem_delta is not None
    assert rem_delta["change_type"] == "REMOVED_FUNCTION"


def test_auditor_section_localization():
    doc_text = """# Architecture Guide

## Overview
This is the system overview.

## Authentication System
The `authenticate_user` function authenticates users via OAuth2.
It currently accepts username and password.

## Database
PostgreSQL database specs.
"""
    section, heading, found = find_relevant_doc_section(doc_text, "authenticate_user", "src/auth.py")
    assert found is True
    assert "Authentication System" in heading
    assert "authenticate_user" in section
    assert "Database" not in section


def test_auditor_and_patcher_pipeline():
    orig_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            root = Path(tmpdir)
            (root / "docs").mkdir(parents=True)
            (root / "README.md").write_text("# Project\n\n## API\n\ndef calculate_tax(amount): calculates tax.", encoding="utf-8")

            # Change cwd for test
            os.chdir(root)

            state: SyncState = {
                "git_diff": "",
                "ast_deltas": [{
                    "file_path": "src/tax.py",
                    "change_type": "MODIFIED_FUNCTION",
                    "target": "calculate_tax",
                    "signature_delta": {
                        "raw_signature": "def calculate_tax(amount: float, country_code: str) -> float:",
                        "parameters": ["amount", "country_code"]
                    }
                }],
                "linked_docs": [],
                "drift_reports": [],
                "proposed_patches": []
            }

            audited_state = audit_specification_drift(state)
            assert len(audited_state["drift_reports"]) > 0

            patched_state = generate_documentation_patches(audited_state)
            assert len(patched_state["proposed_patches"]) > 0
            patch = patched_state["proposed_patches"][0]
            assert "calculate_tax" in patch["target"]
            assert patch["patched_text"] != ""
        finally:
            os.chdir(orig_cwd)


def test_apply_patch_safe_in_place_replacement():
    with tempfile.TemporaryDirectory() as tmpdir:
        doc_file = Path(tmpdir) / "docs" / "guide.md"
        doc_file.parent.mkdir(parents=True)

        original_content = """# Guide

## API
### `getUser`
Old description of getUser.

## Architecture
Rest of document that must NOT be erased.
"""
        doc_file.write_text(original_content, encoding="utf-8")

        patch = {
            "file": str(doc_file),
            "original_text": "### `getUser`\nOld description of getUser.",
            "patched_text": "### `getUser`\nNew description with ID parameter.",
            "heading": "API",
            "is_new_file": False
        }

        apply_patch_to_file(doc_file, patch)

        updated = doc_file.read_text(encoding="utf-8")
        # Check that target section was updated
        assert "New description with ID parameter." in updated
        # Check that the rest of the file was preserved!
        assert "## Architecture" in updated
        assert "Rest of document that must NOT be erased." in updated


def test_parser_multi_language():
    js_diff = """diff --git a/frontend/api.ts b/frontend/api.ts
--- a/frontend/api.ts
+++ b/frontend/api.ts
@@ -1,3 +1,5 @@
+export async function fetchUserProfile(userId: string): Promise<User> {
+export class UserCacheManager {
+const calculateDiscount = (price: number, rate: number) => {
"""
    state = parse_git_diff_ast({"git_diff": js_diff})
    targets = [d["target"] for d in state["ast_deltas"]]
    assert "fetchUserProfile" in targets
    assert "UserCacheManager" in targets
    assert "calculateDiscount" in targets

    go_diff = """diff --git a/pkg/service.go b/pkg/service.go
--- a/pkg/service.go
+++ b/pkg/service.go
@@ -1,2 +1,3 @@
+func HandleWebhook(payload []byte) error {
"""
    go_state = parse_git_diff_ast({"git_diff": go_diff})
    go_targets = [d["target"] for d in go_state["ast_deltas"]]
    assert "HandleWebhook" in go_targets


def test_graph_manager_multi_format_and_relevance_ranking():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "docs").mkdir(parents=True)

        (root / "README.md").write_text("# Main Readme", encoding="utf-8")
        (root / "docs" / "auth.markdown").write_text("# Auth Guide\n\nMentions `verify_token`.", encoding="utf-8")
        (root / "docs" / "guide.adoc").write_text("= AsciiDoc Guide\n\nOverview.", encoding="utf-8")
        (root / "docs" / "notes.txt").write_text("API Notes\n\nGeneral notes.", encoding="utf-8")

        gm = ArchitectureGraphManager(root_dir=root)
        docs = gm.discover_docs()
        assert "docs/auth.markdown" in docs
        assert "docs/guide.adoc" in docs
        assert "docs/notes.txt" in docs

        # Linking with verify_token symbol should rank auth.markdown at top
        linked = gm.get_linked_docs(
            "src/security/verifier.py",
            structural_deltas=[{"target": "verify_token", "signature_delta": {}}]
        )
        assert linked[0] == "docs/auth.markdown"


def test_no_extra_docs_created():
    """Verify that mapped docs that do not exist are not created, and only existing docs are modified."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "config").mkdir(parents=True)
        # Create mapping pointing to nonexistent docs
        config_data = {
            "mappings": [
                {"code_glob": "backend/**/*.py", "doc_path": "docs/nonexistent_auth.md"}
            ]
        }
        (root / "config" / "mapping.yaml").write_text(yaml.dump(config_data), encoding="utf-8")
        
        # Only create README.md
        (root / "README.md").write_text("# Project Readme\n\n## Backend\nBackend details.", encoding="utf-8")

        gm = ArchitectureGraphManager(root_dir=root)
        discovered = gm.discover_docs()
        # Ensure nonexistent mapped file is NOT in discovered docs
        assert "docs/nonexistent_auth.md" not in discovered
        assert discovered == ["README.md"]

        # Ensure get_linked_docs links existing README.md, NOT nonexistent_auth.md
        linked = gm.get_linked_docs("backend/service.py")
        assert "docs/nonexistent_auth.md" not in linked
        assert "README.md" in linked

        # Ensure apply_patch_to_file refuses to create nonexistent files
        nonexistent_file = root / "docs" / "nonexistent_auth.md"
        patch = {
            "file": str(nonexistent_file),
            "target": "login",
            "patched_text": "### `login`\nNew login function."
        }
        apply_patch_to_file(nonexistent_file, patch)
        assert not nonexistent_file.exists()


def test_no_repetitions_in_patch_application():
    """Verify that patches do not cause duplicate sections, duplicate headings, or repeated text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        doc_file = Path(tmpdir) / "README.md"
        doc_file.write_text("# Project\n\n## API Reference\n\n### `create_user`\nOld signature.\n", encoding="utf-8")

        patch = {
            "file": str(doc_file),
            "target": "create_user",
            "original_text": "### `create_user`\nOld signature.",
            "patched_text": "### `create_user`\nUpdated signature with email.",
            "heading": "API Reference"
        }

        # Apply once
        apply_patch_to_file(doc_file, patch)
        content_after_first = doc_file.read_text(encoding="utf-8")
        assert content_after_first.count("### `create_user`") == 1
        assert "Updated signature with email." in content_after_first

        # Apply second time (idempotent / no duplicate)
        apply_patch_to_file(doc_file, patch)
        content_after_second = doc_file.read_text(encoding="utf-8")
        assert content_after_second.count("### `create_user`") == 1

        # Test patcher deduplication when multiple drift reports for same target exist
        state: SyncState = {
            "drift_reports": [
                {
                    "doc_file": str(doc_file),
                    "target": "create_user",
                    "discrepancy": "signature changed",
                    "excerpt": "### `create_user`\nUpdated signature with email.",
                    "heading": "API Reference",
                    "is_symbol_present": True,
                    "delta": {"target": "create_user", "change_type": "MODIFIED_FUNCTION"}
                },
                {
                    "doc_file": str(doc_file),
                    "target": "create_user",
                    "discrepancy": "parameter added",
                    "excerpt": "### `create_user`\nUpdated signature with email.",
                    "heading": "API Reference",
                    "is_symbol_present": True,
                    "delta": {"target": "create_user", "change_type": "MODIFIED_FUNCTION"}
                }
            ]
        }
        patched_state = generate_documentation_patches(state)
        # Should be deduplicated to exactly 1 patch
        assert len(patched_state["proposed_patches"]) == 1


