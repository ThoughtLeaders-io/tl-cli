"""tl proposals — Shortcut for proposed sponsorships."""

from typing import Optional

import typer

from tl_cli.commands.sponsorships import do_create, do_list, do_show
from tl_cli.output.formatter import detect_format

app = typer.Typer(help="Proposals — matches proposed to both sides (shortcut for sponsorships status:proposal)")


@app.callback(invoke_without_command=True)
def proposals(ctx: typer.Context) -> None:
    """Proposals — matches proposed to both sides."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_cmd, args=[], json_output=False, csv_output=False, md_output=False, limit=50, offset=0)


@app.command("list")
def list_cmd(
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs). Run 'tl describe show sponsorships' for available filters."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """List proposals with optional filters.

    Examples:
        tl proposals list                     # List recent proposals
        tl proposals list brand:"Nike"        # Filter proposals
    """
    fmt = detect_format(json_output, csv_output, md_output)
    do_list(args or [], fmt, limit, offset, default_status="proposal", title="Proposals")


@app.command("show")
def show_cmd(
    item_id: str = typer.Argument(..., help="Sponsorship ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show proposal detail by ID.

    Examples:
        tl proposals show 12345
    """
    fmt = detect_format(json_output, False, False)
    do_show(item_id, fmt)


@app.command("create")
def create_cmd(
    channel: int = typer.Option(..., "--channel", "-c", help="Channel ID"),
    brand: int = typer.Option(..., "--brand", "-b", help="Brand ID"),
    price: Optional[float] = typer.Option(None, "--price", "-p", help="Deal price"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Create a new proposal (free, no credits charged).

    Examples:
        tl proposals create --channel 1 --brand 2
    """
    fmt = detect_format(json_output, False, False)
    do_create(channel, brand, price, fmt, status="proposed")
