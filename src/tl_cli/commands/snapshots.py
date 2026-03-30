"""tl snapshots — Firebolt time-series metrics for channels and videos."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format, output

app = typer.Typer(help="Historical metrics snapshots (Firebolt time-series)")


@app.command("channel")
def channel_snapshots(
    channel_id: int = typer.Argument(..., help="Channel ID"),
    since: str | None = typer.Option(None, "--since", help="Start date (YYYY-MM-DD)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(100, "--limit", "-l", help="Max data points"),
) -> None:
    """Channel metrics over time (subscribers, total views).

    Requires a paid plan.

    Examples:
        tl snapshots channel 12345
        tl snapshots channel 12345 --since 2025-01-01
    """
    fmt = detect_format(json_output, csv_output, md_output, quiet)

    params: dict[str, str] = {"limit": str(limit)}
    if since:
        params["since"] = since

    client = get_client()
    try:
        data = client.get(f"/snapshots/channel/{channel_id}", params=params)
        output(
            data,
            fmt,
            columns=["scrape_date", "reach", "total_views"],
            title=f"Channel {channel_id} Metrics",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("video")
def video_snapshots(
    video_id: str = typer.Argument(..., help="Video/article ID"),
    channel: int = typer.Option(
        ..., "--channel", "-c",
        help="Channel ID (required — Firebolt needs this for fast queries)",
    ),
    since: str | None = typer.Option(None, "--since", help="Start date (YYYY-MM-DD)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(100, "--limit", "-l", help="Max data points"),
) -> None:
    """Video view curve over time (views, likes, comments by age).

    --channel is required because Firebolt's primary index is (channel_id, id).
    Without it, queries scan 7.4 billion rows.

    Requires a paid plan.

    Examples:
        tl snapshots video dQw4w9WgXcQ --channel 12345
    """
    fmt = detect_format(json_output, csv_output, md_output, quiet)

    params: dict[str, str] = {"channel_id": str(channel), "limit": str(limit)}
    if since:
        params["since"] = since

    client = get_client()
    try:
        data = client.get(f"/snapshots/video/{video_id}", params=params)
        output(
            data,
            fmt,
            columns=["scrape_date", "age", "view_count", "like_count", "comment_count"],
            title=f"Video {video_id} View Curve",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
