import os
import re
from pathlib import Path
from typing import Optional, Dict
import typer
from rich.panel import Panel

from src.state import SyncState
from src.utils.git_utils import get_staged_diff
from src.utils.logger import console, log_info, log_success
from src.agents.parser import parse_git_diff_ast
from src.agents.auditor import audit_specification_drift
from src.agents.patcher import generate_documentation_patches

app = typer.Typer(help="Chronicle-Sync: Semantic Architectural Drift Sentinel")


def clean_text_repetitions(content: str) -> str:
    """Collapses consecutive duplicate lines and excess blank lines."""
    # Collapse 3 or more newlines to exactly 2
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip() + "\n"


def apply_patch_to_file(
    file_path: Path, 
    patch: dict, 
    file_content_cache: Optional[Dict[str, str]] = None,
    dry_run: bool = False
) -> None:
    """Safely applies documentation patches ONLY to existing files in-place,

    guaranteeing no extra files are created and avoiding duplicate sections.
    """
    # Strict rule: DO NOT create extra files. Only work on existing docs!
    if not file_path.exists() or not file_path.is_file():
        console.print(f"[yellow]Skipping patch: '{file_path}' does not exist. (Working on existing documentation only)[/yellow]")
        return

    patched_text = patch.get("patched_text", "").strip()
    original_text = patch.get("original_text", "").strip()
    heading = patch.get("heading", "").strip()
    target = patch.get("target", "").strip()

    if not patched_text:
        return

    cache_key = str(file_path.resolve())
    if file_content_cache is not None and cache_key in file_content_cache:
        existing_content = file_content_cache[cache_key]
    else:
        existing_content = file_path.read_text(encoding="utf-8", errors="ignore")

    # Check 1: Avoid repetition if patched_text is already present in document
    if patched_text in existing_content:
        console.print(f"[cyan]Doc already up to date for target '{target}' in {file_path}[/cyan]")
        return

    updated_content = None

    # Check 2: Exact replacement of original excerpt
    if original_text and original_text in existing_content:
        updated_content = existing_content.replace(original_text, patched_text, 1)

    # Check 3: Check if target symbol already has a section/definition in the doc
    if updated_content is None and target:
        # Match heading for target symbol e.g. '### `my_func`' or '### my_func'
        target_sec_pattern = rf"(?m)(^#{1,6}\s+[`*]?{re.escape(target)}[`*]?.*?(?=(?:^#{1,6}\s+)|\Z))"
        target_match = re.search(target_sec_pattern, existing_content, re.DOTALL)
        if target_match:
            # Replace the existing subsection rather than creating a duplicate
            updated_content = (
                existing_content[:target_match.start(1)] + 
                patched_text + "\n\n" + 
                existing_content[target_match.end(1):]
            )

    # Check 4: Section insertion under existing heading (avoiding heading duplication)
    if updated_content is None and heading:
        heading_pattern = rf"(^#+\s+{re.escape(heading)}[^\n]*\n)"
        if re.search(heading_pattern, existing_content, re.MULTILINE):
            # If patched_text starts with the same heading, remove it to prevent duplicate headers
            clean_patch = patched_text
            h_match = re.match(r"^#+\s+[^\n]+\n", clean_patch)
            if h_match and heading.lower() in h_match.group(0).lower():
                clean_patch = clean_patch[h_match.end():].strip()

            replacement = rf"\1\n{clean_patch}\n\n"
            updated_content = re.sub(heading_pattern, replacement, existing_content, count=1, flags=re.MULTILINE)

    # Check 5: Append cleanly to the end of the existing file
    if updated_content is None:
        updated_content = existing_content.rstrip() + "\n\n" + patched_text + "\n"

    # Clean any internal blank line repetitions
    updated_content = clean_text_repetitions(updated_content)

    if file_content_cache is not None:
        file_content_cache[cache_key] = updated_content

    if not dry_run:
        file_path.write_text(updated_content, encoding="utf-8")
        console.print(f"\n[green]✔ Successfully updated:[/green] {file_path}")
    else:
        console.print(f"\n[cyan][DRY RUN] Would update:[/cyan] {file_path}")

    console.print(f"[cyan]Patched Documentation Section ({target}):[/cyan]\n{patched_text}")


@app.callback(invoke_without_command=True)
def main(
    repo_path: Optional[str] = typer.Option(None, "--repo", "-r", help="Path to git repository root."),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to mapping configuration YAML."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview patches without writing to disk.")
):
    """Runs the multi-agent sentinel pipeline against the current git diff and auto-fixes existing docs."""
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
        console.print(f"\n[bold yellow]{action_label} Documentation Patches to Existing Files...[/bold yellow]")
        
        file_cache: Dict[str, str] = {}
        for patch in state["proposed_patches"]:
            file_path = Path(patch['file'])
            if not file_path.is_absolute():
                file_path = root_dir / file_path
            apply_patch_to_file(file_path, patch, file_content_cache=file_cache, dry_run=dry_run)
    else:
        log_success("Documentation is fully in sync with code changes!")


if __name__ == "__main__":
    app()