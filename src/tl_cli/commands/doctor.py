"""tl doctor — Health check for auth, connectivity, and version."""

import typer
from rich.console import Console

from tl_cli import __version__
from tl_cli.auth.token_store import load_tokens
from tl_cli.client.errors import ApiError
from tl_cli.client.http import get_client
from tl_cli.config import get_config

app = typer.Typer(help="Health check (auth, connectivity, version)")
console = Console()


@app.callback(invoke_without_command=True)
def doctor(ctx: typer.Context) -> None:
    """Check CLI health: version, auth status, API connectivity, credits."""
    console.print(f"\n[bold]tl-cli[/bold] v{__version__}\n")
    config = get_config()
    all_ok = True

    # API URL
    console.print(f"  API:    {config.cli_api_base}")

    # Auth
    tokens = load_tokens()
    if not tokens:
        console.print("  Auth:   [red]not logged in[/red]")
        all_ok = False
    elif tokens.is_expired:
        console.print(f"  Auth:   [yellow]token expired[/yellow] ({tokens.email})")
        all_ok = False
    else:
        console.print(f"  Auth:   [green]ok[/green] ({tokens.email})")

    # Connectivity + balance
    if tokens and not tokens.is_expired:
        client = get_client()
        try:
            data = client.get("/balance")
            balance_val = data.get("balance", "?")
            console.print(f"  API:    [green]connected[/green]")
            console.print(f"  Credits: {balance_val}")
        except ApiError as e:
            console.print(f"  API:    [red]error ({e.status_code})[/red]")
            all_ok = False
        except Exception as e:
            console.print(f"  API:    [red]unreachable[/red]")
            all_ok = False
        finally:
            client.close()
    else:
        console.print("  API:    [dim]skipped (not authenticated)[/dim]")

    console.print()
    if all_ok:
        console.print("[green]Everything looks good.[/green]")
    else:
        console.print("[yellow]Issues found. Run 'tl auth login' to authenticate.[/yellow]")
