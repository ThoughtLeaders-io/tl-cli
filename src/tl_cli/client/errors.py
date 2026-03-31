"""User-friendly error handling for API responses."""

import json
import sys
import traceback

from rich.console import Console

err = Console(stderr=True)


class ApiError(Exception):
    """Raised when the API returns a non-success status."""

    def __init__(self, status_code: int, detail: str, raw: dict | None = None, url: str | None = None, response_text: str | None = None):
        self.status_code = status_code
        self.detail = detail
        self.raw = raw
        self.url = url
        self.response_text = response_text
        super().__init__(f"HTTP {status_code}: {detail}")


def _print_debug(error: ApiError) -> None:
    """Print detailed debug info for an API error."""
    from tl_cli.config import debug

    if not debug:
        return
    err.print(f"\n[dim]--- debug ---[/dim]")
    if error.url:
        err.print(f"[dim]URL: {error.url}[/dim]")
    err.print(f"[dim]HTTP {error.status_code}: {error.detail}[/dim]")
    if error.response_text:
        err.print(f"[dim]Response body:[/dim]")
        err.print(f"[dim]{error.response_text}[/dim]")
    err.print(f"[dim]Traceback:[/dim]")
    err.print(f"[dim]{''.join(traceback.format_exception(error))}[/dim]")


def handle_api_error(error: ApiError) -> None:
    """Print a user-friendly error message and exit with the right code."""
    if error.status_code == 401:
        err.print("[red]Authentication required.[/red] Run: tl auth login")
        _print_debug(error)
        sys.exit(2)
    elif error.status_code == 402:
        err.print("[red]Insufficient credits.[/red]")
        err.print("Deposit more at: https://app.thoughtleaders.io/settings/billing")
        _print_debug(error)
        sys.exit(4)
    elif error.status_code == 403:
        err.print(f"[red]Access denied:[/red] {error.detail}")
        err.print("Your plan may not include access to this resource.")
        _print_debug(error)
        sys.exit(1)
    elif error.status_code == 404:
        err.print(f"[yellow]Not found:[/yellow] {error.detail}")
        _print_debug(error)
        sys.exit(1)
    elif error.status_code == 429:
        err.print("[yellow]Rate limited.[/yellow] Please wait and try again.")
        _print_debug(error)
        sys.exit(3)
    elif error.status_code >= 500:
        err.print(f"[red]Server error ({error.status_code}):[/red] {error.detail}")
        _print_debug(error)
        sys.exit(3)
    else:
        err.print(f"[red]Error ({error.status_code}):[/red] {error.detail}")
        _print_debug(error)
        sys.exit(1)
