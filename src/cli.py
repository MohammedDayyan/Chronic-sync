import os
import re
from pathlib import Path
from typing import Optional
import typer
from rich.panel import Panel
from rich.table import Table

from src.state import SyncState
from src.utils.git_utils import get_staged_diff
from src.utils.logger import console, log_info, log_success
from src.agents.parser import parse_git_diff_ast
from src.agents.auditor import audit_specification_drift
from src.agents.patcher import generate_documentation_patches

app = typer.Typer(help="Chronicle-Sync: Semantic Architectural Drift Sentinel")


def apply_patch_to_file(
    file_path: Path, 
    patch: dict, 
    dry_run: bool = False
) -> None:
    """Safely applies documentation patch to a file, updating sections in-place

    or creating new documentation files without wiping existing content.
    """
    patched_text = patch.get("patched_text", "").strip()
    original_text = patch.get("original_text", "").strip()
    heading = patch.get("heading", "")
    is_new_file = patch.get("is_new_file", False)

    if not patched_text:
        return

    if file_path.exists() and not is_new_file:
        existing_content = file_path.read_text(encoding="utf-8", errors="ignore")

        # 1. Exact replacement of original excerpt
        if original_text and original_text in existing_content:
            updated_content = existing_content.replace(original_text, patched_text, 1)
        # 2. Section insertion under existing heading
        elif heading and re.search(rf"^#+\s+{re.escape(heading)}\b", existing_content, re.MULTILINE):
            # Insert right below the heading
            pattern = rf"(^#+\s+{re.escape(heading)}[^\n]*\n)"
            replacement = rf"\1\n{patched_text}\n\n"
            updated_content = re.sub(pattern, replacement, existing_content, count=1, flags=re.MULTILINE)
        # 3. Clean append to file
        else:
            updated_content = existing_content.rstrip() + "\n\n" + patched_text + "\n"

        if not dry_run:
            file_path.write_text(updated_content, encoding="utf-8")
            console.print(f"\n[green]✔ Successfully updated:[/green] {file_path}")
        else:
            console.print(f"\n[cyan][DRY RUN] Would update:[/cyan] {file_path}")
        console.print(f"[cyan]Patched Documentation Section:[/cyan]\n{patched_text}")

    else:
        # File does not exist: create directory and file
        if not dry_run:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(patched_text + "\n", encoding="utf-8")
            console.print(f"\n[green]✔ Created and patched new documentation file:[/green] {file_path}")
        else:
            console.print(f"\n[cyan][DRY RUN] Would create new documentation file:[/cyan] {file_path}")
        console.print(f"[cyan]Content:[/cyan]\n{patched_text}")


@app.callback(invoke_without_command=True)
def main(
    repo_path: Optional[str] = typer.Option(None, "--repo", "-r", help="Path to git repository root."),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to mapping configuration YAML."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview patches without writing to disk.")
):
    """Runs the multi-agent sentinel pipeline against the current git diff and auto-fixes docs."""
    console.print(Panel.fit("[bold magenta]Chronicle-Sync Sentinel Running...[/bold magenta]", border_style="magenta"))
    
    root_dir = Path(repo_path).resolve() if repo_path else Path.cwd()
    if config_path:
        os.environ["CHRONICLE_CONFIG_PATH"] = str(Path(config_path).resolve())

    log_info(f"Fetching git diff from {root_dir}...")
    diff = get_staged_diff(repo_path=root_dir)
    
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

    log_info("Executing Agent 1: Diff Parser & Structural AST Extractor...")
    state = parse_git_diff_ast(state)
    log_success(f"Extracted {len(state['ast_deltas'])} structural deltas.")

    log_info("Executing Agent 2: Specification Auditor (Local HF Model)...")
    state = audit_specification_drift(state)
    log_success(f"Identified {len(state['drift_reports'])} potential documentation drifts.")

    log_info("Executing Agent 3: Evaluator-Optimizer Patch Generator...")
    state = generate_documentation_patches(state)

    if state["proposed_patches"]:
        action_label = "Previewing" if dry_run else "Applying"
        console.print(f"\n[bold yellow]{action_label} Documentation Patches to Files...[/bold yellow]")
        for patch in state["proposed_patches"]:
            file_path = Path(patch['file'])
            if not file_path.is_absolute():
                file_path = root_dir / file_path
            apply_patch_to_file(file_path, patch, dry_run=dry_run)
    else:
        log_success("Documentation is fully in sync with code changes!")


if __name__ == "__main__":
    app()