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


def _split_hint(error: ApiError) -> tuple[str, str | None]:
    """Separate the server's `hint` from `detail`.

    The server sends the remediation both concatenated into `detail` (for
    older clients) and as a separate `hint` key — render it on its own line
    so it can't get lost in the error. Used by every branch: a hint on a
    billing refusal (402/403/429) is just as actionable as one on a 400.
    """
    detail = error.detail or ""
    hint = (error.raw or {}).get("hint") if isinstance(error.raw, dict) else None
    if isinstance(hint, str) and hint:
        if detail.endswith(hint):
            detail = detail[: -len(hint)].rstrip()
        return detail, hint
    return detail, None


def _print_hint(hint: str | None) -> None:
    if hint:
        err.print(f"[bold yellow]Hint:[/bold yellow] [yellow]{hint}[/yellow]")


def handle_api_error(error: ApiError) -> None:
    """Print a user-friendly error message and exit with the right code."""
    detail, hint = _split_hint(error)
    if error.status_code == 401:
        err.print("[red]Authentication required.[/red] Run: tl auth login")
        _print_debug(error)
        sys.exit(2)
    elif error.status_code == 402:
        # The server composes the whole story into `detail` — a prepaid org is
        # told to deposit, a subscription org that it can also just wait for
        # the next top-up — and only the server knows which org this is. The
        # old fixed copy overwrote that with the same top-up pitch for
        # everyone. The `tl credits buy` line stays: it is the CLI-native way
        # to act on the refusal, which the server's sentence can't know about.
        if detail:
            err.print(f"[red]{detail}[/red]")
        else:
            err.print("[red]Insufficient credits.[/red]")
            err.print("Or visit: https://app.thoughtleaders.io/billing")
        _print_hint(hint)
        err.print("Top up with: [bold]tl credits buy --amount-usd 10[/bold]")
        _print_debug(error)
        sys.exit(4)
    elif error.status_code == 403:
        # Verbatim, with no upsell line of our own: a billing 403's detail
        # already names the paid plan (UPGRADE_MESSAGE and friends), and the
        # rest of the 403s — "Superuser only", permission errors — are not
        # plan problems, so "your plan may not include this" was misdirection
        # exactly where the user needed the real reason.
        err.print(f"[red]Access denied:[/red] {detail}")
        _print_hint(hint)
        _print_debug(error)
        # Exit 5 is the machine-readable "access denied" signal (plan gates,
        # permission errors) — scripts branch on it instead of parsing stderr.
        sys.exit(5)
    elif error.status_code == 404:
        err.print(f"[yellow]Not found:[/yellow] {detail}")
        _print_hint(hint)
        _print_debug(error)
        sys.exit(1)
    elif error.status_code == 429:
        # Both quota gates refuse with 429 and compose the whole explanation
        # into `detail` — which cap was hit, how much of it is used, and when it
        # frees up. Collapsing that to a flat "rate limited" line drops the only
        # thing that tells the user whether to wait, buy credits, or ask for a
        # seat. An edge/WAF 429 carries no detail and keeps the generic wording.
        if detail:
            err.print(f"[yellow]{detail}[/yellow]")
        else:
            err.print("[yellow]Rate limited.[/yellow] Please wait and try again.")
        _print_hint(hint)
        _print_debug(error)
        sys.exit(3)
    elif error.status_code >= 500:
        err.print(f"[red]Server error ({error.status_code}):[/red] {error.detail}")
        _print_debug(error)
        sys.exit(3)
    else:
        err.print(f"[red]Error ({error.status_code}):[/red] {detail}")
        _print_hint(hint)
        _print_debug(error)
        sys.exit(1)
