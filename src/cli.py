from pathlib import Path
import typer
from rich.panel import Panel
from src.state import SyncState
from src.utils.git_utils import get_staged_diff
from src.utils.logger import console, log_info, log_success
from src.agents.parser import parse_git_diff_ast
from src.agents.auditor import audit_specification_drift
from src.agents.patcher import generate_documentation_patches

app = typer.Typer(help="Chronicle-Sync: Semantic Architectural Drift Sentinel")

@app.callback(invoke_without_command=True)
def main():
    """Runs the multi-agent sentinel pipeline against the current git diff and auto-fixes docs."""
    console.print(Panel.fit("[bold magenta]Chronicle-Sync Sentinel Running (CPU Mode)...[/bold magenta]", border_style="magenta"))
    
    log_info("Fetching git diff...")
    diff = get_staged_diff()
    
    if not diff.strip():
        log_success("No git changes detected to analyze.")
        raise typer.Exit(0)

    state: SyncState = {
        "git_diff": diff,
        "ast_deltas": [],
        "linked_docs": [],
        "drift_reports": [],
        "proposed_patches": []
    }

    log_info("Executing Agent 1: Diff Parser & AST Extractor...")
    state = parse_git_diff_ast(state)
    log_success(f"Extracted {len(state['ast_deltas'])} structural deltas.")

    log_info("Executing Agent 2: Specification Auditor (Local HF GGUF Model)...")
    state = audit_specification_drift(state)
    log_success(f"Identified {len(state['drift_reports'])} potential documentation drifts.")

    log_info("Executing Agent 3: Evaluator-Optimizer Patch Generator...")
    state = generate_documentation_patches(state)

    if state["proposed_patches"]:
        console.print("\n[bold yellow]Applying Documentation Patches to Files...[/bold yellow]")
        for patch in state["proposed_patches"]:
            file_path = Path(patch['file'])
            if file_path.exists():
                # Write the clean patched text directly back into the documentation file
                file_path.write_text(patch['patched_text'], encoding="utf-8")
                console.print(f"\n[green]✔ Successfully updated:[/green] {patch['file']}")
                console.print(f"[cyan]New Content Written:[/cyan]\n{patch['patched_text']}")
            else:
                # If the file didn't exist, create it
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(patch['patched_text'], encoding="utf-8")
                console.print(f"\n[green]✔ Created and patched new file:[/green] {patch['file']}")
    else:
        log_success("Documentation is fully in sync with code changes!")

if __name__ == "__main__":
    app()