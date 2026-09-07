"""tl workflow — create a workflow (a pipeline of report-stages) from a blueprint.

A workflow is an ordered funnel of report-stages channels/brands move through
(Sourced → Qualify → … → Sold). `tl workflow create` POSTs a blueprint —
`{name, report_type, steps:[{title, include_report_ids, exclude_report_ids}]}` —
to the Bearer endpoint `/api/cli/v1/workflows/build`, which builds the whole
pipeline (stages + linked reports + exclude-earlier chaining) in one atomic call
and returns the workflow. The result shows up in the web app's workflow
list/detail immediately.

One shape caveat, and it matters: every stage is created with a fresh empty
FilterSet, and steps accept only report *links*. A link contributes the entities
explicitly listed on the linked report — it does not run that report's query. So
linking a LIST report into stage 1 works (its channels land there), but linking a
QUERY report contributes nothing and stage 1 renders empty. The in-app "Convert to
workflow" flow (superuser-only) is the only path that makes a saved query report
itself stage 1. See the `tl-create-workflow` skill: design + source the entry
report there, then feed the blueprint here.
"""

import json

import typer
from tl_cli._typer_utils import AlphaSortedTyperGroup
from pytoon import encode as toon_encode
from rich.console import Console

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format

app = typer.Typer(cls=AlphaSortedTyperGroup, help="Workflows (create a pipeline of report-stages)")
err = Console(stderr=True)

REPORT_TYPE_LABELS = {1: "Content", 2: "Brands", 3: "Channels", 8: "Sponsorships"}


@app.command("create")
def create_workflow(
    file: str | None = typer.Option(
        None, "--file", "-f",
        help="Path to a blueprint JSON file: {name, report_type, steps:[{title, include_report_ids, exclude_report_ids}]}",
    ),
    config_json: str | None = typer.Option(
        None, "--config",
        help="Blueprint as inline JSON (mutually exclusive with --file; prefer --file to avoid shell quoting).",
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Workflow name (supplies/overrides the blueprint's name)."),
    report_type: int | None = typer.Option(
        None, "--report-type", help="1 content · 2 brands · 3 channels · 8 sponsorships (overrides the blueprint)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output."),
    toon_output: bool = typer.Option(False, "--toon", help="TOON output (token-efficient for LLMs)."),
) -> None:
    """Create a workflow from a blueprint.

    The blueprint is a name + report_type + an ordered list of stages, each
    optionally linking Include/Exclude reports:

        {
          "name": "Q3 Creator Outreach",
          "report_type": 3,
          "steps": [
            {"title": "Sourced",   "include_report_ids": [4021], "exclude_report_ids": []},
            {"title": "Qualify",   "include_report_ids": [], "exclude_report_ids": []},
            {"title": "Reach out", "include_report_ids": [], "exclude_report_ids": []}
          ]
        }

    Only reports you may edit are linked (others are dropped); the workflow is
    owned by you. Later stages start empty and fill by moving.

    Note the entry stage: a linked report contributes only the entities listed on
    it, never its query results. Linking a LIST report (as "Sourced" does above)
    puts those channels on stage 1. Linking a QUERY report contributes nothing and
    stage 1 comes out EMPTY — for a live-query entry use "Convert to workflow" in
    the web app instead, which makes the query report itself stage 1. See the
    tl-create-workflow skill.

    Examples:
        tl workflow create --file blueprint.json
        tl workflow create --file blueprint.json --yes
        tl workflow create --config "$(cat blueprint.json)" -n "Q3 Outreach"
    """
    if (file is None) == (config_json is None):
        err.print("[red]Provide exactly one of --file <path> or --config '<json>'.[/red]")
        raise typer.Exit(2)

    try:
        if file is not None:
            with open(file, encoding="utf-8") as fh:
                raw = fh.read()
        else:
            raw = config_json or ""
        blueprint = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        err.print(f"[red]Could not read blueprint JSON: {e}[/red]")
        raise typer.Exit(1)

    if not isinstance(blueprint, dict):
        err.print("[red]Blueprint must be a JSON object.[/red]")
        raise typer.Exit(1)
    if name is not None:
        blueprint["name"] = name
    if report_type is not None:
        blueprint["report_type"] = report_type

    wf_name = str(blueprint.get("name") or "").strip()
    rt = blueprint.get("report_type")
    steps = blueprint.get("steps")
    if not wf_name:
        err.print("[red]Blueprint needs a name (pass one in the JSON or with --name).[/red]")
        raise typer.Exit(1)
    if rt not in REPORT_TYPE_LABELS:
        err.print("[red]report_type must be one of 1 (content), 2 (brands), 3 (channels), 8 (sponsorships).[/red]")
        raise typer.Exit(1)
    if not isinstance(steps, list) or not steps:
        err.print("[red]Blueprint needs a non-empty 'steps' list.[/red]")
        raise typer.Exit(1)
    if not all(isinstance(step, dict) for step in steps):
        err.print("[red]Each step in 'steps' must be a JSON object, e.g. {\"title\": \"Sourced\"}.[/red]")
        raise typer.Exit(1)

    payload = {"name": wf_name, "report_type": rt, "steps": steps}

    if not yes:
        err.print(
            f"\n[bold]About to create a workflow:[/bold]"
            f"\n  Name:   {wf_name}"
            f"\n  Type:   {REPORT_TYPE_LABELS[rt]} (report_type={rt})"
            f"\n  Stages: {len(steps)}"
        )
        for i, step in enumerate(steps):
            title = str((step or {}).get("title") or f"Step {i + 1}")
            inc = len((step or {}).get("include_report_ids") or [])
            exc = len((step or {}).get("exclude_report_ids") or [])
            err.print(f"    {i + 1}. {title}  [dim]({inc} include, {exc} exclude reports)[/dim]")
        if not typer.confirm("Create this workflow?", default=True):
            err.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)

    fmt = detect_format(json_output, False, False, toon_output)
    client = get_client()
    try:
        data = client.post("/workflows/build", json_body=payload)
    except ApiError as e:
        handle_api_error(e)
        return
    finally:
        client.close()

    if fmt == "toon":
        print(toon_encode(data))
    elif fmt == "json":
        print(json.dumps(data, indent=2, default=str))
    else:
        results = data.get("results", [{}])
        workflow = results[0] if results else {}
        breadcrumbs = data.get("_breadcrumbs") or []
        open_path = next((c.get("command") for c in breadcrumbs if c.get("hint") == "Open in app"), None)
        err.print()
        err.print("[green bold]Workflow created![/green bold]")
        err.print(f"  ID:   {workflow.get('id', '?')}")
        err.print(f"  Name: {workflow.get('name', wf_name)}")
        err.print(f"  Stages: {len(workflow.get('steps', steps))}")
        if open_path:
            err.print(f"  Open in app: https://app.thoughtleaders.io{open_path}")
        err.print(
            "\n[dim]Work the funnel in the app: open a stage, filter, select, and Move to the next stage.[/dim]"
        )
