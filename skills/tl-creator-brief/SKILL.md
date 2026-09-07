---
name: tl-creator-brief
tl-blurb: custom brief for a booked creator
description: >
  Build a made-for-the-creator sponsorship brief once a specific YouTube
  channel is booked: mine the creator's own transcripts for verbatim quotes
  that bridge into the brand's talking points, find a real moment where they
  already do or advocate what the product enables, and rank which framings
  their audience rewards. Use when a sponsorship is booked (or nearly booked)
  and someone wants the ad tailored to that creator. Triggers: "custom brief
  for [creator]", "tailor our talking points to [channel]", "talking points
  for [creator]", "personalize the ad for [channel]", "how should [creator]
  pitch our product", "creator brief", "/tl-creator-brief".
---

# Creator Brief (custom talking points for a booked channel)

Turns a generic brand pitch into an ad the creator could have written
themselves. The output is a short brief arguing one thesis — this creator is
a natural evangelist for this product, not an ad-reader — and proving it with
their own words.

Companion to a brand's campaign-wide brief: that document says what every
creator should convey; this one says how *this* creator gets there in their
own voice.

## Step 1 — Resolve, then ask once

Resolve everything the data can answer before asking anything:

- This skill reads brand records and video transcripts, both of which need
  an Intelligence plan. `tl whoami --json` reports the plan — if it isn't
  Intelligence, say so and stop before asking the user anything.
- `tl whoami --json` also settles the brand: a brand user with one brand
  needs no brand question. Otherwise resolve the brand the user named with
  `tl brands find` (never fuzzy-match names by hand); if they named none and
  the profile lists several, add the brand to the question batch below
  rather than guessing. If the brand rebranded, resolve former names too —
  their earlier reads live under the old brand.
- Resolve the channel with `tl channels find`. If the creator runs separate
  interview or clips channels, resolve each by name with its own
  `tl channels find` — there is no automatic link between them.

Then gather the rest in **one** consolidated prompt — the host agent's
structured question tool if it has one (AskUserQuestion in Claude Code),
otherwise a single chat message listing all four — never drip questions:

1. **The current offer and CTA** — what this campaign sells and the exact
   call to action (link, code, landing page). The single most load-bearing
   input; past reads may carry an expired offer.
2. **The booked placement** — a specific upcoming video/topic, or open.
3. **What they know about the creator personally** — businesses, team,
   backstory, stated philosophies. Seeds the personal-connection search;
   "nothing" is a fine answer, Step 2 finds it from transcripts.
4. **Hard mandatories** — read length, must-say claims, compliance limits.

## Step 2 — Four research agents, in parallel

Launch all four as parallel subagents so raw transcripts and query results
never enter the main conversation. Each returns a short digest plus pointers
(video URL + timestamp per quote), nothing raw. Agent 4 is mechanical
scoring — run it on a cheap/fast model. Agents 1, 2, and 3 turn raw speech
into judgment calls (what a quote means, which era of a pitch is canonical,
what is invariant across reads) — give them a mid-tier model. All
queries go through the `tl` CLI, read-only; the query shapes for transcripts
and sponsored mentions are documented in the `tl` skill's references.

**1. Brand voice** — pull the brand's past spoken ad reads (sponsored
mentions across its sponsored videos, all brand IDs from Step 1). Sample
across the whole history but weight recent reads: pitches shift era to era,
and only the current era is canonical. Extract the read skeleton (hook →
problem → product → proof → CTA), recurring claims, and what every read
includes vs what varies. No history yet? Fall back to the brand's site and
the Step 1 answers, and say so in the brief.

**2. Creator on themselves** — search the channel's transcripts for verbatim
self-referential quotes: their business, team, how they work and learn,
origin story, stated beliefs. These become the bridges.

**3. Creator on the category** — every moment the creator touches the
brand's problem space, verbatim with video title and date. The prize is a
**format precedent**: a moment where the creator already does or advocates,
on camera, something the product enables. One genuine precedent beats ten
adjacent quotes — it turns "you could use this" into "you already do this;
here's the upgrade".

**4. Audience resonance** — pull the channel's uploads from the last 12
months (widen to 18 if that leaves fewer than 20 uploads) and score each
upload against the median views of its **own age bucket** — never against a
whole-channel average, since recent videos haven't finished accumulating.
Fixed buckets: 0–30, 31–90, 91–180, and 181+ days old; merge any bucket
with fewer than 5 uploads into its older neighbor. Output: which framings
of the brand's topic this audience rewards and which it punishes.

### Transcript honesty rules (all agents)

- Transcript coverage is partial. Report the coverage rate; absence of a
  quote is not evidence the creator never said it.
- Transcripts are auto-captions: proper nouns get mangled. Search spelling
  and phonetic variants of company and product names before concluding zero
  hits. When quoting, never silently correct a caption: put the corrected
  proper noun in square brackets and note the raw caption text in the
  brief's caveats. Bracketed proper-noun fixes are the only permitted edits.
- Captions carry no speaker labels. On interview channels, verify from
  surrounding context that the *host* is speaking; drop anything ambiguous.
- Quote verbatim or not at all, and link every quote to its video (add a
  `&t=<seconds>s` timestamp when the transcript provides offsets).

## Step 3 — Deliver in two stages

**Stage 1 — as soon as agents 2, 3, and 4 are back (before the brief is
written):** the best verbatim quotes and the audience-resonance read. Useful
raw material for a deal conversation already in motion; don't hold it for
the polished document.

**Stage 2 — the brief** (markdown, or a rendered document if the user wants
one), sections in this order:

1. **Thesis** — one paragraph: why this creator is an evangelist for this
   product, grounded in what the research found.
2. **Quote-bridges** — 5–8 verbatim quotes, each mapped to one brand talking
   point: *they already believe X on the record → the product is X made
   practical*. Video title, date, timestamped link per quote.
3. **The use case** — one specific, believable way the creator or their team
   would use the product in their own work, built on the strongest format
   precedent. The heart of the brief: an exact on-camera workflow, not "you
   could use it".
4. **A sample read** — the brand's read skeleton and mandatories, opened
   from the use case, in the creator's own diction, using one or two
   quote-bridges as segues. Label it in the brief itself as a demonstration
   of how the pieces connect — inspiration, never copy to read aloud.
5. **Angle guidance** — from the resonance ranking: the framing to lean
   into, the framing to avoid, and (if the placement is open) which upcoming
   topics fit best.
6. **Caveats** — transcript coverage, dropped ambiguous quotes, anything
   inferred rather than verified.

## Guardrails

- **Read-only.** Nothing is sent to the creator, the brand, or anyone else;
  the brief comes back to the user for review.
- **Never script the mouth.** The sample read demonstrates fit; the brief
  must say the creator has creative freedom over every spoken word.
- **No economics.** No prices, deal terms, or other creators' terms in any
  deliverable — this document is written to be forwarded to the creator.
- **No fabricated performance claims.** "This framing ran 3× the channel's
  baseline" is observable; "this ad will convert" is not.
