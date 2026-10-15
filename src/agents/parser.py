import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
from src.state import SyncState


def _clean_param_list(params_str: str) -> List[str]:
    """Extracts clean parameter names from a parameters string."""
    params = []
    # Split on commas taking nesting into account
    depth = 0
    current = []
    for char in params_str:
        if char in "([<{":
            depth += 1
            current.append(char)
        elif char in ")]>}":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            p_text = "".join(current).strip()
            if p_text:
                params.append(p_text.split(":")[0].split("=")[0].strip())
            current = []
        else:
            current.append(char)
    last_p = "".join(current).strip()
    if last_p:
        params.append(last_p.split(":")[0].split("=")[0].strip())
    return [p for p in params if p and p != "*"]


def _extract_python_ast_info(file_path: str, target_name: str) -> Optional[Dict[str, Any]]:
    """Inspects the actual Python file AST on disk if available to enrich signature details."""
    p = Path(file_path)
    if not p.exists() or not p.is_file() or p.suffix.lower() != ".py":
        return None

    try:
        source = p.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except Exception:
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_name:
            args = [a.arg for a in node.args.args]
            decorators = []
            for d in node.decorator_list:
                if isinstance(d, ast.Name):
                    decorators.append(d.id)
                elif isinstance(d, ast.Call) and isinstance(d.func, (ast.Name, ast.Attribute)):
                    decorators.append(ast.unparse(d))
                elif hasattr(ast, "unparse"):
                    decorators.append(ast.unparse(d))

            docstring = ast.get_docstring(node) or ""
            return {
                "parameters": args,
                "decorators": decorators,
                "docstring": docstring[:300] if docstring else "",
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            }
        elif isinstance(node, ast.ClassDef) and node.name == target_name:
            methods = [
                m.name for m in node.body 
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            docstring = ast.get_docstring(node) or ""
            return {
                "methods": methods,
                "docstring": docstring[:300] if docstring else "",
            }

    return None


def parse_git_diff_ast(state: SyncState) -> SyncState:
    """Dynamically parses a git diff across languages, extracting structural deltas

    for functions, classes, methods, decorators, and signature changes.
    """
    git_diff = state.get("git_diff", "")
    ast_deltas: List[Dict[str, Any]] = []

    if not git_diff.strip():
        state["ast_deltas"] = []
        return state

    # Split diff into per-file chunks
    file_chunks = re.split(r"(?m)^diff --git ", git_diff)
    if len(file_chunks) == 1 and not git_diff.startswith("diff --git"):
        # Single diff block without standard header
        file_chunks = [git_diff]

    for chunk in file_chunks:
        if not chunk.strip():
            continue

        # Extract file path
        current_file = "unknown.py"
        file_match = re.search(r"(?:^\+\+\+ b/|^--- a/|^[ab]/)([\w\-\./]+)", chunk, re.MULTILINE)
        if file_match:
            current_file = file_match.group(1).replace("\\", "/")
        else:
            header_match = re.search(r"a/([\w\-\./]+)\s+b/([\w\-\./]+)", chunk)
            if header_match:
                current_file = header_match.group(2).replace("\\", "/")

        ext = Path(current_file).suffix.lower()
        lines = chunk.splitlines()

        recent_decorators: List[str] = []
        processed_targets: Set[str] = set()

        for idx, line in enumerate(lines):
            stripped = line.strip()

            # Track decorators on added lines (exclude git diff hunk headers like @@ -1,5 +1,5 @@)
            if not stripped.startswith(("@@", "+@@")) and (stripped.startswith("+@") or stripped.startswith("@")):
                dec = stripped.lstrip("+").strip()
                recent_decorators.append(dec)
                continue

            # 1. Added / Modified Python Function or Method
            if line.startswith("+") and not line.startswith("+++"):
                added_line = line[1:].strip()

                # Python function or async function
                func_match = re.match(
                    r"(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)(?:\s*->\s*(.*?))?:",
                    added_line
                )
                if func_match:
                    func_name = func_match.group(1)
                    params_raw = func_match.group(2)
                    return_type = func_match.group(3).strip() if func_match.group(3) else None
                    parameters = _clean_param_list(params_raw)

                    # Enrich from disk AST if possible
                    ast_info = _extract_python_ast_info(current_file, func_name) or {}
                    if "parameters" in ast_info and ast_info["parameters"]:
                        parameters = ast_info["parameters"]

                    decorators = list(recent_decorators)
                    if "decorators" in ast_info and ast_info["decorators"]:
                        decorators = list(set(decorators + ast_info["decorators"]))

                    change_type = "API_ROUTE" if any("route" in d or "get" in d or "post" in d for d in decorators) else "MODIFIED_FUNCTION"
                    
                    ast_deltas.append({
                        "file_path": current_file,
                        "change_type": change_type,
                        "target": func_name,
                        "signature_delta": {
                            "raw_signature": added_line,
                            "parameters": parameters,
                            "return_type": return_type,
                            "decorators": decorators,
                            "docstring": ast_info.get("docstring", "")
                        }
                    })
                    processed_targets.add(func_name)
                    recent_decorators = []
                    continue

                # Python Class Definition
                class_match = re.match(r"class\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\((.*?)\))?:", added_line)
                if class_match:
                    class_name = class_match.group(1)
                    bases = [b.strip() for b in class_match.group(2).split(",")] if class_match.group(2) else []
                    ast_info = _extract_python_ast_info(current_file, class_name) or {}

                    ast_deltas.append({
                        "file_path": current_file,
                        "change_type": "MODIFIED_CLASS",
                        "target": class_name,
                        "signature_delta": {
                            "raw_signature": added_line,
                            "bases": bases,
                            "methods": ast_info.get("methods", []),
                            "docstring": ast_info.get("docstring", "")
                        }
                    })
                    processed_targets.add(class_name)
                    recent_decorators = []
                    continue

                # TypeScript / JavaScript function or class support
                if ext in (".ts", ".js", ".tsx", ".jsx"):
                    js_func = re.match(r"(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)", added_line)
                    js_arrow = re.match(r"(?:export\s+)?(?:const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:async\s*)?\((.*?)\)\s*=>", added_line)
                    js_class = re.match(r"(?:export\s+)?class\s+([a-zA-Z_][a-zA-Z0-9_]*)", added_line)

                    if js_func or js_arrow:
                        m = js_func or js_arrow
                        name = m.group(1)
                        params = _clean_param_list(m.group(2))
                        ast_deltas.append({
                            "file_path": current_file,
                            "change_type": "MODIFIED_FUNCTION",
                            "target": name,
                            "signature_delta": {
                                "raw_signature": added_line,
                                "parameters": params
                            }
                        })
                        processed_targets.add(name)
                        continue
                    elif js_class:
                        name = js_class.group(1)
                        ast_deltas.append({
                            "file_path": current_file,
                            "change_type": "MODIFIED_CLASS",
                            "target": name,
                            "signature_delta": {
                                "raw_signature": added_line
                            }
                        })
                        processed_targets.add(name)
                        continue

                # Go support
                if ext == ".go":
                    go_func = re.match(r"func\s+(?:\(.*?\)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)", added_line)
                    if go_func:
                        name = go_func.group(1)
                        ast_deltas.append({
                            "file_path": current_file,
                            "change_type": "MODIFIED_FUNCTION",
                            "target": name,
                            "signature_delta": {
                                "raw_signature": added_line,
                                "parameters": _clean_param_list(go_func.group(2))
                            }
                        })
                        processed_targets.add(name)
                        continue

            # 2. Removed Functions or Classes
            elif line.startswith("-") and not line.startswith("---"):
                removed_line = line[1:].strip()
                rem_func = re.match(r"(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", removed_line)
                rem_class = re.match(r"class\s+([a-zA-Z_][a-zA-Z0-9_]*)", removed_line)
                if rem_func:
                    name = rem_func.group(1)
                    if name not in processed_targets:
                        ast_deltas.append({
                            "file_path": current_file,
                            "change_type": "REMOVED_FUNCTION",
                            "target": name,
                            "signature_delta": {"raw_signature": removed_line}
                        })
                        processed_targets.add(name)
                elif rem_class:
                    name = rem_class.group(1)
                    if name not in processed_targets:
                        ast_deltas.append({
                            "file_path": current_file,
                            "change_type": "REMOVED_CLASS",
                            "target": name,
                            "signature_delta": {"raw_signature": removed_line}
                        })
                        processed_targets.add(name)

            # Reset recent decorators if non-decorator code line passed
            if not stripped.startswith(("+@", "@", "+def", "+async def", "+class")):
                recent_decorators = []

        # If general modifications occurred in this file chunk but no specific signature was captured
        file_deltas = [d for d in ast_deltas if d["file_path"] == current_file]
        if not file_deltas and current_file != "unknown.py":
            ast_deltas.append({
                "file_path": current_file,
                "change_type": "GENERAL_DIFF",
                "target": Path(current_file).stem,
                "signature_delta": {
                    "raw_diff_summary": chunk[:400].strip()
                }
            })

    # Global fallback if nothing extracted
    if not ast_deltas and git_diff.strip():
        ast_deltas.append({
            "file_path": "code_modification",
            "change_type": "GENERAL_DIFF",
            "target": "repository",
            "signature_delta": {"raw_diff_summary": git_diff[:400].strip()}
        })

    state["ast_deltas"] = ast_deltas
    return state