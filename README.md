# tl cli

ThoughtLeaders CLI — query sponsorship data, channels, brands, and intelligence from the terminal.

## What you can do with it

`tl` is a thin client over the ThoughtLeaders sponsorship platform. It exposes the same data the internal web app uses — deals, brands, channels, transcripts, view-curves, recommender — to a terminal, and is designed to be driven by AI agents (Claude Code, OpenCode, Gemini, Codex) as well as humans. Typical things teams build on top of it:

### For account managers and sales

- **Pipeline reporting on the fly.** *"How many deals did we close in Q1?"*, *"What's my weighted pipeline by sales owner?"*, *"Which proposals are stuck in `pending` for more than 14 days?"* — one raw SQL or one structured command, instead of waiting on a dashboard.
- **Brand intelligence in seconds.** *"What channels does Nike sponsor?"*, *"Which brands sponsor `MrBeast`?"*, *"What's Holafly's sponsorship history through us vs. through everyone?"* — answers are one `tl db es` call away.
- **Vetting candidates before a pitch.** Look up a channel by ID, name, YouTube URL, or `@handle`; pull its adspots, audience demographics, evergreenness score, and detected sponsor history before drafting the IO.
- **Pre-flight before booking.** Confirm MSN/TPP membership, integration availability, and persona/plan eligibility for a brand profile with one SQL join.

### For media buyers and brand-side analysts

- **Find channels that look like a known winner.** `tl channels similar` runs vector similarity over a ~200-dim audience/category profile and ranks candidates by score; `tl channels look-alike` is the same command under the AM-facing name.
- **Discover topical creators without guessing category codes.** `tl recommender top-channels "<tag>"` ranks channels by how strongly they load on a topic, demographic, or format tag — `Cooking`, `Age 18-24`, `USA share`, etc. Browse valid tag names with `tl recommender tags`.
- **Surface the right channels for a brand.** `tl recommender channels-for-brand Nike` runs the brand's ideal-audience vector against the channel index and returns the closest unproposed channels.
- **Surface the right brands for a channel.** `tl recommender brands-for-channel <ref>` runs the inverse search — brands most likely to sponsor a given channel, ranked.

### For data, finance, and reporting

- **Ad-hoc SQL against the production schema.** `tl db pg` accepts any read-only `SELECT` (sanitised, capped at 500 rows per page) with the full set of aggregates, window functions, joins, and JSONB operators. `tl schema pg [<table>]` prints the live column catalogue. `tl db fb` and `tl db es` expose Firebolt (time-series view-curves, subscriber growth) and Elasticsearch (transcripts, brand mentions, current channel/video metrics).
- **Exports for the spreadsheet on the other side of the conversation.** `--csv` exports stream straight to a file; `--md` produces tables you can paste into Slack or a brief; `--toon` produces a token-efficient encoding for LLM round-trips.
- **Saved reports as a contract.** `tl reports run <id>` re-runs a campaign config someone set up in the web app, so a report that lives in a Slack thread can be a one-command rerun next week.

### For AI agents

- **Built-in skills install for free.** `tl setup claude` / `opencode` / `gemini` / `codex` drop ready-made skill files into the right agent directories so the agent answers natural-language questions like *"how many deals did we close last quarter?"* by composing the right `tl db pg|fb|es` calls itself.
- **Discoverable surface.** `tl describe` lists every resource and its credit cost; `tl describe show <resource>` lists fields and filters; `tl <command> --help` is detailed enough that an agent can plan without external documentation.
- **Predictable output shapes.** Every command's `--json` envelope follows the same `{results, total, limit, offset, usage, _breadcrumbs}` contract, so an agent can pipe one command's IDs into the next without bespoke parsing.

## Requirements

- Python 3.12+
- [jq](https://stedolan.github.io/jq/)
- [ripgrep](https://github.com/BurntSushi/ripgrep)
- [duckdb](https://duckdb.org/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)

For automated installs on MacOS, prefer installing Python and the requirements on Homebrew and use the pipx package manager, but ask the user if they have the admin access (sudo) password first. If not, proceed by using `uv`.

## Install

### As a developer

```bash
git clone https://github.com/ThoughtLeaders-io/thoughtleaders-cli.git
cd thoughtleaders-cli
python -m venv .venv
pip install -e .
```

### As a user

```bash
# Recommended:
pipx install thoughtleaders-cli
# or
uv tool install thoughtleaders-cli
# or (avoid this — plain `pip` will install into your current environment instead of a fresh venv)
pip install thoughtleaders-cli
```

#### Windows with Smart App Control / WDAC

The pipx/uv install above creates an **unsigned** launcher stub that Windows
Smart App Control (and enforced WDAC policies) block — `tl` fails to start with
a Code Integrity error. Fix it with:

```powershell
tl setup windows
```

That drops a small `tl.cmd` on your `PATH` (ahead of the blocked stub) which
launches the CLI through the already-signed `python.exe`, so it runs cleanly
under SAC. If `tl` is *already* blocked and won't start, bootstrap it through
the signed interpreter directly, then reopen your terminal:

```powershell
& "$env:USERPROFILE\pipx\venvs\thoughtleaders-cli\Scripts\python.exe" -m tl_cli setup windows
```

Then set up:
```bash
tl auth login          # authenticate with ThoughtLeaders (OAuth2 browser flow, device code, or API key)
tl setup claude        # install Claude Code plugin (optional)
tl setup opencode      # install OpenCode skill (optional)
tl setup gemini        # install Gemini CLI skill (optional)
tl setup codex         # install Codex CLI skill (optional)
```

`tl auth login` offers three options:

1. **OAuth2 in a local browser** (default) — opens a URL on this machine.
2. **Device code** — for headless environments; complete the flow on another device.
3. **API key** — paste a pre-issued `APIKey` from Django admin. The CLI verifies it via `/whoami` and stores it tagged so every request sends `X-TL-Auth: API-KEY`.

## Quick Start

```bash
# Login
tl auth login

# Show information about the logged-in user
tl whoami

# Sold sponsorships for Nike in Q1 — write the SQL directly.
# `publish_status = 3` is sold; brand is reached via the
# profile → profile_brands → brand chain.
tl db pg "SELECT al.id, al.weighted_price, al.purchase_date
          FROM thoughtleaders_adlink al
          JOIN thoughtleaders_profile p           ON p.id  = al.advertiser_profile_id
          JOIN thoughtleaders_profile_brands pb   ON pb.profile_id = p.id
          JOIN thoughtleaders_brand b             ON b.id  = pb.brand_id
          WHERE al.publish_status = 3
            AND b.name = 'Nike'
            AND al.purchase_date >= '2026-01-01'
            AND al.purchase_date <  '2026-04-01'
          ORDER BY al.purchase_date DESC
          LIMIT 500 OFFSET 0"

# Show a specific sponsorship by ID
tl sponsorships show 12345

# Resolve a free-form string to a single channel — accepts names,
# slugs, YouTube channel URLs, @handles, raw channel IDs, or video URLs.
# Default output is a pretty `id  name` line; --json / --csv / --md / --toon
# return machine-readable shapes. Ambiguous matches print candidates.
tl channels find "MrBeast"
tl channels find https://www.youtube.com/@MrBeast
tl channels find https://www.youtube.com/watch?v=dQw4w9WgXcQ --json

# Same for brands — matches name, slug, website domain, or any keyword.
tl brands find Nike
tl brands find nike.com

# Show channel detail — accepts numeric ID or channel name.
tl channels show 12345
tl channels show "Economics Explained"

# Find similar channels (recommender, 25 credits, Intelligence plan).
# msn: tri-state (default yes): yes = MSN only, no = non-MSN only, both = no filter.
# tpp: tri-state (default both): yes = TPP only, no = non-TPP only, both = no filter.
tl channels similar 12345 --limit 10
tl channels similar "Tremending girls" min-score:0.85 --limit 5

# Recommender — discovery by category/demographic tag (Intelligence plan).
# `tags` is free; everything else costs 25 credits flat.
tl recommender tags                              # List every tag (free)
tl recommender tags cooking                      # Search tag names by substring
tl recommender top-channels "Cooking" msn:yes --limit 50   # Top channels for a tag
tl recommender top-brands "Cooking" --limit 30             # Top brands (deduped from profiles)
tl recommender channels-with-tag "Cooking"                 # ALL channel IDs loaded on a tag (--min defaults to 0.00001; paged; 1 credit/result)
tl recommender inspect-channel 12345             # Per-tag breakdown of a channel's vector
tl recommender inspect-brand Nike                # Per-tag breakdown of a brand's ideal profile
tl recommender channels-for-profile 842          # Channels closest to a specific brand profile
tl recommender channels-for-brand Nike msn:yes   # Same, but takes a brand ref (uses its newest profile with a vector)
tl recommender brands-for-channel 12345          # Brands most likely to sponsor a channel

# Brand intelligence
tl brands show Nike
tl brands find Nike                   # Resolve a string → single brand id

# Search videos and transcripts via Elasticsearch
tl db es '{"size":20,"query":{"term":{"channel.id":12345}},"_source":["title","views"]}'
tl db es '{"size":50,"query":{"term":{"sponsored_brand_mentions":"5612"}}}'

# Historical view-curves (Firebolt — channel_id required by index)
tl db fb "SELECT id, age, view_count FROM article_metrics
          WHERE channel_id = 12345 AND id IN ('abc', 'def')
          ORDER BY id, age"

# Run a saved report
tl reports                            # list saved reports
tl reports run 42

# Comments — available on sponsorships, channels, brands, and uploads
tl sponsorships comment-list 12345
tl sponsorships comment-add 12345 "Looks good"
tl channels comment-add 7890 "Strong recent winners"

# Check credits
tl balance

# Health check — auth, connectivity, version, latency, and required external tools.
# Run this first when something feels off; it surfaces token expiry,
# missing `jq`/`rg`/`duckdb`/`yt-dlp`, and slow endpoints in one snapshot.
tl doctor
```

## Credits

Every data query costs credits based on the type and number of results. Use `tl describe` to see credit rates and `tl balance` to check your balance.

```bash
tl describe                                # All resources + credit costs
tl describe show sponsorships --filters    # Available filters for sponsorships
tl balance                                 # Your credit balance
```

`tl db pg` is priced **per-row**: the per-row rate is the **sum of the rates of the tables the query touches** (default 1.0/row; some tables are cheaper or dearer), plus a flat per-row charge for every expensive column read (demographics, channel outreach emails), all times the rows returned. Aggregate queries (`count`/`GROUP BY`) add a surcharge proportional to the rows they aggregate. Run `tl describe show db --json` to see the live `pg_pricing` map, and check `usage.credit_rate` in the response envelope after a query to see what your query was actually charged.

To preview a query's cost **before** running it, add `--pricing`: `tl db pg "SELECT … LIMIT 100" --pricing` runs only the planner's `EXPLAIN`, prints the cost breakdown and an upper-bound estimate (at the query's `LIMIT`), and costs a flat **1 credit** — the query itself never executes. Works with `--json` too. `--pricing` is also available on `tl db fb` and `tl db es`; those backends have no per-table or per-column charges, so the estimate is the flat per-row rate at the query's row ceiling (`LIMIT` for Firebolt, `size` — or the aggregation doc cap — for Elasticsearch).

# Terminology

ThoughtLeaders has its internal terminology that's exposed throughout this tool.

* **Brands** — Usually companies, sometimes individual products. Brands are the sponsors.
* **Channels** — Usually YouTube channels, sometimes podcasts. Channels are creators, they are being sponsored.
* **Sponsorships** — Either possible or realised business relationships between brands and channels, stored in `thoughtleaders_adlink`. There are several specific sub-types differentiated by the row's `publish_status`:
    * *Deals* — Contractually agreed-upon sponsorships (sold; `publish_status = 3`). They can be in a production pipeline or already published.
    * *Matches* — Possible brand-channel pairings (`publish_status = 7`); ThoughtLeaders thinks they could work.
    * *Proposals* — Open sponsorships actively in negotiation between the two sides (`publish_status = 10`).
* **Adspots** — types of ads a channel carries (e.g. mention, dedicated video, product placement). Returned by `tl channels show`; each carries price/cost and a computed CPM.
* **AdLink** — engineering / DB name for the row that backs a sponsorship. Treat as interchangeable with "sponsorship"; the table is `thoughtleaders_adlink`.
* **MSN** (Media Selling Network) — the ~12k YouTube channels that have opted in to receive sponsorship offers. A channel is in MSN if `channel.media_selling_network_join_date IS NOT NULL`.
* **TPP** (ThoughtLeaders Partner Program) — TL's closest-partner channels, a strict subset of MSN. A channel is TPP if `channel.is_tpp = TRUE`. Prefer TPP channels when booking — fastest response, easiest to close.
* **MBN** (Media Buying Network) — the brand-side counterpart to MSN: profiles that have opted in to receive proposed sponsorships (`profile.media_buying_network_join_date IS NOT NULL`).

Sponsorships are the centre of attention in ThoughtLeaders — all other analytics and operations serve to produce or optimise sponsorships. Note that the term "Sponsorship" is wide and encompasses pre-deal stages. The funnel is large at the Sponsorship end and narrowest at the Deal end.

# Integrations

The same set of natural-language skills is published for every supported agent. Running `tl update` after an upgrade re-syncs every agent whose binary is on PATH.

## Claude Code

```bash
tl setup claude
```

Registers the ThoughtLeaders marketplace, installs the plugin, and copies skills to `~/.claude/` for short `/tl` invocation. If the `claude` binary isn't on PATH, it still installs the standalone skills and prints manual instructions for the plugin.

Talk naturally in Claude Code:

```
/tl Which channels did we sponsor in Q1?
/tl sold sponsorships for Nike in Q1
/tl show me pending proposals with scheduled dates in April
/tl what channels does Nike sponsor?
/tl find me Cooking creators in the US with mobile-heavy audiences
/tl check my balance
```

Resource-specific slash commands:
```
/tl-sponsorships pending with scheduled dates in April
/tl-reports run my Q1 pipeline
/tl-balance
```

## OpenCode, Gemini, Codex

```bash
tl setup opencode      # copies skills to ~/.config/opencode/skills/
tl setup gemini        # copies skills to ~/.agents/skills/
tl setup codex         # copies skills to ~/.agents/skills/ (same target as gemini)
```

Each agent discovers the skill automatically and uses it when you ask about sponsorships, deals, channels, brands, or intelligence. Gemini and Codex share the `~/.agents/skills/` install target.

## Skills shipped with the CLI

The plugin ships several focused skills (installed by all the `tl setup *` commands):

- **`tl`** — the data-analyst skill. Defaults to raw database queries via `tl db pg|fb|es` for anything non-trivial; uses the structured `tl <resource> show` / `find` / `similar` commands for single-record lookups and similarity / ID-resolution special cases. Comes with full schema references for Postgres, Elasticsearch, and Firebolt under `references/`.
- **`tl-keyword-research`** — turns a topic into a validated keyword-group filter set + a clickable report link, plus the results it selects — context-validated channels with sponsorability flags, or the matching videos/uploads for trend reports — refining a boolean filter over the live index instead of hand-guessing terms. Keyword-distribution counts remain as an opt-in mode.
- **`tl-save-report`** — persists the result set from an in-chat exploration session as a saved TL report ("save this as a report", "turn this into a campaign").
- **`tl-channel-authenticity`** — vets a YouTube channel for non-organic views and bot/spam comments before booking (or after delivering) a sponsorship.
- **`tl-views-guarantee`** — sizes a multi-video sponsorship buy for a channel, returning the video bundle size, views guarantee, and likelihood to hit.
- **`tl-top-partnerships`** — brand-user performance report. Ranks a brand's sold sponsorships by live eCPM vs the sold-date projection, aggregates per channel, and delivers a two-tab Google Sheet ("By Deal" / "By Channel") via `gws`. Uses only public CLI commands (`tl whoami`, `tl sponsorships list`).

## Distributed skills

Beyond the bundled set above, an organization can be granted additional skills that aren't part of a CLI release — `tl skill` fetches and installs them on demand into the same directories `tl setup` uses (Claude Code's standalone skills directory, OpenCode's skills directory, and the directory shared by Gemini and Codex), so every supported agent picks them up automatically.

```bash
tl skill list                 # skills available to your organization, with installed/latest versions
tl skill list --all           # full catalog (full-access accounts only)
tl skill download my-skill    # fetch and install into every AI-agent skill directory
tl skill download my-skill --force   # overwrite a directory tl doesn't already manage
tl skill update                # refresh every downloaded skill to its latest version
tl skill remove my-skill        # uninstall a downloaded skill
```

A directory `tl skill download` doesn't already manage (no prior `tl`-managed install there) is left alone unless `--force` is passed — it never overwrites a skill you installed some other way. `tl` warns once a day, on any command, when a downloaded skill has a newer version available. `tl setup` and self-update re-syncs also respect these directories: a downloaded skill is never rmtree'd or overwritten, even if its name collides with a bundled skill.

## Output Formats

By default, output is a styled table in the terminal and JSON when piped.

```bash
tl sponsorships show 12345 --json | jq '.results'
tl db pg "SELECT id, channel_name FROM thoughtleaders_channel WHERE is_tpp = TRUE
          LIMIT 200 OFFSET 0" --csv > tpp.csv
tl channels show "MrBeast" --md      # markdown table for Slack / docs
tl channels show "MrBeast" --toon    # token-efficient encoding for LLMs
```

TOON (Token-Oriented Object Notation) is a compact text format designed to encode structured data with fewer tokens than JSON when feeding output back into an LLM. See [the TOON format repository](https://github.com/toon-format/toon) for the specification.

## Documentation

- [Calling the HTTP API directly](API.md) — `curl` and Python recipes for the `whoami`, `balance`, `db pg|fb|es`, and `schema pg|fb|es` endpoints, authenticated with an API key.
- `tl describe` — discover available resources, fields, filters, and credit costs from the CLI itself
- `tl schema pg|fb|es` — live schema for the underlying stores
- `tl <command> --help` — detailed help for any command
- `tl doctor` — diagnostic snapshot of auth, connectivity, version, latency, and required external tools

# Notes

* Tested with Claude Code, OpenCode (including the `nemotron-cascade-2-30b-a3b-i1` local model), Gemini CLI, and Codex CLI.
