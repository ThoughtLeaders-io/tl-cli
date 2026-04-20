"""tl brands — Brand detail and sponsorship history."""

import urllib.parse

import typer

from rich.console import Console

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.hints import detail_hint
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="Brand intelligence (detail, sponsorship history, channel mentions)")


@app.callback(invoke_without_command=True)
def brands(ctx: typer.Context) -> None:
    """Brands — detail and sponsorship history."""
    if ctx.invoked_subcommand is None:
        ctx.get_help()
        raise typer.Exit()


def _handle_brand_api_error(e: ApiError) -> None:
    """Print a candidates list for ambiguous brand name matches."""
    if e.status_code == 400 and isinstance(e.raw, dict) and e.raw.get("candidates"):
        err = Console(stderr=True)
        err.print(f"[yellow]{e.detail}[/yellow]")
        err.print()
        err.print("[bold]Candidates:[/bold]")
        err.print(f"  {'brand_id':>10}  {'website':<30}  name")
        err.print(f"  {'-' * 10}  {'-' * 30}  {'-' * 40}")
        for c in e.raw["candidates"]:
            err.print(f"  {c['brand_id']:>10}  {c.get('website', ''):<30}  {c['name']}")
        raise typer.Exit(1)
    handle_api_error(e)


@app.command("show")
def show_cmd(
    query: str = typer.Argument(..., help="Brand name or numeric ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output (flattens nested fields)"),
) -> None:
    """Show brand detail by name or ID.

    Accepts either a numeric brand ID or a partial name. Names that
    match more than one brand return an error with candidate IDs.

    Examples:
        tl brands show Nike
        tl brands show 21416
    """
    fmt = detect_format(json_output, csv_output, False)
    encoded_query = urllib.parse.quote(query, safe="")
    client = get_client()
    try:
        data = client.get(f"/brands/{encoded_query}")
        for r in (data.get("results", []) if isinstance(data.get("results"), list) else []):
            r["brand_id"] = r.pop("id", None)
        output_single(data, fmt)
        if fmt == "table" and data.get("show_cta"):
            record = data.get("results", data)
            if isinstance(record, list) and record:
                record = record[0]
            if isinstance(record, dict):
                hint = detail_hint(client, brand=record.get("name"))
                if hint:
                    Console(stderr=True).print(f"\n[yellow]{hint}[/yellow]")
    except ApiError as e:
        _handle_brand_api_error(e)
    finally:
        client.close()


@app.command("history")
def history_cmd(
    query: str = typer.Argument(..., help="Brand name or numeric ID"),
    channel: int | None = typer.Option(None, "--channel", "-c", help="Filter to a specific channel"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """Show a brand's sponsorship history (videos where the brand was detected).

    Requires an Intelligence plan.

    Examples:
        tl brands history Nike                          # Nike's sponsorship history
        tl brands history 21416                         # By brand ID
        tl brands history Nike --channel 12345          # Nike mentions on a specific channel
    """
    fmt = detect_format(json_output, csv_output, md_output)

    params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
    if channel is not None:
        params["channel_id"] = str(channel)

    encoded_query = urllib.parse.quote(query, safe="")
    client = get_client()
    try:
        data = client.get(f"/brands/{encoded_query}/history", params=params)
        brand_name = data.get("brand", {}).get("name", query)
        output(
            data,
            fmt,
            columns=["video_id", "title", "channel_id", "channel", "views", "publication_date", "is_tl"],
            title=f"Brand History: {brand_name}",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


SIMILAR_COLUMNS = ["score", "brand_id", "brand_name", "website", "mbn"]
SIMILAR_COLUMN_CONFIG = {
    "score": {"justify": "right"},
}


def _format_score(results: list[dict]) -> list[dict]:
    """Convert raw cosine score (0.0-1.0) to percentage string."""
    for row in results:
        score = row.get("score")
        if isinstance(score, (int, float)):
            row["score"] = f"{score * 100:.1f}%"
    return results


@app.command("similar")
def similar_cmd(
    query: str = typer.Argument(..., help="Brand name or numeric ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Find brands similar to a given one (by ID or name).

    Costs 50 credits per call. Intelligence plan required.

    Examples:
        tl brands similar Nike
        tl brands similar 6037
        tl brands similar 6037 mbn:yes --limit 10
    """
    fmt = detect_format(json_output, csv_output, md_output)
    encoded_query = urllib.parse.quote(query, safe="")
    params: dict[str, str] = {"limit": str(limit)}

    client = get_client()
    try:
        data = client.get(f"/brands/{encoded_query}/similar", params=params)
        brand_name = data.get("brand", {}).get("name", query)
        if fmt in ("table", "md"):
            _format_score(data.get("results", []))
        output(
            data,
            fmt,
            columns=SIMILAR_COLUMNS,
            title=f"Brands similar to {brand_name}",
            column_config=SIMILAR_COLUMN_CONFIG,
        )
    except ApiError as e:
        _handle_brand_api_error(e)
    finally:
        client.close()
