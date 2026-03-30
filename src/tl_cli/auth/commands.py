"""Auth CLI commands: tl auth login/logout/status."""

import typer
from rich.console import Console
from rich.prompt import Prompt

from tl_cli.auth.login import login_browser, login_device_code
from tl_cli.auth.token_store import clear_tokens, load_tokens

app = typer.Typer(help="Authentication commands")
console = Console(stderr=True)


@app.command("login", help="Log in to ThoughtLeaders.")
def login_cmd() -> None:
    """Log in to ThoughtLeaders."""
    console.print("[bold]How would you like to authenticate?[/bold]")
    console.print("  [cyan]1[/cyan] — Browser on this machine (default)")
    console.print("  [cyan]2[/cyan] — Device code (use a browser on another device)")
    console.print()
    choice = Prompt.ask("Choose", choices=["1", "2"], default="1", console=console)

    if choice == "2":
        login_device_code()
    else:
        login_browser()


@app.command("logout")
def logout_cmd() -> None:
    """Clear stored authentication tokens."""
    clear_tokens()
    console.print("[green]Logged out successfully.[/green]")


@app.command("status")
def status_cmd() -> None:
    """Show current authentication status."""
    tokens = load_tokens()
    if not tokens:
        console.print("[yellow]Not logged in.[/yellow] Run: tl auth login")
        raise SystemExit(2)

    if tokens.is_expired:
        console.print(f"[yellow]Token expired.[/yellow] Logged in as: {tokens.email or 'unknown'}")
        console.print("Run: tl auth login")
        raise SystemExit(2)

    console.print(f"[green]Authenticated[/green] as: {tokens.email or 'unknown'}")
