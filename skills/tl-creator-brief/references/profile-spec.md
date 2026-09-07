# Output specification

## One ledger, one page

A build leaves exactly two kinds of deliverable in `tl-creator-profiles/`
under the invocation directory (create it if missing; never write inside the
skill's own directory):

- **`<channel_id>-facts.jsonl`** — the machine ledger, ONE file per creator.
  Its first line is the **meta record** (what the build was: when, over which
  videos, how much it read, what it found — the record a later run reads to
  decide whether the ledger is fresh enough to reuse); every following line
  is one fact. This is **the stable interface** other skills, personas and
  CONNECT runs consume, and the thing a second brand reuses. Full
  thoroughness lives here. Every reader and writer goes through
  `scripts/ledger_io.py` (`read_ledger` → `(meta, facts)`, `write_ledger`);
  nothing in the repo iterates a ledger's lines raw, so the header is never
  mistaken for a fact.
- **`<channel_id>-<brand_id>-connections.html`** — the one human page, per
  brand: who the creator is (rendered from the ledger), what the brand is,
  the ranked connections between them, and the ledger's own honesty strip.
  Rendered by `scripts/build_html.py` from a working markdown source that
  lives in the corpus directory, never in the deliverable directory.

There is no pure-profile human surface: PROFILE mode's human output is the
run report in chat (the funnel, the counts, the selected facts as a short
list, and the ledger path). There is no `<channel_id>-meta.json`, no
`<channel_id>-profile-ledger.html`, and no markdown twin of the page in
`tl-creator-profiles/`. Working files — the passage store, batches, returns,
clusters, merge input and decisions, the verified working facts, the
connections markdown — live under `tl-creator-profiles/.corpus/<channel_id>/`
and are the cache the reuse path depends on.

All outputs are files, never chat messages: chat scrolls away and these are
made to be picked up later. Return the paths, and name files from resolved
IDs only — IDs are exact, names are fuzzy and change on rebrands, and
deterministic names let a re-run overwrite its own output.

## The machine ledger: `<channel_id>-facts.jsonl`

Line 1 is the meta record (next section). Then one JSON object per line, only
quote-verified and judgment-passed facts, written by `scripts/merge_pass.py
expand` from the merge pass's decisions and the clusters (never typed by a
model), verified by `scripts/verify_quotes.py`, and copied into the ledger
by `scripts/ledger_meta.py write --from`:

```json
{"fact_id": "f012",
 "claim": "adopted a rescue dog named Luna",
 "domain": "pets",
 "provenance": "transcript",
 "quote": "we finally adopted luna from the shelter last spring",
 "video": "48247:dQw4w9WgXcQ",
 "start": 512,
 "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=512s",
 "published": "2024-05-01",
 "recurrence": 3,
 "confidence": "confirmed",
 "sensitivity": "none",
 "sensitive": false,
 "superseded_by": null,
 "selected": true,
 "members": ["dQw4w9WgXcQ:497", "9bZkp7q19f0:1210", "3JZ_D3ELwOQ:88"]}
```

- `domain`: one of `origin`, `family`, `pets`, `home`, `work`, `money`,
  `health`, `habits`, `tastes`, `beliefs`, `relationships`, `other`.
- `provenance`: `transcript` | `social` | `web`, per `evidence-rules.md` —
  lanes never masquerade. `social`/`web` facts carry `source_url` and
  `seen_date` instead of `quote`/`video`/`start`/`url`.
- `quote`: verbatim, in the source language; exact-verified by
  `scripts/verify_quotes.py` before it lands here. A non-English quote may
  carry a `gloss` (English translation, labelled — never the quote itself).
- `recurrence`: distinct videos/sources, never snippet count.
- `confidence`: `confirmed` | `unconfirmed`, per `evidence-rules.md`
  (dropped facts never enter the ledger; their count goes in the run
  report). When the opt-in socials lane did not run there are no
  `social`/`web` facts, so cross-lane corroboration is unavailable: a fact
  reaches `confirmed` only on a transcript-side rule (solo format, or a
  host-anchored window), never by corroboration. That is a ceiling on the
  evidence, not a defect in the run — say so in the run report.
- `sensitivity`: `none` | `lifestyle` | `clinical` | `children` | `location`,
  per `evidence-rules.md`. `sensitive` is the **derived** boolean — true
  exactly for the withheld tiers (`clinical`, `children`, `location`) — kept
  so readers written against the old flag keep working. The tier is the fact;
  never set the boolean independently of it. Every fact carries the tier;
  the ledger view renders it as a badge and tallies it.
- `superseded_by`: the `fact_id` of the newer fact when latest-wins applies;
  superseded facts stay in the ledger as history.
- `selected`: true on at most 20 facts. The merge pass proposes the picks
  (confirmed, recurring, cross-lane-corroborated, connection-fertile); the
  expand script owns the final set across the whole active ledger — it fills
  to 20 by recurrence and confidence and trims past 20 the same way, so a
  refresh never leaves 40 selected or none. The connections page's "who they
  are" section takes `selected` facts first, then the most recurring.
- `members`: the passage keys (`<video_id>:<window start>`) the fact was
  built from — its identity across rounds. A refresh matches re-clustered
  passages to existing facts by these keys (`transcript-mining.md`, Layer 4),
  never by `start`, which the verifier rewrites to the located cue.
- `gloss`: optional, on a non-English quote only — the labelled English
  translation, never the quote itself.

CONNECT loads this file, not any markdown.

## The meta record — line 1 of the ledger

```json
{"schema": "tl-creator-meta/v2",
 "channel_id": 123456,
 "channel_name": "…",
 "generated_at": "2026-09-02",
 "corpus_window": ["2016-03-01", "2026-08-20"],
 "coverage": {"videos_with_transcript": 287, "videos_matched": 141,
              "passages": 2252, "windows_judged": 500, "gems": 310,
              "facts": 91},
 "format": "solo",
 "format_evidence": "fp density 41/1k words median; 2 videos with interview markers",
 "lanes": "transcripts",
 "context": {"social_links": ["https://instagram.com/…"],
             "second_channel_candidates": [{"name": "… Clips", "id": 123457}]},
 "latest_video_date": "2026-08-29",
 "rounds": 1,
 "facts_file": "123456-facts.jsonl",
 "credits_spent": 1840}
```

Written by `scripts/ledger_meta.py write`, never by hand; readers know it by
its `schema` (`tl-creator-meta/…`) — a ledger whose first line carries no
such record is incomplete and the reuse check says `build`.

```bash
# fresh build or refresh: verified working facts → the ledger, header first
python3 scripts/ledger_meta.py write --channel <id> \
  --from tl-creator-profiles/.corpus/<id>/facts.jsonl.verified.jsonl \
  --channel-name "…" --format <label> --format-evidence "…" \
  --context <channel_context.json> [--lanes transcripts+socials] [--rounds N]
# re-header an existing ledger (recount only)
python3 scripts/ledger_meta.py write --channel <id>
```

`--from` refuses (exit 2) if any transcript fact's `verify.match` is not
`exact` — a partial match never publishes — and strips the `verify` object
on the way in.

- `corpus_window`: earliest and latest publication date among the videos
  whose passages were stored — the span the ledger can speak for.
- `coverage`: `videos_with_transcript` (the channel's transcript-bearing
  uploads at fetch time), `videos_matched` (videos a cue passage came from),
  `passages` (windows fetched across all rounds), `windows_judged` (windows
  an extractor gave a verdict on), `gems`, `facts` (fact lines in the
  ledger, header excluded). Every number is counted from the build's own
  files; a count it could not derive is 0 and the file it needed is named in
  `missing`.
- `format`: `solo` | `interview` | `multi_host` | `faceless_scripted`, with
  its evidence — the label the format call produced, passed in on `write`.
- `lanes`: `transcripts` or `transcripts+socials` — which creator-source
  lanes built the ledger. The reuse check compares it with what the current
  run asks for.
- `context`: the linked platforms and sibling-channel candidates from
  `channel_context.py` (`write --context <file>`), so the page can list
  them: each platform as read or "linked but unread", each sibling as
  "not mined".
- `latest_video_date`: the channel's newest upload when the build fetched.
  The reuse check counts uploads after it.
- On a refresh, `write` carries `channel_name`, `format`, `format_evidence`,
  `lanes`, `context` and `credits_spent` over from the existing header unless
  they are passed again; only the counts are recomputed.
- `rounds`: extraction rounds so far (default: one per fetch summary in the
  corpus directory). An incremental refresh is round `rounds + 1`.
- `credits_spent`: optional, when the run tallied it.

## Reuse — a found ledger wins, with a freshness check

Every run, PROFILE or CONNECT, starts with one command:

```bash
python3 scripts/ledger_meta.py check --channel <id> [--lanes transcripts+socials] \
  [--rebuild] [--no-refresh] [--max-new-videos 5] [--max-age-days 60]
```

When `<channel_id>-facts.jsonl` exists with its meta header it prints one
announcement line, which the run report repeats verbatim —

> Found a ledger for Sydney Watson built 2026-09-01 over 2016-03 → 2026-08-20,
> 91 facts. 3 videos uploaded since.

— and a JSON decision. The uploads count is one cheap index count after
`meta.latest_video_date`. The rule:

- **`reuse`** — at most 5 uploads since and the ledger is at most 60 days
  old (`--max-new-videos`, `--max-age-days`): use the ledger as is. CONNECT
  goes straight to the brand read; PROFILE re-renders the ledger view.
- **`refresh`** — more uploads than that, or an older ledger, or the count
  failed, or the run asks for the socials lane and the ledger was built from
  transcripts only (a ledger that read socials covers a transcripts-only
  request; the reverse does not): run ONE incremental round (SKILL.md, "Incremental refresh") —
  fetch with `--round N --since <latest_video_date> --exclude
  classified.jsonl`, extract only the new
  batches, assemble with `--append`, re-cluster, `merge_pass.py prepare
  --existing --state` (only genuinely new clusters reach the agent), expand,
  verify, `ledger_meta.py write --from … --rounds N`. Cost scales with the new uploads, not the
  corpus.
- **`build`** — no ledger, a ledger without the meta header (a v1/v2
  profile, or a legacy facts + sidecar `meta.json` pair that predates the
  single-file ledger), or `--rebuild`: full build.

`--rebuild` forces a full build; `--no-refresh` forces reuse as is, whatever
is new. Never reuse silently — the announcement line is the user's notice
that the ledger predates today's uploads — and never refuse to rebuild.

## Mode B: the connection map source

The connection pass writes a ranked connection map as a working markdown
file, `tl-creator-profiles/.corpus/<channel_id>/connections-<brand_id>.md`
— the source the page renders from, never a deliverable itself.
Frontmatter:

```yaml
---
schema: tl-creator-connections/v2
channel_id: 123456
channel_name: "…"
brand_id: 50485
brand_name: "…"
facts_file: 123456-facts.jsonl
brand_read_date: 2026-09-02
---
```

The sections, in this order. The renderer reads the order from the file.

**`## About <creator name>`** — two or three sentences on who they are, in
prose, drawn from the ledger. Not a fact list: the renderer has the ledger and
renders the honesty strip itself. This leads the page because the reader needs
to know whose brief they are holding.

**`## Thesis`** — three or four sentences on why these two fit. The core of
the page, and the part the reader acts on. It sits **above** the brand
introduction deliberately: the argument first, the background after.

**`## About <brand name>`** — two or three neutral sentences on what the brand
is: positioning, product lines, stated audience, written from the **web and
brand-social lanes and TL's public category / product description only**.
Never from sponsorship patterns, never a price, never another client's data:
this paragraph is forwarded with the page. The renderer shows it as prose, not
as a card.

**One `## ` section per connection**, strongest first — the section order IS
the ranking and the page numbers them — with the type as a bold tag on the
heading line: `## Runs on four hours of sleep — **direct**`. Each section
holds, in this order:

1. **The creator's own words** (or the social/web fact, labelled as such) —
   verbatim, timestamped, from the ledger, as a `>` quote with its `&t=`
   link. The quote is the card's evidence and appears nowhere else on the
   page, so it carries the connection on its own: pick the line that makes
   the fit obvious, not the longest one. The renderer requires a timestamped
   link INSIDE the blockquote, so put the attribution on a `>` continuation
   line, not below the quote.
2. **What the brand offers that meets it**, and which brand-read lane that
   came from (`[web]`, `[social: instagram]`, ad-read sample, sponsorship
   patterns).
3. **How this could be used** — one neutral line on the use case.
4. **Sample read** — ONE short illustrative line, optional, showing how the
   angle could sound in the creator's own register. Labelled as an
   illustration, never as approved copy, never a full script or a CTA. One
   per connection at most.
5. **Do** and **Do not** — one line each, the angle guidance in the form the
   reader can act on. "Do" names the framing that works; "Do not" names the
   specific way this angle goes wrong for this creator. Both concrete: "do
   not open on the licence terms, he will explain them better unscripted"
   beats "do not be too technical".

**`## Where this could go wrong`** — the honest mismatch, always last and
always present, even on a strong fit. What in the creator's material argues
against this brand, stated plainly. The renderer keeps it out of the numbered
connections so it can never be mistaken for an angle.

Types: **direct** (fact ↔ product), **adjacent** (lifestyle/context fit),
**category precedent** (the creator already does what the product enables,
from the confirm-only probe). Facts at sensitivity tier `children` or
`location` do not appear unless a human opted one in; `clinical` facts
appear only when the creator discusses them repeatedly (three or more
videos) or frames them as part of their own story, otherwise they too wait
for a human opt-in (`evidence-rules.md`). Beliefs are ordinary material.

If nothing honestly connects, the document has the About section and no
connection sections: a **no fit** verdict in prose, what was searched, and it
stops — a no-fit verdict is the deliverable, not a failure.

## The page

One deterministic template, never hand-written per run:

```bash
python3 scripts/build_html.py \
  --in tl-creator-profiles/.corpus/<id>/connections-<brand>.md \
  --facts tl-creator-profiles/<id>-facts.jsonl \
  [--out tl-creator-profiles/<id>-<brand>-connections.html]
```

`--out` defaults to `<facts dir>/<channel_id>-<brand_id>-connections.html`.
The meta record comes from the ledger's header (`--meta <file>` only fills
in for a legacy headerless ledger). Top to bottom:

1. **Header** — creator × brand, with the brand-read date and the ledger's
   build date.
2. **Who they are** — the markdown's `## About <creator>` prose, followed by
   the `selected` facts as a short readable run rather than a grid of
   domain-labelled subsections. Facts at tier `children` or `location` never
   enter this section; `clinical` and `lifestyle` facts appear with their tier
   badge; superseded facts stay in the ledger only.
3. **The thesis** — the markdown's `## Thesis` section, rendered as the
   page's lead block, above the brand. This is what the reader came for.
4. **About the brand** — the markdown's `## About <brand>` section, as prose.
5. **Connections** — one numbered card per connection section with its type
   badge, each carrying its quote, what the brand offers, the use case, the
   optional labelled sample read, and the do / do-not pair. Provenance labels
   in the markdown are kept: a connection map names its lanes. A no-fit map
   renders its verdict as prose, no cards.
6. **Where this could go wrong** — its own block after the cards, never
   numbered among them, so an honest mismatch is never mistaken for an angle.
7. **About this ledger** — the honesty strip the old ledger view carried:
   fact count with confidence tallies; sensitivity tiers with the count
   withheld from angles (`clinical` counts as withheld only below three
   videos, per `evidence-rules.md`); the coverage line —
   `<matched>/<with transcript> transcript videos matched, <N> passages
   judged — absence is not evidence`; format, corpus window, rounds, lanes;
   and the other channels and platforms from `context` (each platform read
   or "linked but unread", each sibling "not mined").

When the host supports publishing artifacts, publish the page so the user
gets a link; the files in `tl-creator-profiles/` are the durable copies.

## Never in any file

Prices, costs, rate cards, deal terms, other clients' internal data, or
performance grades. Every output is built to be forwarded.

Ad copy is bounded rather than banned: ONE short, clearly labelled **Sample
read** line per connection is allowed, so the reader can see the angle land.
Full scripts, CTA wording, alternate versions, and anything presented as
approved copy are not.
