---
name: tl-creator-brief
tl-blurb: creator self-disclosure profile, and its connections to a brand
description: >
  Mine a YouTube creator's own transcripts, socials and the web for the
  places they talk about THEMSELVES — history, family, pets, habits, tastes —
  and build a reusable creator profile. Optionally map that profile's real
  connections to a named brand. Triggers: "creator profile", "what do we know
  about [creator]", "find self references", "creator brand connection",
  "personal angle for [channel]", "creator brief", "/tl-creator-brief".
---

# Creator Profile & Connections

Two modes, one contract:

- **PROFILE** (channel only, no brand needed): build the reusable ledger —
  `tl-creator-profiles/<channel_id>-facts.jsonl`, ONE file: its first line is
  the meta record (when it was built, over which videos, what it found), then
  one verified fact per line. It is the machine interface other skills and
  CONNECT consume; there is no human profile page. The run report in chat
  (funnel, counts, the selected facts, the path) is what the user reads.
  Spec: `references/profile-spec.md`.
- **CONNECT** (channel + brand): reuse (or build) the ledger, do a
  deliberately light brand read, write the ranked connection map as a working
  file (`.corpus/<channel_id>/connections-<brand_id>.md`) and render the one
  human deliverable, `tl-creator-profiles/<channel_id>-<brand_id>-connections.html`
  — who they are (from the ledger), what the brand is, the ranked
  connections, and the ledger's honesty strip. A no-fit verdict is a valid
  output.

Every run starts the same way, whichever mode: **look for the ledger**
(`## Reuse` below). A found ledger is used and announced; it is rebuilt
only when it is stale or the user says `--rebuild`.

Two standing rules: all data access from scripts goes through the shared
wrapper (`skills/_shared/tl_data.py` — stdin-passed bodies, timeouts, loud
failures), and a channel or brand name resolves via `tl channels find` /
`tl brands find`, never `ILIKE` on a name. ES query gotchas live in the tl
skill's `references/elasticsearch-schema.md`; the attribution doctrine in
`references/evidence-rules.md`; the transcript pipeline in
`references/transcript-mining.md`. Bulk-safe by construction: per-channel
output paths, no `cd` anywhere, so many channels can run concurrently.

## Resolve — basic and instant

A unique identifier (URL, @handle, YouTube ID) resolves directly via
`tl channels find`, done. A bare name gets one fuzzy search; if one candidate
clearly dominates (exact name, an order of magnitude more reach, recently
active), auto-pick it and say so; otherwise show the top 3–4 (name, subs,
URL, last upload) and ask — one question, no tie-break machinery. Localized
sister channels (`- Spanish`, `- Deutsch`) are excluded by default and
listed. Brand (CONNECT only): `tl brands find`; a rebrand returns several IDs
— carry them all.

**Plan gate:** `tl whoami --json` → `organization.plan`. `Intelligence` and
`Superuser` proceed. A known lower tier: say so and stop before building a
corpus that cannot be read. An unrecognised value: name it and continue —
tier names change.

**The gate is bounded.** Run it with `timeout 20 tl whoami --json`. On a
timeout or a non-zero exit, retry ONCE. If the second attempt also fails, say
so in one line and **continue the run**: the gate exists to avoid building a
corpus nobody can read, not to be the reason a run does not happen. Report
`plan gate: unreachable, continued` in the run report so the user knows the
tier was never confirmed. A measured run lost ~100 s to an unbounded retry
here.

## Socials lane — the flag answers it, otherwise ask

The identity & socials lane (web search on the creator, their linked
Instagram/X/about pages actually opened and read) is **opt-in**. Take the
answer from the invocation when it is there, and **ask when it is not**:

- `--socials` / "include socials" / "check their Instagram" / "add web" →
  lane ON, no question. Say which shape you took and move on.
- `--no-socials` / "transcripts only" / "no web" → lane OFF, no question.
- **Nothing said, and the run is interactive → ask**, once, before any fetch,
  as a single question with the default first:

  > **Run the socials/web identity lane?**
  > - **No — transcripts only** (default): the creator's own videos are the
  >   only source. Faster, and every fact carries a timestamped quote.
  > - **Yes — add socials & web**: also search the web for the creator and
  >   read their linked profiles, for facts the videos never state and for
  >   cross-lane confirmation.

- The run is **autonomous / no-pause** (`autonomous`, `--auto`, a scheduled or
  unattended run) or a **fast run** → lane OFF, nothing asked.

**Never drop the lane silently.** An earlier revision made the flag the only
input and treated "nothing said" as a settled no, on the reasoning that the
question merely confirmed the default. That was wrong twice over. The question
is what tells the operator the lane exists at all, and the time it costs is
the operator's own response time, which was never inside the fetch-to-page
figure this skill optimises. It cost a live Airrack run its whole second lane:
66 transcript facts, no `social`/`web` facts, and no cross-lane corroboration,
where a socials run on a comparable channel contributed 9 facts and lifted 5
transcript facts from `unconfirmed` to `confirmed`. The flag exists to skip
the wait when the answer is already known, not to make silence mean no.

Everything below marked *(socials lane ON)* applies only when the lane is on.

## Reuse — the found ledger wins

Before any fetch, one command:

```bash
python3 scripts/ledger_meta.py check --channel <id> [--rebuild] [--no-refresh]
```

Pass `--lanes transcripts+socials` when the socials lane is on. Found: it
prints ONE announcement line, which you repeat to the user verbatim, plus a
JSON `decision` — `reuse` (CONNECT skips fetch → extract → merge and lands at
the brand read; PROFILE reports the ledger as it is), `refresh` (run ONE
incremental round, below, then continue) or `build` (run the full PROFILE
pipeline). The thresholds, the flags and the announcement contract live in
`references/profile-spec.md`, "Reuse"; never reuse silently.

**Incremental refresh** (decision `refresh`, round `N` = the JSON's
`next_round`): `fetch_cues.py --channel <id> --host-terms "…" --round N
--since <latest_video_date from the check's JSON>
--exclude <corpus>/<id>/classified.jsonl` (`--since` bounds the fetch to the
uploads the ledger has not seen — without it a round re-pulls every unjudged
passage in the catalogue and costs a full 20-agent fan-out; `--exclude`
skips passages already judged; the new material is batched into
`batches-rN/`); fan out extractors over those batches only;
`assemble_extracts.py --batches <corpus>/<id>/batches-rN --returns
<corpus>/<id>/returns-rN --out <corpus>/<id> --append`; re-cluster the whole
`gems.jsonl`; `merge_pass.py prepare … --existing
tl-creator-profiles/<id>-facts.jsonl --state <corpus>/<id>/merge-state.json`
— passages already judged map back to their facts by member key, so the
agent sees only the genuinely new clusters (plus the compact list of
existing facts to fold into or supersede); one merge agent; `expand
--existing … --state …` keeps fact_ids, adds, marks superseded; verify;
`ledger_meta.py write --from … --rounds N` (descriptive fields not passed
again are carried over from the header). Cost scales with the new uploads,
not the corpus.

## PROFILE pipeline

One retrieval flow, one extraction fan-out, then judgment. Every stage below
prints a `FUNNEL` line; the details live in `references/transcript-mining.md`.

1. **Fetch the cue passages — one script, under a minute.**

   ```bash
   python3 scripts/fetch_cues.py --channel <id> --host-terms "<surname>,<company>" \
     [--reserve 1]
   ```

   **`--reserve N` when other agents will be in flight during the fan-out.**
   Batches are sized to fill one wave of the host's agent cap, so with the
   socials lane running the 20th extractor is rejected and relaunched a wave
   later. Pass `--reserve 1` whenever the socials lane is on, and one more for
   any other lane you launch alongside.

   It asks the index for the transcript passages around first-person cue
   phrases (`references/cue-phrases.txt`) and writes ranked, capped batches of
   25 windows plus a passage-only `corpus.jsonl.gz`. Measured: 7–25 s on
   channels up to a few hundred matched videos, 36–54 s when 700–1,200 videos
   match a cue — the clock follows matched videos, not the upload count.
   **The fetch never waits for anything.** When
   the socials lane is on, launch both in the SAME message; `--host-terms`
   for this first round come from the channel's own metadata and the user's
   request, and any name the lane turns up later feeds a **second round**
   (`--exclude`, step 3a), never a delayed first one.
   - *(socials lane ON)* the identity & socials subagent runs alongside —
     **spawn it as `general-purpose` with `model: sonnet`**, never on the
     inherited model: (a) channel metadata via `scripts/channel_context.py`;
     (b) a web search on the creator's and channel's names, top results
     actually read; (c) the channel's social links opened and read — the
     personal life often lives on a different platform than the content.
     Facts land with `social`/`web` provenance and URLs, never dressed as
     quotes. **Time-box it**: ~8 lookups, one pass each, no crawling beyond a
     linked page. Whatever it has when the extraction finishes is what the
     merge pass gets; anything unreached is reported "linked but unread".
     - **Give the lane the record contract, in the prompt.** It writes ledger
       records directly rather than judging clusters, so it must be told the
       enums or it invents plausible words that `merge_pass.py expand`
       rejects — a measured run had 5 of 9 facts come back on labels like
       `hobbies`, `identity` and `gear`, and the stage cost a hand-written
       patch. Every fact it returns carries exactly:
       `claim`, `domain`, `sensitivity`, `source_url`, `seen_date`, and a
       `ref` (`s1`, `s2`, …); optionally `provenance` (`social` | `web`),
       `confidence`, `corroborates`, `gloss`. It carries **no** `quote`,
       `video`, `start` or `url` — those belong to the transcript lane alone.
       - `domain` is one of exactly: `origin`, `family`, `pets`, `home`,
         `work`, `money`, `health`, `habits`, `tastes`, `beliefs`,
         `relationships`, `other`. Nothing else. A hobby is `habits`; a job
         or a business is `work`; anything that genuinely fits none of the
         twelve is `other`.
       - `sensitivity` is one of exactly: `none`, `lifestyle`, `clinical`,
         `children`, `location` (`references/evidence-rules.md`).
       `expand` aliases the common near-misses and reports each one, but a
       label it cannot place still fails the whole stage.
   - **Second channels: report, don't mine.** `channel_context.py` emits
     `second_channel_candidates`; each is resolved with `tl channels find`
     and listed in the page's "Other channels and platforms" section
     (name, id). Mining one happens ONLY when the user asks — it roughly doubles
     the run.
   - **Lane OFF — what changes.** `channel_context.py` still runs and still
     reports `social_links` and `second_channel_candidates`: every linked
     platform is listed **"linked but unread"** — here because the lane was
     not run, not because reading failed. No `social`/`web` fact exists, so
     the ledger is transcript-only and cross-lane corroboration cannot raise
     anything (`references/profile-spec.md`). Say in the run report that the
     lane was off, and that turning it on is one re-run away.
2. **Channel context brief — chained into the prompt render, one command.**
   `channel_context.py --channel <id> --corpus ...` calls the format label
   (solo / interview / multi-host / faceless-scripted) with evidence. Its
   corpus stats are now measured over the fetched **passages**, not whole
   transcripts, so read them as a format hint, not a census. Not a gate —
   nothing exits early. Its stdout is the summary only; per-video stat rows go
   to a file via `--per-video-out`.

   Both this and step 3's prompt render are local scripts that take under a
   second each, so run them as ONE `&&` chain, the way step 4 already chains
   assemble → cluster → prepare. Two separate steps cost a turn round-trip
   between them and nothing else: measured at 31 s split versus 2 s chained.

   ```bash
   python3 scripts/channel_context.py --channel <id> --corpus <…> \
     --per-video-out <…>/per-video.jsonl > <…>/context-full.json && \
   python3 -c "…"  # write context.json from context-full.json  && \
   for b in <…>/batches/batch-*.json; do \
     n=$(basename "$b" .json); \
     python3 scripts/extractor_prompt.py --batch "$b" --context <…>/context.json \
       --write-to <…>/returns/$n.extract.json --out <…>/prompts/$n.md; \
   done
   ```
3. **Extraction fan-out — one agent per batch, one message.** Classification
   and extraction are the same pass. Write the context block once
   (`context.json`: `channel_name`, `host_names`, `known_facts`,
   `format_label`, `format_evidence`), render every batch's message in one
   shell loop with `scripts/extractor_prompt.py --batch … --context … 
   --write-to <…>/returns/batch-NNN.extract.json --out <…>/prompts/batch-NNN.md`,
   then spawn ONE `tl-cli:gem-classifier` agent per rendered message, all of
   them as tool calls in a **single assistant message** with nothing else in
   flight. The agent's prompt is two lines — read that one file, follow it,
   one Write, one-line receipt; the file carries the rubric, the evidence
   rules, the context and the windows. Never paste the message into the
   prompt, never hand an agent two batches, never a transcript, never spawn
   one at a time. Batches are sized by `fetch_cues.py` to fill one wave of
   the host's agent cap (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, 20 when
   unset — the variable is read by Claude Code 2.1.x but not documented; set
   it to 40 in the host settings `env` and check the agents start together
   before relying on it. Every running agent counts against the cap, so
   launch the round alone).
   If `tl-cli:gem-classifier` does not resolve (a checkout rather than the
   installed plugin), either copy `agents/gem-classifier.md` into
   `~/.claude/agents/` (or the project's `.claude/agents/`) before the
   session starts and spawn it as `gem-classifier`, or spawn
   `general-purpose` with `model: sonnet` and the same prompt; say which in
   the run report. The scripted extractor (`scripts/classify_gems.py`, the
   same message sent to any OpenAI-compatible chat-completions endpoint,
   configured by three environment variables) is the fallback for a host
   that cannot spawn agents at all — measured worse on speaker attribution
   and span discipline (`references/transcript-mining.md`, Layer 3), never
   the default.
   - **3a. A deeper round is additive, not a re-run.** When 500 windows is not
     enough, or the socials lane turned up new host terms, run
     `fetch_cues.py --channel <id> --host-terms "…" --exclude
     <out>/classified.jsonl`: passages already judged are skipped, so the new
     batches are new material, and the round costs another fetch plus another
     fan-out.
4. **Assemble, cluster, prepare — one command, never a hand patch.** As soon
   as the receipts are in:

   ```bash
   python3 scripts/assemble_extracts.py --batches <…>/batches \
     --returns <…>/returns --out <…> > <…>/assemble.json && \
   python3 scripts/cluster_gems.py --in <…>/gems.jsonl > <…>/cluster.json && \
   python3 scripts/merge_pass.py prepare --clustered <…>/gems-clustered.jsonl \
     --format <label> --out <…> > <…>/prepare.json
   ```

   Assemble validates the count contract, the echoed `start`, the enums, and
   cuts each quote out of the window text so every quote is verbatim by
   construction. It writes `classified.jsonl`, `gems.jsonl`,
   `candidates.jsonl` and `respawn.json`, and **coverage decides its exit
   code**: unjudged windows within `--min-coverage` (default 0.95) are
   reported on the funnel line as `unjudged=N` and the chain continues —
   they stay out of `classified.jsonl`, so a later `--exclude` round can
   still pick them up. Exit `3` (below the threshold, or a batch with no
   return file at all) stops the chain: re-judge exactly the windows in
   `respawn.json` with `extractor_prompt.py --indexes … --write-to
   …/batch-NNN.extract.r2.json`, one agent per batch, then re-run the
   command. Never edit a return file by hand. Read the three FUNNEL lines
   from stderr and spawn the merge agent (step 6) **in the same message**.
5. **Cluster the repeats — in the command above.** `cluster_gems.py` writes
   `gems-clustered.jsonl` beside `gems.jsonl`, so a back-catalogue channel
   that has said "BioShock is my favorite game" in thirty videos costs the
   merge pass one read instead of thirty. Merge rules:
   `references/transcript-mining.md`, Layer 4. Do not hand-merge what the
   script left apart.
6. **Merge pass — decisions from sharded agents, the ledger from a script.**
   **Model: Opus, deliberately.** This is the one pass where the expensive
   context is the point (`references/transcript-mining.md`, opening): it is
   judgment no script can encode. Do not downgrade it to make the run faster;
   shard it instead.

   **Shard by default.** `merge_pass.py prepare --shards N` splits the input
   by life domain into N files for N agents, spawned in ONE message like the
   extraction fan-out. Folds never cross a domain, so each shard is a complete
   judgment unit. Size N as `clusters / 60`, floor 1, ceiling 6 — 226 clusters
   means 4. One agent on 226 clusters measured 475 s.
   - **The largest domain sets the floor.** Shards hold whole domains, so a
     channel whose clusters pile into one domain cannot split below that
     domain's size: 226 clusters on a solo tech channel packed 105 / 40 / 40 /
     41 because `work` alone held 105. Raising N past the point where the
     biggest domain has its own shard buys nothing. Read the shard line sizes
     from `prepare`'s JSON before assuming more shards will help.
   - Each shard's reply is saved as its own `merge-decisions-r1-sN.json` and
     all of them are passed to `expand` as repeated `--decisions` flags.
     `selected` is unioned across shard files (each shard can only nominate
     from the domains it saw), and `expand` still owns the final count.

   `merge_pass.py prepare` (already run in step 4) writes `merge-input.jsonl`.
   The script applies the deterministic parts of `evidence-rules.md` itself
   (guest and co-host voices dropped; unclear voices dropped on shared-voice
   formats; solo-format `unclear`/narration kept as host, capped
   unconfirmed) and hands ONE agent `merge-input.jsonl`: one compact line per
   cluster — claim, quote, domain, tier, speaker, distinct-video count,
   ad-read and anchor flags — **never the windows, never a transcript**. The
   agent returns ONE JSON object of decisions, a few kilobytes, as its final
   message (`references/transcript-mining.md`, Layer 4, holds the contract):
   per cluster `keep` (optionally with a re-tier, a narrowed claim, a
   confidence override, a `supersedes`, a `gloss`), `fold` into another
   cluster or an existing fact, or `drop` with a reason — its proposed
   `selected` picks, and, when the socials lane ran, the lane's `social`/`web`
   facts as `facts` records (URL + seen-date, never dressed as quotes). Save it as `<…>/merge-decisions-r1.json`, then

   ```bash
   python3 scripts/merge_pass.py expand --clustered <…>/gems-clustered.jsonl \
     --decisions <…>/merge-decisions-r1.json --format <label> --channel <id> \
     --out <…>/facts.jsonl
   ```

   Expand validates the contract (every cluster decided exactly once, targets
   exist, enums, a narrowed claim introduces no number the quote lacks) and
   exits **3** listing the offending ids: re-ask the agent for exactly those
   ids ONCE, save the reply as a second `--decisions` patch file, re-run; a
   second failure runs `expand --fallback-original`, which keeps the
   cluster's own claim for the still-offending ids and reports them. Never
   hand-patch a decision. Expand builds every record (recurrence over folds
   from distinct videos, the format-gated confidence default, the derived
   `sensitive` flag, fact_ids, `members`), owns the final `selected` set, and
   writes `facts.jsonl` plus `merge-state.json` (the member-key map the next
   refresh reads). Then `scripts/verify_quotes.py --in facts.jsonl --corpus
   ...` re-checks every transcript quote against the stored passages: exact
   matches publish (their timestamps are authoritative), partial/none get
   fixed to the caption text or dropped.
7. **Emit** per `references/profile-spec.md`:

   ```bash
   python3 scripts/ledger_meta.py write --channel <id> \
     --from <…>/facts.jsonl.verified.jsonl --channel-name "…" \
     --format <label> --format-evidence "…" --context <channel_context.json> \
     [--lanes transcripts+socials]
   ```

   That writes the one ledger, `tl-creator-profiles/<channel_id>-facts.jsonl`,
   header first: the meta record is counted from the build's files, never
   typed in; `--context` is `channel_context.py`'s output saved to a file,
   so the linked platforms ("linked but unread" when the lane was off) and
   sibling channels ("not mined") reach the record and, later, the page.
   `--from` refuses (exit 2, nothing written) any transcript fact whose
   verification is missing or not `exact`.
   PROFILE ends here — the run report (below) is its human output.

## Run report — the funnel, every run

Every PROFILE run ends with a funnel table in the **run report** (the CLI
answer to the user), never in the profile itself. Each stage script prints one
`FUNNEL stage=… key=value …` line to stderr; the two model stages are Claude
passes, so **they report their own counts in the same format**. Echo all six
lines, as they were emitted:

```
FUNNEL stage=fetch_cues round=… videos_with_transcript=… videos_matched=… passages=… windows_capped=… batches=… batch_size=… agent_cap=… sponsor_source=… elapsed_s=…
FUNNEL stage=extract batches=… agents=… windows=… gems=… elapsed_s=…   ← you print this one
FUNNEL stage=assemble windows_expected=… windows_assembled=… gems=… unjudged=… coverage=… elapsed_s=…
FUNNEL stage=cluster gems=… clusters=… merged=… elapsed_s=…
FUNNEL stage=merge clusters=… judged=… auto_dropped=… additive=… facts=… folded=… dropped=… selected=… identity_facts=… domain_aliases=… elapsed_s=…
FUNNEL stage=verify candidates=… verified=… rejected=… passed_through=… elapsed_s=…
```

Then one line naming the extraction shape and its cost:
`extraction: <N> sonnet agents × <M> windows, one round, <U> unjudged;
merge: 1 agent, <N> decisions`. A second `--exclude`
round adds its own fetch/extract/assemble lines rather than replacing the
first round's. Cost and path belong in the run report **once, and never in the
profile**.

And one line saying whether the opt-in socials lane ran, always:
`socials lane: off (transcripts only) — N linked platforms listed unread` or
`socials lane: on — N sources read`. Off is the default, so it is never an
error to report; say it plainly, with "re-run with socials on" as the fix
when the user wants that coverage.

And, when a ledger was found, the reuse announcement line exactly as
`ledger_meta.py check` printed it, plus the decision it took (`reuse`,
`refresh` round N, or `build`).

Then, on a PROFILE run, the `selected` facts as a short list (claim, domain,
recurrence) and the ledger path — the closest thing to a profile the user
sees, and deliberately not a file.

Why this is mandatory: the connections page shows a dozen facts, while the
ledger holds every verified fact. A run that renders "9 facts" is not a
failed run until the funnel says which stage lost them — without the table
the user cannot tell selection from yield.

## Fast runs

A "fast run" is a run shape, not a script flag (no script takes `--fast`):
PROFILE only, the primary channel only (no second-channel mining), **the
socials lane off, without asking**, the default 500-window cap, and ONE
extraction round — no `--exclude` deepening. Wall clock is the fan-out plus
the merge pass: the fetch is under a minute and the local scripts are
seconds, so a run is roughly one extractor generation (one wave of agents,
each one Read, one Write and a receipt) plus the merge pass (one agent
returning decisions, minutes not tens of them), with assemble → cluster →
prepare and expand → verify → write each run as one command so no
notify-then-act gap sits between scripts. Everything that could outrun that
is bounded rather than skipped: one wave of the host's agent cap is the
round, the identity lane is time-boxed as in step 1 when
the user turned it on, and the merge pass stays at one agent returning
decisions (`--shards N` on `merge_pass.py prepare` splits the input by life
domain across N agents when one is still slow; folds never cross domains). Report the
per-stage `elapsed_s` from the funnel lines so a slow run says which stage was
slow. A deeper pass (a second `--exclude` round, a second channel) is always
one re-run away and is never taken on the skill's own initiative.

## CONNECT pipeline

Run the `## Reuse` check first: `reuse` loads `<channel_id>-facts.jsonl`
(header and facts, via `scripts/ledger_io.py`) as it is, `refresh` runs one
incremental round,
`build` runs the PROFILE pipeline. Then:

1. **Brand read — four lanes plus the category probe, FIVE agents spawned in
   ONE message, one-shot.** Every one of them is `general-purpose` with an
   explicit **`model: sonnet`**. None of them may inherit the orchestrator's
   model: an unnamed model is how a measured run put the category probe on
   Opus for 456 s against 121 s for the same probe on Sonnet.

   All five need only the channel and the brand, never the creator ledger, so
   this message goes out **as early as the brand is resolved** — alongside the
   merge pass on a build run, immediately on a `reuse` run. The four lanes
   already ran concurrently with the merge and cost nothing; the probe is the
   one that was left queueing until step 2 and paid full price for it.
   - **TL data**: `tl brands find` + category + product description, plus
     `scripts/brand_reads.py` — the recency-ordered ad-read sample (weight
     the newest era; an old read can describe a dead product or CTA). Search
     phonetic brand-name variants so mangled reads still count.
   - **Sponsorship patterns**: who the brand sponsors across the corpus, and
     above all *personalization precedents* — moments creators already tie
     this brand to their own lives. Direct evidence of which connection types
     the brand converts. Public signals only — never other clients' internal
     proposals or deal terms.
   - **Web**: website + search results actually read + recent news: current
     positioning, product lines, stated audience.
   - **Brand social**: the brand's own Instagram/X/TikTok — campaign themes,
     how they use creators, and the brand's personal surface (founder story,
     office dog, a championed cause). Connections run both directions.
   - **Category precedent probe** (the fifth agent, and the one that used to
     run late): a channel-scoped search of the transcript corpus for the
     moments the creator already does what the product enables, without ever
     naming the brand. **It works out its own search terms from the brand and
     its category** — deciding which terms are worth probing IS the judgment
     here, so never hand it a fixed keyword list. It returns the term counts
     over the channel's transcribed videos plus the strongest few windows with
     their `&t=` links, titles, dates and view counts, and it is
     **confirm-only**: it deepens a candidate connection, never invents one.
     Save its reply as `<corpus>/category-probe.json`; step 2 reads that file
     rather than spawning anything.
2. **Connection pass.** Put the facts ledger next to the brand read and
   `<corpus>/category-probe.json`. Three types — **direct** (fact ↔ product),
   **adjacent** (lifestyle fit), **category precedent** (the probe's moments,
   where the creator already does what the product enables). Follow-up queries
   are **confirm-only**: they deepen a candidate connection, never invent one.

   Write the map as a working file,
   `tl-creator-profiles/.corpus/<id>/connections-<brand>.md`, per
   `references/profile-spec.md`. **One deliverable per creator plus brand, no
   second file.** The section contract, in this order:

   - `## About <creator name>` — two or three sentences on who they are, in
     prose, from the ledger. Not a fact list; the renderer has the ledger for
     that.
   - `## Thesis` — the core of the page. Why these two fit, in three or four
     sentences, written well enough to be read aloud in a meeting. It leads,
     above the brand introduction, because the reader wants the argument
     before the background.
   - `## About <brand name>` — two or three neutral sentences from the
     web/social lanes and the public category. Never sponsorship patterns,
     never a price.
   - One `## ` section per connection, strongest first, the type as a bold tag
     on the heading. Section order IS the ranking.
   - `## Where this could go wrong` — the honest mismatch, last. **Always
     present, even on a strong fit.** A creator who talks constantly about
     fear of AI replacing people, paired with an AI upskilling brand, carries
     that warning on the page. Honest mismatch beats overfitting to a perfect
     match, and the renderer keeps it out of the numbered connections so it
     never reads as an angle.

   Then render the deliverable:

   ```bash
   python3 scripts/build_html.py \
     --in tl-creator-profiles/.corpus/<id>/connections-<brand>.md \
     --facts tl-creator-profiles/<id>-facts.jsonl
   ```

   That writes `tl-creator-profiles/<id>-<brand>-connections.html` — the only
   file a CONNECT run adds to the deliverable directory. It opens with **who
   they are** (rendered from the ledger, never written by hand), then the
   brand, the ranked connection cards, and the ledger's honesty strip
   (tallies, coverage, "absence is not evidence", linked platforms).
   Publish it as an artifact where the host supports one; it is the only
   page anyone sees.

## Guardrails

- **Read-only.** Nothing is sent to anyone; output comes back for review.
- **No prices, costs, rate cards or deal terms in any output**, ever.
- **One illustrative sample read per connection, clearly labelled — and
  nothing more.** This replaces the older blanket "not ad copy" rule. A
  connection card may carry ONE short **Sample read** line showing how the
  angle could sound in the creator's own register, marked as an illustration
  and never as approved copy. It is there so the reader can see the angle
  land, not so anyone reads it aloud as written. Everything else on the page
  stays connection material: no scripts, no full reads, no CTA wording, no
  alternate versions, and the price, cost and deal-term bans above are
  untouched.
- **Sensitivity is a tier, not a flag** (`evidence-rules.md`): every fact
  carries `none` | `lifestyle` | `clinical` | `children` | `location`.
  Beliefs are not sensitive. Only `clinical`, `children` and `location` are
  excluded from connection angles by default, and `clinical` is usable when
  the creator discusses it across 3+ videos or frames it as part of their
  story. No protected-trait inference, ever.
- **Verbatim or not at all**; a partial quote match never publishes.
- **An empty answer is a real answer** — "no evidence found", with the
  coverage numbers that bound it.
