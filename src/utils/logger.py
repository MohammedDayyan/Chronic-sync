from rich.console import Console

console = Console()

def log_info(msg: str):
    console.print(f"[bold cyan][INFO][/bold cyan] {msg}")

def log_success(msg: str):
    console.print(f"[bold green][SUCCESS][/bold green] {msg}")

def log_warning(msg: str):
    console.print(f"[bold yellow][WARNING][/bold yellow] {msg}")

def log_error(msg: str):
    console.print(f"[bold red][ERROR][/bold red] {msg}")