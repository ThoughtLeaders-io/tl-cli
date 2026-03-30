"""tl brands — Brand intelligence reports."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format, output

app = typer.Typer(help="Brand intelligence (sponsorship activity, channel mentions)")


@app.callback(invoke_without_command=True)
def brands(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Brand name to research"),
    channel: int | None = typer.Option(None, "--channel", "-c", help="Filter to a specific channel"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """Research a brand's sponsorship activity and channel mentions.

    Requires an Intelligence plan.

    Examples:
        tl brands Nike                          # Nike's sponsorship intelligence
        tl brands Nike --channel 12345          # Nike mentions on a specific channel
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, csv_output, md_output, quiet)

    params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
    if channel is not None:
        params["channel_id"] = str(channel)

    client = get_client()
    try:
        data = client.get(f"/brands/{query}", params=params)
        output(
            data,
            fmt,
            columns=["channel", "mentions", "type", "latest_date", "views"],
            title=f"Brand Intelligence: {query}",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
