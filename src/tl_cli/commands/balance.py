"""tl balance — Show credit balance and recent usage."""

import json

import typer
from rich.console import Console
from rich.table import Table

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format

app = typer.Typer(help="Credit balance and usage (free)")
console = Console()


@app.callback(invoke_without_command=True)
def balance(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON only"),
) -> None:
    """Show your credit balance and recent usage (free, no credits).

    Examples:
        tl balance
        tl balance --json
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, False, False, quiet)

    client = get_client()
    try:
        data = client.get("/balance")

        if fmt in ("json", "quiet"):
            print(json.dumps(data, indent=2, default=str))
            return

        balance_val = data.get("balance", 0)
        allow_overage = data.get("allow_overage", False)

        console.print(f"\n[bold]Credit Balance:[/bold] [cyan]{balance_val}[/cyan] credits")
        if allow_overage:
            console.print("[dim]Overage: enabled[/dim]")

        recent = data.get("recent_usage", [])
        if recent:
            table = Table(title="Recent Usage")
            table.add_column("Date")
            table.add_column("Resource")
            table.add_column("Results", justify="right")
            table.add_column("Credits", justify="right")
            for entry in recent[:10]:
                table.add_row(
                    entry.get("date", ""),
                    entry.get("resource", ""),
                    str(entry.get("results_count", "")),
                    str(entry.get("credits_charged", "")),
                )
            console.print(table)

    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
