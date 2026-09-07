# ThoughtLeaders Firebolt Schema Reference

## How to query

All Firebolt access goes through the `tl` CLI:

```bash
tl db fb "SELECT id, age, view_count FROM article_metrics
          WHERE channel_id = 12345 AND id = 'dQw4w9WgXcQ'
          ORDER BY age" --json

# Read SQL from stdin
cat curve.sql | tl db fb -
```

Cost grows non-linearly with result size (raw db queries use the list curve at `mult=1.4`). A tightly-scoped `WHERE channel_id = X AND id = Y` query rarely costs more than ~10 credits even with months of snapshots; a channel-wide `WHERE channel_id = X` over a busy channel can run into the hundreds. See `SKILL.md` for the curve formula and the row-count → credits table.

Output flags: `--json`, `--csv`, `--md`, `--toon`.

For **standard view-curve / channel-growth** questions, prefer the higher-level commands — they implement the project's interpolation/sparseness handling:

```bash
tl snapshots channel <channel_id> --json
tl snapshots video <video_id> --channel <channel_id> --json   # --channel is mandatory
```

Drop to `tl db fb` only when you need a shape `tl snapshots` doesn't produce (custom aggregates, milestone-age slices, multi-channel growth comparisons, etc.).

## Accepted queries

(See `SKILL.md` → "Raw query reference → `tl db fb`" for full reasoning.)

- **SELECT only.** No DDL/DML/transactions/SET/locks.
- **Single table.** No JOIN, CTE (`WITH`), subquery, set operation, or `LATERAL`.
- **Only known tables:** `article_metrics`, `channel_metrics`. Other names return `UNKNOWN_TABLE`.
- **Constant-only probes are allowed:** `SELECT 1` (no FROM, no column references) works as a connectivity check. Anything naming a column or `*` still needs a FROM.
- **WHERE/HAVING may only reference indexed columns** (`channel_id`/`id` for `article_metrics`; `id` for `channel_metrics`). Filtering by `age`, `publication_date`, `view_count`, `duration`, `scrape_date`, etc. in WHERE returns `NON_INDEXED_FILTER:<col>`. Apply those constraints client-side after fetching.
- **Leading index column must be equality-or-IN-filtered with literals** (`channel_id = 1` or `channel_id IN (1,2,3)`). Without it: `MISSING_INDEXED_FILTER`.
- **Trivial-aggregation exception:** a SELECT whose projected expressions are all aggregates and which has no GROUP BY / HAVING may omit WHERE entirely. Use only for tiny sanity checks.
- **No mandatory LIMIT/OFFSET** — but Firebolt will time out on bad plans, so keep the leading-index filter selective.
- **Errors carry the real diagnostic.** A query that Firebolt itself rejects (unknown column, syntax error) comes back as a 400 with Firebolt's own error description, plus a rename hint where one is known. Common trap: `channel_metrics` has `subscribers`, not the older `reach`.

## Tables

### `article_metrics` — Video-Level Time-Series (PRIMARY TABLE)

**7.4 billion rows** | 159 GiB compressed | Data from 2022-03-04 to present.

Tracks YouTube video metrics over time. Each row = one scrape of one video on one date. Videos are scraped repeatedly, so a single video has many rows at different ages.

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT | YouTube video ID (e.g., `'dQw4w9WgXcQ'`). **Bare YouTube ID** — NOT the compound `<channel_id>:<youtube_id>` form used in PG `adlink.article_id` and ES `_id`. |
| `channel_id` | INT | TL channel ID (matches `thoughtleaders_channel.id` in Postgres) |
| `channel_format` | INT | Platform format (4 = YouTube) |
| `publication_date` | DATE | When the video was published |
| `scrape_date` | DATE | When this data point was captured |
| `age` | INT | Days since publication (`scrape_date - publication_date`) |
| `view_count` | INT | Total view count at time of scrape |
| `like_count` | INT | Total likes at time of scrape |
| `comment_count` | INT | Total comments at time of scrape |
| `duration` | INT | Video duration in seconds |

**Primary Index: `(channel_id, id)`** — queries MUST filter by `channel_id` first.

**Shorts vs Longform:** `article_metrics` has no `content_type` column, and `duration` cannot stand in for one — YouTube counts uploads up to 180s as Shorts, so any duration cutoff mislabels them. Take the split from Elasticsearch's `content_type` (`longform` / `short` / `live`), matching on the document id `<channel_id>:<video_id>`.

### `channel_metrics` — Channel-Level Time-Series

**1.1 billion rows** | 6.9 GiB compressed.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INT | TL channel ID |
| `total_views` | INT | Channel total views at time of scrape |
| `subscribers` | INT | Subscriber count at time of scrape |
| `scrape_date` | DATE | When this data point was captured |

**Primary Index: `(id)`**

## When to use Firebolt (and when NOT to)

Firebolt's **only advantage** is historical metric snapshots. For everything else, prefer ES (current state) or the structured `tl` commands (deal/pipeline data).

| Need | Source |
|------|--------|
| Current view count on a video | **Elasticsearch** (`tl uploads show` or `tl db es`) |
| Current subscriber count | **Elasticsearch** (`tl channels show`) |
| Video metadata (title, tags, duration) | **Elasticsearch** |
| Find channels by criteria | **`tl db pg`** against `thoughtleaders_channel` |
| Deal / pipeline / sponsorship data | **`tl db pg`** against `thoughtleaders_adlink` (joins to `adspot`/`channel`/`profile`/`brand`) |
| View curve over time (age 7→30→90→180) | **Firebolt** ✅ |
| Views at age 30 vs age 180 (evergreenness) | **Firebolt** ✅ |
| Channel subscriber growth trend | **Firebolt** ✅ |
| Detect view spikes / anomalies over time | **Firebolt** ✅ |

**Rule: only query Firebolt when you need a value AT A POINT IN TIME that no longer exists in the current ES/PG snapshot.**

## Index rules — verified timings

`article_metrics` has 7.4B rows. The primary index is `(channel_id, id)`. Without `channel_id` in WHERE, the engine does a full table scan and times out — such queries are not accepted up front.

| Query Pattern | Performance | Result |
|--------------|-------------|--------|
| `WHERE channel_id = X AND id = Y` | ~12s | ✅ Full index match |
| `WHERE channel_id = X` | ~12s | ✅ Partial index, viable |
| `WHERE channel_id IN (X, Y, Z)` | ~12–30s | ✅ Multiple lookups, viable |
| `WHERE id = 'abc'` (no channel_id) | n/a | ❌ rejected (`MISSING_INDEXED_FILTER`) |
| `WHERE publication_date > X` | n/a | ❌ rejected (`NON_INDEXED_FILTER:publication_date`) |
| `WHERE age = 30 AND view_count > 50000` | n/a | ❌ rejected (multiple `NON_INDEXED_FILTER`) |

For `channel_metrics`, primary index is `(id)`. Same rule: always filter by `id`.

## Workflow: resolve IDs first, then query

Every Firebolt workflow has two steps:

**Step 1 — get `channel_id` and (optionally) video IDs from PG/ES.**

```bash
# Channels matching some category (recommender — preferred over content_category equality)
tl recommender top-channels "Tech" msn:yes --limit 50 --json \
  | jq '.results[].channel_id'

# Or videos for a specific brand's deals (Postgres side, via raw SQL)
tl db pg "SELECT al.id, al.article_id, s.channel_id
          FROM thoughtleaders_adlink al
          JOIN thoughtleaders_adspot s            ON s.id  = al.ad_spot_id
          JOIN thoughtleaders_profile p           ON p.id  = al.advertiser_profile_id
          JOIN thoughtleaders_profile_brands pb   ON pb.profile_id = p.id
          JOIN thoughtleaders_brand b             ON b.id  = pb.brand_id
          WHERE al.publish_status = 3
            AND b.name = 'Nike'
            AND al.article_id IS NOT NULL
          LIMIT 10000 OFFSET 0" --json \
  | jq -r '.results[] | "\(.channel_id):\(.article_id)"'

# Or videos via Elasticsearch content search
tl db es '{
  "size":100,
  "query":{"term":{"sponsored_brand_mentions":"5612"}},
  "_source":["channel.id","id"]
}' --json | jq '.results[] | {channel_id: .channel.id, id: (.id | split(":")[1])}'
```

**Step 2 — query Firebolt with those IDs.**

```bash
# Best: full index
tl db fb "SELECT id, age, view_count FROM article_metrics
          WHERE channel_id IN (123, 456, 789)
            AND id IN ('abc', 'def', 'ghi')
          ORDER BY id, age"

# Acceptable: channel_id only (scans all videos for that channel)
tl db fb "SELECT id, age, view_count FROM article_metrics
          WHERE channel_id = 12345
          ORDER BY id, age"
```

For non-indexed filters (`age IN (30, 180)`, `view_count > 1000`), pull a slightly wider slice and filter in `jq`/Python.

## Common Query Patterns

### Get a video's full view curve

```bash
tl db fb "SELECT id, age, view_count, like_count, comment_count, duration
          FROM article_metrics
          WHERE channel_id = 12345 AND id = 'dQw4w9WgXcQ'
          ORDER BY age"
```

### Get all videos for a channel, then filter client-side to milestone ages

```bash
tl db fb "SELECT id, age, view_count, like_count, comment_count, duration, publication_date
          FROM article_metrics
          WHERE channel_id = 12345
          ORDER BY id, age" --json \
| jq '.results[] | select(.age == 7 or .age == 30 or .age == 60 or .age == 90 or .age == 180 or .age == 365)'
```

To keep longform only, pull `content_type` from Elasticsearch and intersect on video id — Firebolt cannot tell Shorts apart.

### Channel subscriber/view growth over time

```bash
tl db fb "SELECT scrape_date, total_views, subscribers
          FROM channel_metrics
          WHERE id = 12345
          ORDER BY scrape_date"
```

### Compare multiple channels' growth

```bash
tl db fb "SELECT id, scrape_date, total_views, subscribers
          FROM channel_metrics
          WHERE id IN (123, 456, 789)
          ORDER BY id, scrape_date"
```

## Sparse data warning

Snapshots are **sparse**, especially for older videos (the project's scrape cadence backs off as videos age). Channels are snapshotted daily. Do **not** assume two arbitrary dates have data points. For approximations between gaps, prefer `tl snapshots` (which implements project-internal interpolation logic) over hand-rolled raw queries.

## How Firebolt powers other features

- **Evergreenness:** `evergreenness = (views_at_180 - views_at_30) / views_at_30`. Falls back to `(views_at_90 × 1.265 - views_at_30) / views_at_30` if 180 isn't available. Threshold `views_180 ≥ views_30 × 2`. Min views: 5,000. Stored on the ES `evergreenness` field and on the channel record returned by `tl channels show` — read those first; only recompute from Firebolt for investigations.
- **Growth/Trend stats** (initial → middle → current dynamics) are computed server-side.
- **View-curve approximation:** linear (channel metrics, gap ≤ 10 days) or logarithmic (article metrics, `views = a · log(b · age)`). Server-side; not exposed to raw `tl db fb`.

## Use cases (mostly underexplored)

- Late viral detection: dormant videos that spike — what caused it?
- Evergreen outliers: abnormally high/low evergreen scores — why?
- Engagement shifts: like/comment ratios changing dramatically over time.
- View projection: fit logarithmic curve, project future views.
- Sponsorship timing: when in a video's lifecycle does a sponsorship deliver most views?
- Anomaly alerts: detect unusual view patterns and notify.
- Performance benchmarking: a video's curve vs its niche average.
