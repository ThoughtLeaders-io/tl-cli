"""User-friendly error handling for API responses."""

import sys

from rich.console import Console

err = Console(stderr=True)


class ApiError(Exception):
    """Raised when the API returns a non-success status."""

    def __init__(self, status_code: int, detail: str, raw: dict | None = None):
        self.status_code = status_code
        self.detail = detail
        self.raw = raw
        super().__init__(f"HTTP {status_code}: {detail}")


def handle_api_error(error: ApiError) -> None:
    """Print a user-friendly error message and exit with the right code."""
    if error.status_code == 401:
        err.print("[red]Authentication required.[/red] Run: tl auth login")
        sys.exit(2)
    elif error.status_code == 402:
        err.print("[red]Insufficient credits.[/red]")
        err.print("Deposit more at: https://app.thoughtleaders.io/settings/billing")
        sys.exit(4)
    elif error.status_code == 403:
        err.print(f"[red]Access denied:[/red] {error.detail}")
        err.print("Your plan may not include access to this resource.")
        sys.exit(1)
    elif error.status_code == 404:
        err.print(f"[yellow]Not found:[/yellow] {error.detail}")
        sys.exit(1)
    elif error.status_code == 429:
        err.print("[yellow]Rate limited.[/yellow] Please wait and try again.")
        sys.exit(3)
    elif error.status_code >= 500:
        err.print(f"[red]Server error ({error.status_code}):[/red] {error.detail}")
        sys.exit(3)
    else:
        err.print(f"[red]Error ({error.status_code}):[/red] {error.detail}")
        sys.exit(1)
