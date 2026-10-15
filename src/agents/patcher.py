import re
from typing import Dict, List, Any
from src.state import SyncState
from src.engine.model import LocalHFModel
from src.utils.logger import log_info


def clean_markdown_fences(text: str) -> str:
    """Cleans unwanted enclosing markdown code fences if generated as meta wrappers."""
    stripped = text.strip()
    if stripped.startswith("```markdown") and stripped.endswith("```"):
        stripped = stripped[len("```markdown"): -3].strip()
    elif stripped.startswith("```md") and stripped.endswith("```"):
        stripped = stripped[len("```md"): -3].strip()
    return stripped


def deduplicate_text_blocks(text: str) -> str:
    """Removes accidental consecutive duplicate lines, headings, or paragraphs within generated text."""
    lines = text.splitlines()
    deduped_lines = []
    prev_line = None

    for line in lines:
        stripped = line.strip()
        # Avoid immediate duplicate headings or non-empty duplicate lines
        if stripped and stripped == prev_line:
            continue
        deduped_lines.append(line)
        if stripped:
            prev_line = stripped

    result = "\n".join(deduped_lines).strip()
    # Collapse multiple consecutive blank lines to at most one
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def generate_documentation_patches(state: SyncState) -> SyncState:
    """Evaluates drift reports and structural deltas to dynamically generate

    contextual documentation patches for existing documents without repetition.
    """
    drift_reports = state.get("drift_reports", [])
    proposed_patches: List[Dict[str, Any]] = []
    seen_patch_targets = set()
    llm = LocalHFModel.get_instance()

    for report in drift_reports:
        doc_file = report.get("doc_file")
        target = report.get("target", "component")

        # Deduplicate: Only one patch per (file, target)
        patch_key = (doc_file, target)
        if patch_key in seen_patch_targets:
            continue

        discrepancy = report.get("discrepancy", "Documentation drift detected")
        excerpt = report.get("excerpt", "")
        heading = report.get("heading", "")
        is_symbol_present = report.get("is_symbol_present", False)
        delta = report.get("delta", {})

        change_type = delta.get("change_type", "MODIFICATION")
        sig_delta = delta.get("signature_delta", {})
        raw_sig = sig_delta.get("raw_signature", target)
        params = sig_delta.get("parameters", [])
        return_type = sig_delta.get("return_type", "")
        decorators = sig_delta.get("decorators", [])

        log_info(f"Generating patch for '{doc_file}' (target: {target})...")

        # Case 1: Existing symbol in document requires update
        if is_symbol_present and excerpt.strip():
            prompt = f"""Task: Rewrite the following documentation excerpt to resolve the discrepancy and reflect the structural code change.

Discrepancy: {discrepancy}
Target Symbol: {target}
Change Type: {change_type}
Updated Signature: {raw_sig}
Updated Parameters: {params}
Return Type: {return_type}
Decorators: {decorators}

Original Documentation Excerpt:
{excerpt}

Rules:
- Update the excerpt so that the signature, parameters, and descriptions match the code change.
- Preserve the existing style, headings, and markdown formatting of the excerpt.
- Do NOT repeat the heading or sections multiple times.
- Do NOT include commentary, notes, or conversational filler.
- Output ONLY the updated documentation excerpt.

Updated Excerpt:"""

            token_budget = max(256, min(1024, len(excerpt.split()) * 3 + 120))
            patched_text = llm.generate(prompt, max_tokens=token_budget, temperature=0.1)
            patched_text = clean_markdown_fences(patched_text)
            patched_text = deduplicate_text_blocks(patched_text)

            proposed_patches.append({
                "file": doc_file,
                "target": target,
                "original_text": excerpt,
                "patched_text": patched_text,
                "heading": heading,
                "confidence_score": 0.95
            })
            seen_patch_targets.add(patch_key)

        # Case 2: Document exists but target symbol is not yet documented
        else:
            prompt = f"""Task: Write a single, concise documentation section in Markdown for this code component to be integrated into an existing document.

Target Symbol: {target}
Change Type: {change_type}
Signature: {raw_sig}
Parameters: {params}
Return Type: {return_type}
Decorators: {decorators}
Context Section Heading: {heading}

Rules:
- Write a single Markdown subsection (using '### `{target}`') explaining what this component does, its signature, and its parameters.
- Do NOT duplicate headings or repeat the section.
- Do NOT include commentary or conversational filler.
- Output ONLY the new Markdown subsection.

Documentation Subsection:"""

            token_budget = 384
            patched_text = llm.generate(prompt, max_tokens=token_budget, temperature=0.1)
            patched_text = clean_markdown_fences(patched_text)
            patched_text = deduplicate_text_blocks(patched_text)

            proposed_patches.append({
                "file": doc_file,
                "target": target,
                "original_text": "",
                "patched_text": patched_text,
                "heading": heading,
                "confidence_score": 0.90
            })
            seen_patch_targets.add(patch_key)

    state["proposed_patches"] = proposed_patches
    return state