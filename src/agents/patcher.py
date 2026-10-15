import re
from typing import Dict, List, Any
from src.state import SyncState
from src.engine.model import LocalHFModel
from src.utils.logger import log_info


def clean_markdown_fences(text: str) -> str:
    """Cleans unwanted enclosing markdown code fences if generated as meta wrappers."""
    stripped = text.strip()
    # If the model wrapped the entire markdown text in ```markdown ... ```
    if stripped.startswith("```markdown") and stripped.endswith("```"):
        stripped = stripped[len("```markdown"): -3].strip()
    elif stripped.startswith("```md") and stripped.endswith("```"):
        stripped = stripped[len("```md"): -3].strip()
    return stripped


def generate_documentation_patches(state: SyncState) -> SyncState:
    """Evaluates drift reports and structural deltas to dynamically generate

    contextual documentation patches.
    """
    drift_reports = state.get("drift_reports", [])
    proposed_patches: List[Dict[str, Any]] = []
    llm = LocalHFModel.get_instance()

    for report in drift_reports:
        doc_file = report.get("doc_file")
        discrepancy = report.get("discrepancy", "Documentation drift detected")
        excerpt = report.get("excerpt", "")
        heading = report.get("heading", "")
        is_symbol_present = report.get("is_symbol_present", False)
        delta = report.get("delta", {})

        target = delta.get("target") or report.get("target", "component")
        change_type = delta.get("change_type", "MODIFICATION")
        sig_delta = delta.get("signature_delta", {})
        raw_sig = sig_delta.get("raw_signature", target)
        params = sig_delta.get("parameters", [])
        return_type = sig_delta.get("return_type", "")
        decorators = sig_delta.get("decorators", [])

        log_info(f"Generating patch for '{doc_file}' (target: {target})...")

        # Case 1: Brand new documentation file
        if not excerpt.strip() and not is_symbol_present:
            prompt = f"""Task: Generate an initial technical documentation file in Markdown for a new software module.

Module/File: {doc_file}
Primary Target: {target}
Change Type: {change_type}
Signature: {raw_sig}
Parameters: {params}
Return Type: {return_type}
Decorators: {decorators}

Rules:
- Write clear, professional Markdown documentation.
- Include a title, overview, signature specifications, and parameter descriptions.
- Output ONLY the Markdown documentation without meta-conversation or greetings.

Markdown Content:"""

            token_budget = 512
            patched_text = llm.generate(prompt, max_tokens=token_budget, temperature=0.1)
            patched_text = clean_markdown_fences(patched_text)

            proposed_patches.append({
                "file": doc_file,
                "target": target,
                "original_text": "",
                "patched_text": patched_text,
                "heading": heading,
                "is_new_file": True,
                "confidence_score": 0.95
            })

        # Case 2: Existing symbol in document requires update
        elif is_symbol_present and excerpt.strip():
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
- Do NOT include commentary, notes, or filler.
- Output ONLY the updated documentation excerpt.

Updated Excerpt:"""

            # Dynamic token budget based on excerpt length
            token_budget = max(256, min(1024, len(excerpt.split()) * 3 + 120))
            patched_text = llm.generate(prompt, max_tokens=token_budget, temperature=0.1)
            patched_text = clean_markdown_fences(patched_text)

            proposed_patches.append({
                "file": doc_file,
                "target": target,
                "original_text": excerpt,
                "patched_text": patched_text,
                "heading": heading,
                "is_new_file": False,
                "confidence_score": 0.95
            })

        # Case 3: Document exists but target symbol is not yet documented
        else:
            prompt = f"""Task: Write a concise documentation section in Markdown to document this new/modified code component inside an existing document.

Target Symbol: {target}
Change Type: {change_type}
Signature: {raw_sig}
Parameters: {params}
Return Type: {return_type}
Decorators: {decorators}
Context Section Heading: {heading}

Rules:
- Write a Markdown subsection (using '### `{target}`') explaining what this component does, its signature, and its parameters.
- Do NOT include commentary or conversational filler.
- Output ONLY the new Markdown subsection.

Documentation Subsection:"""

            token_budget = 384
            patched_text = llm.generate(prompt, max_tokens=token_budget, temperature=0.1)
            patched_text = clean_markdown_fences(patched_text)

            proposed_patches.append({
                "file": doc_file,
                "target": target,
                "original_text": "",
                "patched_text": patched_text,
                "heading": heading,
                "is_new_file": False,
                "confidence_score": 0.90
            })

    state["proposed_patches"] = proposed_patches
    return state