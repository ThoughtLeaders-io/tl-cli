"""tl ask — Natural language query (AI fallback for users without an agent)."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format, output

app = typer.Typer(help="Natural language data queries (AI-powered fallback)")


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
) -> None:
    """Ask a question about your data in plain English.

    This is an optional fallback for users who don't have an AI agent (like Claude Code).
    If you have Claude Code installed, use the /tl slash command instead — it's free and
    uses your own Claude to translate questions into structured tl commands.

    Credits: result credits + 2/result surcharge (waived with --llm-key).

    Examples:
        tl ask "show me all sold deals for Nike in Q1"
        tl ask "which channels had the most views last month" --llm-key sk-...
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, csv_output, md_output, quiet)

    body: dict = {"query": question, "limit": limit}
    if llm_key:
        body["llm_key"] = llm_key

    client = get_client()
    try:
        data = client.post("/ask", json_body=body)
        output(data, fmt, title="Results")
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
