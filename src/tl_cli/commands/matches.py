"""tl matches — Shortcut for matched sponsorships."""

from typing import Optional

import typer

from tl_cli.commands.sponsorships import do_create, do_list, do_show
from tl_cli.output.formatter import detect_format

app = typer.Typer(help="Matches — possible brand-channel pairings (shortcut for sponsorships status:match)")


@app.callback(invoke_without_command=True)
def matches(ctx: typer.Context) -> None:
    """Matches — possible brand-channel pairings."""
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
    """List matches with optional filters.

    Examples:
        tl matches list                       # List recent matches
        tl matches list brand:"Nike"          # Filter matches
    """
    fmt = detect_format(json_output, csv_output, md_output)
    do_list(args or [], fmt, limit, offset, default_status="match", title="Matches")


@app.command("show")
def show_cmd(
    item_id: str = typer.Argument(..., help="Sponsorship ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show match detail by ID.

    Examples:
        tl matches show 12345
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
    """Create a new match (free, no credits charged).

    Examples:
        tl matches create --channel 1 --brand 2
    """
    fmt = detect_format(json_output, False, False)
    do_create(channel, brand, price, fmt, status="matched")
