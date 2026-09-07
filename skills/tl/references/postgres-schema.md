# ThoughtLeaders PostgreSQL Schema Reference

**Canonical anchor (within this plugin).** This file is the single source of truth for TL Postgres schema facts for the `tl` command (tables, columns, fetch SQL, hallucinated-column markers, join paths).

This file does not describe every table and column. For the actual current schema, execute `tl schema pg --json`.

Accepted SQL:
- **SELECT only**, single statement. No DDL/DML/transactions/SET/COPY/MERGE.
- Functions accepted from an explicit list (aggregates, window, string, JSON, math, date-time, array). Catalog-resolving casts (`::regclass`, `::regprocedure`, …) are not accepted.
- `LIMIT` and `OFFSET` are optional. Omit them and the server fills in `LIMIT 50 OFFSET 0`. Explicit `LIMIT` must be an integer literal ≤ 10,000. Explicit `OFFSET` ≥ 10,000 is rejected with HTTP 403 (`OFFSET_TOO_DEEP`); paginate with the response's `next_offset`/breadcrumbs instead of jumping deep.
- **Case-insensitive equality: `UPPER(col) = UPPER('…')` / `UPPER(col) IN (UPPER('…'), …)` — never `LOWER(col)`.** The case-insensitive functional indexes on this database are built on `UPPER(…)` — on channel `channel_name`, `common_name` and `url`, among others. A `LOWER(col)` predicate can't use them: it seq-scans the 1.3M-row channel table (~20s+) and often dies on statement timeout (504). This holds for channel **names** exactly as it does for handles and URLs — see *Finding a single channel or brand ID* below for the bulk-lookup form. For substring search use `ILIKE '%…%'` on the **bare** column instead — that's served by trigram indexes where present (channel `channel_name`, `slug`; brand `name`, `website`). `LOWER(col) ILIKE '%…%'` is both redundant (`ILIKE` is already case-insensitive) and slow (the trigram index is on the bare column).

## Core Tables

### `thoughtleaders_adlink` (Deals/Sponsorships)

The main sponsorships table. Each row = one sponsorship between a brand and a YouTube channel. Also called "AdLink" in code, exposed as **sponsorship** in the CLI.

The profile table is tightly coupled with the brand table for media buyers, so many reports that operate on the brand levels must access the profile data first.

> 🚨 **Columns that DO NOT exist on `thoughtleaders_adlink` — common hallucinations:**
> - ❌ `brand_id` — there is NO direct brand FK. Brand is reached via `advertiser_profile_id → profile → profile_brands → brand`.
> - ❌ `organization_id` — there is NO direct org FK. Org is reached via `advertiser_profile_id → profile.organization_id → organization`.
> - ❌ `channel_id` — channel is reached via `ad_spot_id → adspot.channel_id → channel`. Do NOT substitute `advertiser_id` — that's the brand-side user who created the record, not the channel.
> - ❌ `youtube_id` (on channel) — use `external_channel_id`.
> - ❌ `msn_join_date` (on channel) — use `media_selling_network_join_date`.
> - ❌ `mbn_join_date` (on profile) — use `media_buying_network_join_date`.
> - ❌ `url` — renamed to `urls` (array) by migration 0010 (2026-07-16, "AdLink.url becomes urls"). A sponsorship can carry several URLs now; use `urls` — e.g. `urls[1]` for the first entry, or `array_to_string(urls, ', ')` to flatten.

#### Key Columns for the thoughtleaders_adlink table

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Primary key |
| `created_at` | timestamptz | When the deal was created |
| `updated_at` | timestamptz | Last modification |
| `publish_status` | int | Deal status (see constants below) |
| `ad_spot_id` | int FK | → `thoughtleaders_adspot.id` |
| `advertiser_profile_id` | int FK | → `thoughtleaders_profile.id` (the brand/advertiser's profile). ⚠️ The table is named `thoughtleaders_profile`, NOT `advertiser_profile`. |
| `advertiser_id` | int FK | → `auth_user.id` — the brand-side user account that created the sponsorship record (the advertiser/buyer side). Record lineage only — for the channel use `ad_spot_id → adspot.channel_id`, for the brand use `advertiser_profile_id`, for accountability use the `owner_*` fields. |
| `owner_advertiser_id` | int FK | → `auth_user.id` (brand-side owner) |
| `owner_publisher_id` | int FK | → `auth_user.id` (channel-side owner) |
| `owner_sales_id` | int FK | → `auth_user.id` (sales rep) |
| `scheduled_date` | timestamptz | Scheduled send/publish date |
| `publish_date` | timestamptz | Actual publish date |
| `outreach_date` | timestamptz | When outreach was sent |
| `purchase_date` | timestamptz | When deal was purchased/sold |
| `presented_date` | timestamptz | When presented to brand |
| `rejected_date` | timestamptz | When rejected |
| `proposal_approved_date` | timestamptz | When proposal was approved |
| `draft_expected_date` | date | Expected draft delivery |
| `actual_end_date` | timestamptz | Actual end date |
| `scheduled_end_date` | timestamptz | Scheduled end date |
| `rejection_reason` | int | Rejection reason code (1–24). See "`rejection_reason` Constants" below for the code → label mapping. Set when `publish_status IN (4, 5, 9)` (closed-lost). |
| `rejection_reason_details` | text | Free-text rejection details. Sometimes contains AM/agency notes like *"english content only"*, *"isn't talking about stocks"*, *"channel does not exist"*. Use as supplementary context, not primary classification. |
| `cost` | float | Cost of the deal to TL |
| `price` | float | The price of the deal for the brand |
| `payment_status` | int | 0=Unpaid, 1=Paid |
| `performance_grade` | int | Performance rating (see business-glossary) |
| `article_id` | varchar | Compound `<channel_id>:<youtube_id>` — links to ES `_id` and ES `id` field |
| `created_where` | varchar | What mechanism / software / agent created the record |
| `urls` | varchar(600)[] (array) | Destination/reference URLs for the sponsorship. ⚠️ Renamed from the old singular `url` column by migration 0010 (2026-07-16) — a sponsorship can carry several URLs now. Never NULL (empty array `{}` when none set). Use `urls[1]` for "the" URL when a single value is needed, or `array_to_string(urls, ', ')` to flatten for display. |

#### `publish_status` Constants

| Value | Constant | Label | Notes |
|-------|----------|-------|-------|
| 3 | SOLD | Sold | Realized revenue / concluded deal |
| 4 | DENY | Rejected by Advertiser | Closed-lost |
| 5 | REJECT | Rejected by Publisher | Closed-lost |
| 7 | MATCHED | Matched (default) | Pre-negotiation initial stage |
| 9 | REJECTED_AGENCY | Rejected by Agency | Closed-lost |
| 10 | OPEN | Open | Active/in-negotiation deal; progress tracked via per-party approval fields |

Live open deals are a single `OPEN` (10) status driven by three independent per-party approval fields: `brand_approval_status`, `channel_approval_status`, `agency_approval_status` (each `1 PENDING` / `2 APPROVED` / `3 FINISHED`, or NULL), plus `first_contacted_party` (`1 BRAND` / `2 CHANNEL`, or NULL).

A deal is **committed** when it is SOLD, or OPEN with `brand_approval_status` in (APPROVED, FINISHED). `weighted_price` is derived from the brand/channel approval combination on OPEN deals.

#### `rejection_reason` Constants

| Code | Constant | Enum Label (verbatim from Django) | AM-friendly label |
|------|----------|---------------------|-------------------|
| 1 | OTHER | Other (brand) | Brand declined — other reason |
| 2 | COMPETITOR | Channel works with competitor (brand) | Channel runs a competitor |
| 3 | NO_MATCH | Doesn't fit together (brand) | Brand says channel isn't a fit |
| 4 | DISLIKE | Doesn't like the channel | Brand doesn't want this channel |
| 5 | PRICING | Price is unreasonable | Brand says price is too high |
| 6 | WORKING_TOGETHER | Already working together with the channel | Already running with this channel |
| 7 | TIMING | Timing is off (brand) | Brand timing — not now |
| 8 | NO_RESPONSE | Channel did not respond | Channel never replied |
| 9 | DO_NOT_CONTACT | Do not contact channel | Channel is on do-not-contact list |
| 10 | PUBLISHER_OTHER | Other (publisher) | Channel declined — other reason |
| 11 | PUBLISHER_COMPETITOR | Works with competitor (publisher) | Channel already runs the competitor |
| 12 | PUBLISHER_NO_MATCH | Doesn't fit together (publisher) | Channel says brand isn't a fit |
| 13 | PUBLISHER_DISLIKE | Doesn't like the brand | Channel doesn't want this brand |
| 14 | PUBLISHER_PRICING | Brand Price is too low | Channel says price is too low |
| 15 | PUBLISHER_WORKING_TOGETHER | Already working together with the brand | Channel already running with brand |
| 16 | PUBLISHER_TIMING | Timing is off (publisher) | Channel timing — not now |
| 17 | PUBLISHER_NO_RESPONSE | Brand did not respond | Brand never replied |
| 18 | DEMOGRAPHICS_NO_MATCH | Demographics don't fit | Audience demographics don't match |
| 19 | NOT_BRAND_SAFE | Not brand safe | Brand-safety concern |
| 20 | POOR_BRAND_HISTORY | Poor brand sponsorship history | Brand has a poor sponsorship track record |
| 21 | HIGH_VOLATILITY | High Volatility | Channel views are too volatile |
| 22 | LOW_ENGAGEMENT | Low engagement/Low views | Low engagement or low views |
| 23 | DUPLICATE_PROPOSAL | Duplicate proposal | Already pitched recently |
| 24 | NO_FACE_ON_SCREEN | No face on screen | Channel doesn't show a host on screen |

#### Which date column for which question?

`thoughtleaders_adlink` has multiple timestamps. Picking the wrong one silently distorts trend analysis (e.g. grouping by `created_at` mixes outreach-blast batches with steady-state activity; grouping by `purchase_date` drops everything that didn't sell because rejected/pipeline rows have NULL `purchase_date`).

| Question | Use |
|---|---|
| "How many deals **sold** in year X?" | `purchase_date` (only set on sold/transacted deals) |
| "How many deals **created** in year X?" (incl. pipeline + lost) | `created_at` |
| "How much was **active outreach** in window X?" | `outreach_date` (sparse — falls back to `created_at` if null) |
| "When did the ad **go live on YouTube**?" | `publish_date` — null means not yet published; sold deals can still be canceled until this is set |
| "Latest activity / pipeline aging" | `updated_at` |
| "When was the deal **proposed/presented/rejected**?" | `proposal_approved_date` / `presented_date` / `rejected_date` (each only set when that stage was reached) |

**Default for "deals over time" reporting:** `created_at` if you want all flow, `purchase_date` if you want only revenue.

#### Pipeline Stages

- **Active pipeline** = statuses with weight > 0: 0, 2, 6, 7, 8.
- **Won** = 3 (Sold).
- **Lost** = 4, 5, 9.

### `thoughtleaders_brand`

> 🚨 **Columns that DO NOT exist on `thoughtleaders_brand` — common hallucinations:**
> - ❌ `domain` / `url` — the website column is `website`.
> - ❌ `brand_name` — it's plain `name` here (unlike channel, which prefixes its display fields).
> - ❌ `is_active` — brands have no active/inactive flag; every row is live.
> - ❌ `sponsored_topics` (or similar topic columns) — the closest fields are `keywords` and `ai_description`.
> - ❌ `organization_id` — org lives on profile, not brand (see Key Relationships below).

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Primary key |
| `name` | varchar | Brand name. ⚠️ Plain `name`, not `brand_name`. |
| `description` | text | Brand description |
| `website` | varchar | Brand website URL. ⚠️ The column is `website`, NOT `domain`. |
| `slug` | varchar | TL-platform slug |
| `keywords` | varchar | Topic/detection keywords for the brand |
| `ai_description` | JSONB | AI-generated descriptive info about the brand |
| `creator_id` | int FK | User who created it |

#### Junction Tables

| Table | Columns | Purpose |
|-------|---------|---------|
| `thoughtleaders_profile_brands` | `profile_id`, `brand_id` | Profile↔Brand M2M (Django field `profile.brands`). In practice each profile has one brand attached. |
| `thoughtleaders_brand_brands` | `from_brand_id`, `to_brand_id` | Self-referential: related brands. |

### `thoughtleaders_adspot` (Ad Catalogue)

Buyable ad placements. Each adspot links a channel to a seller. Price/cost here are **list prices** — actual deal values live on the adlink.

A channel can have multiple adspots (different sellers: talent manager, direct, multiple agencies).

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Primary key |
| `channel_id` | int FK | → `thoughtleaders_channel.id` |
| `price` | numeric | List/catalogue price |
| `cost` | numeric | List/catalogue cost |
| `integration` | int | 1=YouTube Mentions (live reads). Only one active mention-type adspot per channel. |
| `is_active` | boolean | Active flag |
| `publisher_id` | int FK | → `auth_user.id` (NOT `thoughtleaders_profile.id` — see gotcha below) |

### Key columns for the `thoughtleaders_channel` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Primary key |
| `channel_name` | varchar | Display name. ⚠️ The column is `channel_name`, NOT `name`. |
| `external_channel_id` | varchar | YouTube channel ID (e.g., `UCxxxxxx`). ⚠️ There is NO `youtube_id` column — use this one. |
| `url` | varchar | Channel URL (external — usually the YouTube URL). |
| `slug` | varchar | TL-platform-specific slug. Used to build the canonical TL channel URL: `https://app.thoughtleaders.io/youtube/<slug>`. Prefer this over `url` when linking to a channel from any user-facing surface (reports, samples, Slack posts). Fall back to an ID-based TL path if `slug` is NULL; never fall back to the external YouTube URL. |
| `total_views` | int | Total views for the entire channel |
| `subscribers` | bigint | Subscriber count. |
| `media_selling_network_join_date` | date/timestamptz | When the channel joined the MSN. **MSN membership = this column IS NOT NULL.** |
| `is_tpp` | boolean | True = TPP channel — TL's closest-partner channels (~144 at 100k+ subscribers), a strict subset of MSN. Prefer when booking: fastest response, easiest to close. ⚠️ **This is not the MSN flag.** For MSN, use `media_selling_network_join_date IS NOT NULL`. |
| `content_category` | int | TL's own content-category code (1–22) — **not YouTube's categories**: 1 Backend Development, 2 Design, 3 Entrepreneurship, 4 Frontend Development, 5 Lifestyle, 6 Marketing, 7 Mobile Development, 8 Sales, 9 Travel, 10 Business, 11 Photography, 12 General Knowledge, 13 Personal Finance, 14 News & Politics, 15 Technology, 16 Gaming, 17 Food, 18 Sports, 19 How To & Crafts, 20 Entertainment, 21 Health & Fitness, 22 Music. The assignment is too unreliable for discovery — do not use it to find channels by topic. **For topic/category discovery, prefer `tl recommender top-channels "<tag>"` |
| `is_active` | boolean | Whether the channel is active. ⚠️ **Always include `is_active = true` in channel queries** unless explicitly looking for archived rows. |
| `country` | varchar | Channel's primary country (ISO 3166-1 alpha-2 code, e.g. `US`, `GB`, `BR`). This is often the cleanest answer to "geography" questions on sponsorships. May be NULL or blank. |
| `language` | varchar | Primary content language. ⚠️ **Short ISO 639 codes — NOT BCP-47.** Mostly 2-letter ISO 639-1 (`en`, `pt`, `hi`) for major languages; occasionally 3-letter ISO 639-2/3 (`arc`, `arz`, `ase`, `ceb`) for languages without a 2-letter code. Filtering with `language = 'en-US'` returns zero rows. **Don't assume `LENGTH(language) = 2`** — that silently drops the 3-letter long-tail. May be NULL. |
| `last_published` | date | Date of the channel's most recently seen video. Use for "is the channel still active?" filters — e.g. `last_published >= CURRENT_DATE - INTERVAL '120 days'`. |
| `sponsorship_score` | double precision | TL-internal channel quality score (range 0-10, higher is better, if below 5, the channel is low quality). Useful as a tiebreaker when ranking candidate channels. |
| `ai_description` | JSON | Descriptive information about a channel. Contains fields such as `description`, `audience`, `topic_descriptions`, and `brand_safety`. Useful as a regex-target for thematic filtering when the recommender results are too coarse (e.g. filtering "technology" down to actual tech reviewers via keywords like `tech|gadget|review|software`). |
| `evergreenness` | float | Longform evergreen score: median per-video `(views@180d − views@30d) / views@30d` over the channel's trailing-year uploads; ≥ 1 = evergreen (day-180 views at least double day-30). In ES this lands on the channel doc as `evergreenness_longform`. |
| `demographic_usa_share` | smallint (0–100) | Percentage of the channel's audience based in the US. Convenience for the common "is this a US-heavy channel?" filter — pre-computed from `demographic_geo['US']`. NULL when the channel has no demographic data. |
| `demographic_male_share` | smallint (0–100) | Percentage of the channel's audience that's male. `female_share = 100 - demographic_male_share` (no separate column). NULL when the channel has no demographic data. |
| `demographic_age_median_value` | varchar | The age-bucket label (e.g. `25-34`) corresponding to the median of `demographic_age`, pre-computed on save. Indexed; cheap to filter on. NULL when there's no age data. |
| `demographic_device_primary` | varchar | The dominant viewing-device token from `demographic_device` (e.g. `mobile`, `computer`, `tablet`, `tv`, `game_console`). Pre-computed on save. ⚠️ The DB uses `computer` (not `desktop`) and `game_console` (not `game-console`); the CLI's structured filters translate, but raw SQL filters do not. Indexed. NULL when there's no device data. |
| `demographic_age` | JSONB | Audience age distribution, e.g. `{"18-24": 7, "25-34": 20, "35-44": 21, "45-54": 18, "55-64": 15}`. Percentages don't always sum to 100 (the long-tail buckets are dropped). NULL when the channel has no demographic data. Filter with `demographic_age->>'25-34' >= '20'` (text comparison) or cast to int. |
| `demographic_geo` | JSONB | Audience geography as 2-letter ISO country code → percentage, e.g. `{"US": 53.0, "UK": 12.0, "CA": 8.0, "IN": 5.0}`. Long tail is dropped — entries summing to ≥ ~95% is normal. Filter with `(demographic_geo->>'US')::float >= 60`. NULL when there's no demographic data. |
| `demographic_device` | JSONB | Audience device-mix percentages, e.g. `{"computer": 35, "mobile": 45, "tablet": 8, "tv": 10, "game_console": 2}`. Same DB-token caveats as `demographic_device_primary`. Filter with `(demographic_device->>'mobile')::float >= 60`. NULL when there's no demographic data. |
| `demographics_updated_at` | timestamptz | When any of the demographic_* fields last changed (auto-stamped on save via `Channel.FIELDS_TO_CHECK`). Use as a recency filter when sampling — older demographics are stale. NULL when demographics were never set. |
| `outreach_email` | varchar | Channel outreach email |

**IMPORTANT**: Demographics and outreach columns have additional pricing attached! They are the most valuable, and the most expensive fields to fetch. Never do "SELECT *" on this table because that will also fetch these expensive columns.

#### Hallucination shapes to avoid

When composing `SELECT ... FROM thoughtleaders_channel ...`, do not improvise column names from semantic intuition — consult the output of `tl schema pg thoughtleaders_channel` or the column table above. The `tl schema` command is authoritative. Failed guesses return *"column '\<name\>' does not exist"* and cost a round-trip. Recurring problems:

- ❌ `reach` — this column does NOT exist on Postgres. Use `subscribers`. (The Elasticsearch channel doc uses `reach` as a legacy alias for the same value — see [elasticsearch-schema.md](elasticsearch-schema.md) — but Postgres never had that name; don't carry the ES field name over when switching to a `tl db pg` query.)
- Projected views is in the field named `projected_views`
- ❌ **Suffix/qualifier variants of date columns** (e.g. an `_max` / `latest_` / `_date` form when the canonical column has neither). Date columns  use bare names.
- ❌ **Platform-name-prefixed ID forms** (e.g. a platform-name prefix when the canonical column uses a neutral `external_` prefix). See the column table for the actual ID column.
- ❌ **Bare-noun forms without the table-prefix** (e.g. `name` instead of `channel_name`). This table prefixes its display fields with `channel_` to avoid SQL keyword collisions and ambiguity in joins.
- ❌ **User-facing-term forms used as SQL column names** (the user-facing word is sometimes different from the SQL column name; consult [business-glossary](business-glossary.md) for the canonical mapping when the two diverge).

When the canonical column you need isn't obvious from the previous description, consult the output of `tl schema pg thoughtleaders_channel`. Do **not** rely on a 400 to correct you, and do **not** fall back to `information_schema.columns` as the recovery path — that's a regression marker too.

### Key columns for the `auth_user` table (Django Users)

Standard Django user table. Used for owner lookups.

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Primary key |
| `first_name` | varchar | First name |
| `last_name` | varchar | Last name |
| `email` | varchar | Email |

## Top Tables by Row Count

| Rows | Table | Purpose |
|------|-------|---------|
| 1.3M | `thoughtleaders_channel` | YouTube channels |
| 1.2M | `thoughtleaders_historicaladlink` | Audit trail for adlink changes |
| 150K | `thoughtleaders_adlink` | Deals/sponsorships |
| 43K | `thoughtleaders_adspot` | Ad placements |
| 20K | `auth_user` | Users (team + external) |
| 20K | `thoughtleaders_profile` | User profiles |
| 19K | `thoughtleaders_organization` | Organizations |
| 19K | `dashboard_campaign` | Campaign groupings |
| 13K | `thoughtleaders_dailymetric` | Daily performance metrics |
| 12K | `thoughtleaders_leads` | Sales leads |

## Key Relationships

```
thoughtleaders_adlink
  ├── ad_spot_id → thoughtleaders_adspot.id
  │                  └── channel_id → thoughtleaders_channel.id
  ├── owner_advertiser_id → auth_user.id
  ├── owner_publisher_id → auth_user.id
  ├── owner_sales_id → auth_user.id
  └── advertiser_profile_id → thoughtleaders_profile.id
                              ├── organization_id → thoughtleaders_organization.id
                              └── profile_brands.profile_id → brand.id

⚠️ thoughtleaders_adlink has NO direct brand_id, organization_id, or channel_id column.
⚠️ thoughtleaders_brand has NO organization_id column — org lives on profile.
```

## Finding a single channel or brand ID

As a special case, the `tl channels find` and `tl brands find` commands accept a name of the channel / brand (be sure to properly quote them for the shell) and will return the respective ID. Use this instead of constructing SQL for this particular case. The commands will return a list of possible choices

For bulk handle/name lookups in SQL (e.g. resolving a list of YouTube handles against `common_name`, or a list of channel names against `channel_name`), compare case-insensitively with `UPPER(…)` on both sides — that's the form the functional indexes serve:

```sql
SELECT id, channel_name, common_name
FROM thoughtleaders_channel
WHERE UPPER(common_name) IN (UPPER('@TheInfographicsShow'), UPPER('@DrewDirksen'))
   OR UPPER(channel_name) IN (UPPER('The Infographics Show'), UPPER('Drew Dirksen'))
LIMIT 100 OFFSET 0
```

Wrap **both** sides in `UPPER(…)`. `UPPER(col) IN ('lowercase literal', …)` matches nothing, since the indexed side is upper-cased.

`LOWER(channel_name)`, `LOWER(common_name)` — or `LOWER()` on any indexed column — cannot use those indexes. Every such predicate is a full sequential scan of 1.3M channel rows: ~20s when it survives, a 504 statement timeout when it doesn't. One `OR LOWER(col) IN (…)` branch is enough to force the scan even when every other branch in the `WHERE` is indexed, so do not mix the two forms.

### Common Join Paths

**Adlink → Channel name:**
```sql
JOIN thoughtleaders_adspot s ON a.ad_spot_id = s.id
JOIN thoughtleaders_channel ch ON s.channel_id = ch.id
```

**Adlink → Brand name:**
```sql
JOIN thoughtleaders_profile p ON a.advertiser_profile_id = p.id
JOIN thoughtleaders_profile_brands pb ON p.id = pb.profile_id
JOIN thoughtleaders_brand b ON pb.brand_id = b.id
-- NEVER: JOIN brand b ON b.id = a.advertiser_profile_id (different ID spaces, returns wrong data)
```
This 3-table join is the one brand path that works in every session — use it in anything reusable.

**Adlink → Organization:**
```sql
JOIN thoughtleaders_profile p ON a.advertiser_profile_id = p.id
JOIN thoughtleaders_organization o ON p.organization_id = o.id
```

🚨 **`adspot.publisher_id` is a FK to `auth_user`, not `profile`.** To get the publisher's profile, join through user:
```sql
JOIN auth_user au ON au.id = adspot.publisher_id
JOIN thoughtleaders_profile p ON p.user_id = adspot.publisher_id
```
Joining `adspot.publisher_id → profile.id` directly mixes ID spaces and returns garbage.

## Key columns for the `thoughtleaders_profile` table

The profile is the account record for a person/company on the platform. Every brand-side query hops through it (`adlink.advertiser_profile_id → profile → profile_brands → brand`), which makes it the most-joined table after the sponsorships themselves.

> 🚨 **Columns that DO NOT exist on `thoughtleaders_profile` — common hallucinations:**
> - ❌ `name` / `email` — the person's name and email live on `auth_user` (join via `user_id`: `first_name`, `last_name`, `email`). The company name is `organization_name`, denormalized onto the profile.
> - ❌ `auth_user_id` — the FK column is `user_id`.
> - ❌ `brand_id` — a profile can have several brands; join `thoughtleaders_profile_brands` on `profile_id`.
> - ❌ `is_tpp` — that's a channel attribute; a profile's channels come via `thoughtleaders_profile_channels`.

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Primary key. ⚠️ NOT the same ID space as `auth_user.id` — never join a `*_id` pointing at users to `profile.id` directly. |
| `user_id` | int FK | → `auth_user.id` (the person's name and email live there) |
| `organization_id` | int FK | → `thoughtleaders_organization.id` |
| `organization_name` | varchar | Denormalized company/organization display name |
| `persona` | int | Account persona (see constants below) |
| `is_advertiser` | boolean | Buyer (brand) side capability flag |
| `is_publisher` | boolean | Seller (channel) side capability flag |
| `owner_sales_id` | int FK | → `auth_user.id` — TL-side sales owner for this profile |
| `owner_advertiser_id` | int FK | → `auth_user.id` — TL-side brand account owner |
| `owner_publisher_id` | int FK | → `auth_user.id` — on advertiser profiles: the adops user in charge |
| `media_buying_network_join_date` | date | MBN (brand-side buying network) join date. **MBN membership = this column IS NOT NULL.** ⚠️ Don't confuse with the channel's `media_selling_network_join_date` (MSN — the channel-side network). |
| `superuser_notes` | text | Internal TL notes about the profile |
| `buying_preference_*` | various | The brand's buying preferences (audience age/gender/geo shares, face-on-screen, custom instructions) |

**Profile with the person's name and email:**
```sql
SELECT p.id, u.first_name, u.last_name, u.email, p.organization_name
FROM thoughtleaders_profile p
JOIN auth_user u ON u.id = p.user_id
WHERE p.organization_id = 123
LIMIT 50 OFFSET 0
```

### `thoughtleaders_profile` persona constants

| Value | Label |
|-------|-------|
| 1 | Direct Brand |
| 2 | Creator |
| 3 | Talent Manager |
| 4 | Media Agency |
| 5 | Other |

## `thoughtleaders_profile_channels` (Profile ↔ Channel M2M)

Which channels a seller-side profile represents. One row per profile/channel pair.

| Column | Type | Description |
|--------|------|-------------|
| `id` | int PK | |
| `profile_id` | int FK | → `thoughtleaders_profile.id` |
| `channel_id` | int FK | → `thoughtleaders_channel.id` |
| `created_where` | varchar | What created the link. NULL on every link that predates the column. |
| `created_at` | timestamptz | When the link was created. NULL on every link that predates the column — filtering or ordering on it silently drops that whole population. |

⚠️ **Representation is not deal ownership.** A deal's seller is the adspot's own publisher (`thoughtleaders_adspot.publisher_id` → `auth_user`, then `thoughtleaders_profile.user_id` for the org). Joining sponsorships to an org through this table alone over-reports — representing a channel never widens the seller side to deals the org did not sell. `publisher_id` is scrubbed from the sandbox adspot view, so the seller-side hop is only queryable from a full-access role.

## Example queries

**Total weighted pipeline by sales rep:**
```sql
SELECT owner_sales_id, SUM(weighted_price) AS pipeline
FROM thoughtleaders_adlink
WHERE publish_status IN (7, 10)
GROUP BY owner_sales_id
ORDER BY pipeline DESC
LIMIT 100 OFFSET 0
```

**Sold deals this month:**
```sql
SELECT id, price, purchase_date, ad_spot_id, advertiser_profile_id
FROM thoughtleaders_adlink
WHERE publish_status = 3
  AND purchase_date >= date_trunc('month', CURRENT_DATE)
ORDER BY purchase_date DESC
LIMIT 10000 OFFSET 0
```

**MSN channel joins this month:**
```sql
SELECT id, channel_name, media_selling_network_join_date
FROM thoughtleaders_channel
WHERE media_selling_network_join_date >= date_trunc('month', CURRENT_DATE)
ORDER BY media_selling_network_join_date DESC
LIMIT 10000 OFFSET 0
```

**A specific sponsorship info with brand and channel name:**
```sql
SELECT a.id, a.price, a.publish_status, b.name AS brand, ch.channel_name
FROM thoughtleaders_adlink a
JOIN thoughtleaders_adspot s ON a.ad_spot_id = s.id
JOIN thoughtleaders_channel ch ON s.channel_id = ch.id
JOIN thoughtleaders_profile p ON a.advertiser_profile_id = p.id
JOIN thoughtleaders_profile_brands pb ON p.id = pb.profile_id
JOIN thoughtleaders_brand b ON pb.brand_id = b.id
WHERE a.id = 12345
LIMIT 1 OFFSET 0
```
