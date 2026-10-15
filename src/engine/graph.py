from pathlib import Path
from typing import List

class ArchitectureGraphManager:
    def __init__(self, db_path: str = ".chronicle_sync.db"):
        self.db_path = db_path

    def get_linked_docs(self, code_file: str) -> List[str]:
        root_dir = Path.cwd()
        all_mds = []
        
        for path in root_dir.glob("**/*.md"):
            rel_path = str(path.relative_to(root_dir))
            if ".git" not in rel_path and "node_modules" not in rel_path:
                all_mds.append(rel_path)

        linked = []
        code_lower = code_file.lower()

        # Smart keyword matching based on file paths/names
        for md in all_mds:
            md_lower = md.lower()
            # Direct name correlation (e.g. users.py matches api_gateway.md or auth_spec.md if security)
            if "router" in code_lower and ("api" in md_lower or "gateway" in md_lower):
                linked.append(md)
            elif "auth" in code_lower and ("auth" in md_lower or "security" in md_lower):
                linked.append(md)
            elif "readme.md" in md_lower:
                linked.append(md)

        # Fallback: if no specific match, include README and architecture docs
        if not linked:
            linked = [m for m in all_mds if "readme" in m.lower() or "arch" in m.lower()]
            
        # Absolute fallback to any markdown if still empty
        if not linked and all_mds:
            linked = [all_mds[0]]

        return list(set(linked))