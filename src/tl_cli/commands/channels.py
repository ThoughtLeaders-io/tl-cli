"""tl channels — Search and show YouTube channels."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.filters import split_id_and_filters
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="YouTube channels (search and detail)")


@app.callback(invoke_without_command=True)
def channels(
    ctx: typer.Context,
    args: list[str] = typer.Argument(None, help="ID or filters (key:value pairs)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """Search channels or show a single channel by ID.

    Examples:
        tl channels                                       # List channels
        tl channels 12345                                 # Show channel detail
        tl channels category:cooking min-subs:100k        # Search with filters
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, csv_output, md_output, quiet)
    args = args or []
    channel_id, filters = split_id_and_filters(args)

    client = get_client()
    try:
        if channel_id:
            data = client.get(f"/channels/{channel_id}")
            output_single(data, fmt)
        else:
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
