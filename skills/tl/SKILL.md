---
name: tl
description: Query ThoughtLeaders sponsorship data using the tl CLI. Triggers on questions about deals, sponsorships, channels, brands, uploads, videos, metrics, pipeline, revenue, or any business data questions. Use structured tl commands — you ARE the AI layer, not tl ask.
---

# ThoughtLeaders Data Analyst

You have access to the `tl` CLI which queries ThoughtLeaders' sponsorship platform data. Use it to answer questions about deals, channels, brands, uploads, metrics, and more.

## Core Principle

**You are the intelligence layer.** Use structured `tl` commands, not `tl ask`. The `tl ask` command is a server-side LLM fallback for users without Claude — but the user has you. Translate their questions into the right `tl` commands.

## Workflow

1. **Discover first**: Run `tl describe show <resource> --json` to learn available fields, filters, and credit costs before querying
2. **Check saved reports**: Run `tl reports --json` to see if the user has a saved report that already answers their question
3. **Check credits**: Run `tl balance --quiet` before expensive queries. Warn the user if a query will cost many credits.
4. **Query with filters**: Use `key:value` filter syntax for structured queries
5. **Always use --json**: Parse JSON output for multi-step analysis. Use `--quiet` for raw data only.
6. **Chain commands**: For complex questions, chain multiple `tl` commands

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
tl channels show <id>                  # Channel detail
tl brands show <query>                 # Brand intelligence (5 credits/result, 8/detail)
tl brands show <query> --channel <id>  # Brand mentions on specific channel
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
tl sponsorships list status:sold brand:"Nike" since:2026-01
tl uploads list channel:12345 type:longform
tl channels list category:cooking min-subs:100k language:en
```

Date filters accept keywords: `today`, `yesterday`, `tomorrow`.

### Output flags
- `--json` — structured JSON (use this for parsing)
- `--csv` — CSV output
- `--md` — Markdown table
- `--quiet` — raw JSON data only (no envelope/breadcrumbs)
- `--limit N` — max results
- `--offset N` — pagination

## Credit Awareness

Every query costs credits. Before running expensive queries:
1. Check the credit rate: `tl describe show <resource> --json | jq '.credits'`
2. Estimate cost: results × rate
3. If estimated cost > 100 credits, tell the user before running

## Data Scoping

Users only see data their plan allows:
- **Media buyers** see deals where their org is the brand. They see `price` but never `cost`.
- **Media sellers** see deals where their org is the publisher. They see `cost` but never `price`.
- **Intelligence plan** required for `tl brands`, full `tl channels list` search, and full `tl uploads list`.
- **Paid plan** required for `tl snapshots`.

## Important: Firebolt Snapshots

`tl snapshots video` **always requires** `--channel`. Without it, the query scans 7.4 billion rows and times out. Always provide the channel ID.

## Examples

"Show me my sold sponsorships this quarter":
```bash
tl deals list since:2026-01-01 --json
```

"What channels does Nike sponsor?":
```bash
tl brands show Nike --json
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
