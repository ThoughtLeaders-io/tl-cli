"""tl channels — Search and show YouTube channels."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.filters import parse_filters
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="YouTube channels (search and detail)")


@app.callback(invoke_without_command=True)
def channels(ctx: typer.Context) -> None:
    """YouTube channels — search and detail."""
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
    """Search channels with optional filters.

    Examples:
        tl channels list                                  # List channels
        tl channels list category:cooking min-subs:100k   # Search with filters
    """
    fmt = detect_format(json_output, csv_output, md_output, quiet)
    filters = parse_filters(args or [])

    client = get_client()
    try:
        params = {**filters, "limit": str(limit), "offset": str(offset)}
        data = client.get("/channels", params=params)
        output(
            data,
            fmt,
            columns=["id", "name", "subscribers", "category", "sponsorship_score", "trend"],
            title="Channels",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("show")
def show_cmd(
    channel_id: int = typer.Argument(..., help="Channel ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
) -> None:
    """Show channel detail by ID.

    Examples:
        tl channels show 12345
    """
    fmt = detect_format(json_output, False, False, quiet)

    client = get_client()
    try:
        data = client.get(f"/channels/{channel_id}")
        output_single(data, fmt)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
