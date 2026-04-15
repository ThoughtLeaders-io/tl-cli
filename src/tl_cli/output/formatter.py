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
    """Format and print a single record (detail view).

    Nested list-of-dict values (e.g. `adspots`) are rendered as indented
    sub-tables in table/md mode, and as a flattened cross-product in csv mode
    (one row per nested item with parent fields repeated).
    """
    results = data.get("results", data)
    usage = data.get("usage")
    breadcrumbs = data.get("_breadcrumbs", [])

    if fmt in ("quiet", "json"):
        target = results if fmt == "quiet" else data
        print(json.dumps(target, indent=2, default=str))
        return

    # Unwrap single-item list
    record = results[0] if isinstance(results, list) and len(results) == 1 else results
    if not isinstance(record, dict):
        print(json.dumps(results, indent=2, default=str))
        return

    if fmt == "csv":
        _output_detail_csv(record)
    else:
        _output_detail(record)

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


_RIGHT_ALIGN_COLS = {"price", "cost", "cpm"}


def _is_list_of_dicts(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _output_detail(record: dict) -> None:
    """Pretty-print a single record as key-value pairs.

    If a value is a non-empty list of dicts, it's rendered as an indented
    sub-table beneath its label instead of stringified. Empty lists show
    `(none)` to signal "no entries" explicitly rather than printing `[]`.
    """
    console = Console()
    nested_items = [(k, v) for k, v in record.items() if _is_list_of_dicts(v)]
    empty_list_items = [k for k, v in record.items() if isinstance(v, list) and not v]
    nested_or_empty_keys = {k for k, _ in nested_items} | set(empty_list_items)
    flat_items = [(k, v) for k, v in record.items() if k not in nested_or_empty_keys]

    max_key_len = max((len(k) for k, _ in flat_items), default=0)
    for key, value in flat_items:
        # List that's not list-of-dicts → stringify as JSON for readability
        if isinstance(value, list):
            display = json.dumps(value, default=str)
        else:
            display = value
        label = f"[bold]{key:<{max_key_len}}[/bold]"
        console.print(f"  {label}  {display}")

    for key, rows in nested_items:
        console.print(f"\n  [bold]{key}[/bold] ({len(rows)}):")
        sub_cols = list(rows[0].keys())
        sub_table = Table(show_header=True, padding=(0, 1))
        for col in sub_cols:
            kwargs: dict = {"overflow": "ellipsis", "max_width": 40}
            if col in _RIGHT_ALIGN_COLS:
                kwargs["justify"] = "right"
            sub_table.add_column(col, **kwargs)
        for row in rows:
            sub_table.add_row(*[_format_cell(row.get(col)) for col in sub_cols])
        console.print(sub_table)

    for key in empty_list_items:
        console.print(f"\n  [bold]{key}[/bold]: [dim](none)[/dim]")


def _format_cell(value: object) -> str:
    if value is None:
        return ""
    return _truncate(str(value), 40)


def _output_detail_csv(record: dict) -> None:
    """Flatten a detail record to CSV.

    Flat fields become columns on every row. Nested list-of-dict fields are
    cross-joined: one output row per nested item, with parent fields repeated
    and nested fields prefixed with `<key>_` to avoid collisions (parent and
    nested items may share field names like `id` or `name`).

    Records with no nested items emit a single row of flat fields. If there
    are multiple nested list fields, the rows are cross-joined.
    """
    flat = {k: ("" if v is None else v) for k, v in record.items() if not isinstance(v, list)}
    nested = [(k, v) for k, v in record.items() if _is_list_of_dicts(v)]

    # Build header: flat columns + prefixed nested columns
    columns = list(flat.keys())
    for key, rows in nested:
        for col in rows[0].keys():
            columns.append(f"{key}_{col}")

    writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()

    # No nested items → single row
    if not nested:
        writer.writerow(flat)
        return

    # Cross-join: cartesian product over nested lists. In practice there's
    # usually one nested field (e.g. adspots), giving N rows per record.
    from itertools import product
    for combo in product(*(rows for _, rows in nested)):
        row = dict(flat)
        for (key, _), item in zip(nested, combo):
            for col, val in item.items():
                row[f"{key}_{col}"] = "" if val is None else val
        writer.writerow(row)


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
