"""tl matches — Shortcut for matched sponsorships."""

import typer

from tl_cli.commands.sponsorships import create_sponsorship, list_or_show
from tl_cli.output.formatter import detect_format

app = typer.Typer(help="Matches — possible brand-channel pairings (shortcut for sponsorships status:match)")


@app.callback(invoke_without_command=True)
def matches(
    ctx: typer.Context,
    args: list[str] = typer.Argument(None, help="ID or filters (key:value pairs)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """List matches (possible brand-channel pairings) or show one by ID.

    Examples:
        tl matches                        # List recent matches
        tl matches 12345                  # Show match #12345
        tl matches brand:"Nike"           # Filter matches
        tl matches create --channel 1 --brand 2  # Create a match
    """
    if ctx.invoked_subcommand is not None:
        return

    args = args or []
    if args and args[0] == "create":
        create_sponsorship(args[1:], status="matched")
        return

    fmt = detect_format(json_output, csv_output, md_output, quiet)
    list_or_show(args, fmt, limit, offset, default_status="match", title="Matches")
