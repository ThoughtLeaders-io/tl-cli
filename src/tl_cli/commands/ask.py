"""tl ask — Natural language query (AI-powered).

Supports two modes:
1. Data queries → POST /ask → returns results directly
2. Report creation → POST /reports/create → polls → preview → confirm
The LLM determines the intent automatically.
"""

import json
import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format, output

app = typer.Typer(help="Natural language data queries (AI-powered)")
err = Console(stderr=True)

REPORT_TYPE_LABELS = {1: "Content", 2: "Brands", 3: "Channels", 8: "Sponsorships"}
POLL_INTERVAL = 2


# --- Report creation helpers (used when LLM determines intent is to create a report) ---


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
    """Poll the server for the AI pipeline result."""
    deadline = time.time() + timeout
    last_message = ""

    with err.status("[bold blue]Thinking...[/bold blue]") as status:
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
                    err.print("[red]Request failed on the server.[/red]")
                    raise typer.Exit(1)
                return result

            time.sleep(POLL_INTERVAL)

    err.print(f"[red]Request timed out after {timeout}s[/red]")
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

    try:
        idx = int(answer.strip()) - 1
        if 0 <= idx < len(suggestions):
            s = suggestions[idx]
            answer = s.get("title", s) if isinstance(s, dict) else s
    except ValueError:
        pass

    return answer


def _confirm_and_create(client, config: dict, prompt: str, json_output: bool, quiet: bool) -> None:
    """Show report preview, confirm with user, and save."""
    if json_output:
        print(json.dumps(config, indent=2, default=str))
        return

    err.print()
    err.print(_format_preview(config))

    confirmed = typer.confirm("Create this report?", default=True)
    if not confirmed:
        err.print("[dim]Cancelled.[/dim]")
        raise typer.Exit(0)

    data = client.post("/reports/confirm", json_body={
        "config": config,
        "prompts": [prompt],
        "reasoning": "",
    })

    results = data.get("results", [{}])
    result = results[0] if results else {}
    report_url = result.get("report_url", "")
    campaign_id = result.get("campaign_id", "")

    if quiet:
        print(json.dumps(data, indent=2, default=str))
    else:
        err.print()
        err.print("[green bold]Report created![/green bold]")
        err.print(f"  Campaign ID: {campaign_id}")
        err.print(f"  URL: https://app.thoughtleaders.io{report_url}")

        unresolved = result.get("unresolved_names", [])
        if unresolved:
            err.print(f"\n  [yellow]Unresolved names:[/yellow] {', '.join(unresolved)}")


def _handle_ai_pipeline(client, question: str, json_output: bool, quiet: bool, timeout: int) -> bool:
    """Run the AI pipeline via /reports/create. Returns True if handled, False to fall back to /ask."""
    conversation: list[dict[str, str]] = []
    current_prompt = question

    while True:
        try:
            create_data = client.post("/reports/create", json_body={
                "prompt": current_prompt,
                "conversation": conversation,
            })
        except ApiError:
            return False  # Fall back to /ask

        task_id = create_data.get("task_id")
        if not task_id:
            return False

        result = _poll_for_result(client, task_id, timeout)
        action = result.get("action", "")

        if action == "follow_up":
            answer = _handle_follow_up(result)
            conversation.append({"role": "user", "content": current_prompt})
            conversation.append({"role": "assistant", "content": result.get("question", "")})
            current_prompt = answer
            continue

        if action == "preview":
            config = result.get("config", {})
            _confirm_and_create(client, config, question, json_output, quiet)
            return True

        if action == "create_report":
            _confirm_and_create(client, result, question, json_output, quiet)
            return True

        if action in ("error", "unsupported"):
            message = result.get("message", "Request could not be processed.")
            err.print(f"\n[red]{message}[/red]")
            raise typer.Exit(1)

        return False  # Unknown action, fall back to /ask


# --- Main command ---


@app.callback(invoke_without_command=True)
def ask(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Your question in plain English"),
    llm_key: str | None = typer.Option(
        None, "--llm-key", envvar="TL_LLM_KEY",
        help="Your own LLM API key (waives surcharge)",
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    csv_output: bool = typer.Option(False, "--csv", help="CSV output"),
    md_output: bool = typer.Option(False, "--md", help="Markdown output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    timeout: int = typer.Option(300, "--timeout", help="Max wait time in seconds"),
) -> None:
    """Ask a question about your data in plain English.

    The AI determines what you need. If it recognizes a report creation intent,
    it will build a config, show you a preview, and create the report after
    your confirmation.

    Examples:
        tl ask "gaming channels sponsoring energy drinks"
        tl ask "show me all sold sponsorships for Nike in Q1"
        tl ask "which channels had the most views last month" --llm-key sk-...
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, csv_output, md_output, quiet)

    client = get_client()
    try:
        # Try the AI pipeline first (handles report creation, follow-ups)
        handled = _handle_ai_pipeline(client, question, json_output, quiet, timeout)
        if handled:
            return

        # Fall back to the simple /ask endpoint for data queries
        body: dict = {"query": question, "limit": limit}
        if llm_key:
            body["llm_key"] = llm_key

        data = client.post("/ask", json_body=body)
        output(data, fmt, title="Results")
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
