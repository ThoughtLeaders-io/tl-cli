"""tl deals — List, show, and create sponsorship deals."""

from typing import Optional

import typer

from tl_cli.client.errors import handle_api_error, ApiError
from tl_cli.client.http import get_client
from tl_cli.filters import split_id_and_filters
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="Sponsorship deals (adlinks + adspot + channel + brand)")


@app.callback(invoke_without_command=True)
def deals(
    ctx: typer.Context,
    args: list[str] = typer.Argument(None, help="ID or filters (key:value pairs)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """List deals or show a single deal by ID.

    Examples:
        tl deals                              # List recent deals
        tl deals 12345                        # Show deal #12345
        tl deals status:sold brand:"Nike"     # Filter deals
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, csv_output, md_output, quiet)
    args = args or []
    deal_id, filters = split_id_and_filters(args)

    client = get_client()
    try:
        if deal_id:
            data = client.get(f"/deals/{deal_id}")
            output_single(data, fmt)
        else:
            params = {**filters, "limit": str(limit), "offset": str(offset)}
            data = client.get("/deals", params=params)
            output(
                data,
                fmt,
                columns=["id", "brand", "channel", "status", "price", "send_date", "owner"],
                title="Deals",
            )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("create")
def create(
    channel: int = typer.Option(..., "--channel", "-c", help="Channel ID"),
    brand: int = typer.Option(..., "--brand", "-b", help="Brand ID"),
    price: Optional[float] = typer.Option(None, "--price", "-p", help="Deal price"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON only"),
) -> None:
    """Create a new deal proposal (free, no credits charged)."""
    fmt = detect_format(json_output, False, False, quiet)
    body = {"channel_id": channel, "brand_id": brand}
    if price is not None:
        body["price"] = price

    client = get_client()
    try:
        data = client.post("/deals", json_body=body)
        output_single(data, fmt)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
