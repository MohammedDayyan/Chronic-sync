import re
from typing import Dict, List
from src.state import SyncState

def parse_git_diff_ast(state: SyncState) -> SyncState:
    git_diff = state.get("git_diff", "")
    ast_deltas: List[Dict] = []
    current_file = "unknown.py"

    for line in git_diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            added_line = line[1:].strip()
            # Detect function definitions or signature modifications in diff
            if added_line.startswith("def "):
                func_match = re.match(r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)", added_line)
                if func_match:
                    func_name = func_match.group(1)
                    params_str = func_match.group(2)
                    parameters = [
                        p.strip().split(':')[0].split('=')[0].strip() 
                        for p in params_str.split(',') 
                        if p.strip()
                    ]
                    ast_deltas.append({
                        "file_path": current_file,
                        "change_type": "MODIFIED_FUNCTION",
                        "target": func_name,
                        "signature_delta": {
                            "raw_signature": added_line,
                            "parameters": parameters
                        }
                    })

    # Fallback if general changes exist but no exact 'def ' match was parsed
    if not ast_deltas and git_diff.strip():
        ast_deltas.append({
            "file_path": current_file,
            "change_type": "GENERAL_DIFF",
            "target": "file_modification",
            "signature_delta": {"raw_diff_summary": git_diff[:300]}
        })

    state["ast_deltas"] = ast_deltas
    return state