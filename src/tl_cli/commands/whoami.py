"""tl whoami — Show information about the logged-in user."""

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tl_cli.client.errors import handle_api_error, ApiError
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format

app = typer.Typer(help="Show current user, profile, org, and brands (free)")


def _render_whoami(data: dict) -> None:
    """Rich-formatted whoami output."""
    console = Console()
    user = data.get("user", {})
    profile = data.get("profile", {})
    org = data.get("organization", {})
    profiles = data.get("associated_profiles", [])
    brands = data.get("brands", [])

    # --- User ---
    name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    title = Text()
    title.append(name or user.get("email", ""), style="bold cyan")
    if name:
        title.append(f"  {user.get('email', '')}", style="dim")

    flags = profile.get("flags", [])
    persona = profile.get("persona")

    lines = Text()
    if persona:
        lines.append(f"Persona: ", style="dim")
        lines.append(persona, style="bold")
        lines.append("\n")
    if flags:
        lines.append("Flags:   ", style="dim")
        lines.append(", ".join(flags), style="green")
        lines.append("\n")
    lines.append("Paid:    ", style="dim")
    lines.append("yes" if profile.get("is_paid") else "no", style="green" if profile.get("is_paid") else "yellow")
    lines.append("\n")
    lines.append("Joined:  ", style="dim")
    lines.append(user.get("date_joined", "")[:10])

    console.print(Panel(lines, title=title, border_style="cyan"))

    # --- Organization ---
    org_lines = Text()
    org_lines.append(org.get("name", ""), style="bold")
    plan = org.get("plan")
    if plan:
        org_lines.append(f"  ({plan})", style="dim")
    org_lines.append("\n")
    if org.get("is_managed_services"):
        org_lines.append("Managed services", style="magenta")
        org_lines.append("\n")
    start = org.get("contract_start_date")
    end = org.get("contract_end_date")
    if start or end:
        org_lines.append("Contract: ", style="dim")
        org_lines.append(f"{start or '?'} → {end or '?'}")

    console.print(Panel(org_lines, title="Organization", border_style="blue"))

    # --- Associated Profiles ---
    if profiles:
        table = Table(title="Profiles in Organization", border_style="dim", show_lines=False)
        table.add_column("Name", style="bold")
        table.add_column("Email")
        table.add_column("Flags", style="green")
        for p in profiles:
            table.add_row(
                p.get("name", ""),
                p.get("email", ""),
                ", ".join(p.get("flags", [])),
            )
        console.print(table)

    # --- Brands (grouped by brand, emails comma-separated) ---
    if brands:
        grouped: dict[int, dict] = {}
        for b in brands:
            bid = b.get("id")
            if bid not in grouped:
                grouped[bid] = {"id": bid, "name": b.get("name", ""), "website": b.get("website", ""), "emails": []}
            email = b.get("profile_email", "")
            if email and email not in grouped[bid]["emails"]:
                grouped[bid]["emails"].append(email)

        table = Table(title="Brands in Organization", border_style="dim", show_lines=False)
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold yellow")
        table.add_column("Website")
        table.add_column("Profile Emails", style="dim")
        for g in grouped.values():
            table.add_row(
                str(g["id"]),
                g["name"],
                g["website"],
                ", ".join(g["emails"]),
            )
        console.print(table)


@app.callback(invoke_without_command=True)
def whoami(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="User info only"),
) -> None:
    """Show information about the logged-in user.

    Displays user details, profile flags, organization, associated
    profiles, and brands (for buyers).

    Examples:
        tl whoami                         # Pretty-printed info
        tl whoami --json                  # Full JSON response
        tl whoami --quiet                 # Just user info
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, False, False, quiet)

    client = get_client()
    try:
        data = client.get("/whoami")

        if fmt == "quiet":
            print(json.dumps(data.get("user", data), default=str))
        elif fmt == "json":
            print(json.dumps(data, indent=2, default=str))
        else:
            _render_whoami(data)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
