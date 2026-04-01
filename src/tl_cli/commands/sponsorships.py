"""tl sponsorships — List, show, and create sponsorships."""

from typing import Optional

import typer
from rich.console import Console

from tl_cli.client.errors import handle_api_error, ApiError
from tl_cli.client.http import get_client
from tl_cli.filters import split_id_and_filters
from tl_cli.output.formatter import detect_format, output, output_single

COLUMNS = ["id", "brand", "channel", "status", "price", "send_date", "owner"]
COLUMN_CONFIG = {"price": {"justify": "right"}}


def _format_results(results: list[dict]) -> list[dict]:
    """Clean up sponsorship results for display."""
    for row in results:
        # send_date: strip time portion, keep date only
        sd = row.get("send_date")
        if sd and isinstance(sd, str) and "T" in sd:
            row["send_date"] = sd[:10]
        # price: no decimal places
        price = row.get("price")
        if price is not None:
            try:
                row["price"] = str(int(float(price)))
            except (ValueError, TypeError):
                pass
    return results


def list_or_show(
    args: list[str],
    fmt: str,
    limit: int,
    offset: int,
    *,
    default_status: str | None = None,
    title: str = "Sponsorships",
) -> None:
    """Shared logic for listing/showing sponsorships with an optional default status filter."""
    item_id, filters = split_id_and_filters(args)

    if default_status and "status" in filters:
        Console(stderr=True).print(
            f"[red]Error:[/red] The [bold]{title.lower()}[/bold] command does not accept a status filter.\n"
            f"Use [bold]tl sponsorships[/bold] for finer-grained status filtering."
        )
        raise typer.Exit(1)

    if default_status and not item_id:
        filters.setdefault("status", default_status)

    client = get_client()
    try:
        if item_id:
            data = client.get(f"/sponsorships/{item_id}")
            output_single(data, fmt)
        else:
            params = {**filters, "limit": str(limit), "offset": str(offset)}
            data = client.get("/sponsorships", params=params)
            if "results" in data:
                data["results"] = _format_results(data["results"])
            output(data, fmt, columns=COLUMNS, title=title, column_config=COLUMN_CONFIG)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


def create_sponsorship(args: list[str], status: str | None = None) -> None:
    """Shared create logic. Parses --channel, --brand, --price, --json, --quiet from args."""
    import click

    # Minimal arg parsing for create subcommand
    parser = click.OptionParser()
    parser.add_option(click.Option(["--channel", "-c"], type=int, required=True))
    parser.add_option(click.Option(["--brand", "-b"], type=int, required=True))
    parser.add_option(click.Option(["--price", "-p"], type=float, required=False))
    parser.add_option(click.Option(["--json"], is_flag=True, flag_value=True, default=False))
    parser.add_option(click.Option(["--quiet", "-q"], is_flag=True, flag_value=True, default=False))
    try:
        opts, _, _ = parser.parse_args(args)
    except click.UsageError as e:
        Console(stderr=True).print(f"[red]Error:[/red] {e}")
        Console(stderr=True).print("Usage: tl <command> create --channel <id> --brand <id> [--price <amount>] [--json] [--quiet]")
        raise typer.Exit(1)

    channel = opts.get("channel")
    brand = opts.get("brand")
    if not channel or not brand:
        Console(stderr=True).print("[red]Error:[/red] --channel and --brand are required.")
        raise typer.Exit(1)

    fmt = detect_format(opts.get("json", False), False, False, opts.get("quiet", False))
    body: dict = {"channel_id": channel, "brand_id": brand}
    price = opts.get("price")
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


app = typer.Typer(help="Sponsorships (deals, matches, proposals)")


@app.callback(invoke_without_command=True)
def sponsorships(
    ctx: typer.Context,
    args: list[str] = typer.Argument(None, help="ID or filters (key:value pairs)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """List sponsorships or show a single sponsorship by ID.

    Examples:
        tl sponsorships                              # List recent sponsorships
        tl sponsorships 12345                        # Show sponsorship #12345
        tl sponsorships status:sold brand:"Nike"     # Filter sponsorships
        tl sponsorships create --channel 1 --brand 2 # Create a proposal
    """
    if ctx.invoked_subcommand is not None:
        return

    args = args or []
    if args and args[0] == "create":
        create_sponsorship(args[1:], status="proposed")
        return

    fmt = detect_format(json_output, csv_output, md_output, quiet)
    list_or_show(args, fmt, limit, offset)
