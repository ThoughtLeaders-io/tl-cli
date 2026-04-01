"""TTY-aware output formatting.

- Terminal (TTY): Rich tables with styled output
- Piped (non-TTY): Clean JSON
- Explicit flags: --json, --csv, --md override detection
"""

import csv
import io
import json
import sys

from rich.console import Console
from rich.table import Table

# Stderr console for status messages (never pollutes piped data)
err_console = Console(stderr=True)


def detect_format(json_flag: bool, csv_flag: bool, md_flag: bool, quiet: bool) -> str:
    """Determine output format from flags and TTY detection."""
    if quiet:
        return "quiet"
    if json_flag:
        return "json"
    if csv_flag:
        return "csv"
    if md_flag:
        return "md"
    if sys.stdout.isatty():
        return "table"
    return "json"


def output(
    data: dict,
    fmt: str,
    columns: list[str] | None = None,
    title: str | None = None,
    column_config: dict[str, dict] | None = None,
) -> None:
    """Format and print API response data.

    Args:
        data: API response dict with 'results', 'total', 'usage', '_breadcrumbs'
        fmt: Output format ('table', 'json', 'csv', 'md', 'quiet')
        columns: Which fields to show in table/csv/md mode. If None, auto-detect from data.
        title: Optional title for table mode.
    """
    results = data.get("results", [])
    total = data.get("total")
    usage = data.get("usage")
    breadcrumbs = data.get("_breadcrumbs", [])

    if fmt == "quiet":
        # Raw JSON data only — no envelope
        print(json.dumps(results, indent=2, default=str))
        return

    if fmt == "json":
        print(json.dumps(data, indent=2, default=str))
        return

    if not results:
        err_console.print("[dim]No results found.[/dim]")
        _print_usage(usage)
        return

    if columns is None:
        columns = _auto_columns(results)

    if fmt == "csv":
        _output_csv(results, columns)
    elif fmt == "md":
        _output_markdown(results, columns)
    else:
        _output_table(results, columns, title, total, column_config)

    _print_usage(usage)
    _print_breadcrumbs(breadcrumbs)


def output_single(data: dict, fmt: str) -> None:
    """Format and print a single record (detail view)."""
    results = data.get("results", data)
    usage = data.get("usage")
    breadcrumbs = data.get("_breadcrumbs", [])

    if fmt in ("quiet", "json"):
        target = results if fmt == "quiet" else data
        print(json.dumps(target, indent=2, default=str))
        return

    # For table/md mode, show as key-value pairs
    if isinstance(results, dict):
        _output_detail(results)
    elif isinstance(results, list) and len(results) == 1:
        _output_detail(results[0])
    else:
        print(json.dumps(results, indent=2, default=str))

    _print_usage(usage)
    _print_breadcrumbs(breadcrumbs)


def _auto_columns(results: list[dict]) -> list[str]:
    """Pick columns from the first result, limiting to a reasonable set."""
    if not results:
        return []
    keys = list(results[0].keys())
    # Show at most 8 columns in table mode to keep it readable
    return keys[:8]


def _output_table(
    results: list[dict],
    columns: list[str],
    title: str | None,
    total: int | None,
    column_config: dict[str, dict] | None = None,
) -> None:
    """Rich table output for TTY.

    column_config maps column names to kwargs passed to table.add_column(),
    e.g. {"price": {"justify": "right"}}.
    """
    console = Console()
    column_config = column_config or {}
    header = title or "Results"
    if total is not None:
        header += f" ({len(results)} of {total})"

    table = Table(title=header, show_lines=False)
    for col in columns:
        extra = column_config.get(col, {})
        table.add_column(col, overflow="ellipsis", max_width=40, **extra)

    for row in results:
        table.add_row(*[_truncate(str(row.get(col, "")), 40) for col in columns])

    console.print(table)


def _output_csv(results: list[dict], columns: list[str]) -> None:
    """CSV output to stdout."""
    writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in results:
        writer.writerow({k: row.get(k, "") for k in columns})


def _output_markdown(results: list[dict], columns: list[str]) -> None:
    """Markdown table output."""
    # Header
    print("| " + " | ".join(columns) + " |")
    print("| " + " | ".join(["---"] * len(columns)) + " |")
    # Rows
    for row in results:
        values = [str(row.get(col, "")) for col in columns]
        print("| " + " | ".join(values) + " |")


def _output_detail(record: dict) -> None:
    """Pretty-print a single record as key-value pairs."""
    console = Console()
    max_key_len = max(len(k) for k in record) if record else 0
    for key, value in record.items():
        label = f"[bold]{key:<{max_key_len}}[/bold]"
        console.print(f"  {label}  {value}")


def _print_usage(usage: dict | None) -> None:
    """Print credit usage to stderr."""
    if not usage:
        return
    charged = usage.get("credits_charged", 0)
    remaining = usage.get("balance_remaining")
    if remaining is not None:
        err_console.print(f"[dim]{charged} credits · {remaining} remaining[/dim]")
    elif charged:
        err_console.print(f"[dim]{charged} credits used[/dim]")


def _print_breadcrumbs(breadcrumbs: list[dict]) -> None:
    """Print next-command suggestions to stderr."""
    if not breadcrumbs:
        return
    err_console.print()
    for bc in breadcrumbs[:3]:
        hint = bc.get("hint", "")
        cmd = bc.get("command", "")
        err_console.print(f"[dim]  → {hint}:[/dim] [cyan]{cmd}[/cyan]")


def _truncate(s: str, max_len: int) -> str:
    """Truncate a string to max_len, adding ellipsis if needed."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
