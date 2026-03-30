"""Parse key:value filter pairs from CLI arguments.

This module only handles parsing — it does not know which filters are valid
for which resource. Each command module validates its own filters.

Examples:
    parse_filters(["status:sold", 'brand:"Nike"', "since:2026-01"])
    → {"status": "sold", "brand": "Nike", "since": "2026-01"}
"""

import re
import sys


def parse_filters(args: list[str]) -> dict[str, str]:
    """Parse a list of key:value filter strings into a dict.

    Supports:
        key:value           → {"key": "value"}
        key:"quoted value"  → {"key": "quoted value"}
        key:'quoted value'  → {"key": "quoted value"}

    Returns a dict of filter_name → filter_value. Prints an error and exits
    if a filter is malformed.
    """
    filters: dict[str, str] = {}

    for arg in args:
        match = re.match(r'^([a-zA-Z_-]+):(.+)$', arg)
        if not match:
            print(f"Error: invalid filter '{arg}'. Expected format: key:value", file=sys.stderr)
            raise SystemExit(1)

        key = match.group(1)
        value = match.group(2)

        # Strip surrounding quotes
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        filters[key] = value

    return filters


def split_id_and_filters(args: list[str]) -> tuple[str | None, dict[str, str]]:
    """Split args into an optional leading ID and remaining filters.

    If the first arg doesn't contain ':', it's treated as an ID.
    Everything else is parsed as filters.

    Returns (id_or_none, filters_dict).
    """
    if not args:
        return None, {}

    if ":" not in args[0]:
        return args[0], parse_filters(args[1:])

    return None, parse_filters(args)
