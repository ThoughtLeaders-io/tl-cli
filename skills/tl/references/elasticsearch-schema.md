# ThoughtLeaders Elasticsearch Schema Reference

## How to query

All ES access goes through the `tl` CLI:

```bash
tl db es '{"size": 1, "query": {"match_all": {}}}' --json

# Read body from stdin
cat query.json | tl db es -
```

The index is **fixed server-side**. The client cannot select an index — there is no `--index` flag.

Cost grows non-linearly with result size (raw db queries use the list curve at `mult=1.4`). Aggregation queries bill on `min(hits.total, 200)` instead of `len(hits)`. See `SKILL.md` for the curve formula and the row-count → credits table.

Output flags: `--json`, `--csv`, `--md`, `--toon`. The CLI flattens hits into rows of `{_id, _score, ...source}`; aggregations come back in the response envelope and are rendered after the rows in TTY mode.

## Accepted query bodies

See the output of `tl db es`" for the object schema. Highlights:

- **Top-level keys** accepted: `query`, `aggs`/`aggregations`, `sort`, `_source`, `size`, `from`, `search_after`, `track_total_hits`, `highlight`, `fields`, `min_score`, `timeout`, `collapse`, `post_filter`. Anything else (incl. `scroll`, `pit`, `runtime_mappings`, `knn`) is not accepted.
- `size` ≤ 10,000. `from + size` ≤ 10,000 — to page past 10,000 hits use `search_after` (see *Deep pagination* below), not `from`.
- `search_after` must be a non-empty array of ≤ 10 scalar sort values, requires an explicit `sort`, and `from` must be 0 or omitted.
- **Accepted query types** include `term`/`terms`/`match`/`bool`/`nested`/`range`/`exists`/`match_phrase`/`simple_query_string` (the sanctioned rich-Boolean text surface — `+` required, `|` OR, `-` NOT, `"phrase"`, `"phrase"~N` slop, trailing-`*` prefix, `~N` fuzzy, `(...)` grouping, per-field `^boosts`; always set `default_operator: "and"` and an explicit `fields` list or counts balloon). `query_string`, `regexp`, `wildcard`, `fuzzy`, `more_like_this`, `has_child`, `has_parent`, `parent_id` are not accepted.
- **No scripts** — keys that start with `script` (e.g. `script_fields`, `script_score`, `scripted_metric`) or end with `_script` (e.g. `bucket_script`) are not accepted. A field whose name merely contains `script` as a substring (e.g. `transcript`, `description`) is fine.
- **Aggregations are bounded, not forbidden**: up to ~50 agg nodes per body, bucket nesting ≤ 3 levels deep (single-bucket framing aggs like `filter`/`nested`/`missing` count toward depth), terms-like bucket `size` ≤ 10,000, and a worst-case total bucket count cap — so a `filter` agg wrapping a `cardinality` (the common counts-plus-recency shape) is fine in one call. Heavy/experimental agg types (e.g. `significant_terms`, script-based pipeline aggs) are not accepted.
- **`highlight` is accepted in the body but highlight fragments are not returned** — result rows carry only `_id`, `_score`, and the `_source` fields. Request the `_source` fields you need for validation samples instead.

### ElasticSearch document structure ("articles")

The `doc_type` join field distinguishes video uploads ("articles") from channel data — channel docs are parents, article docs are their children. Filter with `{"term": {"doc_type": "article"}}` or `{"term": {"doc_type": "channel"}}`. ⚠️ Term-querying `doc_type.name` matches nothing — even though article docs' `_source` shows `doc_type` as an object with a `name` key, that's join-field syntax, not a queryable subfield.

#### Upload/video Fields (selected — 73 total)

Filter with `{"term": {"doc_type": "article"}}`. Coverage percentages are live `exists` counts (July 2026, ~676M video docs) — they drift slowly as the index grows.

| Field | Type | Description |
|-------|------|-------------|
| `id` | keyword | Video/article ID. Compound form `<channel_id>:<youtube_id>` (matches PG `adlink.article_id` and ES `_id`). |
| `title` | text | Video title (~100%) |
| `description` | text | ⚠️ **Does not exist on video docs** — `exists` matches 0 of ~676M (verified). The video's description text lives in `summary`; `description` is a channel-doc field (the channel's "About this channel" text). |
| `content` | text | ⚠️ **Podcast episodes only** — the episode's show-notes/body text from the podcast feed (often HTML fragments). ~7% of docs overall; effectively absent on YouTube videos (~256k legacy docs holding flat transcript prose, and 0 YouTube docs since 2025). Never search it for YouTube content — use `summary` / `transcript`. |
| `transcript` | text | Raw transcript — stored as YouTube timed-text **XML**, not plain text (see note below). ~57% of docs; present on both longform and shorts. |
| `transcript_language` | keyword | Language code of the caption track the transcript came from (present when `transcript` is) |
| `summary` | text | ⚠️ **Misleading name — this is the video's creator-written description** (the text under the video: promo links, hashtags, timestamps, subscribe blocks), NOT an AI summary. Verified by sampling old and recent docs. ~86% of docs. This is *the* field for searching video-description text. |
| `evergreenness` | float | Per-video evergreen score: `(views at age 180d − views at age 30d) / views at age 30d`. ≥ 1 = evergreen (views at day 180 are at least double the day-30 views). Only computed for videos with ≥ 5,000 views published since 2022 (~16% of docs). |
| `publication_date` | date | When the video was published (~100%) |
| `discovery_time` | date | When TL first indexed the video. Only ~39% of docs — absent on older docs. |
| `url` | keyword | Watch/episode URL. **Stored-only: retrievable in `_source` but not searchable** (`exists`/`term` match 0 docs). |
| `image_url` | keyword | Thumbnail URL on podcast docs; absent on YouTube video docs. Stored-only, not searchable. |
| `views` | long | View count at the last metrics update (~92%) |
| `projected_views` | long | **The channel's projected views for this video's format, frozen at the time TL first indexed the video** (~45%) — the web app's "video projected views". TL's prediction of views at age = 30 days, computed **only from the channel's recent uploads of the same content type** (a short's value comes from the channel's shorts, a longform video's from its longform uploads — never mixed): ≥ 4 same-format videos' day-30 views, median-anchored with outliers trimmed. **Never updated after first index**, so it's a snapshot: compare it to the channel doc's current `impression*` to see whether the channel grew or declined since the upload, and to the video's `views` to see whether the video over- or under-performed expectations. Caveats: the format bucket at stamp time is duration-based (≤ 60s → shorts projection; anything longer — including live streams — gets the longform projection), and for channels added to TL after the fact "first indexed" is discovery time, not the publish date. |
| `likes` | long | Like count (~87%) |
| `comments` | integer | Comment count (~67%) |
| `duration` | integer | Video duration in seconds (~100%) |
| `content_type` | keyword | `longform` / `short` / `live` — the complete value set. ~71% of docs; older docs have none (missing ≠ longform). Podcast/RSS docs have no `content_type`. |
| `content_aspects` | keyword | Flags: `podcast`, `paid_promotion`, `unlisted` — the complete value set. Only ~2.6% of docs carry any. |
| `hashtags` | keyword | Hashtags from the video description, stored **without the leading `#`** and lowercase (e.g. `marchmadness`); non-Latin tags appear percent-encoded (`%D0%B0…`) (~32%) |
| `channel` | object | Embedded channel subset: `channel.id`, `channel.content_category`, `channel.format`, `channel.country`, `channel.language` — no text fields, no metrics. This is where a video's language/country/format/category live (top-level `language`, `country`, `format`, `content_category` exist only on channel docs). |

⚠️ **Channel-doc fields that look like video fields but match 0 video docs:** `total_views`, `engagement`, `duration_live`/`duration_longform`/`duration_shorts`, `language`, `country`, `format`, `content_category`, `face_on_screen`. They live on channel docs (see below); on video docs use the embedded `channel.*` subset where available.

#### Brand Mention Fields

| Field | Type | Description |
|-------|------|-------------|
| `brand_mentions` | nested | Detected brand-mention objects (`id`, `type` organic/sponsored, `field`, `snippet`, `start_ts`/`end_ts`, `position`, `probability`, `detection_tool`) (~12%). Being `nested`, it must be queried with a `nested` query — a plain `{"exists": {"field": "brand_mentions"}}` matches 0 docs. Reading the results: the returned list holds ALL of the doc's mentions, not just the ones your nested query matched — re-check `id`/`type`/`field` on every element. `field` values are `summary` (the creator-written upload description), `transcript`, `title`, and rarely `content`: a `summary` hit is the affiliate link in the description, not speech; a `(0,0)` `start_ts`/`end_ts` span is a detection with no position, never evidence about where in the video the mention sits. |
| `all_brand_mentions` | keyword | Brand IDs with any mention — the union of sponsored + organic (~12%) |
| `sponsored_brand_mentions` | keyword | Brand IDs with a sponsored mention |
| `organic_brand_mentions` | keyword | Brand IDs with an organic mention |
| `not_sponsored_by` | object | Brand IDs marked as explicitly *not* sponsoring this video. **Not searchable** (`exists` matches 0 docs). |

#### Channel Fields

Filter with `{"term": {"doc_type": "channel"}}`. ~45.5M channel docs (July 2026); coverage percentages below are live `exists` counts against that total.

Contains a denormalized subset of the PostgreSQL channel data.

⚠️ **Channel docs are duplicated** — one channel id can appear as several identical docs (a well-known channel showed 8+ copies). Doc counts ≠ channel counts; dedupe with `collapse` on `id` or aggregate with `cardinality`.

⚠️ **Legacy field names** — PostgreSQL and Firebolt renamed these, Elasticsearch did not. Raw ES queries must use the old names; the new names match **0 docs** (verified live, they fail silently):

| ES (use this) | PG `thoughtleaders_channel` | Firebolt `channel_metrics` |
|---|---|---|
| `reach` | `subscribers` | `subscribers` |
| `impression` / `impression_live` / `impression_shorts` | `projected_views*` | `projected_views` |
| `is_tl_channel` | `is_tpp` | — |

| Field | Type | Description |
|-------|------|-------------|
| `name` | text | Channel display name (~100%) |
| `description` | text | The channel's creator-written YouTube "About this channel" text (~97%): usually first-person, links, promo. Especially worth investigating if it contains creator contact information. |
| `description.domains` | text | Same "About this channel" text, additionally indexed so **website domains are searchable as single terms** — `{"term": {"description.domains": "patreon.com"}}` matches channels whose About text *links to* patreon.com anywhere (including inside full URLs), while plain topic words match identically to `description` (verified: same counts). This is the field the platform's `channel_description` report filter actually searches. |
| `reach` | long | Subscriber count (~98%). ⚠️ NOT ad-industry "reach" (unique audience exposed) — this is the channel's subscriber count. Same data as PG `thoughtleaders_channel.subscribers` / Firebolt `channel_metrics.subscribers`. |
| `impression` | long | TL's projected views per longform video at age = 30 days — computed only from the channel's recent **longform** videos' day-30 views (≥ 4 required; median-anchored, outliers trimmed) (~27%). This is the channel's *current* projection; a video doc's `projected_views` is the same quantity frozen when that video was first indexed — diff them to see channel growth/decline since the upload. ⚠️ NOT actual views and NOT ad-industry "impressions"; for actual views see `total_views` / the video docs. |
| `impression_live` | long | Projected views per live stream at age = 30 days, from the channel's live streams only (~6%) |
| `impression_shorts` | long | Projected views per short at age = 30 days, from the channel's shorts only (~21%) |
| `total_views` | long | Lifetime actual views across the channel (~68%) |
| `engagement` | long | Views-per-comment ratio over the channel's last 30 days of uploads: `sum(views) / sum(comments)`, rounded (~25%). **Lower = more engaged audience** (fewer views per comment). Not an engagement count. |
| `duration_longform` | integer | Average longform video duration in seconds, over the channel's uploads from the trailing 365 days (~46%) |
| `duration_shorts` | integer | Average short duration, same window (~32%) |
| `duration_live` | integer | Average live-stream duration, same window (~11%) |
| `is_tl_channel` | boolean | TPP partner channel (100%) |
| `is_active` | boolean | Channel is active (100%) |
| `media_selling_network_join_date` | date | MSN join date; non-null = MSN member (~1%) |
| `has_outreach_email` | boolean | Has contact email (100%) |
| `outreach_email` | text | Contact email (~45%) |
| `social_links` | text | Flat array of the channel's known profile links as **canonical keys** — lowercase `host/path` with no scheme, no `www.`, no query string, and `x.com` spelled as `twitter.com`, e.g. `["twitter.com/mrbeast", "instagram.com/mrbeast", "linktr.ee/foo"]` (~47%, growing). Sourced from the `all_social_links` accumulator (About-page scrapes plus web-search discoveries); platform names are dropped, e-mails never appear. |
| `social_links.canonical` | text | Subfield of the above with a path-segment analyzer: one token per URL segment, subdomain-insensitive host. `{"match_phrase": {"social_links.canonical": "twitch.tv/ludwig"}}` is the precise way to ask "which channels carry exactly this profile link" — it matches `m.twitch.tv/ludwig` and deep paths under the profile, but never a channel that merely mentions the same words in different links. Prefer it over matching the parent field when the query is a `domain/handle` pair. |
| `male_share` | byte | Male audience % — only ~1.6% of channel docs have demographic data |
| `usa_share` | byte | US audience % — same ~1.6% coverage |
| `device` | object | Audience device demographics where known: `device.primary` (most common device) and `device.share` (per-device % map). Very sparse (~0.2%). |
| `sponsorship_price` | scaled_float | Estimated price of a sponsored video on this channel, from **TL's sponsorship calculator** (~27%). Inputs: the channel's last-30-day views and comments, fulfillment rate, renewal rate, and longform evergreenness. Recomputed on the channel's regular metrics-update cycle, so it moves as the channel's data changes. |
| `sponsorship_score` | scaled_float | TL-internal sponsorship track-record score, **range 0–10, higher = better** (~96%). Blends how many distinct brands sponsored the channel in the last 2 years (40%) with how many of them booked repeatedly (60%). The scale is deliberately skewed: raw low scores are compressed below 5, so **< 5 reads as weak/no track record and ≥ 5 as a real one**. Internal-only — don't quote the raw decimal externally (see business glossary). |
| `evergreenness` | float | ⚠️ **Dead — 0 docs.** Use the per-format fields below. |
| `evergreenness_longform` | scaled_float | Median per-video evergreenness of the channel's longform uploads from the trailing 365 days (~24%). Per-video evergreenness = `(views@180d − views@30d) / views@30d`; ≥ 1 = evergreen (day-180 views at least double day-30). Recomputed on the metrics-update cycle. |
| `evergreenness_shorts` | scaled_float | Same, for shorts (~20%) |
| `evergreenness_live` | scaled_float | Same, for live streams (~4%) |
| `trend` | float | View-trend angle for longform uploads (~13%). Positive = growing views. |
| `trend_shorts` | scaled_float | View-trend angle for shorts (~12%) |
| `trend_live` | scaled_float | View-trend angle for live streams (~3%). There is **no `trend_longform`** — the longform trend is the bare `trend`. |
| `posts_per_90_days` | integer | Longform uploads per 90 days, normalized from the trailing 365 days (~96%) |
| `posts_per_90_days_shorts` | integer | Shorts per 90 days (~67%) |
| `posts_per_90_days_live` | integer | Live streams per 90 days (~67%) |
| `fulfillment_rate` | scaled_float | Share of the channel's longform uploads (trailing 365 days) that carry a sponsored mention (~40%) |
| `renewal_rate` | scaled_float | Rate at which the channel's sponsoring brands come back (~96%) |
| `metrics_update_period` | byte | ⚠️ Vestigial — populated on only ~2,300 docs (~0.005%). |
| `offline_since` | date | ⚠️ **Dead — 0 docs.** Use `is_active`. |
| `content_category` | integer | TL's own content-category code, 1–22 (~94%). **Not YouTube's categories** — see the category map in `postgres-schema.md`. |
| `format` | integer | Platform format code (100%): 1 = Newsletter, 3 = Podcast, **4 = YouTube**, 5 = Blog, 7 = Twitch, 8 = TikTok, 9 = Instagram, 10 = LinkedIn. |
| `face_on_screen` | boolean | ThoughtLeaders-sourced flag: whether the creator shows their face on screen when doing brand sponsorships (~1% of channel docs). |

#### AI & Enrichment Fields

| Field | Type | Description |
|-------|------|-------------|
| `ai` | object | **Channel docs only** (0 video docs). Holds exactly the three AI-generated fields below (~93% of channel docs). |
| `ai.description` | text | AI-generated third-person channel profile, always in English regardless of the channel's language (~93%) |
| `ai.topic_descriptions` | text | AI-generated prose paragraph describing the channel's content topics. A **single string, not an array**. Only ~37% of channel docs — absence means "not yet generated", not "off-topic". |
| `ai.brand_safety` | keyword | Brand-safety letter grade `A`–`F` (~93%; A ≈ 91% of graded channels) |
| `applied_enrichments` | keyword | Enrichment names applied to the video (e.g. `brand_extractor`) |
| `article_category` | object | ⚠️ **Dead — 0 docs.** |

#### System Fields

| Field | Type | Description |
|-------|------|-------------|
| `@timestamp` | date | Index/update timestamp (~92% of video docs) |
| `doc_type` | join | Parent-child join (channel→video) |
| `es_index_tag` | keyword | Publication-period tag: quarterly from 2019 (`2025-q2`), yearly 2016–2018 (`2017`), `2015-and-before` for older. **Stored-only — not searchable** (`exists`/`term` match 0 docs). |

## Common Query Patterns

### Search videos by sponsored brand mention

```bash
tl db es '{
  "size": 50,
  "query": {"term": {"sponsored_brand_mentions": "5612"}},
  "_source": ["title", "channel.id", "publication_date", "views"],
  "sort": [{"publication_date": "desc"}]
}'
```

### Search videos for a single channel

```bash
tl db es '{
  "size": 100,
  "query": {"term": {"channel.id": 12345}},
  "sort": [{"publication_date": "desc"}]
}'
```

### Count sponsored mentions for a brand (size:0 + track_total_hits)

```bash
tl db es '{
  "size": 0,
  "track_total_hits": true,
  "query": {"term": {"sponsored_brand_mentions": "5612"}}
}'
```

### Full-text search on title/summary/transcript

(`summary` = the video's creator-written description. `description` field is not pupulated for articles, and `content` is podcast-only — see the field table.)

```bash
tl db es '{
  "size": 20,
  "query": {
    "multi_match": {
      "query": "ergonomic keyboard review",
      "fields": ["title^3", "summary", "transcript"]
    }
  },
  "_source": ["title", "channel.id", "publication_date"]
}'
```

### Filter by date range

```bash
tl db es '{
  "size": 100,
  "query": {
    "bool": {
      "filter": [
        {"term": {"channel.id": 12345}},
        {"range": {"publication_date": {"gte": "2026-01-01", "lte": "2026-03-31"}}}
      ]
    }
  }
}'
```

### Aggregation example (aggregations are bounded — see *Accepted query bodies* above)

```bash
tl db es '{
  "size": 0,
  "aggs": {
    "by_channel": {
      "terms": {"field": "channel.id", "size": 20}
    }
  },
  "query": {"term": {"sponsored_brand_mentions": "5612"}}
}'
```

For more dimensions, run multiple `tl db es` calls and join client-side.

### Deep pagination — `search_after`

`from + size` is capped at 10,000, and the stateful cursors (`scroll`, `pit`) are not accepted. To page past 10,000 hits, use the stateless `search_after` cursor: sort deterministically with a unique tiebreaker (the `id` field — not `_id`), then pass each response's `next_search_after` envelope value back as `search_after` in the next request, keeping the same `query` and `sort`:

```bash
# First page
tl db es '{
  "size": 10000,
  "query": {"term": {"channel.id": 12345}},
  "sort": [{"publication_date": "asc"}, {"id": "asc"}]
}'
# → envelope includes "next_search_after": ["2025-09-14", "12345:abc123"]

# Next page — identical query & sort, plus the cursor
tl db es '{
  "size": 10000,
  "query": {"term": {"channel.id": 12345}},
  "sort": [{"publication_date": "asc"}, {"id": "asc"}],
  "search_after": ["2025-09-14", "12345:abc123"]
}'
```

Repeat until a page comes back short (`next_search_after` is absent on an empty page). Pages are not a consistent snapshot — concurrent indexing can occasionally duplicate or skip a boundary row, which is fine for analytics sweeps. Date-range windowing (filtering by `publication_date` ranges) remains a good alternative when you want resumable, idempotent slices.

## Text analyzer behavior

`text` fields on article docs (`title`, `summary`, `transcript`) appear to use the `standard` analyzer (tokenize + lowercase, no stemmer, no English-possessive filter), so inflections, plurals, and possessives are each indexed as distinct terms. For example: `bitcoin` (4,466,300) vs `bitcoins` (489,262). For stemming-style recall, expand the query side with a `bool.should` over the variants.

One consequence: URLs in article fields tokenize on punctuation (`substack.com` → `substack`, `com`), so you can't term-match a domain there. The exception is the channel-doc `description.domains` subfield, where whole domains are single searchable terms — use it to find channels by a linked domain (see the channel field table).

## Transcript field format

The `transcript` field's `_source` is **YouTube timed-text caption XML**, not plain prose. Each caption cue is a `<text start="…" dur="…">` element wrapped in `<transcript>`. The inner text is HTML-entity-encoded — on older docs **double-encoded** (an apostrophe is `&amp;#39;`, i.e. an escaped `&#39;`), on recent docs single-encoded (`&apos;`):

```xml
<?xml version="1.0" encoding="utf-8" ?>
<transcript><text start="0.05" dur="4.33">I&amp;#39;m going to</text>...</transcript>
```

- **Searching is unaffected** — the field is analyzed as `text`, so `match` / `match_phrase` queries hit the words directly regardless of the markup. The XML only matters when you retrieve and read the raw `_source`.
- **For plain prose**, strip the markup yourself, e.g. `jq -r '.results[0].transcript' | sed -E 's/<[^>]+>/ /g'`, then unescape entities (twice on older docs). Don't reach for the `content` field — it's podcast-only and absent on YouTube docs.

## Notes & gotchas

- **Composite IDs:** `tl-platform.id` and `_id` are `<channel_id>:<youtube_id>`. The `youtube_id` portion alone is what Firebolt's `article_metrics.id` stores.
- **Add a `publication_date` range filter** whenever the question is time-bounded — the alias is fixed, so this is the only way to narrow the search.
- `sponsored_brand_mentions` and `organic_brand_mentions` are keyword arrays — use `term` queries.
- For brand mention details (position, snippet, detection_tool), the data is in the `brand_mentions` nested field.
- **Stored-only fields** — retrievable in `_source` but invisible to `exists`/`term`/`match` (queries on them silently match 0 docs): `url`, `image_url`, `es_index_tag`, `not_sponsored_by`.
- No write access. The CLI only exposes `_search` against `tl-platform-*`.
