"""tl sponsorships — List, show, and create sponsorships."""

from typing import Optional

import typer
from rich.console import Console

from tl_cli.client.errors import handle_api_error, ApiError
from tl_cli.client.http import get_client
from tl_cli.filters import parse_filters
from tl_cli.output.formatter import detect_format, output, output_single

COLUMNS = ["id", "brand_id", "brand", "channel_id", "channel", "status", "price", "send_date", "owner", "owner_email"]
COLUMN_CONFIG = {"price": {"justify": "right"}}


def _format_results(results: list[dict]) -> list[dict]:
    """Clean up sponsorship results for display."""
    for row in results:
        sd = row.get("send_date")
        if sd and isinstance(sd, str) and "T" in sd:
            row["send_date"] = sd[:10]
        price = row.get("price")
        if price is not None:
            try:
                row["price"] = str(int(float(price)))
            except (ValueError, TypeError):
                pass
    return results


def do_list(
    args: list[str],
    fmt: str,
    limit: int,
    offset: int,
    *,
    default_status: str | None = None,
    title: str = "Sponsorships",
) -> None:
    """Shared list logic with optional default status filter."""
    filters = parse_filters(args)

    if default_status and "status" in filters:
        Console(stderr=True).print(
            f"[red]Error:[/red] The [bold]{title.lower()}[/bold] command does not accept a status filter.\n"
            f"Use [bold]tl sponsorships list[/bold] for finer-grained status filtering."
        )
        raise typer.Exit(1)

    if default_status:
        filters.setdefault("status", default_status)

    client = get_client()
    try:
        params = {**filters, "limit": str(limit), "offset": str(offset)}
        data = client.get("/sponsorships", params=params)
        if "results" in data:
            data["results"] = _format_results(data["results"])
        output(data, fmt, columns=COLUMNS, title=title, column_config=COLUMN_CONFIG)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


def do_show(item_id: str, fmt: str) -> None:
    """Shared show logic."""
    client = get_client()
    try:
        data = client.get(f"/sponsorships/{item_id}")
        output_single(data, fmt)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


def do_create(
    channel: int,
    brand: int,
    price: float | None,
    fmt: str,
    status: str | None = None,
) -> None:
    """Shared create logic."""
    body: dict = {"channel_id": channel, "brand_id": brand}
    if price is not None:
        body["price"] = price
    if status is not None:
        body["status"] = status

    client = get_client()
    try:
        data = client.post("/sponsorships", json_body=body)
        output_single(data, fmt)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


# --- Typer app ---

app = typer.Typer(help="Sponsorships (deals, matches, proposals)")


@app.callback(invoke_without_command=True)
def sponsorships(ctx: typer.Context) -> None:
    """Sponsorships — the centre of attention in ThoughtLeaders."""
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
    """List sponsorships with optional filters.

    Examples:
        tl sponsorships list                              # List recent sponsorships
        tl sponsorships list status:sold brand:"Nike"     # Filter sponsorships
    """
    fmt = detect_format(json_output, csv_output, md_output, quiet)
    do_list(args or [], fmt, limit, offset)


@app.command("show")
def show_cmd(
    item_id: str = typer.Argument(..., help="Sponsorship ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
) -> None:
    """Show sponsorship detail by ID.

    Examples:
        tl sponsorships show 12345
    """
    fmt = detect_format(json_output, False, False, quiet)
    do_show(item_id, fmt)


@app.command("create")
def create_cmd(
    channel: int = typer.Option(..., "--channel", "-c", help="Channel ID"),
    brand: int = typer.Option(..., "--brand", "-b", help="Brand ID"),
    price: Optional[float] = typer.Option(None, "--price", "-p", help="Deal price"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON only"),
) -> None:
    """Create a new sponsorship proposal (free, no credits charged).

    Examples:
        tl sponsorships create --channel 1 --brand 2
    """
    fmt = detect_format(json_output, False, False, quiet)
    do_create(channel, brand, price, fmt, status="proposed")
