from typing import TypedDict, List, Dict

class SyncState(TypedDict):
    git_diff: str
    ast_deltas: List[Dict]
    linked_docs: List[Dict]
    drift_reports: List[Dict]
    proposed_patches: List[Dict]