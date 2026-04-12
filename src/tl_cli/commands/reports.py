"""tl reports — List, run, and create reports."""

import json
import os
import subprocess
import sys
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

KEYWORD_SUBPROCESS_TIMEOUT = 120
SIMILAR_CHANNELS_TIMEOUT = 60


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
# tl reports create — AI Report Builder (bundled skills orchestration)
# ---------------------------------------------------------------------------


def _find_skills_path() -> str:
    """Locate the skills scripts directory.

    Search order:
    1. TL_SKILLS_PATH env var (explicit override)
    2. Bundled inside the tl-cli package (installed via pip/pipx)
    3. Sibling thoughtleaders-skills repo (dev checkout)
    """
    # 1. Explicit env var
    env_path = os.environ.get("TL_SKILLS_PATH")
    if env_path and os.path.isdir(env_path):
        return env_path

    # 2. Bundled with the package — look for skills/ relative to the installed package
    #    pyproject.toml force-includes skills/ → tl_cli/_plugin/skills/
    package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(package_dir, "_plugin", "skills")
    if os.path.isdir(bundled):
        return bundled

    # Also check the repo root (when running from source checkout)
    repo_root = os.path.dirname(os.path.dirname(package_dir))
    repo_skills = os.path.join(repo_root, "skills")
    if os.path.isdir(repo_skills) and os.path.isfile(os.path.join(repo_skills, "create-report", "scripts", "orchestrate_preview.py")):
        return repo_skills

    # 3. Common dev locations
    for path in [
        os.path.expanduser("~/thoughtleaders-skills"),
        os.path.expanduser("~/projects/thoughtleaders-skills"),
        os.path.expanduser("~/code/thoughtleaders-skills"),
    ]:
        if os.path.isdir(path):
            return path

    return ""


def _check_env_vars() -> list[str]:
    """Check for required environment variables, return list of missing ones."""
    missing = []
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY")):
        missing.append("OPENROUTER_API_KEY")
    if not (os.environ.get("ES_HOST") or os.environ.get("ELASTIC_SEARCH_URL")):
        missing.append("ES_HOST")
    return missing


def _format_preview(config: dict) -> Panel:
    """Format a report config as a Rich panel for terminal display."""
    lines = Text()

    report_type = config.get("report_type", 3)
    lines.append("Report Type: ", style="bold")
    lines.append(f"{REPORT_TYPE_LABELS.get(report_type, report_type)}\n")

    title = config.get("report_title", "Untitled")
    lines.append("Title: ", style="bold")
    lines.append(f"{title}\n")

    # Keywords
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

    # Key filters
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

    # Summary
    summary = config.get("summary", "")
    if summary:
        lines.append("\nSummary: ", style="bold dim")
        lines.append(summary, style="dim")
        lines.append("\n")

    return Panel(lines, title="[bold]Report Preview[/bold]", border_style="blue")


def _run_orchestration(
    skills_path: str,
    prompt: str,
    conversation: list[dict[str, str]],
    timeout: int,
) -> dict:
    """Run orchestrate_preview.py from the bundled skills. Returns config dict."""
    script = os.path.join(skills_path, "create-report", "scripts", "orchestrate_preview.py")
    if not os.path.isfile(script):
        err.print(f"[red]Script not found:[/red] {script}")
        raise typer.Exit(1)

    args = [sys.executable, script, "--prompt", prompt, "--conversation", json.dumps(conversation)]

    with err.status("[bold blue]Analyzing your request...[/bold blue]") as status:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )

        stderr_lines: list[str] = []
        try:
            for line in proc.stderr:  # type: ignore[union-attr]
                stderr_lines.append(line)
                stripped = line.strip()
                if stripped.startswith("{"):
                    try:
                        entry = json.loads(stripped)
                        if isinstance(entry, dict) and "stage" in entry and "message" in entry:
                            status.update(f"[bold blue]{entry['message']}[/bold blue]")
                    except json.JSONDecodeError:
                        pass

            stdout = proc.stdout.read() if proc.stdout else ""
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            err.print(f"[red]Orchestration timed out after {timeout}s[/red]")
            raise typer.Exit(1)

    if proc.returncode != 0:
        stderr_text = "".join(stderr_lines)
        err.print("[red]Report generation failed:[/red]")
        error_lines = [l for l in stderr_text.strip().splitlines() if not l.strip().startswith("{")]
        for line in error_lines[-5:]:
            err.print(f"  {line}")
        raise typer.Exit(1)

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        err.print("[red]Failed to parse orchestrator output.[/red]")
        raise typer.Exit(1)


@app.command("create")
def create_report(
    prompt: str = typer.Argument(..., help="Natural language description of the report you want"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON config"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
    timeout: int = typer.Option(300, "--timeout", help="Max orchestration time in seconds"),
) -> None:
    """Create a report from a natural language description.

    Runs the AI Report Builder pipeline: keyword research, config generation,
    and review. Then confirms with the server to create the campaign.

    Requires: OPENROUTER_API_KEY, ES_HOST (or ELASTIC_SEARCH_URL).

    Examples:
        tl reports create "gaming channels sponsoring energy drinks"
        tl reports create "tech review channels with 100K+ subscribers" --yes
        tl reports create "beauty brands on YouTube" --json
    """
    # Check prerequisites
    skills_path = _find_skills_path()
    if not skills_path:
        err.print("[red]Cannot find skills scripts.[/red]")
        err.print("Try: pip install --upgrade tl-cli")
        raise typer.Exit(1)

    missing_vars = _check_env_vars()
    if missing_vars:
        err.print(f"[red]Missing environment variables:[/red] {', '.join(missing_vars)}")
        err.print("These are needed for LLM calls and keyword validation.")
        raise typer.Exit(1)

    # --- Stage 1 & 2: Run orchestration with follow-up support ---
    conversation: list[dict[str, str]] = []
    current_prompt = prompt

    while True:
        config = _run_orchestration(skills_path, current_prompt, conversation, timeout)
        action = config.get("action", "create_report")

        if action == "follow_up":
            question = config.get("question", "Could you provide more details?")
            suggestions = config.get("suggestions", [])
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

            conversation.append({"role": "user", "content": current_prompt})
            conversation.append({"role": "assistant", "content": question})
            current_prompt = answer
            continue

        if action in ("error", "unsupported"):
            message = config.get("message", "Request could not be processed.")
            err.print(f"\n[red]{message}[/red]")
            suggestion = config.get("suggestion", "")
            if suggestion:
                err.print(f"[dim]{suggestion}[/dim]")
            raise typer.Exit(1)

        if action != "create_report":
            err.print(f"[yellow]Unexpected action: {action}[/yellow]")
            if json_output:
                print(json.dumps(config, indent=2, default=str))
            raise typer.Exit(1)

        break  # Got a config, exit the loop

    # --- Stage 3: Show preview ---
    if json_output:
        print(json.dumps(config, indent=2, default=str))
        if not yes:
            raise typer.Exit(0)
    else:
        err.print()
        err.print(_format_preview(config))

    # --- Stage 4: Confirm ---
    if not yes:
        confirmed = typer.confirm("Create this report?", default=True)
        if not confirmed:
            err.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)

    # --- Stage 5: Call API to create the campaign ---
    client = get_client()
    try:
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
