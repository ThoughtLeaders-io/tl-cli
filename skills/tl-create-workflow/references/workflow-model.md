# The Workflow data model — read this before designing anything

A Workflow looks like a bespoke pipeline object. It isn't. It's a thin wrapper
over the same **Report** (campaign) objects the platform already has. Get this
model right and the rest of the skill is easy; get it wrong and you'll design
workflows that break the way hand-built ones do.

## A stage IS a Report

- A **Workflow** has a `name`, a `report_type` (content / brands / channels /
  sponsorships), an `owner`, and an ordered set of **steps**.
- **There is no separate "step" / "stage" object.** A step *is* a `Campaign`
  (the platform's word for a saved Report), linked to the workflow by a
  `workflow` FK + a `workflow_step_number` that gives the order.
- So "add a stage" = "create a report and hang it on the workflow at position
  N". "Rename a stage" = "rename that report". Everything you know about
  reports applies to stages.
- A workflow is **typed**: every stage is the same `report_type`. You can't mix
  channels and brands in one workflow.

## Query vs list — the distinction that matters most

Every report (stage) has a **FilterSet**. Whether a stage behaves as a **query**
or a **list** is *not a stored flag* — it's implied by what's in that FilterSet:

- **QUERY stage** — the FilterSet holds **filter criteria**: keywords, date
  ranges, view/subscriber ranges, categories, content filters. The stage
  *computes* its members by matching the index. It's a live search.
- **LIST stage** — the FilterSet holds **explicit included / excluded
  entities**: specific channels (or brands) that were put there. The stage *is*
  that set. It doesn't search; it holds.

Why it matters for building a workflow:

- **The entry stage should be a QUERY.** It's the pool — the funnel needs a way
  to *find* candidates, and a query does that (e.g. "channels matching this
  topic / these guide-brand look-alikes").
- **The entry stage IS the query — its own FilterSet holds the criteria.** Do
  not build stage 1 as an empty list that *links* a saved query report. That
  isn't a cosmetic compromise: a link resolves to the linked report's *listed*
  entities and never runs its query (see "Linked reports" below), so the
  wrapper gets nothing and **stage 1 renders empty**. The in-app **Convert to
  workflow** flow gets this right by turning the saved query report itself into
  stage 1.
- **Every stage after the entry should be a LIST.** Downstream stages are where
  you *move* entities into as they progress. Moving an entity = adding it to the
  target stage's included list. A downstream stage that's a query has nothing to
  hold what you move into it.
- A query as anything but the first stage is the classic broken-workflow bug.
  If a user asks for a mid-funnel "query" stage, what they usually want is a
  **filter applied to a list stage's view** (narrow the visible rows) — that's a
  transient view filter, not a query stage. Keep the stage a list.

## Moving entities through the funnel

- Users advance entities by **bulk-selecting** rows in a stage and clicking
  **Move** (to the next stage) — this adds them to the target stage's included
  list.
- **Exclude / Remove** puts selected entities on a stage's *exclude* list
  (removes them from that stage's results).
- Moving and excluding are **non-destructive** to the rest of the stage — they
  add to include/exclude lists, they don't rewrite the whole set. (This was a
  real data-loss bug historically; it's fixed, but design as if every move is a
  targeted add, because that's what it is.)

## Linked reports (nesting) — powerful, easy to overuse

- A stage can **include or exclude another report's entities** (not just
  individual channels). E.g. a "Reach out" stage that *excludes* a "No email /
  captcha" report so those channels never appear.
- **A link resolves to the linked report's *listed* entities, not its query
  results.** The platform reads the explicit channels / brands / articles /
  sponsorships held on that report and stops there — it never runs the linked
  report's filters. So linking a **list** report works, and linking a **query**
  report contributes nothing at all. This is the mechanic behind pitfall 1b:
  a stage whose only content is a link to a query report has no positive filter,
  and a workflow stage with no positive filter resolves to **zero rows**.
- This nests: report A includes report B, which includes report C… There is **no
  hard depth limit in the platform**, and deep chains are a documented
  performance + comprehension trap (exponential resolution on wide/deep nests).
- **Design rule: ≤1–2 layers of nesting.** Prefer a flat stage with its own
  filters over a clever multi-level include/exclude chain. If you find yourself
  drawing a nest more than two deep, flatten it.

## Columns are per-stage

- Each stage (report) has its own **columns**. A "Get face on screen" stage
  wants a *Face On Screen* column; a "Reach out" stage wants an *Outreach email*
  column. Note the acted-on column for each stage in the blueprint — the team
  works off it.

## Multi-user

- A workflow is **shared** across the team by default — several account managers
  work the same stages. (Per-user saved views — each person hiding the reports
  that aren't theirs — is a platform feature area, not something you configure
  from this skill.)

## One-line summary to tell a user

> A workflow is an ordered set of reports. The first one is a *search* (a query)
> that finds your pool; each one after it is a *list* that you move channels
> into as they move through the funnel.
