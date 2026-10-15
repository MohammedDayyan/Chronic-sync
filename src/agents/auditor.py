import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from src.state import SyncState
from src.engine.graph import ArchitectureGraphManager
from src.engine.model import LocalHFModel
from src.utils.logger import log_info


def find_relevant_doc_section(
    doc_content: str, 
    target: str, 
    file_path: str
) -> Tuple[str, str, bool]:
    """Dynamically locates the most relevant section in a documentation file for a given code delta.

    Returns: (section_text, section_heading, is_symbol_present)
    """
    lines = doc_content.splitlines()
    if not lines:
        return "", "", False

    target_lower = target.lower()
    file_stem = Path(file_path).stem.lower()

    # Step 1: Search for target symbol or file stem in lines
    match_line_idx = -1
    is_symbol_present = False

    for idx, line in enumerate(lines):
        # Exact word match for target symbol
        if re.search(rf"\b{re.escape(target)}\b", line, re.IGNORECASE):
            match_line_idx = idx
            is_symbol_present = True
            break

    if match_line_idx == -1:
        # Search for file stem
        for idx, line in enumerate(lines):
            if file_stem in line.lower() and len(file_stem) > 3:
                match_line_idx = idx
                break

    # If symbol or file stem was found, extract the enclosing heading block
    if match_line_idx != -1:
        # Scan backward for nearest heading
        heading_line_idx = match_line_idx
        heading_text = ""
        heading_level = 6
        for i in range(match_line_idx, -1, -1):
            h_match = re.match(r"^(#{1,6})\s+(.+)$", lines[i].strip())
            if h_match:
                heading_line_idx = i
                heading_level = len(h_match.group(1))
                heading_text = h_match.group(2).strip()
                break

        # Scan forward for next heading of same or higher level
        end_line_idx = len(lines)
        for i in range(match_line_idx + 1, len(lines)):
            h_match = re.match(r"^(#{1,6})\s+", lines[i].strip())
            if h_match and len(h_match.group(1)) <= heading_level:
                end_line_idx = i
                break
            # Safety limit for very long sections
            if i - heading_line_idx > 60:
                end_line_idx = i
                break

        section_lines = lines[heading_line_idx:end_line_idx]
        section_text = "\n".join(section_lines).strip()
        return section_text, heading_text, is_symbol_present

    # Step 2: Symbol not found. Look for architectural or API headings where it should be documented
    for idx, line in enumerate(lines):
        h_match = re.match(r"^(#{1,4})\s+(.+)$", line.strip())
        if h_match:
            h_title = h_match.group(2).lower()
            if any(k in h_title for k in ("api", "architecture", "component", "overview", "reference", "engine")):
                # Extract up to 25 lines under this heading
                end_idx = min(len(lines), idx + 25)
                for j in range(idx + 1, end_idx):
                    if re.match(r"^#{1,4}\s+", lines[j].strip()):
                        end_idx = j
                        break
                return "\n".join(lines[idx:end_idx]).strip(), h_match.group(2).strip(), False

    # Step 3: Default fallback to top excerpt of the document
    top_lines = lines[:30]
    return "\n".join(top_lines).strip(), "Overview", False


def audit_specification_drift(state: SyncState) -> SyncState:
    """Dynamically audits structural code deltas against linked documentation files

    using the ArchitectureGraphManager and local LLM reasoning.
    """
    graph_manager = ArchitectureGraphManager()
    ast_deltas = state.get("ast_deltas", [])
    linked_docs_list: List[Dict[str, Any]] = []
    drift_reports: List[Dict[str, Any]] = []

    llm = LocalHFModel.get_instance()

    for delta in ast_deltas:
        file_path = delta.get("file_path", "")
        if not file_path or file_path == "unknown.py":
            continue

        target = delta.get("target", "unnamed_symbol")
        sig_delta = delta.get("signature_delta", {})
        raw_sig = sig_delta.get("raw_signature", target)
        params = sig_delta.get("parameters", [])

        # Pass structural delta to graph manager for dynamic, context-aware linking
        linked_files = graph_manager.get_linked_docs(file_path, structural_deltas=[delta])
        log_info(f"Auditing code change in '{file_path}' (target: {target}) against docs: {linked_files}")

        for doc_file in linked_files:
            doc_path = Path(doc_file)
            if not doc_path.is_absolute():
                doc_path = graph_manager.root_dir / doc_file

            if doc_path.exists() and doc_path.is_file():
                try:
                    doc_content = doc_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                
                section_text, heading_text, is_symbol_present = find_relevant_doc_section(
                    doc_content, target, file_path
                )
                linked_docs_list.append({
                    "doc_file": doc_file,
                    "target": target,
                    "excerpt": section_text[:300],
                    "heading": heading_text
                })

                # Formulate auditor prompt
                prompt = f"""You are a technical documentation auditor. Compare the code change with the documentation excerpt. Determine if there is a semantic discrepancy or drift.

Code Delta:
- File: {file_path}
- Change Type: {delta.get('change_type', 'MODIFICATION')}
- Target: {target}
- Signature: {raw_sig}
- Parameters: {params}

Documentation File: {doc_file}
Heading: {heading_text}
Current Excerpt:
{section_text}

Respond ONLY with a valid JSON object in this exact format (no extra markdown wrappers or text):
{{"has_drift": true, "discrepancy": "brief description of how documentation should be updated", "severity": "MEDIUM"}}
JSON:"""

                response_text = llm.generate(prompt, max_tokens=256)

                has_drift = False
                discrepancy = f"Update documentation to reflect structural changes in '{target}'."
                severity = "MEDIUM"

                try:
                    # Parse JSON safely
                    clean_json = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.MULTILINE)
                    clean_json = re.sub(r"\s*```$", "", clean_json.strip(), flags=re.MULTILINE)
                    match = re.search(r"\{.*?\}", clean_json, re.DOTALL)
                    if match:
                        data = json.loads(match.group(0))
                        has_drift = bool(data.get("has_drift", False))
                        discrepancy = data.get("discrepancy", discrepancy)
                        severity = data.get("severity", severity)
                    elif "true" in response_text.lower():
                        has_drift = True
                except Exception:
                    if "true" in response_text.lower():
                        has_drift = True

                # If symbol is newly added/modified and missing from the doc, flag drift
                if not is_symbol_present and delta.get("change_type") in ("MODIFIED_FUNCTION", "ADDED_FUNCTION", "API_ROUTE", "MODIFIED_CLASS"):
                    has_drift = True
                    discrepancy = f"Target symbol '{target}' is not documented in '{doc_file}' under section '{heading_text}'."

                if has_drift:
                    drift_reports.append({
                        "doc_file": doc_file,
                        "target": target,
                        "change_type": delta.get("change_type", "MODIFICATION"),
                        "discrepancy": discrepancy,
                        "severity": severity,
                        "excerpt": section_text,
                        "heading": heading_text,
                        "is_symbol_present": is_symbol_present,
                        "delta": delta
                    })

            else:
                # Mapped documentation file does not exist yet on disk
                log_info(f"Target documentation file '{doc_file}' does not exist yet. Flagging creation.")
                drift_reports.append({
                    "doc_file": doc_file,
                    "target": target,
                    "change_type": delta.get("change_type", "NEW_DOCUMENTATION"),
                    "discrepancy": f"Documentation file '{doc_file}' needs to be initialized to document '{target}'.",
                    "severity": "HIGH",
                    "excerpt": "",
                    "heading": "API Documentation",
                    "is_symbol_present": False,
                    "delta": delta
                })

    state["linked_docs"] = linked_docs_list
    state["drift_reports"] = drift_reports
    return state