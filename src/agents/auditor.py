import json
import re
from pathlib import Path
from src.state import SyncState
from src.engine.graph import ArchitectureGraphManager
from src.engine.model import LocalHFModel
from src.utils.logger import log_info

def audit_specification_drift(state: SyncState) -> SyncState:
    graph_manager = ArchitectureGraphManager()
    ast_deltas = state.get("ast_deltas", [])
    linked_docs_list = []
    drift_reports = []
    
    llm = LocalHFModel.get_instance()

    for delta in ast_deltas:
        file_path = delta.get("file_path", "")
        if file_path == "unknown.py" or not file_path:
            continue
            
        linked_files = graph_manager.get_linked_docs(file_path)
        log_info(f"Auditing code change in '{file_path}' against docs: {linked_files}")

        for doc_file in linked_files:
            doc_path = Path(doc_file)
            if doc_path.exists():
                with open(doc_path, "r", encoding="utf-8", errors="ignore") as f:
                    doc_content = f.read(2000)
            else:
                continue

            linked_docs_list.append({"doc_file": doc_file, "excerpt": doc_content[:300]})

            prompt = f"""You are a technical documentation auditor. Compare the code change with the documentation excerpt. Determine if there is a semantic discrepancy or drift.

Code Delta:
{delta}

Documentation Excerpt ({doc_file}):
{doc_content}

Respond ONLY with a valid JSON object in this exact format (no extra markdown wrappers or text):
{{"has_drift": true, "discrepancy": "brief description", "severity": "MEDIUM"}}
JSON:"""

            response_text = llm.generate(prompt, max_tokens=256)
            
            try:
                match = re.search(r"\{.*\}", response_text, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    if data.get("has_drift", False):
                        drift_reports.append({
                            "doc_file": doc_file,
                            "discrepancy": data.get("discrepancy", "Unspecified drift"),
                            "severity": data.get("severity", "MEDIUM"),
                            "excerpt": doc_content[:200]
                        })
            except Exception:
                if "true" in response_text.lower():
                    drift_reports.append({
                        "doc_file": doc_file,
                        "discrepancy": f"Detected mismatch in target '{delta.get('target', 'code')}'.",
                        "severity": "MEDIUM",
                        "excerpt": doc_content[:200]
                    })

    state["linked_docs"] = linked_docs_list
    state["drift_reports"] = drift_reports
    return state