---
name: tl-creator-brief
tl-blurb: creator self-disclosure profile, and its connections to a brand
description: >
  Mine a YouTube creator's own transcripts (and, opt-in, their socials and the
  web) for the places they talk about THEMSELVES, their history, family, pets,
  habits and tastes, and build a reusable creator profile. Optionally map that
  profile's real connections to a named brand. Triggers: "creator profile",
  "what do we know about [creator]", "find self references", "creator brand
  connection", "personal angle for [channel]", "creator brief",
  "/tl-creator-brief".
---

# Creator Profile & Connections

Two modes, one contract:

- **PROFILE** (channel only): build the ledger
  `tl-creator-profiles/<channel_id>-facts.jsonl`: line 1 the meta record
  (when, over which videos, what it found), then one verified fact per line.
  Other skills and CONNECT consume it. No human page; the run report in chat
  is the human output.
- **CONNECT** (channel + brand): reuse or build the ledger, read the brand
  lightly, and render the one human page,
  `tl-creator-profiles/<channel_id>-<brand_id>-connections.html`. A no-fit
  verdict is a valid output.

`<skill>` is this skill's own directory (the installed plugin's copy); every
command is `python3 <skill>/scripts/…`. Outputs land under the invocation
directory, never inside the skill. `<corpus>` is
`tl-creator-profiles/.corpus/<channel_id>/`, the working directory.

Detail lives in three references; open the one you need:
`references/transcript-mining.md` (script flags, the extractor and merge
contracts, the incremental round), `references/profile-spec.md` (ledger and
meta formats, the connection map's sections, the page),
`references/evidence-rules.md` (what counts, attribution, sensitivity).

Standing rules: scripts reach the platform only through
`skills/_shared/tl_data.py`; names resolve via `tl channels find` /
`tl brands find`, never a name match in a query; no `cd`; per-channel paths.

## Resolve

A URL, @handle or YouTube ID resolves directly with `tl channels find`. A bare
name gets one fuzzy search: auto-pick a clearly dominant candidate and say so,
otherwise show the top 3 or 4 and ask once. Localized sister channels are
excluded and listed. Brand: `tl brands find`; a rebrand returns several IDs,
carry them all.

**Plan gate**, bounded in the command because macOS has no `timeout`:

```bash
python3 -c "
import subprocess, sys
try:
    sys.exit(subprocess.run(['tl','whoami','--json'], timeout=20).returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
"
```

`organization.plan` of `Intelligence` or `Superuser` proceeds; a known lower
tier stops with a message; an unrecognised value is named and continues. On
failure retry once, then continue and report `plan gate: unreachable, continued`.

## Socials lane (opt-in)

The identity and socials lane (web search on the creator, their linked
profiles read) runs only when asked for:

- `--socials`, "include socials", "add web": ON, no question.
- `--no-socials`, "transcripts only": OFF, no question.
- Nothing said and the run is interactive: ask once, before any fetch, the
  default first:

  > **Run the socials/web identity lane?**
  > - **No, transcripts only** (default): the creator's own videos are the
  >   only source. Faster, and every fact carries a timestamped quote.
  > - **Yes, add socials and web**: also search the web for the creator and
  >   read their linked profiles, for facts the videos never state and for
  >   cross-lane confirmation.

- Autonomous, unattended, or a fast run: OFF, nothing asked.

The run report always says whether it ran. *(socials ON)* below means only
when it is on.

## Reuse: every run starts here

```bash
python3 <skill>/scripts/ledger_meta.py check --channel <id> \
  [--lanes transcripts+socials] [--rebuild] [--no-refresh]
```

A found ledger prints one announcement line, which you repeat to the user
verbatim, and a JSON `decision`:

- `reuse`: CONNECT goes straight to the brand read; PROFILE reports the
  ledger as it is.
- `refresh`: run one incremental round (`transcript-mining.md`, "Incremental
  round"), then continue.
- `build`: run the PROFILE pipeline.

Never reuse silently; never refuse `--rebuild`.

## PROFILE pipeline

Every stage prints a `FUNNEL` line to stderr. Scripts that take under a
second are chained with `&&` in one command: a turn between two scripts costs
more than the scripts.

1. **Fetch the cue passages.**

   ```bash
   python3 <skill>/scripts/fetch_cues.py --channel <id> \
     --host-terms "<surname>,<company>" [--reserve 1]
   ```

   Writes `<corpus>/windows.jsonl.gz`, `<corpus>/batches/batch-NNN.json` (one
   file per extractor agent, sized to fill one wave of the host's agent cap)
   and `<corpus>/corpus.jsonl.gz`. `--out` names the parent, not the channel
   directory. `--reserve 1` when the socials lane is on, plus one per other
   agent in flight. Host terms come from the channel metadata and the request.
   - *(socials ON)* Spawn the identity lane in the same message:
     `general-purpose`, **`model: sonnet`**, about 8 lookups. It runs
     `channel_context.py --channel <id>`, searches the creator's names and
     reads the linked profiles. Put the `social`/`web` fact record and its
     enums from `profile-spec.md` in its prompt, or it invents labels that
     `expand` rejects. What it has when extraction finishes is what the merge
     pass gets; the rest is reported "linked but unread".
   - Second channels are reported, never mined, unless the user asks. A
     deeper round (`--exclude <corpus>/classified.jsonl`, `transcript-mining.md`
     "Entity expansion") is never taken on the skill's own initiative.

2. **Context, format call, prompts.** First the stats:

   ```bash
   python3 <skill>/scripts/channel_context.py --channel <id> \
     --corpus <corpus>/corpus.jsonl.gz --per-video-out <corpus>/per-video.jsonl \
     > <corpus>/context-full.json
   ```

   Read `context-full.json` and call the format (`solo`, `interview`,
   `multi_host`, `faceless_scripted`) with one line of evidence; the stats are
   a hint, never a gate. Then write the context block and render every batch's
   message in one chain:

   ```bash
   python3 <skill>/scripts/channel_context.py --from <corpus>/context-full.json \
     --format-label <label> --format-evidence "<evidence>" \
     [--host-names "<a>,<b>"] [--known-facts "<x>;<y>"] \
     --write-context <corpus>/context.json && \
   for b in <corpus>/batches/batch-*.json; do n=$(basename "$b" .json); \
     python3 <skill>/scripts/extractor_prompt.py --batch "$b" \
       --context <corpus>/context.json \
       --write-to <corpus>/returns/$n.extract.json --out <corpus>/prompts/$n.md; \
   done
   ```

3. **Extraction fan-out: one agent per prompt file, all in ONE message.**
   One `tl-cli:gem-classifier` agent per `<corpus>/prompts/batch-NNN.md`, all
   spawned in a single assistant message with nothing else in flight. The
   prompt is two lines: read that one file and follow it exactly; one Write,
   then the one-line receipt. Never paste the message in, never two batches
   per agent, never one at a time, never poll or sleep. If the agent name does
   not resolve, use `general-purpose` with `model: sonnet` and say so. The cap
   is `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (20 when unset).

4. **Assemble, cluster, prepare: one command.** As soon as the receipts are
   in:

   ```bash
   python3 <skill>/scripts/assemble_extracts.py --batches <corpus>/batches \
     --returns <corpus>/returns --out <corpus> > <corpus>/assemble.json && \
   python3 <skill>/scripts/cluster_gems.py --in <corpus>/gems.jsonl > <corpus>/cluster.json && \
   python3 <skill>/scripts/merge_pass.py prepare --clustered <corpus>/gems-clustered.jsonl \
     --format <label> --shards <N> --out <corpus> > <corpus>/prepare.json
   ```

   Assemble cuts every quote out of the window text (verbatim by
   construction) and exits 0 while coverage is at least 0.95. Exit 3 stops
   the chain: re-judge exactly the windows in `respawn.json` with
   `extractor_prompt.py --indexes … --write-to <corpus>/returns/batch-NNN.extract.r2.json`,
   one agent per batch, then re-run. Never edit a return file by hand. Shards
   `N` = `clusters / 60`, floor 1, ceiling 6; shards hold whole life domains,
   so the largest domain sets the floor. Spawn the merge agents in the same
   message that reads this result.

5. **Merge pass: sharded agents decide, the script builds the ledger.**
   **Model: Opus, deliberately**; shard it rather than downgrade it. One
   agent per shard file (`<corpus>/merge-input-N.jsonl`, or
   `merge-input.jsonl` when `N` is 1), all in one message. Each reads its
   compact cluster lines, never the windows, and returns one JSON object of
   decisions (`keep` / `fold` / `drop`, `selected` picks, *(socials ON)* the
   lane's facts; contract in `transcript-mining.md`, Layer 4). Save each as
   `<corpus>/merge-decisions-r1-sN.json`, then:

   ```bash
   python3 <skill>/scripts/merge_pass.py expand --clustered <corpus>/gems-clustered.jsonl \
     --decisions <corpus>/merge-decisions-r1-s1.json [--decisions …] \
     --format <label> --channel <id> --out <corpus>/facts.jsonl && \
   python3 <skill>/scripts/verify_quotes.py --in <corpus>/facts.jsonl \
     --corpus <corpus>/corpus.jsonl.gz
   ```

   Expand exits 3 listing offending ids: re-ask for exactly those once as
   another `--decisions` file; on a second failure add `--fallback-original`.
   Never hand-patch a decision. Verify re-locates every quote; only exact
   matches publish, partial or none get fixed to the caption text or dropped.

6. **Write the ledger.**

   ```bash
   python3 <skill>/scripts/ledger_meta.py write --channel <id> \
     --from <corpus>/facts.jsonl.verified.jsonl --channel-name "…" \
     --format <label> --format-evidence "…" --context <corpus>/context-full.json \
     [--lanes transcripts+socials]
   ```

   Refuses (exit 2, nothing written) any transcript fact whose verification
   is not `exact`. PROFILE ends here.

## Run report: every run

Echo every `FUNNEL` line as emitted, and print the two model stages' lines
yourself in the same format:

```
FUNNEL stage=fetch_cues round=… videos_with_transcript=… videos_matched=… passages=… windows_capped=… batches=… batch_size=… agent_cap=… sponsor_source=… elapsed_s=…
FUNNEL stage=extract batches=… agents=… windows=… gems=… elapsed_s=…
FUNNEL stage=assemble windows_expected=… windows_assembled=… gems=… unjudged=… coverage=… elapsed_s=…
FUNNEL stage=cluster gems=… clusters=… merged=… elapsed_s=…
FUNNEL stage=merge clusters=… judged=… auto_dropped=… additive=… facts=… folded=… dropped=… selected=… identity_facts=… enum_aliases=… elapsed_s=…
FUNNEL stage=verify candidates=… verified=… rejected=… passed_through=… elapsed_s=…
```

Then one line each: the extraction shape (`N sonnet agents × M windows, U
unjudged; merge: N shards`); the socials lane (`off, N linked platforms
listed unread` or `on, N sources read`); the reuse announcement and decision
when a ledger was found; on PROFILE, the `selected` facts as a short list plus
the ledger path. Cost and path never go in a deliverable.

## Fast run

A run shape, not a flag: PROFILE only, the primary channel only, socials OFF
without asking, the default 500-window cap, one extraction round.

## CONNECT pipeline

Run the reuse check first. Then:

1. **Brand read: five agents in ONE message, as soon as the brand resolves**
   (alongside the merge pass on a build, immediately on a reuse). All five are
   `general-purpose` with an explicit **`model: sonnet`**, never the inherited
   model, and need only the channel and brand.
   - **TL data**: `tl brands find`, category, product description, and
     `python3 <skill>/scripts/brand_reads.py --brand <id>` for the newest
     sponsored reads (weight the newest era).
   - **Sponsorship patterns**: who the brand sponsors and, above all, moments
     creators already tie it to their own lives. Public signals only.
   - **Web**: site, search results actually read, recent news.
   - **Brand social**: the brand's own accounts: campaign themes, how it uses
     creators, its personal surface (founder story, a cause).
   - **Category precedent probe**: a channel-scoped transcript search for
     moments the creator already does what the product enables, without
     naming the brand. It picks its own terms, returns term counts plus the
     strongest windows with `&t=` links, and is confirm-only. Three rules:
     every query in the foreground, never a background job; write
     `<corpus>/category-probe.json` before any optional deepening, gaps in
     `coverage.note`; cap every query's result size.

2. **Connection pass.** Start when the four lanes are in; the probe is a
   bonus, never a gate. If `category-probe.json` is missing, write the map
   without category-precedent connections and say so in the run report and
   the page's caveat section. Follow-up queries are confirm-only. Write
   `<corpus>/connections-<brand_id>.md` with the frontmatter and sections in
   `profile-spec.md`, "CONNECT" (About creator, Thesis, About brand, one
   section per connection strongest first, Where this could go wrong last).
   Each quote is a `>` block with its `&t=` link on a `>` continuation line,
   or `--check` fails it. Then:

   ```bash
   python3 <skill>/scripts/build_html.py --check --in <corpus>/connections-<brand_id>.md \
     --facts tl-creator-profiles/<id>-facts.jsonl && \
   python3 <skill>/scripts/build_html.py --in <corpus>/connections-<brand_id>.md \
     --facts tl-creator-profiles/<id>-facts.jsonl
   ```

   `--check` exits 3 listing what the map lacks and writes nothing. The
   render writes `tl-creator-profiles/<id>-<brand_id>-connections.html`, the
   only file CONNECT adds. Publish it as an artifact where the host supports
   one.

## Guardrails

- **Read-only.** Nothing is sent to anyone; output comes back for review.
- **No prices, costs, rate cards or deal terms in any output**, ever.
- **One labelled sample read per connection at most.** No scripts, full
  reads, CTA wording or alternate versions.
- **Sensitivity is a tier** (`evidence-rules.md`): `clinical`, `children`
  and `location` stay out of connection angles by default. Beliefs are not
  sensitive. No protected-trait inference, ever.
- **Verbatim or not at all**; a partial quote match never publishes.
- **An empty answer is a real answer**: "no evidence found", with the
  coverage numbers that bound it.
