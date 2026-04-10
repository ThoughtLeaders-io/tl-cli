"""TL CLI — ThoughtLeaders command-line interface.

Query sponsorship data, channels, brands, and intelligence.
"""

import re
import sys
import traceback
from pathlib import Path
from typing import Optional

import click
import typer
from rich.console import Console
from rich.markdown import Markdown

from tl_cli import __version__
from tl_cli import config as tl_config
from tl_cli.auth.commands import app as auth_app
from tl_cli.commands.ask import app as ask_app
from tl_cli.commands.balance import app as balance_app
from tl_cli.commands.brands import app as brands_app
from tl_cli.commands.channels import app as channels_app
from tl_cli.commands.comments import app as comments_app
from tl_cli.commands.deals import app as deals_app
from tl_cli.commands.matches import app as matches_app
from tl_cli.commands.proposals import app as proposals_app
from tl_cli.commands.sponsorships import app as sponsorships_app
from tl_cli.commands.describe import app as describe_app
from tl_cli.commands.doctor import app as doctor_app
from tl_cli.commands.reports import app as reports_app
from tl_cli.commands.setup import app as setup_app
from tl_cli.commands.snapshots import app as snapshots_app
from tl_cli.commands.uploads import app as uploads_app
from tl_cli.commands.whoami import app as whoami_app

app = typer.Typer(
    name="tl",
    help="ThoughtLeaders CLI — query sponsorship data, channels, brands, and intelligence.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def version_callback(value: bool) -> None:
    if value:
        print(f"tl-cli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v", callback=version_callback, is_eager=True,
        help="Show version",
    ),
    debug: bool = typer.Option(
        False, "--debug", help="Show detailed error information",
    ),
    full_access: bool = typer.Option(
        False, "--full-access", help="Show all data across all brands and channels (requires permission)",
    ),
) -> None:
    """ThoughtLeaders CLI."""
    tl_config.debug = debug
    tl_config.full_access = full_access

    # Skip hints/warnings for setup commands
    import sys
    if "setup" not in sys.argv:
        # First-run hint
        from tl_cli.auth.token_store import load_tokens
        tokens = load_tokens()
        if not tokens:
            err = Console(stderr=True)
            err.print("[dim]Welcome to tl-cli! Get started:[/dim]")
            err.print("[dim]  tl auth login          # authenticate[/dim]")
            err.print("[dim]  tl setup claude        # install Claude Code plugin[/dim]")
            err.print("[dim]  tl setup opencode      # install OpenCode skill[/dim]")
            err.print()

        from tl_cli.commands.setup import check_plugin_version
        for warn in check_plugin_version():
            Console(stderr=True).print(f"[yellow]{warn}[/yellow]")


# System
app.add_typer(auth_app, name="auth")
app.add_typer(setup_app, name="setup")

# Data commands (primary interface)
app.add_typer(sponsorships_app, name="sponsorships")
app.add_typer(matches_app, name="matches")
app.add_typer(proposals_app, name="proposals")
app.add_typer(deals_app, name="deals")
app.add_typer(uploads_app, name="uploads")
app.add_typer(channels_app, name="channels")
app.add_typer(brands_app, name="brands")
app.add_typer(snapshots_app, name="snapshots")
app.add_typer(reports_app, name="reports")
app.add_typer(comments_app, name="comments")

# Discoverability
app.add_typer(describe_app, name="describe")
app.add_typer(balance_app, name="balance")
app.add_typer(doctor_app, name="doctor")
app.add_typer(whoami_app, name="whoami")

# AI fallback
app.add_typer(ask_app, name="ask")


def _get_terminology() -> str | None:
    """Extract the Terminology section from README.md.

    Tries to locate README.md relative to the package source first,
    then falls back to importlib.metadata.
    """
    try:
        text = None
        readme = Path(__file__).resolve().parent.parent.parent / "README.md"
        if readme.is_file():
            text = readme.read_text()
        else:
            from importlib.metadata import metadata
            text = metadata("tl-cli").get_payload()
        if not text:
            return None
        match = re.search(r"^# Terminology\s*\n(.+?)(?=\n# |\Z)", text, re.DOTALL | re.MULTILINE)
        if not match:
            return None
        return match.group(1).strip()
    except Exception:
        return None


@app.command(name="help", hidden=True)
def help_command(
    ctx: typer.Context,
    command: Optional[str] = typer.Argument(None, help="Command to show help for"),
) -> None:
    """Show help for the CLI or a specific command."""
    root_ctx = ctx.parent
    root_cmd = root_ctx.command

    if command is None:
        click.echo(root_cmd.get_help(root_ctx))
        terminology = _get_terminology()
        if terminology:
            import shutil
            term_width = shutil.get_terminal_size().columns
            console = Console(width=int(term_width * 0.9))
            console.print(Markdown(terminology))
            console.print()
        raise typer.Exit()

    # Look up the subcommand
    sub_cmd = root_cmd.get_command(root_ctx, command)
    if sub_cmd is None:
        click.echo(f"Unknown command: {command}", err=True)
        raise typer.Exit(1)

    sub_ctx = click.Context(sub_cmd, info_name=command, parent=root_ctx)
    click.echo(sub_cmd.get_help(sub_ctx))
    raise typer.Exit()


def cli() -> None:
    """Entry point that wraps the Typer app with top-level error handling."""
    try:
        app()
    except SystemExit:
        raise
    except Exception as exc:
        if tl_config.debug:
            traceback.print_exc(file=sys.stderr)
        else:
            Console(stderr=True).print(f"[red]Error:[/red] {exc}")
            Console(stderr=True).print("[dim]Run with --debug for details.[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
