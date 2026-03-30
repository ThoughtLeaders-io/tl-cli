"""tl reports — List and run saved reports."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="Saved reports (list and run)")


@app.callback(invoke_without_command=True)
def reports(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
) -> None:
    """List your organization's saved reports (free, no credits).

    Examples:
        tl reports
        tl reports --json
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, csv_output, md_output, quiet)

    client = get_client()
    try:
        data = client.get("/reports")
        output(
            data,
            fmt,
            columns=["id", "title", "report_type", "created_by", "updated_at"],
            title="Saved Reports",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("run")
def run_report(
    report_id: int = typer.Argument(..., help="Report ID to execute"),
    since: str | None = typer.Option(None, "--since", help="Override start date"),
    until: str | None = typer.Option(None, "--until", help="Override end date"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(100, "--limit", "-l", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
) -> None:
    """Run a saved report with its configured filters.

    Credits are charged based on the results returned (varies by report type).

    Examples:
        tl reports run 789
        tl reports run 789 --since 2026-01-01 --json
    """
    fmt = detect_format(json_output, csv_output, md_output, quiet)

    params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
    if since:
        params["since"] = since
    if until:
        params["until"] = until

    client = get_client()
    try:
        data = client.get(f"/reports/{report_id}/run", params=params)
        output(data, fmt, title=f"Report #{report_id}")
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
