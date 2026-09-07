---
name: tl-keyword-research
tl-blurb: build & validate keyword filter sets + context-validated channels
description: |
  Turn a topic into a *validated* content filter and the channels it selects.
  Invoke whenever the user wants to find videos or channels by what they are
  about — topics, concepts, niches, not IDs or names: "a group/set of keywords
  for <topic>", "keywords that represent <topic> content", "find content/channels
  about <topic>", "find investing channels" — or when you'd otherwise
  hand-compose a `tl db es` content search. It expands the topic into candidate
  keywords (with a gated web lookup for post-cutoff entities), probes
  Elasticsearch for each (counts + samples), validates matches against the
  user's stated intent, refines a boolean filter over ≥3 rounds, and delivers
  a keyword-group filter set + a clickable report link + the results the user
  chose: trend data at the video level (matching uploads + prevalence), or
  channel targets classified by topic intensity (core / recurring / one-off)
  and context-validated with cheap agents, or both. It ASKS which deliverable
  and which run mode (quick single pass vs deep ≥3-round refinement) the user
  wants whenever the request doesn't say — never a silent default;
  `autonomous` / `--auto` means deep + both deliverables with every pause
  skipped. Keyword-distribution output (counts per keyword) is OPT-IN — only
  when the user explicitly asks for "keyword counts / distribution / how
  common is X". Also invoke for HELP asks about this skill — "help", "how
  does keyword research work", "what are my options", "describe this skill" —
  answered free from the built-in guide, no queries run.
---

# tl-keyword-research — topic → validated filter set + the results you choose

Turn a fuzzy topic into a **precise, validated content filter** over our data —
and whichever results the user actually wants from it: the trend picture at
the video level, channel targets classified by how much they cover the topic,
or both. The value is not brainstorming synonyms (anyone can do that, and a
free YouTube search does it too) — it's writing real **Boolean queries over
the right fields**, then **validating that the matches are actually on-topic**
against our corpus. That combination is what our data makes possible and a
plain keyword list does not.

The deliverable has one mandatory layer and two optional layers **the user
chooses between** (never assume — see *Choosing the path & deliverable*):

1. **The topic itself (always).** A filter set of keyword groups + a
   **clickable report link** that opens the platform with the filter applied
   (+ a persist config) — `build_report.py`. The topic and its prevalence
   (matching videos, distinct channels) is a complete deliverable on its own —
   a trend journalist writing "how big is this on YouTube" needs exactly this
   and may not want a channel list at all.
2. **Trend data (video level).** The matching uploads — sortable by
   date/views, windowed — via `search_videos.py`. A creator who covered the
   topic once counts here.
3. **Channel targets.** Channels classified by their **relationship to the
   topic** — core / recurring / occasional / one-off (`search_channels.py
   --intensity`) — then ranked, sense-checked, and flagged for sponsorability.
   Never assume "channels about the topic" means only channels *entirely*
   about it: for niche topics there may be almost none, and the real
   sponsorship market is channels that return to the topic repeatedly.

The **canonical artifact is the keyword-group filter set**: each group is a
self-contained boolean query (an exclusion can be scoped to its own arm —
`("mythos 5" | mythos5) -keto` — which a flat CNF cannot express). The rendered
boolean **expression** is recorded alongside for provenance and re-runs
verbatim via `search_channels.py --group` / `search_videos.py --group`.

> Read `references/elasticsearch-content-search.md` before writing queries — it
> covers the article-vs-channel doc types, the content fields (and ES's
> **legacy** channel field names), `simple_query_string` Boolean syntax,
> tokenization, the report-link keyword grammar, and why we return `_source`
> samples (ES `highlight` fragments are only returned with `--highlight`).

`<SKILL_DIR>` below is this skill's directory (the one holding `SKILL.md`).

## When to invoke / skip

Invoke when the user wants **videos or channels by content** (topics, concepts,
niches), gives seed keywords or an NL phrase to widen into a content filter, or
you're about to hand-compose a `tl db es` content search — delegate here first.

Skip when:
- The user has explicit channel/brand IDs or names → `tl channels find` / `tl brands find`.
- Intent maps cleanly to a curated recommender tag (e.g. "Cooking channels") →
  `tl recommender top-channels "<tag>"`. Don't re-discover curated tags by text match.

## Help mode — explain yourself on request, for free

When the user asks for help, what this skill does, how it works, what the
options/parameters are, or to "describe the skill" ("help", "how does this
work", "what can you do here", "what are my options", "explain the flow"):
**run nothing** — no queries, no scripts, zero credits. Read
`references/help.md` and present it clearly, sized to what they asked (the
full guide for "explain how this works"; just the relevant slice for "what
sorting options are there?"). Close by offering to start a run. Mid-run
option questions ("what does deep mean?", "what's the recurring tier?") get
the same treatment — answer from the guide, then continue where you paused.

## Choosing the path & deliverable — the user picks, never a silent default

Two choices shape the run, and both belong to the USER:

**Path** (how hard to refine):
- **Quick** — one expand → probe → validate → deliver pass, no refinement
  rounds (≈ 10–20 credits). Even here, tokenization variants, entity-family
  expansion, and sample validation are mandatory — speed never excuses a
  shallow synonym list.
- **Deep** — the full pipeline: ≥3 refinement rounds (Stage 4) plus
  materialization (Stage 5) (≈ 60–120 credits depending on how much gets
  materialized and validated).

**Deliverable** (what comes back besides the filter + link, which are always
included):
- **Trend data** — the matching videos + prevalence numbers (video level).
- **Channel targets** — channels tiered by topic intensity, sense-checked,
  sponsorability-flagged.
- **Both.**

How to pick:

1. **The request names it → run it, no questions.** "quick"/"fast"/"just a
   starting set" → quick; "deep"/"thorough"/"take your time" → deep. "trend",
   "prevalence", "how big is this", "who's talking about it right now" →
   trend data; "channels to sponsor", "targets", "channels for [client]" →
   channel targets.
2. **Otherwise, ask before starting — ONE combined question** covering
   whichever of the two is unstated, with the trade in a line each (include
   the rough credit costs above). Don't silently default: guessing quick
   hides what the skill can do; guessing deep spends credits the user never
   asked for; and assuming the user wants a channel list when they wanted
   trend data (or vice versa) answers a question they didn't ask.
3. **Exception:** `autonomous` / `--auto` / "don't stop to ask" with no named
   path/deliverable means the **deep path, both deliverables, every pause
   skipped** — the user has asked for zero questions.

**Autonomy within the deep path:** at any point the user can say run
autonomously / without pausing (or invoke with `autonomous` / `--auto`) —
keep refining without checkpoints per Stage 4. The preference holds for the
rest of the session unless revoked.

## The pipeline (you orchestrate; scripts + cheap agents do the work)

| Stage | What happens | Tooling |
|---|---|---|
| 0 Set up | intent, deliverable, operator, breadth judgment, scope; gated web entity resolution | `keyword-entity-resolver` agent + `expand_entities.py` |
| 1 Expand | deep, creative candidate generation | you |
| 2 Probe | counts + samples per candidate | `probe.py` |
| 3 Validate keywords | on-intent check per keyword; scope calls to the user | inline / `select_keywords.py` + `keyword-relevance-validator` agent |
| 4 Refine | ≥3 rounds of boolean composition, fitness, backtracking | `probe.py --mode sqs`, `search_channels.py` |
| 5 Materialize | intensity triage, then trend data and/or channel targets, validated | `search_channels.py --intensity` · `search_videos.py --group` · `search_channels.py --group` → `fetch_context.py` → `keyword-context-classifier` agent |
| 6 Deliver | filter set + report link + the chosen deliverables; offer to save | `build_report.py`, `tl-save-report` |

**Narrate the run.** The user must always know what's happening: one line at
every stage transition — what you're doing, why, and the approximate credits
spent so far (*"Stage 2 — probing 18 candidates, one query each (≈25 credits
so far)"*). Never run a silent stage, and surface every drop/prune/failure as
it happens, not just at the end.

**Spend tokens as carefully as credits.** Deterministic work belongs in the
scripts (zero model tokens — tokenization variants, tier math, pruning, link
building are all scripted). Judgment calls go to the **cheapest capable
model** via the bundled agents: `keyword-relevance-validator` and
`keyword-context-classifier` run on Haiku; only the web-reading
`keyword-entity-resolver` runs on Sonnet. Don't do in your own context what a
script or a Haiku batch can do.

### Stage 0 — Set up: intent, deliverable, operator, breadth, scope

Keep the user's own sentence **verbatim as the intent** — it's the yardstick
every validation judges against. Then state your assumptions so the user can
correct them:

- **Deliverable** — trend data (videos), channel targets, or both: the user's
  choice from *Choosing the path & deliverable*. Either way, the research
  runs at **topic level** (probe `--level topic` — videos are where the
  keywords live); channel-doc probes (`--level channel`) remain a tool for
  channel-fit checks and the existence probe below. Two calibration facts to
  establish early:
  - **Does a channel-identity version of this topic even exist?** One cheap
    channel-doc probe on the core term (`probe.py --level channel --samples 3
    "<core>"`) answers it: a tiny distinct-channel count means essentially no
    channel is *about* this topic — say so, and set expectations that the
    channel deliverable will be built from **recurring-coverage** channels
    (Stage 5's intensity tiers), not identity matches. Never return a
    near-empty "core" list as if it were the whole answer, and never pad it.
  - **A one-off mention is not a target.** For trend math every matching
    upload counts; for sponsorship targeting a channel that mentioned the
    topic once usually doesn't. The intensity tiers keep these apart —
    don't collapse them.
- **Operator.** Default `OR` (union of a niche's facets); `AND` only for a true
  intersection ("both X and Y", composite nouns). Under AND, keep candidates
  *inside* the intersection — don't broaden each component independently.
- **Breadth judgment — say it back.** There is no universal right result size.
  "Underwater basket weaving" returning a few dozen channels is a complete
  answer; "basketball" returning a few dozen is a miss. If the topic reads
  niche, offer to broaden; if broad, offer to narrow to a sub-area. This
  judgment is what Stage 4's coverage check measures against. The probe's
  per-keyword **`channels`** count (distinct channels) is the measuring stick.
- **Scope — state it.** Everything is scoped to **YouTube uploads** and, at
  video level, **longform** by default; offer `--content-type all` (or
  `short`/`live`) if the user wants more.

**Gated web entity resolution — only when memory can't.** Expansion is the one
input drawn purely from model knowledge, so it fails where knowledge does (the
classic miss: expanding a launch to the company name and never generating the
product, its version, or its **sibling**). Trigger a web lookup ONLY when the
topic names an entity that **postdates the knowledge cutoff**, was **recently
renamed/rebranded**, or is a **live trend / insider-jargon-dense niche** you
cannot enumerate from memory. For a topic you know cold (cooking, retirement
planning, basketball), skip it. When the gate fires, say so and why, then
delegate to the **`keyword-entity-resolver`** sub-agent (Agent tool,
`subagent_type: keyword-entity-resolver`) so the noisy page text never enters
your context — it returns a compact JSON of *names* only:

```
topic: <the topic>
intent: <one sentence — what the user actually wants>
level: topic|channel
known: ["...", "..."]    # your own candidates so far — it fills the gaps
```

Turn its reply into probe-ready candidates with `expand_entities.py` — it
generates the tokenization spelling variants, folds each family into one
boolean group, pairs rename aliases into `(old | new)`, and dedupes against
`--existing`:

```bash
python3 <SKILL_DIR>/scripts/expand_entities.py --probe-batch \
  --existing "cannes lions" "advertising awards" < resolver.json \
  | python3 <SKILL_DIR>/scripts/probe.py --samples 5
```

Non-batch mode also returns `collisions` (a polluter watch-list for the Stage 4
NOT-rescue) and `hashtags` (for optional `hashtags` field targeting). **Web is
for entity NAMES, never synonyms / breadth / validation** — breadth is the
distinct-`channels` count over our index, relevance is judged only from this
index's samples, and every web-sourced name earns its place through the same
probe + validation as any other candidate (no benefit of the doubt; a
hallucinated name probes to `count: 0` and drops).

### Stage 1 — Expand: go wide, go deep, go creative

A shallow synonym list is the #1 failure mode. Coverage is won or lost here.

- **Decompose the topic into facets and expand each one.** An event/launch:
  lead-up → the launch → product specifics → **sibling & ecosystem products** →
  reactions → fallout → regulatory. A niche: sub-areas, tools, personas, jargon.
- **Expand every named entity into its whole family** — company, product line,
  model/version, codename, **sibling shipped alongside**. (Live miss: searching
  `anthropic` for the Claude Fable 5 launch missed `fable 5` (713 ch),
  `claude mythos` (620 ch — the sibling), and `mythos 5` (433 ch) — most of the
  topic.)
- **Generate tokenization variants for every name/number.** The index tokenizes
  `fable5`, `fable 5`/`fable-5`, and `fable five` as *different terms that miss
  each other's documents* — probe each spelling (solid / spaced / spelled-out /
  hashtag-handle) as its own candidate. No stemming either: expand
  inflections/plurals yourself (`invest`/`investing`/`investments` are distinct).
- **Candidates can be boolean groups, not just phrases** — a self-contained
  `simple_query_string` like `("fable 5" | fable5 | "claude fable")`, or a
  family carrying its own scoped de-noising `("mythos 5" | mythos5) -keto`.
- **Scale the candidate count to the topic.** A one-line niche: ~8–15. A
  multi-facet event: **30–50**, covering every family and its spelling variants.

Rules:
- **Never add the bare over-broad root** — single word *or* generic
  collocation. For "TikTok Shop" don't add `tiktok`; `supply chain risk` alone
  matches its own broad domain unless entity-qualified.
- **Mine the data, don't only brainstorm.** After the first probe, read the
  on-topic samples for recurring terms you didn't think of and re-probe them.
  This data-driven discovery is what a synonym list can't do.
- **Brands**: when the topic IS an entity/event, full entity-family expansion is
  mandatory (see above). For a generic topic, don't drift into naming specific
  brands unless the seeds contain one (then adjacent brands in the category are
  fine). **No specific channel names** (`tl channels find`). No random padding.

### Stage 2 — Probe (`probe.py`)

One ES query per candidate → counts + samples for validation:

```bash
# topic level (videos): phrase candidates
python3 <SKILL_DIR>/scripts/probe.py --level topic \
  "tiktok shop" "selling on tiktok" "tiktok affiliate"

# Boolean candidates via simple_query_string (default_operator=and is set for you)
python3 <SKILL_DIR>/scripts/probe.py --mode sqs \
  '"tiktok shop" +(marketing|affiliate|ecommerce)' '("mythos 5" | mythos5) -keto'

# channel level (whole channels); JSON array on stdin also works
python3 <SKILL_DIR>/scripts/probe.py --level channel "cooking" "baking"
```

Output: `{operator, level, fields, scope, keywords:[{keyword, count, documents,
channels, subsumed_by, samples, …recency}], dropped, failed, recency}`.

- **Two counts, always.** `documents` = raw match total; **`channels`** =
  DISTINCT channels reached (cardinality agg). Channel docs are duplicated
  across quarterly indexes, so at channel level `documents` is meaningless —
  `count` (the ranking headline) is documents at topic level, channels at
  channel level. Judge niche size by **`channels`**.
- **Samples are collapsed to distinct channels**, so one prolific channel can't
  flood the slots — that's what makes validation meaningful. Topic samples:
  `title`/`summary` (+ `channel_id`, `category`, `url`); channel samples:
  `name`/`topic` (+ `channel_description`, `channel_id`).
- **Scope is always-on** (YouTube `format` 4; longform at topic level unless
  `--content-type all|short|live`) and echoed under `scope` — tell the user.
- **Recency rides the same query** (no extra credits): topic level
  `recent_documents`/`recent_channels` over `--recency-months` (default 12);
  channel level `active_channels` (`posts_per_90_days > 0` — channel docs have
  no date). Each keyword carries `stale` (absolute-first rule, so high-volume
  evergreens are never mislabeled) and `thin` (below floor but proportionally
  alive). Annotations only — nothing is dropped here.
- **`failed` lists candidates whose probe errored/timed out** — retry those
  individually; they are not dropped keywords.
- `subsumed_by` is informational (a broader phrase is present); pruning happens
  after validation, in `build_report.py`, or the broad root would always win.
- `--since/--until` are topic-only (channel docs have no publication date).

SQS power for candidates (`--mode sqs`): trailing `*` catches inflections,
`~1` absorbs typos, `"a b"~2` catches near-phrases — **research-only**: the
report filter set can't hold `*`/`~` (see the reference), so enumerate the
surviving variants before delivery.

### Stage 3 — Validate keywords against the intent

This fixes "the word is there but the topic isn't" — the step that separates
this skill from a free YouTube search.

**Inline (≤ ~15 candidates):** read each keyword's `samples` against the
verbatim intent. Drop off-intent keywords, `count: 0`, and redundant
`subsumed_by` duplicates.

**At scale (the `keyword-relevance-validator` sub-agent):**

```bash
python3 <SKILL_DIR>/scripts/probe.py "tiktok shop" "selling on tiktok" "tiktok" > /tmp/kw_probe.json
python3 <SKILL_DIR>/scripts/select_keywords.py --emit-batch < /tmp/kw_probe.json > /tmp/kw_batch.json
```

Send the batch (prepending one line — `intent: <one sentence>`) to the
`keyword-relevance-validator` agent (Agent tool), save the strict reply
`[{i,relevant}]`, optionally run a second pass for a majority vote, then:

```bash
python3 <SKILL_DIR>/scripts/select_keywords.py --apply /tmp/verdict1.json [/tmp/verdict2.json] < /tmp/kw_probe.json
```

Keeps a keyword only when a strict majority of its samples are on-topic, lists
`dropped` with reasons, surfaces `candidate_channels`/`candidate_videos` from
validated samples, and emits `groups` for Stage 6. **Completeness is checked**:
if the verdict doesn't cover every batch sample, `--apply` fails and lists the
missing indices — re-send just those samples to a fresh validator and merge
(cheap models silently drop the tail of long lists; never assume a batch came
back whole).

**Ask the user on scope, not relevance.** Two different questions hide here:
*relevance* ("is this term's match on-topic?" — you judge from samples) and
*scope* ("is this sub-topic part of what the user wants?" — the user's call).
When a candidate family is on-relevance but its scope is a genuine judgment —
a sibling product, an adjacent model, a broad policy framing — surface 2–4
representative sample snippets and ask in or out. (Live: *"Mythos 5 is the
sibling model launched alongside Fable 5 — include it? Opus 4.8 is a different
model — count it as fallout?"*) Keep it to the few families that swing the
result.

### Stage 4 — Refine: ≥3 rounds of boolean composition (the heart)

You **research the topic by composing and recomposing boolean queries** —
narrowing, expanding, and backtracking based on what the corpus shows. This is
a search through query-space, not a single pass. **Run at least 3 rounds**
(three is the floor, not a cap — don't stop earlier even if round 1 looks
good). Each round:

1. **Compose/recompose.** Round 1 is usually the validated OR-union. Later
   rounds add structure with the moves below.
2. **Measure.** Probe changed groups (`probe.py --mode sqs`) and the union's
   real coverage — the OR-union as ONE sqs candidate at channel level (keyword
   sets overlap; per-keyword `channels` don't sum):
   ```bash
   python3 <SKILL_DIR>/scripts/probe.py --level channel --mode sqs \
     '"retirement planning" | "pension planning" | annuities | 401k'
   ```
   (A big union over `transcript` can time out — measure coverage on
   `--fields title,summary`, or chunk the union.)
3. **Validate** what changed (Stage 3 machinery; 15–20 samples for noise-rate
   audits — 5 is too few to estimate a noise rate).
4. **Score fitness** and write it down: share of on-topic samples, whether
   noise clusters on one confusable sense, coverage vs the Stage 0 breadth
   judgment, what the round changed.
5. **Decide the move** and record (query, fitness, decision) so you can
   backtrack. **Backtracking is expected, not failure** — when a move reduced
   fitness, discard it, return to the recorded query, try a different axis.
6. **Keep a running validated set** across rounds (dedupe by `channel_id`):
   carry forward keywords and channels confirmed on-topic even as the query
   shifts. If the final filter no longer selects some previously-validated
   channels, surface them separately rather than dropping them silently —
   losing a strong channel is itself a backtrack signal.

**The move set** (mechanics + verified numbers in the reference):

- **Narrow** a noisy set: add a required dimension (`+(marketing | affiliate)`),
  target a field, or exclude the bad sense.
- **Expand** a thin one: mine emergent keywords from on-topic samples and the
  channel validators' `adjacent_terms` (e.g. "tiktok shop" keeps surfacing
  `amazon`/`affiliate` → probe them). Auto-add only terms that validate
  on-intent; widening beyond the stated intent (e.g. retirement → general
  investing) is a **scope change — ask the user first**. If the topic is
  genuinely niche and mining runs dry, a small result is the correct answer —
  say so rather than padding with off-intent terms.
- **Judge marginal value on the residual, not the headline.** Before keeping a
  broad candidate, subtract what the core already catches: probe
  `<candidate> -"<core phrase>"` and read *those* samples (`_score` floats the
  relevant docs to the top and hides redundancy). **Don't dismiss a small clean
  residual** — ~20–50 genuinely-new on-intent channels earns a group; a report
  holds many groups at no performance cost.
- **NOT-rescue a polluted term — scoped to its own group.** When a term is
  on-intent but diluted, find the recurring token the off-intent docs share and
  the on-intent docs don't, and exclude it *inside that family's group*:
  `("FIRE movement") -"Free Fire"`. A whole-filter exclusion over-cuts (live:
  scoping `-openclaw` to its arm kept 51 on-topic docs a global exclude lost).
  **Guard against over-exclusion**: re-run the on-intent core with vs without —
  the count should barely move; a material drop means the token is shared —
  exclude the multi-word **phrase** instead (`-"film festival"` cut the Cannes
  core 4%; bare `-film` cut 24%).
- **AND-anchor a broad root.** A root too broad alone (`cannes`) is rescued by
  a mandatory anchor plus a domain OR-qualifier:
  `cannes +lions +(advertising | agency | campaign | "young lions") -"film festival"`.
  Non-adjacent AND reaches on-intent docs the phrase `"cannes lions"`
  structurally misses (live: +815 distinct channels, ~80% on-intent, surfacing
  the Young/Future/Media Lions competitions). Judge it on the residual.
- **Field-narrow rescue (title ≫ summary ≫ transcript).** `transcript` is by
  far the noisiest field, `title` the cleanest — a term too noisy corpus-wide
  can be "already very qualified" restricted to titles. Probe it per-field
  (`probe.py --fields title` or `--fields title,summary`); if the title-only
  samples are clean, keep the group **with per-group `content_fields`** in the
  deliverable (`{"text": "cannes +advertising", "content_fields": ["title"]}` — a plain
  two-word group is an adjacent *phrase*; use `+` for a true AND)
  instead of dropping it. Ranking uses the same knowledge: `search_channels.py`
  weights `title^4,summary^2,transcript^1`.
- **Flag stale keywords** from the probe's recency fields on the final set —
  exclude them from the suggested filter or keep with a visible STALE tag,
  never drop silently; `thin` niches are surfaced, not hidden.

**After round 3 (and every round thereafter) — checkpoint.** Present the
current validated set (the rendered expression, fitness, what changed), and
ask: accept · more rounds · adjust direction. If the user chooses more
rounds, **interview them about the intent behind the keywords** first —
which sense to include/exclude, audience/format, must-have sub-topics,
brands that should or must not count, reach/language/recency constraints —
then fold the answers into the groups and the validators' TOPIC/NOT lines.
**Autonomous mode** (the user opted out of pauses): skip checkpoints, still
run ≥3 rounds, stop when fitness stops improving (sane cap ~6 rounds), note
that you ran autonomously.

### Stage 5 — Materialize the chosen deliverables

Everything here takes the final filter verbatim via `--group`, so what you
deliver is exactly what the filter selects.

**Budget reserve — decide it before Stage 4, not after.** Materialization and
its validation must not be starved by refinement: reserve roughly one query
per channel you intend to context-validate (plus ~5 for the searches and
triage) before you start spending on refinement rounds. If the budget runs
out anyway, degrade EXPLICITLY, never silently: intensity triage (below) is
nearly free, and a title-level sense-check of the top results costs zero
extra queries — deliver that, clearly labeled **"not context-validated"**,
with the full validation offered as a follow-up.

**Step 1 — Intensity triage (always, both deliverables): 2–3 ES calls total.**

```bash
python3 <SKILL_DIR>/scripts/search_channels.py --intensity \
  --group '("cannes lions" | canneslions)' \
  --group 'cannes +lions +(advertising | agency | "young lions") -"film festival"'
```

One aggregation call measures every channel's **relationship to the topic**
— matching uploads (all-time + recent window) per channel — a second computes
each channel's topic share, and enrichment adds names + sponsorability. Tiers:

- **core** — the topic is the channel's identity (recurring + share ≥ 50%)
- **recurring** — ≥3 matching uploads (tunable `--recurring-min`): channels
  that keep returning to the topic. **For niche topics this tier IS the
  sponsorship market** — say so plainly: *"only N channels are about this
  topic; the real market is these M recurring-coverage channels."*
- **occasional** / **one_off** — count for trend math; usually wrong targets.

Present the tier summary to the user before spending anything further —
it's the cheapest honest picture of the topic's channel landscape, and it
prioritizes every later per-channel spend.

**Step 2a — Trend data (if chosen): the videos + prevalence.**

```bash
# trend feed: newest matching uploads in the window
python3 <SKILL_DIR>/scripts/search_videos.py --sort date --since 2026-06-01 \
  --size 50 --group '("fable 5" | fable5 | "fable five")'
# biggest matching videos; --distinct-channels for one row per channel
python3 <SKILL_DIR>/scripts/search_videos.py --sort views --distinct-channels \
  --group '("mythos 5" | mythos5) -keto'
```

Videos come back with title, url, publication date, views/likes/duration, and
the channel's name + subscribers. Headline prevalence numbers come free from
what you already ran: total matching videos + true distinct channels (the
intensity call's `distinct_channels`). Sense-check a sample of top videos
(titles/summaries — Stage 3 machinery); one-off channels COUNT here. For
date-sorted feeds prefer `--fields title,summary` — under a non-relevance
sort, incidental transcript mentions surface as prominently as genuinely
on-topic uploads. Tell the user when one channel dominates and offer
`--distinct-channels`.

**Step 2b — Channel targets (if chosen): rank, then context-validate by tier.**

1. **Rank** with the final filter (`search_channels.py --size 200 --group …`):
   one collapsed ES call ranks channels by their best-matching video
   (`title^4,summary^2,transcript^1`) with sponsorability flags. During
   refinement rounds the coarser `--any/--not` composition is a quick
   narrowing lever; the `--group` form re-runs the delivered filter exactly.
2. **Fetch context** for candidates, prioritized by intensity tier — core and
   recurring first, occasional only if budget remains, one-offs not at all
   (unless the user asks):
   ```bash
   python3 <SKILL_DIR>/scripts/fetch_context.py --channels 466311,2105 \
     --samples 4 --window 160 investing
   ```
   Extracts the text window around each keyword occurrence (transcript is
   caption XML — the script strips/unescapes it client-side).
3. **Classify** with the **`keyword-context-classifier`** agent (Agent tool,
   `subagent_type: keyword-context-classifier` — Haiku-cheap). Give each batch
   a `TOPIC:` line, usually a `NOT:` line, and the indexed evidence. Batch
   ≈50–100 channels, run batches in parallel. Returns per channel:
   `verdict on_topic|mixed|off_topic`, `confidence`, `evidence_quote`,
   `adjacent_terms` (feed those back to Stage 4).
   **Completeness ritual, non-negotiable:** anchor the count in the prompt
   (*"There are exactly 50 channels (indices 0–49). Return exactly 50 objects.
   The last channel_id is 778812."*), and after each batch **diff the returned
   `channel_id`s against what you sent; re-send missing ones to a fresh agent
   and merge.** Never assume a batch came back whole.
4. **Disposition:** the final channel table carries **tier × verdict ×
   sponsorability**. Keep `on_topic` AND `mixed` (labelled, with confidence);
   exclude only clear `off_topic` — and surface the excluded list. Rank all,
   **flag don't filter** on sponsorability: the user decides what to do with
   unbookable matches.

### Stage 6 — Deliver (`build_report.py`)

Hand the validated groups to the builder — start from `select_keywords.py
--apply`'s `groups`, adding per-group `content_fields` / `exclude` as needed:

```bash
echo '{
  "operator":"OR","report_type":"channels","title":"Fable 5 launch",
  "groups":[
    {"text":"fable 5"},
    {"text":"(\"mythos 5\" | mythos5) -keto"},
    {"text":"cannes +advertising","content_fields":["title"]},
    {"text":"dropshipping","exclude":true}
  ]
}' | python3 <SKILL_DIR>/scripts/build_report.py
```

It prunes union-redundant plain phrases (boolean groups are opaque — never
pruned, never prune others), **translates boolean-group SQS text into the web
app's keyword grammar** (uppercase `AND`/`OR`/`NOT` + parens + quoted atoms —
raw `|`/`+`/`-` are literal text in a link; `*`/`~` are rejected, enumerate
variants first), and emits:

- `filter_set` — platform shape (`keywords`, `keyword_operator`,
  `content_fields`, per-group field/exclude maps). Fields are **recognized
  content field names** (`title`, `summary`, `transcript`, `channel_description`,
  `channel_topic_description`, …) — unknown names fail loudly.
- `report_link` — paste-ready URL that opens the report with the filter applied
  (no saved record, no credits). **The default thing to hand the user.**
- `report_config` — for `tl reports create --config-file <f> --yes` to persist
  a named, shareable report.
- `expression` — the whole-filter boolean rendering; **record it alongside any
  saved report** so the filter is reproducible (it re-runs via
  `search_channels.py --group`).
- `pruned` / `translated` — nothing happens silently.

Show the user: the final keyword groups, the `report_link`, and the validated
channels/videos (with verdicts and sponsorability). Then **offer** to save via
`tl-save-report` / `tl reports create` — never save unprompted.

## Opt-in: keyword distribution

Only when the user explicitly asks for keyword counts / distribution / "how
common is X", the probe's ranked counts ARE the deliverable:

```bash
python3 <SKILL_DIR>/scripts/probe.py crypto bitcoin DeFi Web3 "smart contract"
```

Emits `{operator, level, fields, scope, keywords:[{keyword, count, …}]}`
sorted descending — a superset of the old `{operator, keywords:[{keyword,
count}]}` envelope, so existing consumers (report-builder keyword steps,
operator mirroring in `tl-save-report`) keep working.

## Cost

One `tl db es` query per candidate probe (~1–2 credits each); ~10 candidates ≈
10–20 credits. `search_channels.py` and `search_videos.py` are 2 calls each;
`--intensity` is 2–3 calls TOTAL for the whole tier table regardless of
channel count (aggregations) — always run it before spending per-channel.
`fetch_context.py` is 1 call per channel with priced fields — keep `--samples`
small (default 4) and validate the top candidates, not the tail. Haiku validation is cheap by design;
batch and parallelize. `build_report.py` and `expand_entities.py` are free (no
ES). The gated web step adds no credits — a few WebSearch/WebFetch calls inside
the resolver's own context; it fires only on post-cutoff / renamed / trend /
jargon-dense topics. Run `tl describe show db` for live rates; preview with
`tl db es … --pricing`.

## Self-check before you finish

1. You stated the deliverable, operator, **scope** (YouTube + longform
   default), and your **breadth judgment**, and they match the intent. Both
   the **path** (quick/deep) and the **deliverable** (trend data / channel
   targets / both) were the user's explicit choice — named in the request or
   answered when you asked in ONE combined question with rough credit costs;
   never a silent default or an assumed channel list.
2. You expanded **deeply** — facets, every entity's full family (company →
   product → model → codename → **sibling**), **tokenization variants**
   (`fable5` *and* `fable 5`) — and mined probe samples for terms you missed.
   No bare over-broad root or generic collocation survived. For a post-cutoff /
   renamed / trend / jargon-dense topic you ran the gated
   `keyword-entity-resolver` lookup, and every web name passed validation like
   any other candidate.
3. Every surviving keyword was validated against the verbatim intent — you read
   the **`channels`** count and distinct-channel samples, not the inflated doc
   total. Genuine **scope** calls went to the user with sample snippets.
4. On the deep path, **≥3 refinement rounds** ran, each reporting its query,
   fitness, and move (narrow / expand / backtrack); broad terms were judged
   on their **residual**; rescues (NOT scoped in-group, AND-anchor,
   field-narrow) were tried before dropping a real term; exclusions were
   verified not to over-cut the core. On the quick path, you said so in the
   result. You **narrated every stage** with its running credit spend, and
   the Stage 5 budget reserve was set aside before refinement began.
5. Coverage was measured on the union and matches the intended breadth; any
   scope-widening was user-confirmed (never silent drift).
6. Intensity triage ran before any per-channel spend, and its tier summary
   was shown to the user. For the channel-targets deliverable, the final
   table carries **tier × verdict × sponsorability**: context validation ran
   with the completeness ritual (every batch diffed by `channel_id`, missing
   items re-sent), prioritized core + recurring; only clear `off_topic`
   channels excluded, and the exclusion surfaced; all ranked, none filtered
   for being unbookable. If the topic has few/no core channels you said so
   and framed the recurring tier as the market — no padding, no near-empty
   list passed off as the answer. If budget forced a degraded validation you
   labeled it "not context-validated" explicitly. For the trend deliverable
   you materialized the matching videos (`search_videos.py`, sort/window
   matched to the ask) with prevalence numbers and sense-checked a sample.
7. Redundant terms pruned and reported; **stale** terms flagged (excluded or
   visibly tagged), `thin` niches surfaced — nothing dropped silently.
8. The deliverable includes the filter set **and** a working `report_link`
   (group text in app grammar — no raw `|`/`+`/`-`/`*`/`~`) **and** the
   validated channels/videos **and** the recorded `expression`.
9. Checkpoint honored per the chosen path: the deep path paused after round 3
   with the intent interview on request — or the user's autonomy preference
   was honored and noted. Nothing was saved without confirmation.
10. If the user requests a chart, create it as an SVG graphic.
