"""tl recommender — Recommender introspection and discovery.

Surfaces the channel/profile similarity machinery that powers the
"Recommender Insights" web view: list the similarity tags (categories,
demographics, formats, etc.), find the top channels and profiles
scoring high on a given tag, inspect a single channel or brand
similarity profile, fetch channels similar to a brand's ideal profile,
or fetch brands likely to sponsor a given channel.

For 1:1 similarity use `tl channels similar` and `tl brands similar`.
"""

import urllib.parse

import typer
from tl_cli._typer_utils import AlphaSortedTyperGroup
from rich.console import Console

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.filters import parse_filters
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(cls=AlphaSortedTyperGroup, help="Recommender (similarity tags, top-channels/profiles/brands, similarity-profile inspection, profile→channel and channel→brand similarity)")


TOP_CHANNEL_COLUMNS = ["value", "channel_id", "channel_name", "slug"]
TOP_PROFILE_COLUMNS = ["value", "profile_id", "profile_email", "brand_name", "brand_slug"]
TOP_BRAND_COLUMNS = ["value", "brand_slug", "brand_name", "profile_id"]
TOP_COLUMN_CONFIG = {"value": {"justify": "right"}}


def _handle_recommender_error(e: ApiError) -> None:
    """Show ambiguity candidates inline; otherwise default handler."""
    if e.status_code == 400 and isinstance(e.raw, dict) and e.raw.get("candidates"):
        err = Console(stderr=True)
        err.print(f"[yellow]{e.detail}[/yellow]")
        err.print()
        err.print("[bold]Candidates:[/bold]")
        for c in e.raw["candidates"]:
            cid = c.get("channel_id") or c.get("brand_id") or "?"
            name = c.get("name", "")
            extra = c.get("website") or c.get("subscribers") or ""
            err.print(f"  {cid:>10}  {name}  [dim]{extra}[/dim]")
        raise typer.Exit(1)
    handle_api_error(e)


@app.callback(invoke_without_command=True)
def recommender(ctx: typer.Context) -> None:
    """Recommender."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command("tags")
def tags_cmd(
    args: list[str] = typer.Argument(None, help="Optional substring (matches tag or normalized name)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
) -> None:
    """List similarity tag names (free).

    Use this to discover the tag names accepted by `tl recommender top`.
    Each tag is one signal in a channel or brand similarity profile —
    e.g. content categories like "Cooking", demographic buckets like
    "Age 18-24", device shares, country shares.

    Examples:
        tl recommender tags
        tl recommender tags cooking
        tl recommender tags "age 18"
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    query = _strip_quotes(" ".join(args or []).strip())
    params = {"q": query} if query else {}
    client = get_client()
    try:
        data = client.get("/recommender/tags", params=params)
        output(
            data,
            fmt,
            columns=["group", "field_name"],
            title="Similarity tags",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


def _strip_quotes(value: str) -> str:
    """Strip one matching pair of surrounding quotes if present.

    Lets users paste an example like `tl recommender top-channels "cooking"`
    where the shell already strips quotes, but also tolerates a layer of
    extra quoting from agents or scripts that re-wrap the literal.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _do_top(kind: str, tag: str, args: list[str], fmt: str, limit: int, columns: list[str], title: str) -> None:
    tag = _strip_quotes(tag)
    filters = parse_filters(args or [])
    server_keys = {"msn", "mbn", "exclude-for-channel", "exclude-for-profile"}
    params = {k: v for k, v in filters.items() if k in server_keys}
    params["tag"] = tag
    params["limit"] = str(limit)

    client = get_client()
    try:
        data = client.get(f"/recommender/top/{kind}", params=params)
        output(
            data,
            fmt,
            columns=columns,
            title=title,
            column_config=TOP_COLUMN_CONFIG,
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("top-channels")
def top_channels_cmd(
    tag: str = typer.Argument(..., help='Similarity tag name (e.g. "Cooking", "Age 18-24"). Run `tl recommender tags` to discover valid names.'),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Top channels scoring high on a single similarity tag.

    Costs 25 credits per call. Intelligence plan required.

    Filters:
        msn:<yes|no|all>            MSN membership (default: all)
        exclude-for-profile:<id>    Drop channels already proposed for this profile

    Examples:
        tl recommender top-channels "Cooking"
        tl recommender top-channels "Tech" msn:yes --limit 30
        tl recommender top-channels "Cooking" exclude-for-profile:842
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    _do_top("channels", tag, args or [], fmt, limit, TOP_CHANNEL_COLUMNS, f"Top channels: {tag}")


@app.command("top-profiles", hidden=True)  # full-access only on the server; not part of the public surface
def top_profiles_cmd(
    tag: str = typer.Argument(..., help='Similarity tag name (e.g. "Cooking", "Age 18-24"). Run `tl recommender tags` to discover valid names.'),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Top brand profiles scoring high on a single similarity tag.

    Costs 25 credits per call. Intelligence plan required. Profiles can
    represent the same brand more than once (one brand → multiple
    profiles); use `top-brands` for brand-deduplicated results.

    Filters:
        mbn:<yes|no|all>            MBN membership (default: all)
        exclude-for-channel:<id>    Drop profiles already proposed for this channel

    Examples:
        tl recommender top-profiles "Cooking"
        tl recommender top-profiles "USA share" mbn:yes --limit 30
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    _do_top("profiles", tag, args or [], fmt, limit, TOP_PROFILE_COLUMNS, f"Top profiles: {tag}")


@app.command("top-brands")
def top_brands_cmd(
    tag: str = typer.Argument(..., help='Similarity tag name (e.g. "Cooking", "Age 18-24"). Run `tl recommender tags` to discover valid names.'),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Top brands scoring high on a single similarity tag (deduplicated from profiles).

    Costs 25 credits per call. Intelligence plan required. Server-side
    aggregates the underlying profile rows by brand, keeping the
    highest-scoring profile per brand.

    Filters:
        mbn:<yes|no|all>            MBN membership of the underlying profile (default: all)
        exclude-for-channel:<id>    Drop brands already proposed for this channel

    Examples:
        tl recommender top-brands "Cooking"
        tl recommender top-brands "USA share" mbn:yes --limit 30
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    _do_top("brands", tag, args or [], fmt, limit, TOP_BRAND_COLUMNS, f"Top brands: {tag}")


@app.command("channels-with-tag")
def channels_with_tag_cmd(
    tag: str = typer.Argument(..., help='Similarity tag name (e.g. "Cooking", "Age 18-24"). Run `tl recommender tags` to discover valid names.'),
    min_value: float = typer.Option(0.00001, "--min", help="Inclusive minimum tag value; only channels scoring at or above this are returned. Defaults to 0.00001, which excludes channels with no loading on the tag."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
    limit: int = typer.Option(100, "--limit", "-l", help="Max results per page (1-1000)"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """Every channel whose value for a similarity tag is at or above a threshold.

    Unlike `top-channels` (which ranks the strongest few), this walks the
    *entire* match set in pages of up to 1000 — including sets larger than
    the search index's 10k window — so you can enumerate every channel above
    a cutoff. Returns channel IDs only; expand them with `tl channels show`
    or `tl recommender inspect-channel`.

    `--min` defaults to 0.00001 — just above zero — so a bare call returns
    every channel with any loading on the tag and drops the zero-fill rest.
    Raise it for a stricter cutoff.

    Costs 1 credit per channel ID returned. Intelligence plan required.

    Examples:
        tl recommender channels-with-tag "Cooking"
        tl recommender channels-with-tag "Age 18-24" --min 0.3 --limit 1000
        tl recommender channels-with-tag "Cooking" --min 0.5 --offset 1000 --json
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    tag = _strip_quotes(tag)
    params = {"tag": tag, "min": str(min_value), "limit": str(limit), "offset": str(offset)}
    client = get_client()
    try:
        data = client.get("/recommender/channels-with-tag", params=params)
        output(
            data,
            fmt,
            columns=["channel_id"],
            title=f"Channels with {tag} >= {min_value}",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("inspect-channel")
def inspect_channel_cmd(
    channel_ref: str = typer.Argument(..., help="Channel ID (numeric) or name (partial match, must be unique)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
) -> None:
    """Show a channel's similarity profile grouped by category.

    Costs 25 credits per call. Intelligence plan required. Returns the
    active similarity tags grouped by category, plus the overall strength.

    Examples:
        tl recommender inspect-channel 12345
        tl recommender inspect-channel "MrBeast"
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    encoded = urllib.parse.quote(channel_ref, safe="")
    client = get_client()
    try:
        data = client.get(f"/recommender/channels/{encoded}/inspect")
        output_single(data, fmt)
    except ApiError as e:
        _handle_recommender_error(e)
    finally:
        client.close()


@app.command("inspect-brand")
def inspect_brand_cmd(
    brand_ref: str = typer.Argument(..., help="Brand ID (numeric) or name (partial match, must be unique)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
) -> None:
    """Show a brand profile's ideal similarity profile grouped by category.

    Costs 25 credits per call. Intelligence plan required. Resolves the
    brand to its (preferred MBN) profile and inspects that profile's
    aggregated similarity tags.

    Examples:
        tl recommender inspect-brand 287
        tl recommender inspect-brand Nike
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    encoded = urllib.parse.quote(brand_ref, safe="")
    client = get_client()
    try:
        data = client.get(f"/recommender/brands/{encoded}/inspect")
        output_single(data, fmt)
    except ApiError as e:
        _handle_recommender_error(e)
    finally:
        client.close()


@app.command("channels-for-profile")
def channels_for_profile_cmd(
    profile_id: int = typer.Argument(..., help="Profile ID (numeric)"),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Channels closest to a brand profile's ideal similarity profile.

    Costs 25 credits per call. Intelligence plan required. Channels the
    brand has already worked with or been proposed are excluded.

    Filters:
        language:<iso>      Content language (default: en)
        msn:<yes|no>        Restrict to MSN channels (default: no)

    Examples:
        tl recommender channels-for-profile 842
        tl recommender channels-for-profile 842 msn:yes --limit 30
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    filters = parse_filters(args or [])
    params = {k: v for k, v in filters.items() if k in {"language", "msn"}}
    params["limit"] = str(limit)
    client = get_client()
    try:
        data = client.get(f"/recommender/profiles/{profile_id}/similar", params=params)
        for r in data.get("results", []):
            score = r.get("score")
            if isinstance(score, (int, float)) and fmt in ("table", "md"):
                r["score"] = f"{score * 100:.1f}%"
        output(
            data,
            fmt,
            columns=["score", "channel_id", "channel_name", "slug"],
            title=f"Channels similar to profile {profile_id}",
            column_config={"score": {"justify": "right"}},
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("channels-for-brand")
def channels_for_brand_cmd(
    brand_ref: str = typer.Argument(..., help="Brand ID, name, slug, or domain (resolved via tl brands find)"),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Channels closest to a brand's ideal similarity profile.

    Resolves the brand to its most-recently-created profile that has an
    indexed search vector and runs the same KNN that
    `channels-for-profile` uses. Costs 25 credits per call. Intelligence
    plan required. Channels the brand has already worked with or been
    proposed are excluded.

    Filters:
        language:<iso>      Content language (default: en)
        msn:<yes|no>        Restrict to MSN channels (default: no)

    Examples:
        tl recommender channels-for-brand 6037
        tl recommender channels-for-brand Nike msn:yes --limit 30
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    filters = parse_filters(args or [])
    params = {k: v for k, v in filters.items() if k in {"language", "msn"}}
    params["limit"] = str(limit)
    encoded = urllib.parse.quote(brand_ref, safe="")
    client = get_client()
    try:
        data = client.get(f"/recommender/brands/{encoded}/channels-for-profile", params=params)
        for r in data.get("results", []):
            score = r.get("score")
            if isinstance(score, (int, float)) and fmt in ("table", "md"):
                r["score"] = f"{score * 100:.1f}%"
        title = f"Channels for brand {brand_ref}"
        prof = data.get("profile") or {}
        if prof.get("brand_name") and prof.get("id"):
            title = f"Channels for {prof['brand_name']} (via profile {prof['id']})"
        output(
            data,
            fmt,
            columns=["score", "channel_id", "channel_name", "slug"],
            title=title,
            column_config={"score": {"justify": "right"}},
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("brands-for-channel")
def brands_for_channel_cmd(
    channel_ref: str = typer.Argument(..., help="Channel ID (numeric) or name (partial match, must be unique)"),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Brands most likely to sponsor a given channel.

    Compares the channel's similarity profile against brand similarity
    profiles and dedupes the results by brand. Costs 25 credits per call.
    Intelligence plan required.

    Filters:
        mbn:<yes|no|all>    MBN membership of the underlying profile (default: all)

    Examples:
        tl recommender brands-for-channel 12345
        tl recommender brands-for-channel "MrBeast" mbn:yes --limit 30
    """
    fmt = detect_format(json_output, csv_output, md_output, toon_output)
    filters = parse_filters(args or [])
    params = {k: v for k, v in filters.items() if k in {"mbn"}}
    params["limit"] = str(limit)
    encoded = urllib.parse.quote(channel_ref, safe="")
    client = get_client()
    try:
        data = client.get(f"/recommender/channels/{encoded}/similar-brands", params=params)
        for r in data.get("results", []):
            score = r.get("score")
            if isinstance(score, (int, float)) and fmt in ("table", "md"):
                r["score"] = f"{score * 100:.1f}%"
        output(
            data,
            fmt,
            columns=["score", "brand_id", "brand_name", "website", "mbn", "profile_id"],
            title=f"Brands likely to sponsor channel {channel_ref}",
            column_config={"score": {"justify": "right"}},
        )
    except ApiError as e:
        _handle_recommender_error(e)
    finally:
        client.close()


@app.command("similar-brands-to-channel", hidden=True)
def similar_brands_to_channel_cmd(
    channel_ref: str = typer.Argument(..., help="Channel ID (numeric) or name (partial match, must be unique)"),
    args: list[str] = typer.Argument(None, help="Filters (key:value pairs)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results (1-100)"),
) -> None:
    """Hidden alias for `brands-for-channel` (older name kept for back-compat)."""
    brands_for_channel_cmd(channel_ref, args, json_output, csv_output, md_output, toon_output, limit)
