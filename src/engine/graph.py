import os
import re
from pathlib import Path, PurePath
from typing import List, Dict, Optional, Set, Tuple, Any
import networkx as nx
import yaml

from src.utils.logger import log_info


def match_glob_pattern(path_str: str, pattern_str: str) -> bool:
    """Matches a normalized path against a glob pattern supporting ** recursively."""
    p = path_str.replace("\\", "/").strip("/")
    pat = pattern_str.replace("\\", "/").strip("/")

    # Check direct pathlib match first
    try:
        if PurePath(p).match(pat):
            return True
    except Exception:
        pass

    # Regex fallback for ** matching 0 or more directory levels
    regex_pat = re.escape(pat)
    regex_pat = regex_pat.replace(r"\*\*/", "(?:.*/)?")
    regex_pat = regex_pat.replace(r"/\*\*", "(?:/.*)?")
    regex_pat = regex_pat.replace(r"\*", "[^/]*")
    regex_pat = regex_pat.replace(r"\?", "[^/]")
    return bool(re.match(f"^{regex_pat}$", p, re.IGNORECASE))


class ArchitectureGraphManager:
    """Dynamic Knowledge & Architecture Graph Manager for linking code changes and structural

    deltas to project documentation files using NetworkX.
    """

    DOC_EXTENSIONS = (".md", ".markdown", ".rst", ".adoc", ".txt")
    DEFAULT_IGNORE_DIRS = {
        ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
        "dist", "build", ".eggs", ".tox", ".mypy_cache", ".pytest_cache"
    }

    def __init__(
        self,
        db_path: str = ".chronicle_sync.db",
        config_path: Optional[str] = None,
        root_dir: Optional[Path] = None,
    ):
        self.db_path = db_path
        self.root_dir = Path(root_dir).resolve() if root_dir else Path.cwd()
        self.config_path = config_path
        self.graph = nx.DiGraph()
        self.mappings: List[Dict[str, str]] = []
        self._doc_cache: Dict[str, str] = {}

        self.load_config()
        self.build_graph()

    def load_config(self) -> List[Dict[str, str]]:
        """Dynamically discovers and loads documentation-to-code routing configuration."""
        candidate_paths = []
        if self.config_path:
            candidate_paths.append(Path(self.config_path))
        env_config = os.getenv("CHRONICLE_CONFIG_PATH")
        if env_config:
            candidate_paths.append(Path(env_config))
        candidate_paths.extend([
                self.root_dir / "config" / "mapping.yaml",
                self.root_dir / "config" / "mapping.yml",
                self.root_dir / ".chronicle-sync.yaml",
                self.root_dir / ".chronicle-sync.yml",
                self.root_dir / "chronicle-sync.yaml",
                self.root_dir / "mapping.yaml",
            ])

        loaded_mappings = []
        for path in candidate_paths:
            if path.exists() and path.is_file():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    raw_mappings = data.get("mappings", [])
                    for item in raw_mappings:
                        code_glob = item.get("code_glob") or item.get("code_path") or item.get("pattern")
                        doc_path = item.get("doc_path") or item.get("doc") or item.get("target")
                        if code_glob and doc_path:
                            loaded_mappings.append({
                                "code_glob": code_glob.replace("\\", "/"),
                                "doc_path": doc_path.replace("\\", "/")
                            })
                    if loaded_mappings:
                        break
                except Exception as err:
                    log_info(f"Warning: Failed reading config {path}: {err}")

        self.mappings = loaded_mappings
        return self.mappings

    def discover_docs(self) -> List[str]:
        """Discovers all documentation files in the repository dynamically."""
        docs: Set[str] = set()

        for root, dirs, files in os.walk(self.root_dir):
            # Prune ignored directories
            dirs[:] = [
                d for d in dirs 
                if d not in self.DEFAULT_IGNORE_DIRS and not d.endswith(".egg-info")
            ]

            rel_root = Path(root).relative_to(self.root_dir)
            for file in files:
                file_lower = file.lower()
                ext = Path(file).suffix.lower()

                # Handle markdown, rst, adoc
                if ext in (".md", ".markdown", ".rst", ".adoc"):
                    rel_file = str(rel_root / file).replace("\\", "/")
                    if rel_file.startswith("./"):
                        rel_file = rel_file[2:]
                    docs.add(rel_file)
                # Handle text files only if doc-related
                elif ext == ".txt":
                    rel_file = str(rel_root / file).replace("\\", "/")
                    if rel_file.startswith("./"):
                        rel_file = rel_file[2:]
                    parts = rel_file.lower().split("/")
                    if any(p in ("docs", "doc", "documentation", "spec", "specs") for p in parts) or \
                       any(k in file_lower for k in ("readme", "architecture", "changelog", "spec")):
                        docs.add(rel_file)

        # Only include mapped doc paths if the file actually exists on disk
        for m in self.mappings:
            doc_path = m["doc_path"]
            if (self.root_dir / doc_path).is_file():
                docs.add(doc_path)

        return sorted([d for d in docs if (self.root_dir / d).is_file()])

    def _read_doc_text(self, rel_path: str) -> str:
        """Reads document content with caching."""
        if rel_path in self._doc_cache:
            return self._doc_cache[rel_path]

        full_path = self.root_dir / rel_path
        if full_path.exists() and full_path.is_file():
            try:
                content = full_path.read_text(encoding="utf-8", errors="ignore")
                self._doc_cache[rel_path] = content
                return content
            except Exception:
                return ""
        return ""

    def build_graph(self):
        """Builds the NetworkX knowledge graph connecting code globs, files, and docs."""
        self.graph.clear()
        discovered_docs = self.discover_docs()

        # Add documentation nodes
        for doc in discovered_docs:
            doc_node = f"doc:{doc}"
            doc_name = Path(doc).name
            doc_stem = Path(doc).stem
            self.graph.add_node(
                doc_node,
                type="doc",
                path=doc,
                name=doc_name,
                stem=doc_stem
            )

        # Connect configuration mappings
        for mapping in self.mappings:
            code_glob = mapping["code_glob"]
            doc_path = mapping["doc_path"]
            glob_node = f"glob:{code_glob}"
            doc_node = f"doc:{doc_path}"

            self.graph.add_node(glob_node, type="glob", pattern=code_glob)
            self.graph.add_edge(glob_node, doc_node, weight=1.0, relation="config_mapping")

    def _extract_tokens(self, text: str) -> Set[str]:
        """Extracts identifier words and tokens from camelCase, snake_case, and prose."""
        # Split camelCase and snake_case
        s1 = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
        words = re.findall(r'[a-zA-Z0-9]+', s1.lower())
        stop_words = {
            "the", "a", "an", "is", "in", "it", "of", "to", "and", "or", "for", 
            "with", "on", "at", "by", "from", "up", "about", "into", "over", 
            "after", "self", "none", "true", "false", "py", "md", "txt", "def", "class"
        }
        return {w for w in words if len(w) > 2 and w not in stop_words}

    def get_linked_docs(
        self, 
        code_file: str, 
        structural_deltas: Optional[List[Dict[str, Any]]] = None
    ) -> List[str]:
        """Dynamically identifies and ranks documentation files linked to a code file

        and its extracted structural deltas using the knowledge graph and semantic cues.
        """
        # Normalize code file path relative to root
        code_p = Path(code_file)
        if code_p.is_absolute():
            try:
                code_file = str(code_p.relative_to(self.root_dir))
            except ValueError:
                code_file = str(code_p)
        code_file = code_file.replace("\\", "/").strip("/")

        doc_scores: Dict[str, float] = {}
        doc_reasons: Dict[str, List[str]] = {}

        def record_score(doc: str, score: float, reason: str):
            doc_scores[doc] = doc_scores.get(doc, 0.0) + score
            doc_reasons.setdefault(doc, []).append(reason)

        all_docs = self.discover_docs()
        code_stem = Path(code_file).stem.lower()
        code_name = Path(code_file).name.lower()
        code_parts = [p.lower() for p in Path(code_file).parts if p not in ("src", "lib", "app")]

        # Extract delta targets, functions, classes, and parameter names
        delta_symbols: Set[str] = set()
        delta_tokens: Set[str] = set()

        if structural_deltas:
            for delta in structural_deltas:
                target = delta.get("target")
                if target and target not in ("file_modification", "unknown"):
                    delta_symbols.add(target)
                    delta_tokens.update(self._extract_tokens(target))
                
                # Parameters
                sig_delta = delta.get("signature_delta", {})
                params = sig_delta.get("parameters", [])
                for p in params:
                    if isinstance(p, str) and p not in ("self", "cls"):
                        delta_tokens.update(self._extract_tokens(p))

        # 1. Config mapping check (Highest deterministic priority for existing files)
        for mapping in self.mappings:
            code_glob = mapping["code_glob"]
            doc_path = mapping["doc_path"]
            if (self.root_dir / doc_path).is_file() and match_glob_pattern(code_file, code_glob):
                record_score(doc_path, 1.2, f"matches config rule '{code_glob}'")

        # 2. Document content inspection for exact symbol & file mentions
        for doc in all_docs:
            if not (self.root_dir / doc).is_file():
                continue
            doc_content = self._read_doc_text(doc)
            doc_lower = doc.lower()
            doc_stem = Path(doc).stem.lower()

            if doc_content:
                # Direct code file mention (e.g. "src/engine/graph.py" or "graph.py")
                if code_file.lower() in doc_content.lower():
                    record_score(doc, 0.95, f"mentions code file '{code_file}'")
                elif code_name in doc_content.lower():
                    record_score(doc, 0.8, f"mentions file name '{code_name}'")

                # Exact symbol mentions (e.g. function or class name from AST)
                for sym in delta_symbols:
                    # Look for exact word boundary match
                    pattern = rf"\b{re.escape(sym)}\b"
                    if re.search(pattern, doc_content):
                        record_score(doc, 1.0, f"contains exact symbol '{sym}'")
                    elif sym.lower() in doc_content.lower():
                        record_score(doc, 0.7, f"mentions symbol '{sym}' (case-insensitive)")

                # Token overlap between deltas and doc content
                if delta_tokens:
                    doc_tokens = self._extract_tokens(doc_content[:10000])
                    overlap = delta_tokens.intersection(doc_tokens)
                    if overlap:
                        record_score(doc, min(0.6, 0.15 * len(overlap)), f"shares tokens: {list(overlap)[:3]}")

            # 3. Path & naming similarity (stem or folder proximity)
            if code_stem == doc_stem:
                record_score(doc, 0.85, f"matches doc stem '{doc_stem}'")
            elif any(part in doc_lower for part in code_parts if len(part) > 3):
                record_score(doc, 0.5, f"shares path segment with '{code_file}'")

        # 4. Default baseline for README / Architecture documents (existing only)
        for doc in all_docs:
            if not (self.root_dir / doc).is_file():
                continue
            doc_lower = doc.lower()
            if "readme" in doc_lower:
                record_score(doc, 0.4, "project readme overview")
            elif any(k in doc_lower for k in ("arch", "architecture", "overview", "spec")):
                record_score(doc, 0.35, "core architecture specification")

        # Update NetworkX graph with computed link edges
        code_node = f"code:{code_file}"
        self.graph.add_node(code_node, type="code", path=code_file)
        for doc, score in doc_scores.items():
            doc_node = f"doc:{doc}"
            if doc_node in self.graph:
                self.graph.add_edge(code_node, doc_node, weight=score, reasons=doc_reasons.get(doc, []))

        # Filter and rank candidates (strictly existing files only)
        existing_docs = [d for d in all_docs if (self.root_dir / d).is_file()]
        ranked_docs = sorted(
            [d for d, s in doc_scores.items() if s >= 0.5 and (self.root_dir / d).is_file()],
            key=lambda d: doc_scores[d],
            reverse=True
        )

        # Fallback if no specific doc reached threshold
        if not ranked_docs:
            fallback = sorted(
                [d for d in doc_scores.keys() if (self.root_dir / d).is_file()],
                key=lambda d: doc_scores[d],
                reverse=True
            )
            if fallback:
                ranked_docs = [fallback[0]]
            elif existing_docs:
                readmes = [d for d in existing_docs if "readme" in d.lower()]
                ranked_docs = [readmes[0]] if readmes else [existing_docs[0]]

        # Ensure deduplicated list preserving order
        seen = set()
        unique_docs = []
        for d in ranked_docs:
            if d not in seen and (self.root_dir / d).is_file():
                seen.add(d)
                unique_docs.append(d)

        return unique_docs