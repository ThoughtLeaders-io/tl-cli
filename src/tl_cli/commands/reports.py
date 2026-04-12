"""tl reports — List, run, and create reports."""

import json
import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format, output

app = typer.Typer(help="Saved reports (list, run, create)")
err = Console(stderr=True)

# Report type labels matching Django's ReportType enum
REPORT_TYPE_LABELS = {1: "Content", 2: "Brands", 3: "Channels", 8: "Sponsorships"}

POLL_INTERVAL = 2  # seconds between server polls


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


# ---------------------------------------------------------------------------
# tl reports create — AI Report Builder (server-side)
# ---------------------------------------------------------------------------


def _format_preview(config: dict) -> Panel:
    """Format a report config as a Rich panel for terminal display."""
    lines = Text()

    report_type = config.get("report_type", 3)
    lines.append("Report Type: ", style="bold")
    lines.append(f"{REPORT_TYPE_LABELS.get(report_type, report_type)}\n")

    title = config.get("report_title", "Untitled")
    lines.append("Title: ", style="bold")
    lines.append(f"{title}\n")

    filterset = config.get("filterset", {})
    keyword_groups = filterset.get("keyword_groups", [])
    if keyword_groups:
        lines.append("\nKeywords: ", style="bold")
        kw_texts = []
        for g in keyword_groups:
            if isinstance(g, dict):
                text = g.get("text", "")
                if g.get("exclude"):
                    kw_texts.append(f"-{text}")
                else:
                    kw_texts.append(text)
        lines.append(", ".join(kw_texts[:20]))
        if len(kw_texts) > 20:
            lines.append(f" ... and {len(kw_texts) - 20} more")
        lines.append("\n")

    filters = []
    if filterset.get("languages"):
        filters.append(f"Languages: {', '.join(str(lang) for lang in filterset['languages'])}")
    if filterset.get("content_categories"):
        filters.append(f"Categories: {', '.join(str(c) for c in filterset['content_categories'])}")
    if filterset.get("youtube_views_from") or filterset.get("youtube_views_to"):
        vf = filterset.get("youtube_views_from", "")
        vt = filterset.get("youtube_views_to", "")
        filters.append(f"Views: {vf} - {vt}")
    if filterset.get("days_ago"):
        filters.append(f"Last {filterset['days_ago']} days")

    if filters:
        lines.append("\nFilters: ", style="bold")
        lines.append("; ".join(filters))
        lines.append("\n")

    summary = config.get("summary", "")
    if summary:
        lines.append("\nSummary: ", style="bold dim")
        lines.append(summary, style="dim")
        lines.append("\n")

    return Panel(lines, title="[bold]Report Preview[/bold]", border_style="blue")


def _poll_for_result(client, task_id: str, timeout: int) -> dict:
    """Poll the server for the orchestration result."""
    deadline = time.time() + timeout
    last_message = ""

    with err.status("[bold blue]Analyzing your request...[/bold blue]") as status:
        while time.time() < deadline:
            data = client.get(f"/reports/poll/{task_id}")

            for entry in data.get("status_log", []):
                if isinstance(entry, dict):
                    msg = entry.get("description", "") or entry.get("title", "")
                    if msg and msg != last_message:
                        status.update(f"[bold blue]{msg}[/bold blue]")
                        last_message = msg

            if data.get("finished"):
                result = data.get("end_result")
                if data.get("error") or not result:
                    err.print("[red]Report generation failed on the server.[/red]")
                    raise typer.Exit(1)
                return result

            time.sleep(POLL_INTERVAL)

    err.print(f"[red]Orchestration timed out after {timeout}s[/red]")
    raise typer.Exit(1)


def _handle_follow_up(result: dict) -> str:
    """Display follow-up question and get user's answer."""
    question = result.get("question", "Could you provide more details?")
    suggestions = result.get("suggestions", [])
    err.print(f"\n[yellow]{question}[/yellow]")
    if suggestions:
        for i, s in enumerate(suggestions, 1):
            title = s.get("title", s) if isinstance(s, dict) else s
            err.print(f"  [dim]{i}.[/dim] {title}")
        err.print()

    answer = typer.prompt("Your answer")

    # Allow picking by number
    try:
        idx = int(answer.strip()) - 1
        if 0 <= idx < len(suggestions):
            s = suggestions[idx]
            answer = s.get("title", s) if isinstance(s, dict) else s
    except ValueError:
        pass

    return answer


@app.command("create")
def create_report(
    prompt: str = typer.Argument(..., help="Natural language description of the report you want"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON config"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
    timeout: int = typer.Option(300, "--timeout", help="Max orchestration time in seconds"),
) -> None:
    """Create a report from a natural language description.

    Sends your prompt to the ThoughtLeaders server, which runs the AI Report
    Builder pipeline (keyword research, config generation, review). Then
    confirms with the server to create the campaign.

    Examples:
        tl reports create "gaming channels sponsoring energy drinks"
        tl reports create "tech review channels with 100K+ subscribers" --yes
        tl reports create "beauty brands on YouTube" --json
    """
    client = get_client()
    try:
        conversation: list[dict[str, str]] = []
        current_prompt = prompt

        while True:
            # Send prompt to server, poll for result
            try:
                create_data = client.post("/reports/create", json_body={
                    "prompt": current_prompt,
                    "conversation": conversation,
                })
            except ApiError as e:
                if e.status_code == 503:
                    err.print("[red]AI Report Builder is temporarily unavailable. Please try again later.[/red]")
                    raise typer.Exit(1)
                handle_api_error(e)
                raise typer.Exit(1)

            task_id = create_data.get("task_id")
            if not task_id:
                err.print("[red]Server did not return a task ID.[/red]")
                raise typer.Exit(1)

            result = _poll_for_result(client, task_id, timeout)
            action = result.get("action", "")

            # Server wraps response: "preview" → config in result["config"]
            if action == "follow_up":
                answer = _handle_follow_up(result)
                conversation.append({"role": "user", "content": current_prompt})
                conversation.append({"role": "assistant", "content": result.get("question", "")})
                current_prompt = answer
                continue

            if action in ("error", "unsupported"):
                message = result.get("message", "Request could not be processed.")
                err.print(f"\n[red]{message}[/red]")
                raise typer.Exit(1)

            if action == "preview":
                config = result.get("config", {})
            elif action == "create_report":
                config = result
            else:
                err.print(f"[yellow]Unexpected action: {action}[/yellow]")
                if json_output:
                    print(json.dumps(result, indent=2, default=str))
                raise typer.Exit(1)

            break

        # --- Show preview ---
        if json_output:
            print(json.dumps(config, indent=2, default=str))
            if not yes:
                raise typer.Exit(0)
        else:
            err.print()
            err.print(_format_preview(config))

        # --- Confirm ---
        if not yes:
            confirmed = typer.confirm("Create this report?", default=True)
            if not confirmed:
                err.print("[dim]Cancelled.[/dim]")
                raise typer.Exit(0)

        # --- Save to server ---
        data = client.post("/reports/confirm", json_body={
            "config": config,
            "prompts": [prompt],
            "reasoning": "",
        })

        results = data.get("results", [{}])
        result = results[0] if results else {}
        report_url = result.get("report_url", "")
        campaign_id = result.get("campaign_id", "")

        if json_output or quiet:
            print(json.dumps(data, indent=2, default=str))
        else:
            err.print()
            err.print("[green bold]Report created![/green bold]")
            err.print(f"  Campaign ID: {campaign_id}")
            err.print(f"  URL: https://app.thoughtleaders.io{report_url}")

            unresolved = result.get("unresolved_names", [])
            if unresolved:
                err.print(f"\n  [yellow]Unresolved names:[/yellow] {', '.join(unresolved)}")

            usage = data.get("usage")
            if usage:
                err.print(f"\n  [dim]{usage.get('credits_charged', 0)} credits · {usage.get('balance_remaining', '?')} remaining[/dim]")

    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
