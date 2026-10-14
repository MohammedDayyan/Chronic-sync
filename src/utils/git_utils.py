import git
from pathlib import Path

def get_staged_diff() -> str:
    try:
        repo = git.Repo(Path.cwd(), search_parent_directories=True)
        diff = repo.git.diff('--cached')
        if not diff:
            diff = repo.git.diff('HEAD')
        if not diff:
            diff = repo.git.diff(None)
        return diff or ""
    except Exception:
        return ""