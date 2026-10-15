import git
from pathlib import Path
from typing import Optional, Union

def get_staged_diff(repo_path: Optional[Union[str, Path]] = None) -> str:
    """Retrieves staged, unstaged, or head git diffs from the target repository."""
    target_path = Path(repo_path).resolve() if repo_path else Path.cwd()
    try:
        repo = git.Repo(target_path, search_parent_directories=True)
        diff = repo.git.diff('--cached')
        if not diff:
            diff = repo.git.diff('HEAD')
        if not diff:
            diff = repo.git.diff(None)
        return diff or ""
    except Exception:
        return ""