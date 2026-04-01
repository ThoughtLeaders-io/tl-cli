"""tl deals — Shortcut for contractually agreed-upon sponsorships."""

from typing import Optional

import typer

from tl_cli.commands.sponsorships import do_create, do_list, do_show
from tl_cli.output.formatter import detect_format

app = typer.Typer(help="Deals — agreed-upon sponsorships (shortcut for sponsorships status:deal)")


@app.callback(invoke_without_command=True)
def deals(ctx: typer.Context) -> None:
    """Deals — contractually agreed-upon sponsorships."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_cmd, args=[], json_output=False, csv_output=False, md_output=False, quiet=False, limit=50, offset=0)


@app.command("list")
def list_cmd(
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """List deals with optional filters.

    Examples:
        tl deals list                         # List recent deals
        tl deals list brand:"Nike"            # Filter deals
    """
    fmt = detect_format(json_output, csv_output, md_output, quiet)
    do_list(args or [], fmt, limit, offset, default_status="deal", title="Deals")


@app.command("show")
def show_cmd(
    item_id: str = typer.Argument(..., help="Sponsorship ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
) -> None:
    """Show deal detail by ID.

    Examples:
        tl deals show 12345
    """
    fmt = detect_format(json_output, False, False, quiet)
    do_show(item_id, fmt)
