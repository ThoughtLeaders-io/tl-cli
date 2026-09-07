# Elasticsearch content search — Boolean queries & content fields

How to write real content-search queries against ThoughtLeaders Elasticsearch
(`tl db es`). This is the reference the `tl-keyword-research` skill leans on:
the value of the skill is not generating synonyms — it's writing precise Boolean
queries over the right fields and validating the matches. Pair it with the
schema reference in `../../tl/references/elasticsearch-schema.md`.

## The index has two doc types

The content alias holds **both** article docs and channel docs, distinguished by
`doc_type`:

| `doc_type` | ~count | what it is | descriptive text fields |
|---|---|---|---|
| `article` | ~670M | one video / upload | `title`, `summary`, `transcript` (or in case of podcast records, `content`) |
| `channel` | ~44M | one whole channel | `name`, `description`, `ai.description`, `ai.topic_descriptions` |

**Always scope by `doc_type`** or your counts silently mix videos and channels:

```json
{"query": {"bool": {"filter": [{"term": {"doc_type": "article"}}], "must": [ ... ]}}}
```

- **Topic / video search** → `doc_type: article`, search article fields.
- **Channel search** → `doc_type: channel`, search channel fields.

A `match_phrase` on `title` only ever hits article docs (channel docs have no
`title`), but scoping explicitly keeps counts honest and lets you target channel
fields deliberately.

### Default scope: YouTube + longform

`probe.py` always scopes to **YouTube uploads** (`channel.format` 4 at topic level,
`format` 4 at channel level — that's the inventory we work with), and at **topic
level** also to **longform** video (`content_type: "longform"`) by default. Shorts
and live are excluded unless the user asks for them (`--content-type all|short|live`).
`content_type` is article-only — channel docs have no content type, so the channel
level filters on `format` 4 alone.

```json
{"bool": {"filter": [
  {"term": {"doc_type": "article"}},
  {"term": {"channel.format": 4}},
  {"term": {"content_type": "longform"}}], "must": [ ... ]}}
```

## Content fields

**Article docs** (`doc_type: article`):
- `title` — video title. Strongest relevance signal (see weighting).
- `summary` — **the video's creator-written description** (the text under the video: promo links, hashtags, subscribe blocks). Misleading name — it is NOT an AI summary. This is the main field for searching video description text.
- `transcript` — raw transcript. Stored as timed XML (`<text start="..." dur="...">`), so a transcript hit carries timestamps if you parse it.
- `content` — ⚠️ podcast episodes' only;  usually show notes text or rarely transcripts; effectively absent on YouTube docs. Don't search for YouTube content.
- `content_type` — `longform` | `short` | `live`. The default probe scope is
  `longform` (best sponsorable-content signal).
- `channel` — object with `id`, `content_category`, `country`, `language`, `format` (NO descriptive text — see channel docs for that). `format` 4 = YouTube.
- `publication_date` (date), `views`, `likes`, `duration`, `hashtags`, `url`.

**Channel docs** (`doc_type: channel`):
- `name` — channel name.
- `description` — the channel's own (YouTube) description.
- `ai.description` — AI-written channel description: third-person, always English regardless of the channel's language (~93% of channel docs).
- `ai.topic_descriptions` — AI-written description of the channel's topics, a **single prose string, not an array**. **This is the "topic description" field** — prefer it for channel-fit validation; it's a focused, on-topic summary. ⚠️ Only ~37% of channel docs have it — a no-match here doesn't prove the channel is off-topic.
- `ai.brand_safety` — letter grade (A–F).
- `content_category`, `country`, `language`, `format`, `reach` (subscribers), `total_views`, `sponsorship_price`, `sponsorship_score`, `outreach_email`, `social_links`, `is_tl_channel`.

> **Legacy field names — ES was NOT migrated in the big rename.** PostgreSQL
> and Firebolt renamed `reach`→`subscribers`, `impression*`→`projected_views*`,
> `is_tl_channel`→`is_tpp`; the Elasticsearch index **keeps the old names**.
> Every ES `_source` / `term` / `range` / `sort` on channel docs must use
> `reach`, `impression`/`impression_live`/`impression_shorts`, `is_tl_channel`.
> Do not "fix" a probe to the new names — it silently returns nulls (verified
> live: `subscribers`, `projected_views`, `is_tpp` each match **0** docs). Skill
> *output* uses the new vocabulary (`subscribers`, `is_tpp`); the translation
> point is `search_channels.py`'s sponsorability block.

### Content field names for the deliverable vs ES field names for probes

The platform FilterSet / report link uses its own **content field** names; raw
`tl db es` probes use the **actual ES field paths**. They differ for channel
fields — map them:

| Content field (filter set / report link) | ES field path (probe query) | doc_type |
|---|---|---|
| `title` | `title` | article |
| `summary` | `summary` | article |
| `transcript` | `transcript` | article |
| `content` | `content` | article |
| `channel.channel_name` | `name` | channel |
| `channel_description` | `description.domains` | channel |
| `channel_description_ai` | `ai.description` | channel |
| `channel_topic_description` | `ai.topic_descriptions` | channel |
| `hashtags` | `hashtags` | article |

Use ES paths in `probe.py` / raw queries; use content field names in
`build_report.py` / the report link. `build_report.py` rejects any `content_fields`
value that isn't a recognized content field name, so passing a raw ES path (e.g.
`ai.topic_descriptions`) fails loudly rather than silently mis-filtering.

The `channel_description` content field targets `description.domains` — the
"About this channel" text additionally indexed so **whole domains are single
searchable terms** (verified live: `patreon.com` matches every channel whose
About-this-channel text links to patreon.com, including inside full URLs,
which bare `description` probes
undercount since URLs there tokenize into word pieces). For domain-shaped
keywords (e.g. `substack.com`), probe against `description.domains`, not bare
`description`.

### The report link / FilterSet keyword grammar (NOT simple_query_string)

A keyword group's `text` in a report link / saved FilterSet uses the
platform's **own keyword grammar**, which is not simple_query_string:

- Operators are the **uppercase English words** `AND`, `OR`, `NOT`
  (case-sensitive — lowercase `and` is just a word). `NOT` negates the next
  term or parenthesized group.
- **Parentheses** group for precedence; **double-quoted phrases** match
  exactly; a bare word run behaves as one adjacent phrase (`Coca Cola`
  matches the two words side by side — it is NOT an AND).
- **Raw SQS operator characters are literal text there**: `|`, `+`, and a
  bare `-` fold into the surrounding phrase and match nothing useful. A group
  written as `("fable 5" | fable5) -keto` is a broken link filter; it must be
  `("fable 5" OR fable5) AND NOT keto`.
- `*` prefix and `~` fuzzy/slop are **not supported** in link/filter text.
  They are probe-only research operators — before delivery, enumerate the
  variants (`retire*` → `(retire OR retiring OR retirement)`).
- Write exclusions as `AND NOT x` and join every atom with an explicit
  `AND`/`OR` — a NOT without an AND in front of it behaves like the
  OR-default balloon described below, quietly widening the match.

`build_report.py` performs this SQS→link-grammar translation automatically
(and rejects `*`/`~`), so hand-write it only when composing a link without
the script. In the URL, groups missing `content_fields` inherit the link's
global `content_fields` param.

**Sample keys returned by `probe.py`** (friendly, already validator-ready — not
ES paths): topic level → `title`, `summary`, `channel_id`, `category`, `url`;
channel level → `name`, `topic`, `channel_description`, `channel_id`. These
metadata keys (`channel_id`, `category`, `url`) are for context/candidate
extraction only — they are not valid `content_fields` for a filter spec.

## Tokenization — spelling/spacing variants are *different* terms

The analyzer is `standard`: it lowercases and splits on whitespace **and
punctuation** (hyphen, period, slash, underscore…), but it does **not** split a
letter+digit run. So a name/version written different ways produces different
tokens, and a `match_phrase` / SQS phrase on one form **misses** the others:

| you write | tokens | matches |
|---|---|---|
| `fable 5` | `[fable] [5]` | the spaced form |
| `fable-5` | `[fable] [5]` | **identical to `fable 5`** — the hyphen is a split |
| `fable5` | `[fable5]` | a *distinct single token* — the solid / handle / hashtag form |
| `fable five` | `[fable] [five]` | the spelled-out form |
| `fablefive` | `[fablefive]` | another distinct single token |

Verified live (topic level, all-time): `fable 5` = `fable-5` = **1,215** docs;
`fable5` = **94**; `fable five` = **118**; `fablefive` = **1**. Four different
document populations — collapsing them in your head silently drops coverage.

(One exception to the punctuation-split rule: the channel-doc
`description.domains` subfield keeps whole domains as single terms —
`patreon.com` is one token there. See the content field mapping above.)

**Rule:** for any candidate carrying a number, version, model name, or anything
that could be written solid vs spaced vs hyphenated, **probe each spelling as its
own candidate** (spaced, solid, hyphenated, spelled-out, and the handle/hashtag
form), then fold the survivors into one boolean group (next section):
`("fable 5" | fable5 | "fable five")`. (No stemming either — `bitcoin` ≠
`bitcoins`; expand inflections the same way, see *Relevance signals*.)

## A keyword group is a self-contained boolean query

Every "keyword group" — in `probe.py --mode sqs`, in the FilterSet, and in the
report link — is a full `simple_query_string`, **not** a flat phrase. Parentheses
group includes *and scope exclusions to any sub-expression*, and groups
OR-combine independently. This is the lever for **per-family de-noising**: put an
exclusion *inside* the family it belongs to, not across the whole filter.

```text
("mythos 5" | "claude mythos" | mythos5) -ketogene -keto    # de-noises ONLY the Mythos arm
```

Scoping is real, not cosmetic — verified live (topic level):

| group | docs | what happened |
|---|---|---|
| `("fable 5" \| "anthropic banned")` | 1,236 | union baseline |
| `("fable 5" \| "anthropic banned") -openclaw -opencode` | 1,176 | exclusion across the **whole** group — also cut **51** on-topic `fable 5` videos that mention OpenClaw |
| `("fable 5" \| ("anthropic banned" -openclaw -opencode))` | 1,227 | exclusion scoped to the `anthropic banned` **arm** — removed only its 9 noise docs, **kept** the 51 |

The 51-doc gap is exactly the over-cut a whole-filter exclusion causes and a
scoped one avoids. **Prefer the in-group `(...) -polluter` form for per-family
rescue;** reserve the FilterSet's separate `{"exclude": true}` group (a
whole-filter `must_not`) for exclusions you genuinely want applied to *every*
term. Inside a parenthesized group with the AND default, `(a | b) -c` correctly
means `(a OR b) AND NOT c` — but the bare `-` is only safe with
`default_operator: "and"` set (which `--mode sqs` does); under the OR default it
floats free and balloons (see the footgun below).

## Boolean queries

The accepted/blocked query types, top-level body keys, size caps, and
aggregation bounds are catalogued in the canonical schema reference —
`../../tl/references/elasticsearch-schema.md` → *Accepted query bodies*. The
short version that matters here: `simple_query_string` and `bool` are in;
`query_string`/`wildcard`/`fuzzy`/`regexp` and the parent-child joins are
out; a `filter` agg wrapping a `cardinality` (the probe's counts-plus-recency
shape) is fine in one call.

### `simple_query_string` — the compact Boolean surface (preferred)

`simple_query_string` (SQS) is the sanctioned way to write rich Boolean text
logic — `query_string`, `wildcard`, `regexp`, `fuzzy` and `more_like_this` all
return `400`, and SQS replaces them (it has its own `*` prefix and `~N` fuzzy).
All operators and parameters below were verified against our live endpoint.

| Operator | Meaning | Example |
|---|---|---|
| `+` | term **required** (AND) | `retirement +planning` |
| `\|` | OR between terms | `retirement \| annuity` |
| `-` | NOT (negate a token) — **see footgun** | `retirement -crypto` |
| `"..."` | exact phrase (adjacent, in order) | `"retirement planning"` |
| `"..."~N` | phrase **slop** — up to N words apart | `"retirement planning"~2` |
| `(...)` | grouping / precedence | `(retirement \| annuity) +planning` |
| `term*` | **prefix** (trailing `*` only) | `retire*` → retire/retiring/retiree/… |
| `term~N` | **fuzzy** (edit distance N) | `retirment~1` → retirement |

Useful parameters: `fields` (with `^` boosts — ranking only, never the count),
`default_operator`, `minimum_should_match` (require N-of-M bare terms),
`fuzzy_prefix_length` (1–2 keeps the word start fixed), `flags` (disable risky
operators on untrusted seeds, e.g. `"flags": "AND|OR|PHRASE|PRECEDENCE"`).

#### Three defaults that silently return ~the whole corpus

The corpus is hundreds of millions of `article` docs. Override all three or counts
are meaningless:

1. **Always `track_total_hits: true`** — without it `total` caps at `10000`.
2. **Always `default_operator: "and"`** — SQS defaults to **OR**, so bare terms
   become a *union* (`retirement planning`: 273k under OR vs **4.1k** under AND).
3. **Always pass an explicit `fields` list** — omitting `fields` (or `["*"]`)
   searches *every* field incl. `transcript`/`content` and inflates counts ~60×.

#### The `-` exclusion footgun

Under the default OR operator a `-term` negation **floats free** as `OR NOT term`,
so the query matches "has the term **OR** lacks the excluded word" — i.e. nearly
everything (`retirement +planning -crypto` returns ~the entire 606M corpus). Don't
fix it with inline operators — **lift exclusions into `bool.must_not`** (next
section), or, if you must inline, set `default_operator: "and"` (then `-` is safe):

```json
{"size": 0, "track_total_hits": true,
 "query": {"bool": {"filter": [{"term": {"doc_type": "article"}}], "must": [
   {"simple_query_string": {
      "query": "\"tiktok shop\" +(marketing | affiliate | ecommerce)",
      "fields": ["title^3", "summary", "transcript"],
      "default_operator": "and"}}],
   "must_not": [{"simple_query_string": {"query": "dropshipping",
      "fields": ["title", "summary", "transcript"]}}]}}}
```

**Sanity-check every count:** run the bare `doc_type` filter first to learn corpus
size, then your query. A correct keyword query is orders of magnitude below it; a
result near corpus scale means a balloon (OR-default, floating `-`, or `*`/missing
`fields`).

### `bool` — for cross-field grouped ANDs

`simple_query_string` searches one field set. When you need *term X in field A AND
term Y in field B* (e.g. a topic in the video AND a topic in the channel), use
`bool` with per-clause field targeting:

```json
{"query": {"bool": {
  "filter": [{"term": {"doc_type": "article"}}],
  "must": [
    {"match_phrase": {"title": "tiktok shop"}},
    {"simple_query_string": {"query": "marketing | affiliate | ecommerce",
                              "fields": ["summary", "transcript"]}}
  ],
  "must_not": [{"match_phrase": {"transcript": "dropshipping"}}]}}}
```

`should` = OR, `must` = AND, `must_not` = NOT, `filter` = AND without scoring.
Use `minimum_should_match` to require N of several `should` clauses.

## Exclusion (NOT) patterns — rescue a too-broad term

When a term is on-intent but polluted, **exclude the recurring polluter token
instead of dropping the term** (dropping loses the on-intent docs too). For a
**single family**, scope the exclusion inside that family's own group with the
in-group SQS form — `(a | b) -polluter` (see *A keyword group is a self-contained
boolean query* above); this keeps the exclusion off the other groups. The `bool`
`must_not` form below is for when you need to **target one field** or apply the
exclusion across the whole query — it is explicit and immune to the SQS `-`
footgun:

```json
{"size": 0, "track_total_hits": true,
 "aggs": {"distinct": {"cardinality": {"field": "channel.id"}}},
 "query": {"bool": {
   "filter": [{"term": {"doc_type": "article"}}],
   "must":     [{"simple_query_string": {"query": "\"FIRE movement\"",
                  "fields": ["title^3", "summary", "transcript"]}}],
   "must_not": [{"match_phrase": {"title": "Free Fire"}}]}}}
```

Verified live on the retirement niche:
- **Name collision** — `"FIRE movement"` (3,812 channels) pulls the game *Free
  Fire*; excluding `"Free Fire"` → 3,562, financial-FIRE intact.
- **Foreign jurisdiction** — `"social security"` (channel level, 228) pulls
  Brazilian `INSS` law-firm content; excluding `INSS` → 192.
- **Academic register** — `annuity` (596) pulls coursework (`annuity due`,
  `ordinary annuity`, `perpetuity`, `present value`); excluding that phrase
  cluster → 557, leaving retirement-product content.

**Over-exclusion guard** — the polluter must be absent from on-intent docs. Confirm
cheaply: (1) re-run the on-intent *core* (term AND a strong on-intent signal) with
vs without the exclusion — the count should barely move (`Free Fire` cost the core
13 of 6,722 docs, 0.2%); (2) restrict to the intent's language/country and confirm
the exclusion removes ≈0 there. A material drop means the token is shared (e.g.
excluding `retire` cuts FIRE movement −30%) — pick a narrower polluter. Often that
narrower polluter is the multi-word **phrase**, not a bare token the on-intent docs
share: excluding bare `film` cut a `"cannes lions"` core **24%** (it shares `film`
— the *Film Lions* category, "ad film"), while the phrase `"film festival"` cut
only **4%**. The same NOT mechanic **carves a sub-niche** from a broad root
(`basketball` −`nba` −`wnba` → recreational). A `terms` agg on `channel.country`
(topic) / `language` (channel) surfaces the polluter as a region/language spike.

## Anchor a broad root with AND (rescue a place / generic root)

A root too broad to use alone — a **place** (`cannes`), a generic word — can't be
fixed by exclusion alone (there's no single polluter to cut). Rescue it by
*requiring* a mandatory **anchor token** that pins it to the intent, plus a domain
OR-qualifier, all `+`-required in one group:

```text
cannes +lions +(advertising | agency | campaign | "young lions") -"film festival"
```

Two things make this earn its place:

- **Non-adjacent AND reaches what a phrase can't.** `match_phrase` / `"cannes
  lions"` only fires when the words are side-by-side; `cannes +lions` fires when
  both appear anywhere in the doc — so it catches "**Young Lions** … at
  **Cannes**", "road to **Cannes** … our **Lions**". That's extra on-intent recall
  the phrase structurally misses.
- **The mandatory anchor supplies the precision.** Bare `cannes +lions` is ~half
  off (sports teams — "Prague Lions", "Lions Indomptables"; short films named
  *Lions*); the `+(advertising | …)` qualifier requires an in-domain term too,
  filtering the collisions out. Add more `+`-qualifiers to tighten further.

Verified live: this group added ~815 distinct channels (~80% on-intent) beyond the
`"cannes lions"` phrase. Always judge it on the **residual** — probe
`<group> -"<core phrase>"` and read *those* samples — since `_score` otherwise
floats the already-covered docs to the top and hides what's actually new.

> `simple_query_string -polluter` only works with `default_operator: "and"` set
> explicitly (otherwise the phrase goes optional and the count balloons —
> `"FIRE movement" -"Free Fire"` jumped to 603M docs). Prefer `bool` `must_not`.

## Recency / active content (all-time totals, but verify it's alive)

Headline counts are **all-time** (full term coverage). To confirm a term still
brings back *active* content rather than a stale back-catalogue, layer a recency
agg on the **same** query (no extra round-trip) — this is what `probe.py` emits
unless `--no-recency`:

- **Topic** (`article` docs have `publication_date`): a `recent_window` filter-agg
  over `publication_date >= today-N months` with a nested `cardinality` on
  `channel.id` → `recent_documents` / `recent_channels`. Read `recent_channels`
  (robust to a single channel's volume), not just the share.
- **Channel** (`channel` docs have **no date**): use `posts_per_90_days > 0` (the
  "posting now" signal) with a `cardinality` on `id` → `active_channels`.
  `is_active` is ~94% true globally, so it's only a weak floor, not the signal.

A keyword is **stale** only when its recent/active distinct-channel count is below
a small floor **AND** its share of all-time channels is below a threshold — so a
high-volume evergreen term (large recent reach, small share, e.g. `annuity`:
2,254 recent channels at 8%) is never mislabeled.

## Counts + samples in ONE query (no highlight)

`track_total_hits: true` gives the full count even with a small `size`; `size: N`
returns the top-N (by `_score`) docs. The probe scripts don't request ES
`highlight` — they return the `_source` fields needed for validation instead
(title/summary for articles; `name`/`ai.topic_descriptions` for channels).
Highlight fragments are available behind the `--highlight` flag; its behaviour
(output shape, transcript XML fragments, billing) is documented once under
*`tl db es`* in the `tl` skill's `SKILL.md`. The response is `{results: [...rows with _source flattened...],
total: <int>, usage: {...}}`.

```json
{"size": 5, "track_total_hits": true,
 "_source": ["title", "summary", "channel.id"],
 "query": {"bool": {"filter": [{"term": {"doc_type": "article"}}],
                    "must": [{"match_phrase": {"title": "tiktok shop"}}]}}}
```

`probe.py` builds exactly this per candidate.

## Relevance signals

- **Title ≫ transcript.** A match in `title` almost always means the video is
  about the topic; a single transcript mention is often incidental. Boost
  `title^3` and weight title matches heavily when judging. (A real example: a doc
  looked off-topic from a transcript snippet but was clearly on-topic once you
  saw the term was in the title.)
- **Mention frequency / position.** `transcript` is timed XML — if a term appears
  many times, or early, the video is more likely centrally about it than if it
  appears once in passing. Parsing this is optional/heavier; the count + title
  signal usually suffices for round one.
- **No stemming.** The analyzer is `standard` (tokenize + lowercase, no stemmer),
  so `bitcoin` and `bitcoins` are distinct terms. Expand inflections/plurals on
  the query side with `should`/`|`. The same analyzer makes spacing/punctuation
  variants distinct too (`fable5` ≠ `fable 5`) — see *Tokenization* above.

## Channel-fit validation

Article docs carry no channel descriptive text — only `channel.id`. To check
whether a *channel* (not just one video) is about a topic, query channel docs by
id and read `ai.topic_descriptions` / `ai.description`:

```json
{"query": {"bool": {"filter": [{"term": {"doc_type": "channel"}},
                               {"terms": {"id": [3973, 478986]}}]}},
 "_source": ["name", "ai.topic_descriptions", "ai.description"]}
```

Or run the probe with `--level channel` to search channel docs directly.

**Channel-field probes approximate the delivered filter, they don't equal it.**
A delivered filter keyword targeting a channel content field
(`channel_topic_description`, `channel_description`, …) matches **articles**
whose channel matches — its result counts are videos. Raw probes can't run
that parent-child join (the join query types are not accepted), so you probe
the channel docs directly and count **channels**. Same signal, different
units: validate the keyword's sense on channel-doc samples, but don't expect
the probe count to match the report's row count on channel-field keywords.

## Cost

Each probe is one `tl db es` query — roughly 1–2 credits. One query per
candidate (count + a few samples). Run `tl describe show db` for the live rate.
