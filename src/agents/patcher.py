from src.state import SyncState
from src.engine.model import LocalHFModel

def generate_documentation_patches(state: SyncState) -> SyncState:
    drift_reports = state.get("drift_reports", [])
    proposed_patches = []
    llm = LocalHFModel.get_instance()

    for report in drift_reports:
        doc_file = report.get("doc_file")
        discrepancy = report.get("discrepancy")
        excerpt = report.get("excerpt")

        prompt = f"""Task: Rewrite the following documentation excerpt to resolve this discrepancy: {discrepancy}

Original Excerpt:
{excerpt}

Rules:
- Output ONLY the direct corrected text replacement.
- Do NOT include notes, explanations, or conversational filler.
- Do NOT repeat instructions.

Corrected Text:"""

        # Strict token constraint prevents the model from rambling or looping
        patched_text = llm.generate(prompt, max_tokens=80, temperature=0.0)
        
        # Clean up any accidental markdown wrappers
        patched_text = patched_text.replace("```markdown", "").replace("```json", "").replace("```", "").strip()

        proposed_patches.append({
            "file": doc_file,
            "original_text": excerpt,
            "patched_text": patched_text,
            "confidence_score": 0.95
        })

    state["proposed_patches"] = proposed_patches
    return state