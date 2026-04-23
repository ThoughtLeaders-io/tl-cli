"""tl channels — Search and show YouTube channels."""

import urllib.parse

import typer
from rich.console import Console

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.filters import parse_filters
from tl_cli.hints import detail_hint
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="YouTube channels (search, detail, and similar-channel recommendations)")

# Columns for the `similar` endpoint result table. The server enriches every
# row so the user can size up each suggestion without follow-up queries.
SIMILAR_COLUMNS = ["score", "channel_id", "name", "msn", "tpp", "subscribers", "projected_views", "total_views", "cpm", "audience"]
SIMILAR_COLUMN_CONFIG = {
    "score": {"justify": "right"},
    "subscribers": {"justify": "right"},
    "projected_views": {"justify": "right"},
    "total_views": {"justify": "right"},
    "cpm": {"justify": "right"},
}


@app.callback(invoke_without_command=True)
def channels(ctx: typer.Context) -> None:
    """YouTube channels — search and detail."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_cmd, args=[], json_output=False, csv_output=False, md_output=False, limit=50, offset=0)


@app.command("list")
def list_cmd(
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs). Run 'tl describe show channels' for available filters."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """Search channels with optional filters.

    Examples:
        tl channels list                                  # List channels
        tl channels list category:cooking min-subs:100k   # Search with filters
    """
    fmt = detect_format(json_output, csv_output, md_output)
    filters = parse_filters(args or [])

    client = get_client()
    try:
        params = {**filters, "limit": str(limit), "offset": str(offset)}
        data = client.get("/channels", params=params)
        for r in data.get("results", []):
            r["channel_id"] = r.pop("id", None)
        output(
            data,
            fmt,
            columns=["channel_id", "name", "url", "msn", "tpp", "subscribers", "category", "sponsorship_score", "trend"],
            title="Channels",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("show")
def show_cmd(
    channel_ref: str = typer.Argument(..., help="Channel ID (numeric) or name (partial match, must be unique)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output (flattens adspots: one row per adspot)"),
) -> None:
    """Show channel detail by ID or name (includes active adspots).

    Accepts either a numeric channel ID or a partial name. Names that
    match more than one active channel return a 400 with the candidate
    IDs listed so you can retry with a specific ID.

    Examples:
        tl channels show 12345
        tl channels show "Economics Explained"
        tl channels show 12345 --csv > channel.csv
    """
    fmt = detect_format(json_output, csv_output, False)

    encoded_ref = urllib.parse.quote(channel_ref, safe="")
    client = get_client()
    try:
        data = client.get(f"/channels/{encoded_ref}")
        for i, r in enumerate(data.get("results", []) if isinstance(data.get("results"), list) else []):
            renamed = {}
            for k, v in r.items():
                if k == "id":
                    renamed["channel_id"] = v
                else:
                    renamed[k] = v
            data["results"][i] = renamed
        output_single(data, fmt)
        if fmt == "table" and data.get("show_cta"):
            record = data.get("results", data)
            if isinstance(record, list) and record:
                record = record[0]
            if isinstance(record, dict):
                hint = detail_hint(client, channel=record.get("name"))
                if hint:
                    Console(stderr=True).print(f"\n[yellow]{hint}[/yellow]")
    except ApiError as e:
        _handle_channel_api_error(e)
    finally:
        client.close()


def _handle_channel_api_error(e: ApiError) -> None:
    """Print a candidates list for 400 responses with `candidates` in the
    body (ambiguous channel name) and exit 1; otherwise defer to the
    default handler. Used by both `show` and `similar` since they share
    the server-side _resolve_channel helper and the same error shape.
    """
    if e.status_code == 400 and isinstance(e.raw, dict) and e.raw.get("candidates"):
        err = Console(stderr=True)
        err.print(f"[yellow]{e.detail}[/yellow]")
        err.print()
        err.print("[bold]Candidates:[/bold]")
        err.print(f"  {'channel_id':>10}  {'subscribers':>12}  name")
        err.print(f"  {'-' * 10}  {'-' * 12}  {'-' * 40}")
        for c in e.raw["candidates"]:
            subs = c.get("subscribers") or 0
            err.print(f"  {c['channel_id']:>10}  {subs:>12,}  {c['name']}")
        raise typer.Exit(1)
    handle_api_error(e)


def _format_score(results: list[dict]) -> list[dict]:
    """Convert raw cosine score (0.0-1.0) to percentage string for table/csv/md."""
    for row in results:
        score = row.get("score")
        if isinstance(score, (int, float)):
            row["score"] = f"{score * 100:.1f}%"
    return results


def _do_similar(channel_ref: str, args: list[str], fmt: str, limit: int) -> None:
    """Shared implementation for `similar` and `look-alike`.

    Server-side filters: language, msn, min-score (passed through in the
    query string). Client-side filters: category, min-subs, max-subs,
    exclude (applied to the returned, enriched rows).
    """
    filters = parse_filters(args)

    # Split filters into server-side and client-side sets.
    server_keys = {"language", "msn", "min-score"}
    server_params = {k: filters.pop(k) for k in list(filters) if k in server_keys}
    server_params["limit"] = str(limit)

    encoded_ref = urllib.parse.quote(channel_ref, safe="")
    client = get_client()
    try:
        data = client.get(f"/channels/{encoded_ref}/similar", params=server_params)

        # Client-side post-filters
        results = data.get("results", [])
        if "category" in filters:
            target = filters["category"]
            results = [r for r in results if str(r.get("category", "")) == target]
        if "min-subs" in filters:
            try:
                n = int(filters["min-subs"])
                results = [r for r in results if (r.get("subscribers") or 0) >= n]
            except ValueError:
                pass
        if "max-subs" in filters:
            try:
                n = int(filters["max-subs"])
                results = [r for r in results if (r.get("subscribers") or 0) <= n]
            except ValueError:
                pass
        if "exclude" in filters:
            excluded = {int(x) for x in filters["exclude"].split(",") if x.strip().isdigit()}
            results = [r for r in results if r.get("id") not in excluded]

        data["results"] = results
        for r in data["results"]:
            r["channel_id"] = r.pop("id", None)
        if fmt in ("table", "md"):
            _format_score(data["results"])
        output(
            data,
            fmt,
            columns=SIMILAR_COLUMNS,
            title=f"Channels similar to {channel_ref}",
            column_config=SIMILAR_COLUMN_CONFIG,
        )
    except ApiError as e:
        _handle_channel_api_error(e)
    finally:
        client.close()


@app.command("similar")
def similar_cmd(
    channel_ref: str = typer.Argument(..., help="Channel ID (numeric) or name (partial match, must be unique)"),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs). Run 'tl describe show channels' for available filters."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Find channels similar to a given one (by id or name).

    Costs 50 credits per call. Intelligence plan required. Results are
    ranked by cosine similarity and enriched with subscribers, impression,
    total_views, category, and the channel's representative CPM.

    Server-side filters (pushed to the recommender):
        language:<iso>      Restrict to a content language (default: en)
        msn:<true|false>    Restrict to Media Selling Network (default: true)
        min-score:<0-1>     Minimum cosine similarity (default: 0.5)

    Client-side post-filters (applied after fetch):
        category:<code>     Keep only rows matching this content_category
        min-subs:<N>        Subscribers >= N
        max-subs:<N>        Subscribers <= N
        exclude:<id,id,…>   Drop specific channel ids

    Examples:
        tl channels similar 12345
        tl channels similar "MrBeast" language:en msn:false
        tl channels similar 12345 min-score:0.7 min-subs:1000000 --limit 10
    """
    fmt = detect_format(json_output, csv_output, md_output)
    _do_similar(channel_ref, args or [], fmt, limit)


@app.command("history")
def history_cmd(
    channel_ref: str = typer.Argument(..., help="Channel ID (numeric) or name (partial match, must be unique)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """Show a channel's sponsorship history (videos with detected sponsors).

    Requires an Intelligence plan.

    Examples:
        tl channels history 157060
        tl channels history "Economics Explained"
    """
    fmt = detect_format(json_output, csv_output, md_output)
    encoded_ref = urllib.parse.quote(channel_ref, safe="")
    client = get_client()
    try:
        params = {"limit": str(limit), "offset": str(offset)}
        data = client.get(f"/channels/{encoded_ref}/history", params=params)
        channel_name = data.get("channel", {}).get("name", channel_ref)
        output(
            data,
            fmt,
            columns=["video_id", "title", "brands", "views", "publication_date", "is_tl"],
            title=f"Channel History: {channel_name}",
        )
    except ApiError as e:
        _handle_channel_api_error(e)
    finally:
        client.close()


@app.command("look-alike", hidden=True)
def look_alike_cmd(
    channel_ref: str = typer.Argument(..., help="Channel ID or name"),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs). Run 'tl describe show channels' for available filters."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results"),
) -> None:
    """Alias for `tl channels similar` (matches internal "look-alike channels" terminology)."""
    fmt = detect_format(json_output, csv_output, md_output)
    _do_similar(channel_ref, args or [], fmt, limit)
