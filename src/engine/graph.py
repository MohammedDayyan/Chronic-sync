from pathlib import Path
from typing import List

class ArchitectureGraphManager:
    def __init__(self, db_path: str = ".chronicle_sync.db"):
        self.db_path = db_path

    def get_linked_docs(self, code_file: str) -> List[str]:
        root_dir = Path.cwd()
        md_files = []
        
        # 1. Prioritize root README.md
        readme = root_dir / "README.md"
        if readme.exists():
            md_files.append("README.md")
        
        # 2. Discover all other .md files dynamically across the repo
        for path in root_dir.glob("**/*.md"):
            rel_path = str(path.relative_to(root_dir))
            if rel_path not in md_files and ".git" not in rel_path and "node_modules" not in rel_path:
                md_files.append(rel_path)
                
        # Fallback if no markdown files exist at all
        if not md_files:
            md_files.append("README.md")
            
        return md_files