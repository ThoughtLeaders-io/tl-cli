# TL CLI — Implementation Plan

## Context

We're building a Python CLI (`tl`) for ThoughtLeaders that lets external customers query their sponsorship data. Inspired by Basecamp's CLI approach — agent-first, pipe-friendly, distributed as both a PyPI package and a Claude Code plugin.

The CLI talks to Django API endpoints (not directly to databases) so permissions are enforced server-side and no DB credentials leave our infrastructure.

**Design philosophy** (following Basecamp): The CLI is a dumb tool — structured commands are the primary interface. AI intelligence comes from the user's own agent (Claude Code, etc.) using the CLI as a tool. `tl ask` exists as an optional fallback for users without an AI agent. The skill file + `tl describe` teach agents how to use the CLI effectively.

**Business model**: Prepaid credit balance. Customers deposit funds, credits are deducted per result returned. Different data types cost different amounts based on value. No subscriptions — pure pay-as-you-go.

## Architecture

```
Customer's machine                    TL Infrastructure
┌──────────────┐                     ┌──────────────────────────────────┐
│  tl CLI      │──── HTTPS ────────▶│  Django API                      │
│  (Python)    │  Bearer token       │  /api/cli/v1/...                 │
│              │◀── JSON ───────────│                                   │
└──────────────┘                     │  ┌─ PostgreSQL (deals, channels) │
       │                             │  ├─ Elasticsearch (uploads)      │
       │ Auth0 PKCE                  │  ├─ Firebolt (snapshots)         │
       ▼                             │  └─ Usage metering middleware    │
  Browser login                      └──────────────────────────────────┘
```

## Command Structure

### Structured commands (primary interface)

| Command | Description |
|---------|-------------|
| `tl deals [filters...]` | List sponsorship deals (adlink + adspot + channel + brand) |
| `tl deals <id>` | Show deal detail |
| `tl deals create --channel <id> --brand <id>` | Create a proposal (free, no credits) |
| `tl uploads [filters...]` | List video uploads (ES) |
| `tl uploads <id>` | Show upload detail |
| `tl channels [filters...]` | Search channels |
| `tl channels <id>` | Show channel detail |
| `tl brands <brand>` | Brand intelligence report |
| `tl brands <brand> --channel <id>` | Brand mentions on a specific channel |
| `tl snapshots channel <id>` | Channel metrics over time (Firebolt channel_metrics) |
| `tl snapshots video <id> --channel <id>` | Video view curve (Firebolt article_metrics, --channel required) |
| `tl comments <adlink-id>` | List comments on a deal (free) |
| `tl comments add <adlink-id> "message"` | Add a comment (free) |

### Flexible filtering (all list commands)
Filters are passed as `key:value` pairs:
```bash
tl deals status:sold brand:"Nike" since:2026-01
tl deals status:pending owner:emma limit:20
tl uploads channel:12345 type:longform since:2026-03
tl channels category:cooking min-subs:100k language:en
```

### Discoverability
| Command | Description |
|---------|-------------|
| `tl describe` | List all resources with credit costs |
| `tl describe deals` | Show fields, filters, and credit rate for deals |
| `tl describe deals --filters` | Just the valid filters |
| `tl describe deals --fields` | Just the data fields |
| `tl docs` | Open full docs in browser |

Schema metadata from server (`GET /api/cli/v1/describe/<resource>`) — always in sync. Includes credit cost per result. Free, no credits charged. Agent-friendly: `tl describe deals --json`.

### Saved Reports
| Command | Description |
|---------|-------------|
| `tl reports` | List your saved reports (free) |
| `tl reports run <id>` | Run a saved report (credits based on results returned) |
| `tl reports run <id> --since 2026-01` | Run with filter overrides |

### AI fallback (for users without an agent)
| Command | Description |
|---------|-------------|
| `tl ask "<question>"` | Natural language query (optional, costs credits for results + LLM surcharge) |
| `tl ask "<question>" --llm-key KEY` | Use your own LLM key (no surcharge, only result credits) |

### System commands (all free)
| Command | Description |
|---------|-------------|
| `tl auth login` | Browser-based Auth0 login |
| `tl auth logout` | Clear stored tokens |
| `tl auth status` | Show auth state + credit balance |
| `tl setup claude` | Install Claude Code plugin |
| `tl doctor` | Health check (auth, connectivity, version, balance) |
| `tl balance` | Show credit balance and recent usage |

### Global flags (all commands)
`--json`, `--csv`, `--md`, `--quiet`, `--limit N`, `--offset N`

### Agent support flag
`--help --agent` returns structured JSON help (flags, gotchas, subcommands) for any command — optimized for AI agents to parse.

## Claude Code Plugin

`tl setup claude` installs the plugin to `~/.claude/plugins/tl-cli/`. Zero AI in the plugin — Claude brings the intelligence, the CLI is just a tool.

### Plugin components

#### Slash Commands

| Command | Description |
|---------|-------------|
| `/tl <request>` | Smart router — Claude interprets the request and runs the right `tl` command(s). E.g., `/tl sold deals for Nike in Q1` → `tl deals status:sold brand:"Nike" since:2026-01-01 until:2026-03-31` |
| `/tl-deals [query]` | Quick deal lookup. E.g., `/tl-deals pending with send dates in April` |
| `/tl-channels [query]` | Channel search. E.g., `/tl-channels cooking channels over 100k subs` |
| `/tl-brands [query]` | Brand intelligence. E.g., `/tl-brands Nike` |
| `/tl-reports` | List and run saved reports |
| `/tl-balance` | Check credit balance and recent usage |

Each slash command's markdown file instructs Claude to:
1. Run `tl describe <resource> --json` to discover available filters
2. Translate the user's natural language into the right `tl` command with filters
3. Execute the command and present results
4. Show breadcrumbs for follow-up actions

#### Skill: `tl-data-analyst`
Teaches Claude how to use the CLI effectively. Triggers on data questions about deals, channels, brands, sponsorships, uploads, metrics.

Key instructions in the skill:
- Always run `tl describe <resource> --json` first to discover fields, filters, and credit costs
- Use structured commands, not `tl ask` (the user's Claude IS the AI layer)
- Check `tl balance --json` before expensive queries and warn the user about credit cost
- Use `--json` output for parsing, `--quiet` for clean data
- Chain commands for multi-step analysis (e.g., get brand → find channels → check snapshots)
- Use `tl reports` to check for saved reports before building queries from scratch

#### Agent: `tl-analyst`
Autonomous agent for multi-step data workflows. Triggers on complex analysis, comparisons, investigations.

```markdown
---
name: tl-analyst
description: Use when the user asks to analyze, compare, investigate, or summarize ThoughtLeaders data across multiple dimensions. Chains tl CLI commands to answer complex questions that require multiple queries, cross-referencing, or aggregation.
tools: [Bash, Read]
---
```

What the agent does:
- **Multi-step research**: "Find channels similar to the ones Nike sponsors and compare their pricing" → `tl brands Nike --json` → extract channel IDs → `tl channels <id> --json` for each → compile comparison table
- **Cross-resource analysis**: "Show me deal slippage and add comments" → `tl deals status:pending send-date:2026-03 --json` → identify slipping deals → `tl comments add <id> "flagged for slippage"` for each
- **Report comparison**: "Compare my Q1 report to Q4" → `tl reports run <id> --since 2026-01 --until 2026-03 --json` → `tl reports run <id> --since 2025-10 --until 2025-12 --json` → synthesize
- **Discovery workflows**: "What's my best performing brand this quarter" → `tl deals status:sold since:2026-01 --json` → aggregate by brand → `tl brands <top_brand> --json` → full picture
- **Credit-aware**: checks balance before multi-query workflows, estimates total cost, asks user to confirm if expensive

#### Hooks

**PreToolUse hook on Bash** (`hooks/scripts/pre-check.sh`):
When Claude is about to run a `tl` command:
- **Auth check**: validates `tl auth status --quiet` succeeds before any data command — prevents confusing errors
- **Credit guard**: before commands with `limit:` > 100 or known-expensive resources (brands, channels), runs `tl balance --quiet` and warns if balance is low relative to expected cost
- **Limit safety**: if a `tl` command has no `limit:` filter on a list endpoint, suggests adding one to avoid accidentally draining credits on a large result set

**PostToolUse hook on Bash** (`hooks/scripts/post-usage.sh`):
After a `tl` command completes:
- Parses the `usage` block from JSON output
- If `balance_remaining` < 500 credits, emits a warning to stderr
- If command returned 402, provides a clear "deposit more credits" message with link

**Stop hook** (`hooks/scripts/session-summary.sh`):
When Claude finishes a session:
- Summarizes total credits consumed during the session
- Shows starting vs ending balance
- Lists the commands that were run (parsed from session)

## Usage Metering & Pricing

### Prepaid credit balance
- Customers deposit funds via the TL web dashboard (Stripe integration)
- Credits are deducted per API response based on results returned
- When balance reaches zero, API returns `402 Payment Required` with a link to deposit more
- Customers can opt-in to allow overage (keep working, settle later) — configurable in dashboard
- `tl auth status` and `tl balance` show current credit balance

### Credit rates by data value

| Resource | List (per result) | Detail (single) | Rationale |
|----------|-------------------|-----------------|-----------|
| **Brand intelligence** | 5 credits | 8 credits | Core IP — competitive intelligence on who sponsors whom |
| **Channel** | 3 credits | 5 credits | Rich profile data, demographics, scores |
| **Deal** | 2 credits | 3 credits | User's own sponsorship data |
| **Snapshot** (Firebolt) | 1 credit | 1 credit | Time-series data points, high volume |
| **Upload** (video) | 1 credit | 2 credits | Individual video data |
| **Report run** | Sum of result credits | — | Charged based on what the report returns |
| **Comment** | 0 | 0 | Operational — don't charge |
| **Deal create** | 0 | — | Free — more proposals = more future data consumption |
| **Describe / auth / doctor** | 0 | 0 | System + discoverability must be free |
| **`tl ask` surcharge** | +2 credits per result | — | LLM cost surcharge (waived if user provides own key) |

### Credit formula per request
```
credits = sum(results × rate_per_result) + surcharge_if_ask
```

### Server-side metering implementation

**New Django model**: `CliCreditAccount`
- `organization` (FK) — one account per org
- `balance` (decimal) — current credit balance
- `allow_overage` (bool) — whether to allow negative balance
- `overage_limit` (decimal) — max negative balance allowed

**New Django model**: `CliUsageLog`
- `organization` (FK)
- `user` (FK)
- `endpoint` (string) — which resource was queried
- `results_count` (int) — how many results returned
- `credits_charged` (decimal)
- `balance_after` (decimal)
- `created_at` (datetime)

**Metering decorator** on all `/api/cli/v1/` views:
1. Pre-request: check `CliCreditAccount.balance > 0` (or `allow_overage`)
2. Execute query
3. Post-request: count results, calculate credits, deduct from balance
4. Log to `CliUsageLog`
5. Include usage info in response headers + body

### Response format with usage

```json
{
  "results": [...],
  "total": 142,
  "limit": 50,
  "offset": 0,
  "usage": {
    "credits_charged": 150,
    "credit_rate": 3,
    "balance_remaining": 4850
  },
  "_breadcrumbs": [
    {"hint": "See deal details", "command": "tl deals 12345"},
    {"hint": "Next page", "command": "tl deals status:sold offset:50"}
  ]
}
```

## Files to Create

### CLI Package (new repo: `~/Projects/tl-cli/`)

```
tl-cli/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/tl_cli/
│   ├── __init__.py                   # Version string
│   ├── main.py                       # Typer app, register subcommands
│   ├── config.py                     # Base URL, env vars, ~/.config/tl/
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── pkce.py                   # PKCE code_verifier/challenge generation
│   │   ├── login.py                  # Auth0 browser flow + localhost callback
│   │   ├── token_store.py            # keyring read/write/refresh
│   │   └── commands.py               # tl auth login/logout/status
│   ├── client/
│   │   ├── __init__.py
│   │   ├── http.py                   # httpx client, auth injection, 401 refresh
│   │   └── errors.py                 # User-friendly error messages
│   ├── output/
│   │   ├── __init__.py
│   │   ├── formatter.py              # TTY-aware: Rich tables / JSON / CSV / MD
│   │   └── breadcrumbs.py            # Next-command hints + usage display
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── deals.py                  # tl deals (list/show/create)
│   │   ├── uploads.py                # tl uploads (list/show)
│   │   ├── channels.py              # tl channels (search/show)
│   │   ├── brands.py                # tl brands (brand intelligence)
│   │   ├── snapshots.py             # tl snapshots (Firebolt metrics)
│   │   ├── reports.py               # tl reports / tl reports run
│   │   ├── comments.py              # tl comments (list/add)
│   │   ├── describe.py              # tl describe (schema/filter/pricing discovery)
│   │   ├── ask.py                   # tl ask (optional AI fallback)
│   │   ├── setup.py                 # tl setup claude
│   │   ├── balance.py               # tl balance
│   │   └── doctor.py                # tl doctor
│   ├── filters.py                    # key:value filter parser (parsing only)
│   └── _completions.py              # Shell completion helpers
├── .claude-plugin/
│   └── plugin.json                   # Claude Code plugin manifest
├── commands/                          # Slash commands for Claude Code
│   ├── tl.md                         # /tl — smart router
│   ├── tl-deals.md                   # /tl-deals — quick deal lookup
│   ├── tl-channels.md                # /tl-channels — channel search
│   ├── tl-brands.md                  # /tl-brands — brand intelligence
│   ├── tl-reports.md                 # /tl-reports — saved reports
│   └── tl-balance.md                 # /tl-balance — credit balance
├── skills/
│   └── tl/
│       └── SKILL.md                  # Skill file teaching agents to use the CLI
├── agents/
│   └── tl-analyst.md                 # Multi-step data analysis agent
├── hooks/
│   ├── hooks.json                    # Hook event bindings
│   └── scripts/
│       ├── pre-check.sh             # Auth + credit guard before tl commands
│       ├── post-usage.sh            # Low balance warning after tl commands
│       └── session-summary.sh       # Credit usage summary on session end
├── tests/
│   ├── __init__.py
│   ├── test_auth.py
│   ├── test_client.py
│   ├── test_output.py
│   ├── test_filters.py
│   └── test_commands.py
└── .github/
    └── workflows/
        └── release.yml               # PyPI publish on tag
```

### Server Side (in existing thoughtleaders repo)

```
thoughtleaders/
├── views/
│   └── cli_api.py                    # CLI API endpoints (thin wrappers)
├── serializers/
│   └── cli.py                        # CLI-specific lightweight serializers
├── cli_metering.py                   # Credit check + deduction decorator
├── models.py                         # Add CliCreditAccount, CliUsageLog
└── urls.py                           # Add /api/cli/v1/ route group
```

## Server API Endpoints — Reuse Analysis

Most CLI endpoints can reuse existing views/utilities rather than being built from scratch. The new `/api/cli/v1/` views are thin wrappers that:
1. Accept Auth0 JWT (existing `Auth0JWTAuthentication`)
2. Translate CLI filter params into the format existing views expect
3. Call existing business logic
4. Wrap response in the standard CLI envelope (results + usage + breadcrumbs)

### Endpoint-by-endpoint reuse plan

| CLI Endpoint | Reuse From | What's New |
|-------------|-----------|------------|
| **`GET /api/cli/v1/deals`** | `api/v2/external-sponsorships` (`ExternalSponsorshipsView`) — already has org-scoped auth, returns adlink+channel+brand joined data, supports filters. Also reuse `SponsorshipsView` filter parsing from `api/sponsorships`. | Thin wrapper: translate `key:value` filters → existing view params, wrap in CLI envelope |
| **`GET /api/cli/v1/deals/<id>`** | `api/sponsorships/<id>` (`SponsorshipsView` GET) — returns full deal detail with audit trail | Add CLI envelope + breadcrumbs |
| **`POST /api/cli/v1/deals`** | `api/create-bulk-proposal` (`CreateBulkProposalView`) + MCP `save_proposals_for_email` in `mcp/proposals.py` | Adapt for single-proposal creation from CLI params |
| **`GET /api/cli/v1/uploads`** | `api/articles` (`ArticlesView`) — full ES article search with configurable columns, filters, aggregation. Already handles channel format, brand filters, content type. | Translate CLI filters → ArticlesView params. Restrict to VIDEO format. |
| **`GET /api/cli/v1/uploads/<id>`** | `api/articles/<id>` (`SingleArticleView`) | Add CLI envelope |
| **`GET /api/cli/v1/channels`** | `api/v2/external-youtube-thoughtleaders` (`ExternalYoutubeThoughtleadersView`) — rich ES-powered channel search with configurable columns, aggregation. Also `api/v1/channels/dropdown` for simple name search. | Translate CLI filters → existing view params |
| **`GET /api/cli/v1/channels/<id>`** | `api/v1/channels/<id>` (`ChannelAPIViewSet.retrieve`) — returns channel detail with loyalty metrics | Add CLI envelope + breadcrumbs linking to snapshots |
| **`GET /api/cli/v1/brands/<query>`** | `api/v1/brands` (`BrandsViewSet`) for brand lookup + `api/articles` with `sponsored_brand_mentions` filter for intelligence data. Also `api/brand/<id>/matcher` (`BrandChannelMatcherAPI`) for channel discovery. | Combine brand lookup + ES brand mention query |
| **`GET /api/cli/v1/snapshots/channel/<id>`** | `api/v2/channel-history` (`ChannelHistoryView`) — already queries Firebolt `channel_metrics` with pagination, uses `get_firebolt_query_results()` utility. Also `api/v2/external-channel-total-views` for time-series with granularity. | Direct reuse — just translate params + add CLI envelope |
| **`GET /api/cli/v1/snapshots/video/<id>`** | `api/v2/article-history` (`ArticleHistoryView`) — already queries Firebolt `article_metrics` with pagination, enforces `channel_id:article_id` format. | Direct reuse — already enforces channel_id requirement |
| **`GET /api/cli/v1/reports`** | `api/campaigns` (`CampaignViewSet.list`) — returns user's saved campaigns with ownership filtering | Filter to user's campaigns, add CLI envelope |
| **`GET /api/cli/v1/reports/<id>/run`** | `api/campaigns/<id>` detail + the view's existing data loading via `load_campaign_data()` in `data_api_utils.py` — campaigns store their filter config, which gets passed to the appropriate data view (SponsorshipsView, ArticlesView, ThoughtleadersView) | Load campaign config → dispatch to appropriate existing view → wrap results |
| **`GET /api/cli/v1/comments/<adlink_id>`** | `api/comments/adlink/<id>` (`CommentsView` GET) — already paginated with read status | Add CLI envelope |
| **`POST /api/cli/v1/comments/<adlink_id>`** | `api/comments/adlink/<id>` (`CommentsView` POST) — creates comment | Pass through |
| **`GET /api/cli/v1/balance`** | New — reads `CliCreditAccount` | New (simple model read) |
| **`GET /api/cli/v1/describe`** | New — static metadata | New (hardcoded resource definitions) |
| **`POST /api/cli/v1/ask`** | New — LLM integration | New (but uses existing views for data execution) |

### Key existing utilities to reuse

| Utility | Location | Used By |
|---------|----------|---------|
| `get_firebolt_query_results()` | `thoughtleaders/utils/firebolt_utils.py` | Snapshots endpoints |
| `create_connection_elastic()` | `thoughtleaders/utils/elasticsearch_utils.py` | Uploads, channels, brands |
| `load_campaign_data()` | `thoughtleaders/utils/data_api_utils.py` | Reports run |
| `Auth0JWTAuthentication` | `thoughtleaders/utils/auth0_jwt.py` | All endpoints |
| `DataApiAuthMixin` pattern | `thoughtleaders/views/data_api.py` | Org-scoped auth pattern |
| MCP serialization helpers | `thoughtleaders/mcp/sponsorships.py` | Deals (adlink serialization) |
| `BrandChannelMatcherAPI` | `thoughtleaders/views/brands_api.py` | Brands command |
| `CommentsView` | `thoughtleaders/views/` (comments) | Comments endpoints |
| `CampaignViewSet` | `dashboard/viewsets.py` | Reports endpoints |

### What's actually new server-side

Only these require writing from scratch:
1. **`cli_metering.py`** — credit check/deduction decorator
2. **`CliCreditAccount` + `CliUsageLog` models** — new migration
3. **`describe` endpoint** — static metadata definitions (fields, filters, credit rates per resource)
4. **`balance` endpoint** — simple model read
5. **`ask` endpoint** — LLM prompt + dispatch to existing views
6. **CLI envelope wrapper** — utility to wrap any view's response in `{results, total, usage, _breadcrumbs}`
7. **Filter translator** — converts CLI `key:value` params into the format each existing view expects

Everything else is wiring existing views/utilities together with the new auth + metering layer.

## Data Scoping (critical for external customers)

Auth0 token → Django User → Profile → Organization → Plan. Plan types determine access:
- **Media Buying** (MBN): brand/advertiser perspective
- **Media Selling** (MSN): publisher/channel perspective
- **Intelligence**: research/analytics access
- Plans can be combined (e.g., Media Buying + Intelligence)

### Per-resource scoping rules

- **Deals**:
  - Media Buyer (MBN plan): `adlink.creator_profile.organization == user_org` (deals where their org is the brand/buyer). **Can see `price` only, never `cost`.**
  - Media Seller (MSN plan): `adlink.adspot.publisher.profile.organization == user_org` (deals where their org is the publisher/channel side). **Can see `cost` only, never `price`.**
  - Both plans: union of both querysets. Price/cost visibility still follows the role per deal (buyer-side deals show price, seller-side deals show cost).
- **Uploads** (videos):
  - Intelligence plan: all videos (full ES search)
  - Free / non-intelligence plan: only videos linked to their adlinks via `adlink.article_id`
- **Channels**:
  - Intelligence plan: public search across all channels
  - Non-intelligence plan: only channels from their own deals
- **Brands**:
  - Intelligence plan: full access to all brand intelligence
  - Non-intelligence plan: no access (403)
- **Snapshots** (Firebolt metrics):
  - Paid plan (any): access to channel/video metrics
  - Free plan: no access (403)
- **Reports**: any report from any user in their organization (`dashboard_campaign.user.profile.organization == user_org`)
- **Comments**: any comment related to anyone in their organization

## Firebolt Query Safety

- `snapshots/video/<id>` **requires** `channel_id` — returns 400 without it
- Uses `(channel_id, id)` primary index on `article_metrics` (avoids 7.4B row full scan)
- `snapshots/channel/<id>` uses `(id)` primary index on `channel_metrics`
- Reuse existing `ChannelHistoryView` and `ArticleHistoryView` which already enforce these patterns
- Server adds LIMIT defaults to prevent runaway queries

## Dependencies

```toml
[project]
name = "tl-cli"
requires-python = ">=3.10"
dependencies = [
    "typer[all]>=0.12",
    "rich>=13.0",
    "httpx>=0.27",
    "keyring>=25.0",
    "authlib>=1.3",
]
```

Entry point: `tl = "tl_cli.main:app"`

## Auth Strategy

Reuse existing Auth0 setup:
- New Auth0 Application (type: Native) for the CLI
- Same audience as MCP: `https://app.thoughtleaders.io/mcp`
- Existing `Auth0JWTAuthentication` validates CLI tokens without changes
- PKCE flow with `http://localhost:{port}/callback` redirect
- Tokens stored in OS keychain via `keyring`
- Fallback: `TL_API_KEY` env var for CI/scripts

## Output Design (Basecamp-inspired)

1. **TTY detection**: Rich tables for terminal, JSON when piped
2. **Stdout = data, stderr = status**: Progress/errors to stderr so pipes stay clean
3. **Exit codes**: 0 success, 1 user error, 2 auth error, 3 server error, 4 insufficient credits
4. **Breadcrumbs**: JSON responses include `_breadcrumbs` with next-command suggestions
5. **Usage footer**: credits charged + balance remaining shown after each response
6. **`--quiet`**: Raw JSON data only (no envelope, no breadcrumbs, no usage)
7. **`--help --agent`**: Structured JSON help for AI agents

## Implementation Order

Build client and server together — the CLI is useless without the API.

### Step 1: Foundations (both repos)
**CLI repo:**
1. pyproject.toml + package skeleton → `pip install -e .` → `tl --help`
2. config.py — base URL, env vars, `~/.config/tl/`
3. filters.py — key:value filter parser
4. output/ — TTY detection, Rich tables, JSON/CSV/MD, breadcrumbs, usage display
5. auth/ — PKCE flow, token storage, login/logout/status
6. client/http.py — httpx client with auth injection, 401 refresh, 402 handling

**Server (thoughtleaders repo):**
1. Models — `CliCreditAccount`, `CliUsageLog` + migration
2. cli_metering.py — credit check/deduction decorator
3. CLI envelope wrapper utility
4. URL routing — `/api/cli/v1/` namespace in urls.py
5. Describe endpoints — static metadata with credit rates
6. Balance endpoint

### Step 2: Core data commands (both repos, endpoint by endpoint)
Build each command + its server endpoint together:
1. **Deals** — CLI command + server wrapper around `ExternalSponsorshipsView` / `SponsorshipsView`
2. **Channels** — CLI command + server wrapper around `ExternalYoutubeThoughtleadersView` / `ChannelAPIViewSet`
3. **Uploads** — CLI command + server wrapper around `ArticlesView`
4. **Brands** — CLI command + server wrapper around `BrandsViewSet` + ES brand mentions
5. **Snapshots** — CLI command + server wrapper around `ChannelHistoryView` / `ArticleHistoryView`
6. **Reports** — CLI command + server wrapper around `CampaignViewSet` + `load_campaign_data()`
7. **Comments** — CLI command + server wrapper around `CommentsView`

### Step 3: Plugin + commands + agent + hooks
1. `.claude-plugin/plugin.json`
2. `commands/` — slash commands (`/tl`, `/tl-deals`, `/tl-channels`, `/tl-brands`, `/tl-reports`, `/tl-balance`)
3. `skills/tl/SKILL.md` — comprehensive skill file
4. `agents/tl-analyst.md` — multi-step analysis agent
5. `hooks/` — pre-check, post-usage, session-summary
6. `tl setup claude` command

### Step 4: Polish
1. `tl ask` — optional AI fallback
2. `tl doctor` — health check
3. Tests
4. GitHub Actions — release workflow for PyPI
5. README with install + usage docs

## Verification

1. `pip install -e .` then `tl --help` shows all commands
2. `tl describe` lists all resources with credit rates
3. `tl describe deals --json` returns machine-readable schema + pricing
4. `tl auth login` completes Auth0 PKCE flow
5. `tl auth status` shows user + credit balance
6. `tl balance` shows detailed usage
7. `tl deals status:sold limit:5` — filter parsing + data returns
8. `tl deals --json | jq '.results[0]'` — piping works
9. `tl channels 12345` shows detail view (5 credits deducted)
10. `tl snapshots channel 12345` returns Firebolt time-series
11. `tl snapshots video abc --channel 12345` returns view curve
12. `tl reports` lists saved reports (free)
13. `tl reports run 789` executes saved report (credits based on results)
14. `tl comments 12345` lists comments (free)
15. `tl deals create --channel 1 --brand 2` creates proposal (free)
16. `tl setup claude` installs plugin
17. Claude Code skill triggers on data questions, uses `tl describe` for discovery
18. Claude Code agent chains multiple `tl` commands for complex analysis
19. Hook warns when credit balance is low
20. 402 response when credits exhausted, clear deposit link shown
