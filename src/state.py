from typing import TypedDict, List, Dict, Any, Optional

class SyncState(TypedDict, total=False):
    git_diff: str
    ast_deltas: List[Dict[str, Any]]
    linked_docs: List[Dict[str, Any]]
    drift_reports: List[Dict[str, Any]]
    proposed_patches: List[Dict[str, Any]]
    config: Optional[Dict[str, Any]]