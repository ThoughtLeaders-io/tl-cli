"""tl uploads — List and show video uploads."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.filters import parse_filters
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="Video uploads (YouTube content from Elasticsearch)")


@app.callback(invoke_without_command=True)
def uploads(ctx: typer.Context) -> None:
    """Video uploads from YouTube (Elasticsearch)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_cmd, args=[], json_output=False, csv_output=False, md_output=False, limit=50, offset=0)


@app.command("list")
def list_cmd(
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """List video uploads with optional filters.

    Examples:
        tl uploads list                               # List recent uploads
        tl uploads list channel:12345 type:longform   # Filter uploads
    """
    fmt = detect_format(json_output, csv_output, md_output)
    filters = parse_filters(args or [])

    client = get_client()
    try:
        params = {**filters, "limit": str(limit), "offset": str(offset)}
        data = client.get("/uploads", params=params)
        for r in data.get("results", []):
            r["upload_id"] = r.pop("id", None)
        output(
            data,
            fmt,
            columns=["upload_id", "title", "channel", "views", "publication_date", "content_type"],
            title="Uploads",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("show")
def show_cmd(
    ids: list[str] = typer.Argument(..., help="One or more upload IDs"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show details for one or more uploads by ID.

    IDs can contain colons (e.g. 1174310:0BehkmVa7ak).

    Examples:
        tl uploads show 0BehkmVa7ak
        tl uploads show 1174310:0BehkmVa7ak
        tl uploads show 0BehkmVa7ak dQw4w9WgXcQ
    """
    fmt = detect_format(json_output, False, False)

    client = get_client()
    try:
        for upload_id in ids:
            data = client.get(f"/uploads/{upload_id}")
            for r in (data.get("results", []) if isinstance(data.get("results"), list) else []):
                r["upload_id"] = r.pop("id", None)
            output_single(data, fmt)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
