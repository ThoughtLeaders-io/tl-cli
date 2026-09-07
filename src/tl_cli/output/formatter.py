"""TTY-aware output formatting.

- Terminal (TTY): Rich tables with styled output
- Piped (non-TTY): Clean JSON
- Explicit flags: --json, --csv, --md override detection
"""

import csv
from datetime import datetime
import io
import json
import math
import sys

from pytoon import encode as toon_encode
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.table import Table

# Stderr console for status messages (never pollutes piped data)
err_console = Console(stderr=True)


def _sanitize_for_json(obj: object) -> object:
    """Recursively replace non-finite floats with their string form.

    JSON has no native representation for NaN / +-Infinity. Emitting them as
    bare ``NaN`` / ``Infinity`` tokens (Python's ``json`` default) produces
    output strict parsers reject. Stringifying as ``"nan"`` / ``"inf"`` /
    ``"-inf"`` keeps the JSON valid AND preserves which value was there —
    these strings round-trip through ``float()`` in Python, JS, etc.
    """
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def _dump_json(data: object) -> str:
    """JSON-encode `data`, ensuring strict (no NaN/Inf) output."""
    return json.dumps(_sanitize_for_json(data), indent=2, default=str, allow_nan=False)


def _fmt_credits(n: float) -> str:
    """Render a credit count compactly: no trailing zeros, comma grouping >= 1000."""
    if n is None:
        return "-"
    if isinstance(n, (int, float)) and float(n).is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def detect_format(json_flag: bool, csv_flag: bool, md_flag: bool, toon_flag: bool = False) -> str:
    """Determine output format from flags and TTY detection."""
    if json_flag:
        return "json"
    if csv_flag:
        return "csv"
    if md_flag:
        return "md"
    if toon_flag:
        return "toon"
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
        fmt: Output format ('table', 'json', 'csv', 'md')
        columns: Which fields to show in table/csv/md mode. If None, auto-detect from data.
        title: Optional title for table mode.
    """
    results = data.get("results", [])
    total = data.get("total")
    usage = data.get("usage")
    breadcrumbs = data.get("_breadcrumbs", [])

    if fmt == "json":
        print(_dump_json(data))
        # Banner still goes to stderr so it surfaces even when stdout is
        # piped through `jq` or redirected to a file.
        _print_quota_notice(data)
        _print_upgrade_notice(data)
        _print_server_warnings(data)
        return

    if not results:
        err_console.print("[dim]No results found.[/dim]")
        _print_quota_notice(data)
        _print_upgrade_notice(data)
        _print_server_warnings(data)
        _print_usage(usage)
        return

    if columns is None:
        columns = _auto_columns(results)

    column_types = data.get("column_types")

    if fmt == "csv":
        _output_csv(results, columns)
    elif fmt == "md":
        _output_markdown(results, columns, column_types)
    elif fmt == "toon":
        _output_toon(results, columns)
    else:
        _output_table(results, columns, title, total, column_config, column_types)

    _print_pagination_notice(data)
    _print_quota_notice(data)
    _print_upgrade_notice(data)
    _print_server_warnings(data)
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

    if fmt == "json":
        print(_dump_json(data))
        return

    # Unwrap single-item list
    record = results[0] if isinstance(results, list) and len(results) == 1 else results
    if not isinstance(record, dict):
        print(_dump_json(results))
        return

    if fmt == "toon":
        _output_toon_single(record)
    elif fmt == "csv":
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


_NUMERIC_DATA_TYPES = {"number", "num_days", "currency"}


def _resolve_numeric_columns(
    results: list[dict],
    columns: list[str],
    column_types: dict[str, str] | None = None,
) -> set[str]:
    """Determine which columns are numeric using server metadata first,
    then auto-detection from values as a fallback."""
    if column_types:
        known = {col for col in columns if column_types.get(col) in _NUMERIC_DATA_TYPES}
        # For columns not in column_types, fall back to auto-detection
        unknown = [col for col in columns if col not in column_types]
        if unknown:
            known |= _detect_numeric_columns(results, unknown)
        return known
    return _detect_numeric_columns(results, columns)


def _detect_numeric_columns(results: list[dict], columns: list[str]) -> set[str]:
    """Scan result rows to find columns where all non-None values are numeric.

    Handles int, float, and string representations of numbers (e.g. Django
    DecimalField values serialized as "1437.50").
    """
    numeric = set(columns)
    for row in results[:50]:  # sample first 50 rows
        for col in list(numeric):
            val = row.get(col)
            if val is None or val == "":
                continue
            if isinstance(val, bool):
                numeric.discard(col)
            elif isinstance(val, (int, float)):
                continue
            elif isinstance(val, str):
                try:
                    float(val)
                except (ValueError, OverflowError):
                    numeric.discard(col)
            else:
                numeric.discard(col)
    # Don't treat ID-like columns as numeric
    for col in list(numeric):
        if col.endswith("_id") or col == "id":
            numeric.discard(col)
    # Columns where every sampled value was None/empty aren't meaningfully numeric
    for col in list(numeric):
        if not any(row.get(col) not in (None, "") for row in results[:50]):
            numeric.discard(col)
    return numeric


def _format_numeric(val: object, decimals: bool = False, currency: bool = False) -> str:
    """Format a numeric value for table display.

    Args:
        decimals: If True, always show 2 decimal places (column has fractional values).
        currency: If True, prefix with '$ '.
    """
    if val is None or val == "":
        return ""
    if isinstance(val, bool):
        return str(val)
    # Coerce to float for uniform handling
    try:
        f = float(val)
    except (ValueError, TypeError, OverflowError):
        return str(val)
    if not math.isfinite(f):
        return str(f)
    if decimals or currency:
        text = f"{f:,.2f}"
    else:
        text = f"{int(f):,}" if f == int(f) else f"{f:,.2f}"
    if currency:
        text = f"$ {text}"
    return text


def _column_has_decimals(results: list[dict], col: str) -> bool:
    """Check if any non-None value in a column has a fractional part."""
    for row in results[:100]:
        val = row.get(col)
        if val is None or val == "":
            continue
        try:
            f = float(val)
            if f != int(f):
                return True
        except (ValueError, TypeError, OverflowError):
            pass
    return False


def _output_table(
    results: list[dict],
    columns: list[str],
    title: str | None,
    total: int | None,
    column_config: dict[str, dict] | None = None,
    column_types: dict[str, str] | None = None,
) -> None:
    """Rich table output for TTY.

    column_config maps column names to kwargs passed to table.add_column(),
    e.g. {"price": {"justify": "right"}}.
    Numeric columns are determined from server-provided column_types first,
    then auto-detected from values as a fallback.
    """
    console = Console()
    column_config = column_config or {}
    numeric_cols = _resolve_numeric_columns(results, columns, column_types)
    col_decimals = {col: _column_has_decimals(results, col) for col in numeric_cols}
    col_currency = {col for col in columns if (column_types or {}).get(col) == "currency"}
    header = title or "Results"
    if total is not None:
        header += f" ({len(results)} of {total})"

    table = Table(title=header, show_lines=False)
    for col in columns:
        extra = column_config.get(col, {})
        if col in numeric_cols and "justify" not in extra:
            extra = {**extra, "justify": "right"}
        table.add_column(col, overflow="ellipsis", max_width=40, **extra)

    for row in results:
        cells = []
        for col in columns:
            val = row.get(col, "")
            if col in numeric_cols:
                cells.append(_format_numeric(val, decimals=col_decimals.get(col, False), currency=col in col_currency))
            else:
                cells.append(_truncate(str(val), 40))
        table.add_row(*cells)

    console.print(table)


def _csv_cell(val: object) -> object:
    """Encode a cell value for CSV output.

    Lists and dicts are serialized as JSON so consumers get parseable
    structured data instead of Python's ``repr`` (e.g. ``[1, 2, 3]`` or
    ``{'k': 1}``).
    """
    if val is None:
        return ""
    if isinstance(val, (list, dict)):
        return json.dumps(_sanitize_for_json(val), default=str, allow_nan=False)
    return val


def _output_csv(results: list[dict], columns: list[str]) -> None:
    """CSV output to stdout."""
    writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in results:
        writer.writerow({k: _csv_cell(row.get(k)) for k in columns})


def _output_markdown(results: list[dict], columns: list[str], column_types: dict[str, str] | None = None) -> None:
    """Markdown table output."""
    numeric_cols = _resolve_numeric_columns(results, columns, column_types)
    col_decimals = {col: _column_has_decimals(results, col) for col in numeric_cols}
    col_currency = {col for col in columns if (column_types or {}).get(col) == "currency"}
    # Header
    print("| " + " | ".join(columns) + " |")
    alignments = ["---:" if col in numeric_cols else "---" for col in columns]
    print("| " + " | ".join(alignments) + " |")
    # Rows
    for row in results:
        values = []
        for col in columns:
            val = row.get(col, "")
            if col in numeric_cols:
                values.append(_format_numeric(val, decimals=col_decimals.get(col, False), currency=col in col_currency))
            else:
                values.append(str(val).replace("\n", " ").replace("|", "\\|"))
        print("| " + " | ".join(values) + " |")


def _output_toon(results: list[dict], columns: list[str]) -> None:
    """TOON (Token-Oriented Object Notation) output for LLM consumption."""
    # Build column-filtered rows for uniform tabular encoding
    rows = [{col: row.get(col) for col in columns} for row in results]
    print(toon_encode(rows))


def _output_toon_single(record: dict) -> None:
    """TOON output for a single detail record."""
    print(toon_encode(record))


_RIGHT_ALIGN_COLS = {"price", "cost", "cpm"}


def _is_list_of_dicts(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _output_detail(record: dict) -> None:
    """Pretty-print a single record as key-value pairs.

    If a value is a non-empty list of dicts, it's rendered as an indented
    sub-table beneath its label instead of stringified. Empty lists show
    `(none)` to signal "no entries" explicitly rather than printing `[]`.

    Field names and values are both data, never markup: a square-bracketed
    token ("[/finance]" in a free-text note, a `SELECT ... AS "[x]"` alias) is
    otherwise read as a Rich tag and either swallows the word or aborts the
    whole command with a markup error. Everything interpolated below is
    escaped; only the styling this function itself adds stays live.
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
        # Escape the padded label, not the bare key: the escape only adds
        # backslashes Rich consumes, so the rendered width still lines up.
        label = f"[bold]{rich_escape(f'{key:<{max_key_len}}')}[/bold]"
        console.print(f"  {label}  {rich_escape(str(display))}")

    for key, rows in nested_items:
        console.print(f"\n  [bold]{rich_escape(key)}[/bold] ({len(rows)}):")
        sub_cols = list(rows[0].keys())
        sub_table = Table(show_header=True, padding=(0, 1))
        for col in sub_cols:
            kwargs: dict = {"overflow": "ellipsis", "max_width": 40}
            if col in _RIGHT_ALIGN_COLS:
                kwargs["justify"] = "right"
            sub_table.add_column(rich_escape(col), **kwargs)
        for row in rows:
            sub_table.add_row(*[_format_cell(row.get(col)) for col in sub_cols])
        console.print(sub_table)

    for key in empty_list_items:
        console.print(f"\n  [bold]{rich_escape(key)}[/bold]: [dim](none)[/dim]")


def _format_cell(value: object) -> str:
    """Render one sub-table cell. Escaped for the same reason the detail rows are:
    Rich reads markup in table cells too, so a bracketed token in free text would
    vanish or abort the render."""
    if value is None:
        return ""
    return rich_escape(_truncate(str(value), 40))


def _output_detail_csv(record: dict) -> None:
    """Flatten a detail record to CSV.

    Flat fields become columns on every row. Nested list-of-dict fields are
    cross-joined: one output row per nested item, with parent fields repeated
    and nested fields prefixed with `<key>_` to avoid collisions (parent and
    nested items may share field names like `id` or `name`).

    Records with no nested items emit a single row of flat fields. If there
    are multiple nested list fields, the rows are cross-joined.
    """
    flat = {k: _csv_cell(v) for k, v in record.items() if not isinstance(v, list)}
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
                row[f"{key}_{col}"] = _csv_cell(val)
        writer.writerow(row)


def _print_pagination_notice(data: dict) -> None:
    """Print a visible notice when there are more pages of results."""
    if data.get("has_more") and data.get("next_offset") is not None:
        total = data.get("total", "?")
        next_offset = data["next_offset"]
        shown = len(data.get("results", []))
        err_console.print(
            f"[yellow]Showing {shown} of {total} results. "
            f"Use --offset {next_offset} for the next page.[/yellow]"
        )


def _format_wait_clause(data: dict) -> str:
    """Build the "try again in …" suffix for the quota banner.

    Prefers the precise ``_billing_earliest_retry_at`` ISO timestamp
    emitted by newer servers — that's the moment the oldest in-window
    expensive row rolls off and frees up a slot. Falls back to the
    flat ``_billing_retry_after_hours`` window when only the older
    envelope is available. Returns an empty string when neither field
    is set.
    """
    earliest_iso = data.get("_billing_earliest_retry_at")
    if earliest_iso:
        try:
            retry_at = datetime.fromisoformat(earliest_iso)
        except (TypeError, ValueError):
            retry_at = None
        if retry_at is not None:
            now = datetime.now(retry_at.tzinfo) if retry_at.tzinfo else datetime.now()
            wait = retry_at - now
            seconds = int(wait.total_seconds())
            if seconds <= 0:
                return " — try again now"
            if seconds < 60:
                return f" — try again in {seconds}s"
            if seconds < 3600:
                return f" — try again in {seconds // 60}m {seconds % 60}s"
            hours, remainder = divmod(seconds, 3600)
            return f" — try again in {hours}h {remainder // 60}m"
    hours = data.get("_billing_retry_after_hours")
    if hours:
        return f" — try again within {hours}h"
    return ""


def _print_server_warnings(data: dict) -> None:
    """Print advisory `_warnings` from the response envelope (stderr).

    The server attaches these to queries that ran but did something
    costly or fragile (e.g. a leading-wildcard ILIKE that scans a whole
    table). Advisory only — the results are still valid.
    """
    for warning in data.get("_warnings") or []:
        err_console.print(f"[bold yellow]Warning:[/bold yellow] [yellow]{warning}[/yellow]")


def _print_quota_notice(data: dict) -> None:
    """Print a banner when the server signals a billing-quota truncation
    on a raw-DB call.

    The query ran, but the result list was cut to the org's remaining
    premium-row allowance for the window — so ``results`` is short of what
    the query would naturally return, and the caller needs to know *why*
    and *when the rest becomes reachable*.

    An outright refusal never lands here: it is a 429 carrying the server's
    detail, rendered by `client.errors.handle_api_error`. A short result set
    and a refused query must not look alike.
    """
    if not data.get("_billing_quota_exhausted"):
        return
    quota = data.get("_billing_quota") or {}
    queries_used = quota.get("queries_used")
    queries_max = quota.get("queries_max")
    rows_used = quota.get("rows_used")
    rows_max = quota.get("rows_max")
    err_console.print(
        f"[bold yellow]⚠ Billing quota reached{_format_wait_clause(data)}.[/bold yellow]"
    )
    if queries_max is not None:
        err_console.print(
            f"[yellow]  expensive queries: {queries_used}/{queries_max} this window[/yellow]"
        )
    if rows_max is not None:
        err_console.print(
            f"[yellow]  expensive rows:    {rows_used}/{rows_max} this window[/yellow]"
        )


def _print_upgrade_notice(data: dict) -> None:
    """Print a banner when the server withheld premium fields from a
    successful response (``_upgrade_required``).

    The rows are real, but transcript / brand-mention / demographic fields
    were omitted for the caller's plan — without this line a free-tier user
    reads the gaps as "no data exists". The sentence is the server's own
    (UPGRADE_MESSAGE), the same one the web app's field locks show, so the
    wording stays composed in exactly one place.
    """
    notice = data.get("_upgrade_required")
    if not isinstance(notice, dict):
        return
    message = notice.get("message") or "Some fields are available on paid plans."
    err_console.print(f"[bold yellow]⚠ {message}[/bold yellow]")
    fields = notice.get("fields") or []
    if fields:
        err_console.print(
            f"[yellow]  withheld field(s): {', '.join(str(f) for f in fields)}[/yellow]"
        )


def output_pricing_estimate(data: dict, fmt: str) -> None:
    """Render the `--pricing` dry-run estimate for `tl db pg|fb|es`.

    The server returns a `pricing_estimate` block (no `results`) plus the
    usual `usage` footer reflecting the flat dry-run charge. JSON mode
    dumps the whole envelope; table mode prints a headline cost line, the
    multiplier / per-row split, and a breakdown of the expensive items
    the query touches.

    Firebolt and Elasticsearch have no per-column extras — their estimate
    carries `per_row_extra=0` and empty expensive-item maps, so the
    breakdown table is skipped and only the per-row cost shows.
    A `limit`/cost of `None` (e.g. a Firebolt query with no `LIMIT`) means
    the row count is unbounded and the cost can't be pinned ahead of time.
    """
    if fmt == "json":
        print(_dump_json(data))
        _print_server_warnings(data)
        _print_usage(data.get("usage"))
        return

    est = data.get("pricing_estimate") or {}
    console = Console()

    limit = est.get("limit")
    cost = est.get("estimated_cost_at_limit")
    multiplier = est.get("multiplier")
    per_row = est.get("per_row_extra")
    planner_rows = est.get("planner_estimated_rows")

    console.print("[bold]Query cost estimate[/bold] [dim](query not run)[/dim]")
    if cost is not None and limit is not None:
        console.print(
            f"  Estimated cost: [bold yellow]up to {_fmt_credits(cost)} credits[/bold yellow] "
            f"at {limit} row(s)"
        )
    else:
        console.print(
            "  Estimated cost: [yellow]depends on rows returned[/yellow] "
            "(no row limit set — cost is linear in rows returned)"
        )
    console.print(f"  Per-row rate (sum of table rates):    {multiplier}")
    console.print(f"  Per-row extra (expensive columns):    {per_row}")
    agg_surcharge = est.get("agg_surcharge")
    if agg_surcharge:
        console.print(
            f"  Aggregate surcharge (flat):           {agg_surcharge} "
            f"[dim](≈{est.get('aggregated_rows', 0):,} rows aggregated)[/dim]"
        )
    if planner_rows is not None:
        console.print(
            f"  [dim]Planner row estimate (pre-LIMIT): {planner_rows:,}[/dim]"
        )

    tables = est.get("table_rates") or {}
    columns = est.get("expensive_columns") or {}
    if tables or columns:
        sub = Table(title="Per-row rates this query touches")
        sub.add_column("Item", style="bold")
        sub.add_column("Kind")
        sub.add_column("Rate", justify="right")
        for name, val in sorted(tables.items()):
            sub.add_row(name, "table (per row)", f"{_fmt_credits(val)}/row")
        for path, val in sorted(columns.items()):
            sub.add_row(path, "column (per row)", f"{_fmt_credits(val)}/row")
        console.print(sub)

    _print_server_warnings(data)
    _print_usage(data.get("usage"))


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
