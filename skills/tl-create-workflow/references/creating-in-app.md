# Creating the workflow

**The design rule that picks the path: the entry stage must BE the query** —
the stage-1 campaign's own FilterSet holds the filter criteria. Today only the
in-app **Convert to workflow** flow achieves that (the saved query report
itself becomes stage 1). The CLI's `tl workflow create` builds a whole
workflow in one call, but its steps can only *link* reports into fresh empty
stages — so its entry stage is a list-wrapper around the query report, which
violates the rule. **Prefer the in-app Convert path; offer the CLI one-shot
only if the user explicitly accepts the wrapped-entry tradeoff.**

**Why the wrapped entry isn't merely ugly — it's empty.** A linked report
contributes only the entities *explicitly listed* on it (its channels, brands,
articles, sponsorships). The linked report's **query is never executed**. So a
stage-1 wrapper linking a *query* report contributes nothing at all; the stage
is left with no positive filter; and a workflow stage with no positive filter
resolves to **zero rows** — the platform's guard against an emptied list stage
matching the entire index. The funnel exists and its entrance is blank.

Link a **list** report (one holding explicit channels) and it does work: those
channels land on stage 1. Frozen rather than live, but populated. That is the
only entry shape `tl workflow create` can actually deliver.

**Which path the user can actually run.** Convert is a **superuser-only** menu
item — TL-internal accounts have it, external ones don't, and there's no CLI
equivalent (`tl bulk-import` is superuser-only too).

| Situation | Path |
|---|---|
| Has **Convert to workflow** | Convert — stage 1 IS the query. Prescribed. |
| No Convert, entry is a **list** (a known shortlist) | `tl workflow create` linking that list report. Works; say that the entry won't refresh itself. |
| No Convert, entry must be a **live query** | **Can't be built today.** Hand over the design + the populated entry report, and name the blocker: Convert access, or backend step-adoption. Do not ship a wrapped query entry — it renders empty. |

`tl whoami` narrows it but doesn't settle it: no `full_access` means they
certainly don't have Convert; `full_access` doesn't mean they do. If the user
says the item isn't in their menu, that's the gate.

## Convert in the web app (preferred — stage 1 IS the query)

1. **Build + save the entry report first** so it exists as a saved **query**
   report (populated by `tl-keyword-research`, `tl channels`, `tl recommender`,
   or `tl reports create`). **Title it as the stage** ("Leads", "Sourced") —
   the report title becomes the stage title.
2. Open the saved entry report → **Convert to workflow** → name it. The report
   becomes **stage 1**, query filters and all — no wrapper, no nesting.

   > **Convert consumes the report, and it's one-way.** The report isn't
   > copied — it *becomes* the stage and leaves the saved-reports list. A
   > workflow's first step can't be deleted or detached, and deleting the
   > workflow deletes its stage reports, entry report included. If the user
   > also wants the query to survive as a report of its own, have them
   > **duplicate it first** and convert the duplicate. (The wrapped-entry shape
   > is the opposite trade: uglier stage, but the query report stays a separate
   > object.)
3. **Add stage** for each downstream stage, in blueprint order (each is an
   empty **list**; names persist across reloads).
4. **Link** supporting include/exclude reports where the blueprint calls for it
   (nesting ≤1–2 layers), and set per-stage **columns** the team acts on
   (Face On Screen, Outreach email).
5. **Work the funnel:** on a stage, filter → select → **Move** to the next
   stage (Move / Remove are non-destructive; moved channels leave the source
   stage).

## `tl workflow create` (one call — but the entry query gets wrapped)

POSTs `{name, report_type, steps}` to the Bearer endpoint
`/api/cli/v1/workflows/build` (`create_full_workflow`, the twin of the web
"New Workflow" builder; live in production since 2026-07). One atomic call
creates the workflow + stage campaigns + report links + the
exclude-earlier-stages chaining, and it appears in the web app immediately.

**The limitation:** every stage campaign is created with a **fresh empty
FilterSet**; steps accept only `{title, include_report_ids,
exclude_report_ids}`. The entry query can therefore only be *linked into*
stage 1 (`include_report_ids: [<entryReportId>]`) — stage 1 is a list-wrapper,
not the query itself. `tl reports update` can't fix it up afterwards either
(filterset edits are unsupported).

**So: only link a list report here.** Linking a query report produces an empty
stage 1 for the reason above. Until the backend lets a step *adopt* an existing
report as the stage, a live-query entry cannot be built through this command at
all — that's a Convert-only shape.

```bash
tl workflow create --file blueprint.json        # add --yes to skip the confirm
```

`blueprint.json` — note what the first step is: **this is the wrapped entry**,
the shape pitfall 1b names. It's what this endpoint can express, not a shape to
copy into a Convert-path design — and `<entryReportId>` must be a **list**
report, or stage 1 renders empty.

```json
{
  "name": "Q3 Creator Outreach",
  "report_type": 3,
  "steps": [
    // ⚠ wrapped entry — an empty list stage LINKING the entry report, not the query itself.
    //   Only works if <entryReportId> is a LIST report. A query report yields an empty stage.
    { "title": "Sourced",            "include_report_ids": [<entryReportId>], "exclude_report_ids": [] },
    { "title": "Qualify",            "include_report_ids": [], "exclude_report_ids": [] },
    { "title": "Get face on screen", "include_report_ids": [], "exclude_report_ids": [] },
    { "title": "Reach out",          "include_report_ids": [], "exclude_report_ids": [] }
  ]
}
```

(The `//` line and `<entryReportId>` are annotations — strip both before
sending the file; the endpoint takes strict JSON.)

- `report_type`: **1** content · **2** brands · **3** channels · **8** sponsorships.
- Stages are created **in order**; the rest are empty **lists** channels move
  into. Keep any linked-report nesting shallow (≤1–2).
- Only reports you may edit are linked (others are dropped); the workflow is
  owned by you.
- Use `--config '<json>'` for inline JSON, or `--name` / `--report-type` to
  supply/override those fields. `--json` / `--toon` for machine output.
- The command prints the new workflow **id** and an **"Open in app"** link
  (`/#/workflows/<report_type>/<id>`).

## The endpoints (reference)

| Action | Request | Auth |
|--------|---------|------|
| **Build a full workflow** (`tl workflow create`) | `POST /api/cli/v1/workflows/build` · `{ name, report_type, steps[] }` | **Bearer (CLI)** |
| Convert one report → 1-stage workflow | `POST /api/workflows` · `{ campaignId, workflowName }` | session |
| Add a stage | `POST /api/workflows/add-step` · `{ campaignTitle, workflowId }` | session |
| Delete a stage (any same-org collaborator) | `DELETE /api/workflows/delete-step?stepId=` | session |
| Rename / delete the workflow (delete is owner-only) | `PATCH` / `DELETE /api/workflows/:id` | session |
| Fetch a workflow + stages | `GET /api/workflows/:id` | session |
| Link a report / move entities on a stage | `PATCH` the stage filterset's `add_relation` action | session |

Only the **build** endpoint is on the CLI's Bearer surface; the rest are the web
app's session-authenticated management routes (used from the web UI).

## What to hand the user

- The **entry report link** (populated, openable).
- Either the **blueprint + in-app Convert steps** (preferred) or the **"Open in
  app" workflow link** (if the user chose the `tl workflow create` shortcut).
- The one-line "how to work the funnel": *filter a stage → select → Move to next.*

Never claim a workflow was created unless the user confirmed the in-app
conversion or a `tl workflow create` call actually returned one — otherwise you
prepared a blueprint.
