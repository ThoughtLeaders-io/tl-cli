"""tl describe — Schema and filter discovery for resources."""

import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format

app = typer.Typer(help="Discover available resources, fields, filters, and credit costs")
console = Console()


@app.callback(invoke_without_command=True)
def describe(
    ctx: typer.Context,
    resource: str | None = typer.Argument(None, help="Resource name (deals, channels, etc.)"),
    filters_only: bool = typer.Option(False, "--filters", help="Show only available filters"),
    fields_only: bool = typer.Option(False, "--fields", help="Show only available fields"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON only"),
) -> None:
    """Discover resources, fields, filters, and credit costs.

    Free, no credits charged.

    Examples:
        tl describe                       # List all resources
        tl describe deals                 # Fields + filters for deals
        tl describe deals --filters       # Just the filters
        tl describe deals --json          # Machine-readable (for agents)
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, False, False, quiet)

    client = get_client()
    try:
        if resource:
            data = client.get(f"/describe/{resource}")
        else:
            data = client.get("/describe")

        if fmt in ("json", "quiet"):
            target = data
            if filters_only and "filters" in data:
                target = data["filters"]
            elif fields_only and "fields" in data:
                target = data["fields"]
            print(json.dumps(target, indent=2, default=str))
            return

        if resource:
            _print_resource_detail(data, filters_only, fields_only)
        else:
            _print_resource_list(data)

    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


def _print_resource_list(data: dict) -> None:
    """Print all available resources."""
    resources = data.get("resources", [])
    table = Table(title="Available Resources")
    table.add_column("Resource", style="bold cyan")
    table.add_column("Description")
    table.add_column("Credits (list)", justify="right")
    table.add_column("Credits (detail)", justify="right")

    for r in resources:
        credits = r.get("credits", {})
        table.add_row(
            r["name"],
            r.get("description", ""),
            str(credits.get("list", "free")),
            str(credits.get("detail", "free")),
        )

    console.print(table)


def _print_resource_detail(data: dict, filters_only: bool, fields_only: bool) -> None:
    """Print fields and/or filters for a resource."""
    name = data.get("resource", "")
    desc = data.get("description", "")
    credits = data.get("credits", {})

    if not filters_only:
        console.print(f"\n[bold]{name}[/bold] — {desc}")
        console.print(
            f"Credits: [cyan]{credits.get('list', 'free')}[/cyan]/result, "
            f"[cyan]{credits.get('detail', 'free')}[/cyan]/detail\n"
        )

    if not filters_only:
        fields = data.get("fields", [])
        if fields:
            table = Table(title="Fields")
            table.add_column("Name", style="bold")
            table.add_column("Type")
            table.add_column("Description")
            for f in fields:
                table.add_row(f["name"], f.get("type", ""), f.get("description", ""))
            console.print(table)

    if not fields_only:
        filters = data.get("filters", [])
        if filters:
            table = Table(title="Filters")
            table.add_column("Name", style="bold cyan")
            table.add_column("Type")
            table.add_column("Description")
            table.add_column("Values")
            for f in filters:
                values = ", ".join(f.get("values", [])) if "values" in f else ""
                table.add_row(
                    f["name"],
                    f.get("type", ""),
                    f.get("description", ""),
                    values,
                )
            console.print(table)
