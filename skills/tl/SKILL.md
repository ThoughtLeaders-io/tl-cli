---
name: tl
description: Query ThoughtLeaders sponsorship data using the tl CLI. Triggers on questions about deals, sponsorships, channels, brands, uploads, videos, metrics, pipeline, revenue, or any business data questions. Use structured tl commands — you ARE the AI layer, not tl ask.
---

# ThoughtLeaders Data Analyst

You have access to the `tl` CLI which queries ThoughtLeaders' sponsorship platform data. Run it to answer questions about deals, channels, brands, uploads, metrics, and more.

## Core Principles

**You are the intelligence layer.** Use structured `tl` commands, not `tl ask`. The `tl ask` command is a server-side LLM fallback for users without Claude — but the user has you. Translate their questions into the right `tl` commands.

Always assume there will be more than 1 page of results. You MUST always use `--limit` and `--offset` options in the `tl list` commands to retrieve the entire data set (all pages, until the total records are fetched). You must also always use pagination in scripts you write to collect results. The maximum number of results per page is 500.

Retry after 5 seconds if the server returns a "connection denied" or a "server error" on any request.

Where possible reference sponsorships, brands, channel by numeric IDs.

## Data Model & Terminology

ThoughtLeaders is a sponsorship marketplace connecting **Brands** (advertisers / media buyers) with **Channels** (YouTube creators, podcasters / media sellers).

The centre of the data model is **Sponsorships** — business relationships between brands and channels. Sponsorships have a funnel of types, from broad to narrow:

- **Sponsorships** — the broadest category, encompassing all stages
  - **Matches** — possible brand-channel pairings that ThoughtLeaders thinks could work
  - **Proposals** — matches that have been proposed to both sides to consider
  - **Deals** — contractually agreed-upon sponsorships (sold), either in production or published

The CLI has shortcut commands for each type: `tl matches`, `tl proposals`, `tl deals`. These filter `tl sponsorships` by status.

Other key concepts:
- **Uploads** — YouTube videos indexed from Elasticsearch
- **Snapshots** — historical time-series metrics for channels and videos (Firebolt)
- **Reports** — saved report configurations that can be re-run
- **Comments** — notes attached to sponsorships
- **Adspots** — types of ads a channel carries (e.g. mention, dedicated video, product placement). Returned by `tl channels show`; each carries price/cost.
- **MSN** (Media Selling Network) — the ~11k YouTube channels that have opted in to receive sponsorship offers. Returned as a boolean `msn` field on every channel response (list, detail, similar). Derived server-side from whether `Channel.media_selling_network_join_date` is non-null — the timestamp itself isn't exposed over the CLI, just the boolean. Filterable via `msn:` tri-state: `msn:yes` (MSN only — the default on `similar`; on `list` the default is `both`), `msn:no` (non-MSN only), `msn:both` (no filter).
- **TPP** (ThoughtLeaders Partner Program, a.k.a. "TL channels") — the smaller, exclusive ~169 channels TL manages directly. Returned as the `tpp` boolean field on every channel response (list, detail, similar). Filterable via `tpp:` with the same tri-state vocabulary: `tpp:yes` / `tpp:no` / `tpp:both` (default `both`).
- **`impression`** (on channels) — projected views per video on that channel. Forward-looking estimate. May be null when not yet computed.
- **`views`** (on sponsorships) — actual view count of the sold and published sponsored video, accessible when `article_id` is set.
- **CPM** has two distinct meanings depending on level — pick the one the user actually wants:
  - **Channel CPM** = `(adspot.price / channel.impression) × 1000` — projected price per thousand projected views. Used for pricing decisions **before** a sponsorship is sold. Available for channels with active adspots via `tl channels show <channel_id>`.
  - **Sponsorship CPM** = calculated in either of two ways: if `views` is present, then CPM is `(sponsorship.price / sponsorship.views) × 1000`, meaning realized cost per thousand actual views, computed post-publication. If `views` is null, Compute from the sponsorship's `price` and the channel's `impression` fields.
  - **CPM does not have a range filter.** To find sponsorships in a CPM range (e.g. "around $15"), fetch the record set with other filters first, then apply the CPM range in post-processing (jq, Python, etc.) on the returned `cpm` field. Plan queries and pagination accordingly — the server cannot reduce the result count based on CPM.
- **Sponsorship dates** — each sponsorship has four distinct dates, useful for different queries:
  - **`created_at`** — when the sponsorship record was created in the system
  - **`purchase_date`** — when the sponsorship was purchased (i.e. when the deal was made); These make up bookings.
  - **`send_date`** — the date the video is/was expected to be published (scheduled)
  - **`publish_date`** — the date the video was actually published; These make up live ads.
- **Credits** — every data query costs credits; use `tl describe` to see rates

Users see data scoped by their organization and plan:
- **Media buyers** see sponsorships where their org is the brand. They see `price` but never `cost`.
- **Media sellers** see sponsorships where their org is the publisher. They see `cost` but never `price`.
- **Intelligence plan** is required for `tl brands`, full channel search, and full uploads.

When querying sponsorship bookings, query by `status:sold` and filter the the date range only by `purchase_date`. Otherwise, query for state:sold by `created_at`.

An obsolete name for "sponsorship" is an "adlink".

## Workflow

At the start of session, always run a `tl help` command to find out which commands are available, and the `tl whoami` command to find out what you have access to.

Unless the user specifically asks for running a specific report or showing the result of a specific report, find the data by using other, low-level commands.

1. **Discover first**: Run `tl describe show <resource> --json` to learn available fields, filters, and credit costs before querying
2. **Check saved reports**: Run `tl reports --json` to see if the user has a saved report that already answers their question
3. **Check credits**: Run `tl balance --quiet` before expensive queries. Warn the user if a query will cost many credits.
4. **Query with filters**: Use `key:value` filter syntax for structured queries
5. **Always use --json**: Parse JSON output for multi-step analysis. Use `--quiet` for raw data only.
6. **Chain commands**: For complex questions, chain multiple `tl` commands
7. **Format results**: When the user asks for a list or tabular data, present the results as a well-formatted markdown table. Pick the most relevant columns and use clear headers.

Prefer writing Python code, shell code, or `jq` commands that fetche or analysise large sets of data, instead of analysing it yourself. Create temporary files in `/tmp` that can be analysed later in different ways.

## Available Commands

### Data queries
```bash
tl sponsorships list [filters...]      # Sponsorships (2 credits/result, 3/detail)
tl sponsorships show <id>              # Sponsorship detail
tl sponsorships create --channel <id> --brand <id>  # Create proposal (free)
tl deals list [filters...]             # Shortcut: agreed-upon sponsorships (status:deal)
tl deals show <id>                     # Deal detail
tl matches list [filters...]           # Shortcut: possible brand-channel pairings (status:match)
tl matches show <id>                   # Match detail
tl matches create --channel <id> --brand <id>  # Create match (free)
tl proposals list [filters...]         # Shortcut: proposed matches (status:proposal)
tl proposals show <id>                 # Proposal detail
tl proposals create --channel <id> --brand <id>  # Create proposal (free)
tl uploads list [filters...]           # Video uploads from ES (1 credit/result)
tl uploads show <id>                   # Upload detail (2 credits)
tl channels list [filters...]          # Channel search (3 credits/result, 5/detail)
tl channels show <id-or-name>          # Channel detail (accepts numeric ID or channel name)
tl channels similar <id-or-name>       # Vector-similarity recommender (50 credits; Intelligence plan)
tl brands show <id-or-name>            # Brand detail (8 credits; accepts numeric ID or name)
tl brands history <id-or-name>         # Sponsorship history — videos where brand was detected (5 credits/result)
tl brands history <query> --channel <id>  # Brand mentions on specific channel
tl snapshots channel <id>              # Channel metrics over time (1 credit/point)
tl snapshots video <id> --channel <id> # Video view curve (1 credit/point, --channel required!)
tl reports                             # List saved reports (free)
tl reports run <id>                    # Run a saved report (credits vary)
tl comments list <adlink-id>           # List comments (free)
tl comments add <adlink-id> "msg"      # Add comment (free)
```

### Discovery & system
```bash
tl describe                            # List all resources with credit costs (free)
tl describe show <resource> --json     # Fields, filters, credit rates (free)
tl balance --quiet                 # Credit balance (free)
tl whoami                          # Current user, org, brands (free)
tl auth status                     # Auth check (free)
```

### Filter syntax
All list commands accept `key:value` filters:
```bash
tl sponsorships list status:sold brand:"Nike" purchase-date:2026-01
tl uploads list channel:12345 type:longform
tl channels list category:cooking min-subs:100k language:en
tl channels list tpp:yes                       # list all TPP (TL-managed) channels
tl channels list tpp:no primary-device:mobile  # mobile-first channels that aren't in TPP
tl channels list msn:yes category:tech         # Media Selling Network channels in tech
tl channels list msn:no min-subs:500k          # big non-MSN channels (not yet opted in)
```

Date filters accept keywords: `today`, `yesterday`, `tomorrow`.

#### Channel demographic filters

These filters apply to both `tl channels list` and `tl sponsorships list` (the latter filters by the associated channel's demographics):

```bash
# Primary device type
tl channels list primary-device:mobile
tl channels list primary-device:desktop
tl channels list primary-device:tablet

# Minimum device audience share (0–100)
tl channels list min-mobile-share:60
tl channels list min-desktop-share:30
tl channels list min-tablet-share:10

# Minimum geo share (0–100, ISO country codes lowercase)
tl channels list min-us-share:70
tl channels list min-gb-share:25

# Combine with other filters
tl channels list category:tech primary-device:mobile min-us-share:50 min-subs:100k
tl sponsorships list status:sold primary-device:mobile min-us-share:60
```

### Output flags
- `--json` — structured JSON (use this for parsing)
- `--csv` — CSV output
- `--md` — Markdown table
- `--quiet` — raw JSON data only (no envelope/breadcrumbs)
- `--limit N` — max results
- `--offset N` — pagination

### Response shape
Successful `--json` responses wrap data in an envelope:

```json
{
  "results": [ { "...": "..." } ],
  "total": 42,
  "usage": { "credits_charged": 2, "balance_remaining": 9998 },
  "_breadcrumbs": [ { "hint": "...", "command": "tl ..." } ]
}
```

`--quiet` strips everything but `results`. Errors return `{"detail": "..."}` with an HTTP status (400 / 401 / 403 / 404).

While analysing results, you must always examine the `results` field in the JSON.

## Credit Awareness

Every query costs credits. Before running expensive queries:
1. Check the credit rate: `tl describe show <resource> --json | jq '.credits'` and the user balance.
2. Estimate cost: results × rate
3. If estimated cost is more than 10% of the remaining balance, ask the user to confirm the operation before running.

## Data Scoping

Users only see data their plan allows:
- **Media buyers** see deals where their org is the brand. They see `price` but never `cost`.
- **Media sellers** see deals where their org is the publisher. They see `cost` but never `price`.
- **Intelligence plan** required for `tl brands`, full `tl channels list` search, and full `tl uploads list`.
- **Paid plan** required for `tl snapshots`.

## Important: Status Labels

When presenting sponsorship status data, always use human-readable labels — never raw codes. The `tl` CLI returns lowercase labels (`sold`, `pending`, `matched`, etc.) — capitalize them for display. Full mapping: proposed, unavailable, pending, sold, advertiser_reject → "Rejected by Advertiser", publisher_reject → "Rejected by Publisher", proposal_approved → "Proposal Approved", matched, outreach → "Reached Out", agency_reject → "Rejected by Agency".

## Important: Firebolt Snapshots

`tl snapshots video` **always requires** `--channel`. Without it, the query scans 7.4 billion rows and times out. Always provide the channel ID.

## Examples

"Show me my sold sponsorships this quarter":
```bash
tl deals list purchase-date-start:2026-01-01 --json
```

"What channels does Nike sponsor?":
```bash
tl brands history Nike --json
```

"Compare view curves for two videos":
```bash
tl snapshots video abc123 --channel 456 --json
tl snapshots video def789 --channel 456 --json
```

"Run my Q1 pipeline report":
```bash
tl reports --json  # Find the report ID first
tl reports run 42 --json
```

"Find mobile-first US channels in cooking":
```bash
tl channels list category:cooking primary-device:mobile min-us-share:50 --json
```

"Show sold sponsorships targeting mobile US audiences":
```bash
tl sponsorships list status:sold primary-device:mobile min-us-share:60 --json
```

"Find channels similar to one I know" (vector-similarity recommender, 50 credits per call):
```bash
tl channels similar 29834 --limit 10                         # by ID (defaults to msn:yes, tpp:both)
tl channels similar "Tremending girls" --limit 5             # by unique name
tl channels similar 29834 min-score:0.85 --limit 20          # tighter similarity threshold
tl channels similar 29834 msn:both min-score:0.4 --limit 30  # include both MSN and non-MSN channels
tl channels similar 29834 msn:no --limit 30                  # non-MSN channels only
tl channels similar 29834 tpp:yes --limit 30                 # TPP (TL-managed) channels only
tl channels similar 29834 min-subs:1000000 exclude:477487 --limit 15  # client-side filters
```
**Both `tl channels show` and `tl channels similar` accept either a numeric channel ID or a channel name.** Name arguments are case-insensitive partial matches; if more than one active channel matches, the command prints a candidates table (channel_id, subscribers, name) and exits 1 so you can retry with a specific ID. The `msn` filter on `similar` is tri-state: `yes` (only MSN channels — the default), `no` (only non-MSN channels), `both` (no MSN filter). `tl channels look-alike` is a hidden alias for `similar` that matches the internal "look-alike channels" terminology.
