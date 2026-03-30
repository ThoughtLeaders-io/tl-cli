"""TL CLI — ThoughtLeaders command-line interface.

Query sponsorship data, channels, brands, and intelligence.
"""

import json
import sys

import typer

from tl_cli import __version__
from tl_cli.auth.commands import app as auth_app
from tl_cli.commands.ask import app as ask_app
from tl_cli.commands.balance import app as balance_app
from tl_cli.commands.brands import app as brands_app
from tl_cli.commands.channels import app as channels_app
from tl_cli.commands.comments import app as comments_app
from tl_cli.commands.deals import app as deals_app
from tl_cli.commands.describe import app as describe_app
from tl_cli.commands.doctor import app as doctor_app
from tl_cli.commands.reports import app as reports_app
from tl_cli.commands.setup import app as setup_app
from tl_cli.commands.snapshots import app as snapshots_app
from tl_cli.commands.uploads import app as uploads_app

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
) -> None:
    """ThoughtLeaders CLI."""


# Data commands (primary interface)
app.add_typer(deals_app, name="deals")
app.add_typer(uploads_app, name="uploads")
app.add_typer(channels_app, name="channels")
app.add_typer(brands_app, name="brands")
app.add_typer(snapshots_app, name="snapshots")
app.add_typer(reports_app, name="reports")
app.add_typer(comments_app, name="comments")

# Discoverability
app.add_typer(describe_app, name="describe")

# AI fallback
app.add_typer(ask_app, name="ask")

# System
app.add_typer(auth_app, name="auth")
app.add_typer(setup_app, name="setup")
app.add_typer(balance_app, name="balance")
app.add_typer(doctor_app, name="doctor")


if __name__ == "__main__":
    app()
