# Transcript mining

How a channel's transcripts become a profile. One retrieval flow, one
extraction fan-out, then judgment. Query credits are not a budget here; the
budgets are model tokens and tiers — which is why retrieval is a single
script, extraction runs on sonnet agents that each see one batch of windows
and nothing else, and the expensive context is spent only on judgment no
script can encode.

Measured end to end on five channels (Sydney Watson 20107, Alex Hormozi
253904 = 4,205 videos, Emma Chamberlain 3268, Professor G 1069544, Ali Abdaal
31792): fetch 7–21 s on the first three and 36–54 s on the two channels with
700–1,200 cue-matched videos — the clock follows the number of matched videos
whose passages come back, not the upload count; 310 gems / 500 windows, 405 /
497, 432 / 500; every quote exact by construction. The five-turn extractor
of those runs took 2.3–8 minutes per agent and 5.6–6.3 minutes per 20-agent
round plus re-spawns (10.7 minutes of extraction on Sydney Watson); the
single-message extractor below replaces it (262 s per 500-window round,
no re-spawns — see Speed).

## Layer 1+2: fetch the cue passages, one script

```bash
python3 <skill>/scripts/fetch_cues.py --channel <channel_id> \
  --host-terms "<surname>,<company>,<former role>"
```

Retrieval and selection are the same query. A boolean `should` of
`match_phrase` clauses over the cue phrases selects the videos, and the
index's `highlight` returns the passages around each hit with the timed-text
`start` attributes intact — so a 5,000-video channel costs a few dozen small
queries instead of a full transcript download, and every passage is born with
its `&t=` link. There is no local full-transcript scan any more; nothing is
downloaded that the model layer will not read.

**Flags:**

| flag | default | what it does |
|---|---|---|
| `--channel` | required | internal TL channel id, from `tl channels find` |
| `--host-terms` | none | comma-separated names/companies; a hit on one is a strong host anchor and scores double |
| `--out` | `tl-creator-profiles/.corpus` | corpus root; the channel id becomes a subdirectory, so concurrent channels never collide |
| `--phrases` | `references/cue-phrases.txt` | the cue list |
| `--max-windows` | 500 | the cap on what reaches the model layer in one round |
| `--batch-size` | derived | windows per batch file, one per extractor agent; default `ceil(windows kept / agent cap)` where the cap is `$CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (20 when unset), never below 5 — 500 windows make 20 × 25 on the standard 20-agent host |
| `--per-video-cap` | 8 | no single video may own the batch set |
| `--fragment-size` / `--fragments-per-doc` | 900 / 10 | passage width and how many per video |
| `--page-size` / `--concurrency` | 150 / 4 | paging and parallel year buckets |
| `--reserve` | 0 | agent slots held by other lanes during the fan-out (`1` when the socials lane is on). Batches are sized against `agent cap - reserve`, so the last extractor is not rejected and relaunched a wave later: 500 windows make 19 × 27 rather than 20 × 25 on a 20-agent host |
| `--exclude` | none | a `classified.jsonl` from an earlier round: passages already judged (same video, start within 30 s) are skipped |
| `--round` / `--since` | 1 / none | an incremental round: `--round N` batches into `batches-rN/`, `--since <YYYY-MM-DD>` bounds the fetch to uploads after the ledger's `latest_video_date` (without it a round re-pulls every unjudged passage in the catalogue) |

**`cue-phrases.txt`** is one phrase per line, `#` for comments. A leading
`~` marks a **recurring bit** — a greeting, a sign-off, a channel catchphrase
that fires in nearly every upload ("~welcome back to", "~my name is"). Those
still score, but they are capped hard, so a channel's fixed intro can seed a
few windows and never fill the batch set. Ordinary phrases are capped at the
larger of 12 windows or 8% of the cap; no single video passes
`--per-video-cap`; ties are spread across the channel's years, so a profile
spans the back catalogue rather than the last twelve months.

**Ad reads.** Windows are built with a regex heuristic in `in_sponsor_read`,
and once the cap is taken the kept windows' real sponsored spans are looked up
by video id (`sponsor_segments` in `fetch_cues.py`) and the flag is decided from them —
a window overlaps a read when `[start, start+30]` meets a sponsored span
padded 75 s either side. The lookup is authoritative when it succeeds; the
heuristic stays when it fails. The summary's `sponsor_source` says which one
decided, and it belongs in the run report whenever it reads `regex_fallback`.

**Output**, under `<out>/<channel_id>/`:

- `windows.jsonl.gz` — every passage found, ranked (a window carries
  `video_id` + `start`, not an assembled watch URL; whoever shows one builds
  `…watch?v=<video_id>&t=<start>s` at that point).
- `batches/batch-NNN.json` — the capped set, one file per extractor agent,
  sized to fill one wave of the host's agent cap (`--batch-size`).
- `corpus.jsonl.gz` — the same store shape the verifiers read, holding the
  fetched passages as cues, so `verify_quotes.py` runs unchanged. It is **passages, not transcripts**: `channel_context.py`'s
  corpus stats over it are a format hint, not a coverage census.

The summary (stdout) and one `FUNNEL stage=fetch_cues …` line (stderr) carry
`videos_matched`, `passages`, `windows_capped`, `batches`, `sponsor_source`
and `elapsed_s`. `passages` minus `windows_capped` is what stayed out of this
round — carry it into the profile's coverage header, because "absence is not
evidence" needs it.

**A second round is additive, never a re-run.** One round is the 500-window
cap spread over every agent the host runs at once. To go deeper —
or to use host terms the socials lane turned up after the fetch — run
`fetch_cues.py … --exclude <out>/classified.jsonl`: passages already judged
are skipped, so the new batches are new material and the ledger grows instead
of repeating. Do not raise `--max-windows` past what one round can extract.

## Layer 3: extraction — one fan-out, one message per agent

Every batch file is judged by exactly one extractor: the `tl-cli:gem-classifier`
agent (the file name is historical; the role is a **gem extractor**,
`model: sonnet` — haiku truncated its output at this size in testing). One
pass decides whether the window is self-disclosure AND writes what it says:
the third-person claim, the span of the window that proves it, the life
domain, the speaker guess and the sensitivity tier. The rubric has ONE home:
`references/extractor-rubric.md`, which names the two `evidence-rules.md`
sections it applies.

The extractor never assembles its own input. `scripts/extractor_prompt.py`
renders ONE self-contained message per batch — the rubric, those two
evidence sections, the context block and the batch's windows as JSON, plus
where to write the output — so the agent reads exactly one file and has
nothing else to fetch:

```bash
python3 <skill>/scripts/extractor_prompt.py \
  --batch <corpus>/<channel_id>/batches/batch-007.json \
  --context <corpus>/<channel_id>/context.json \
  --write-to <corpus>/<channel_id>/returns/batch-007.extract.json \
  --out <corpus>/<channel_id>/prompts/batch-007.md
```

`context.json` is written once after the channel context brief:
`{"channel_name", "host_names", "known_facts", "format_label",
"format_evidence"}`. Render every batch in one shell loop (a fraction of a
second each); the rendered message is ~35 KB for 25 windows.

Run the fan-out exactly like this:

1. **The batch files already exist.** `fetch_cues.py` wrote them. Do not
   re-fetch, do not re-batch, do not read `windows.jsonl.gz`.
2. **Render all prompts in one command**, then `ls <prompts_dir>/batch-*.md`
   is the work queue; each path is claimed exactly once.
3. **Spawn all N agents as N `Agent` tool_use blocks in ONE assistant
   message**, with nothing else in flight — every running agent counts
   against the host's concurrency cap, and an agent that queues behind the
   cap starts minutes late. One spawn per message is a bug, not a slow
   success — the same one-message rule governs every fan-out in this skill.
4. **Each agent's prompt is two lines**: read this one file
   (`<prompts_dir>/batch-NNN.md`) and follow it exactly; it is
   self-contained, read nothing else, run nothing, one Write, then the
   one-line receipt. Never paste the rendered message into the prompt, never
   a transcript, never a second batch.
5. **Each agent writes its file and returns one line**
   (`batch=NNN windows=<n> gems=<n>`). Results live in the file; the return
   line is a receipt. Then stop the turn and consume the completion
   notifications as they arrive.
6. **Never validate returns by hand** — `assemble_extracts.py` does it
   mechanically (Layer 3b). Track claimed batches; never spawn the same batch
   twice.

Each agent is three turns — Read the message, Write the JSON, reply the
receipt — so a batch cannot turn into an open-ended session: no
verification scripts, no Bash, no other Reads, no second Write.

**Forbidden in this fan-out, without exception:**

- **No polling.** No `ls`-and-count loops, no re-reading the returns directory
  to see whether agents finished.
- **No `sleep`**, and specifically never `sleep` with
  `run_in_background: true` — a backgrounded sleep returns instantly, so it
  waits for nothing while looking like a wait. A genuine timed wait on
  external state uses `Monitor` with an until-condition.
- **No sequential spawning**, no batching-of-batches, no "start with two and
  see how it goes".
- **No default-model stand-ins.** If `tl-cli:gem-classifier` does not resolve
  (running from a checkout rather than an installed plugin), copy
  `agents/gem-classifier.md` into `~/.claude/agents/` before the session
  starts and spawn `gem-classifier`; failing that, spawn `general-purpose`
  with an explicit `model: sonnet` override and the same two-line prompt —
  the rendered message already carries the whole rubric.
  A general-purpose agent on the inherited (expensive) model is the failure
  mode this list exists to prevent — it is how one past run reached 30M
  tokens.

**Concurrency.** The host runs at most `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`
agents at once, 20 when unset, and 20 is the standard: leave the variable
unset. Raising it to 40 was tested and slowed the run down (a second wave
queued behind the cap instead of one wave finishing together).
`fetch_cues.py` reads the cap and sizes the batches to fill one wave
(Layer 1+2).

There is no scripted extractor: every batch is judged by an agent, and the
only fallback is the `general-purpose` + `model: sonnet` spawn above.

Print the stage's own funnel line from the returned receipts:
`FUNNEL stage=extract batches=… agents=… windows=… gems=… elapsed_s=…`.

The model catches what no cue list can: "I'm allergic to peanuts", "we finally
finished the nursery", proper nouns read through misspellings, sarcasm and
hypotheticals. That is why the fetch layer gates nothing beyond the cap.

## Layer 3b: assemble — the contract is checked, not trusted

```bash
python3 <skill>/scripts/assemble_extracts.py \
  --batches <corpus>/<channel_id>/batches \
  --returns <corpus>/<channel_id>/returns \
  --out <corpus>/<channel_id> [--min-coverage 0.95]
```

Per batch it checks that every index `0 … N-1` appears exactly once across
`gems` and `not_gems`; that each verdict's echoed `start` matches the window
it claims (a hard check — a verdict about a different window is not a
verdict); that the enums are valid; and that the `quote_span` resolves to a
contiguous substring of the window text, which it **cuts mechanically**, so
every published quote is verbatim by construction rather than by trust. The
five-word `anchor` is advisory — agents normalise punctuation — and mismatches
are only counted.

It writes `classified.jsonl` (every judged window, and the `--exclude` input
for a later round), `gems.jsonl` (the cluster step's input), `candidates.jsonl`
and `respawn.json`. Windows that failed a check or were skipped are
**unjudged**: they stay out of those files and are listed in
`respawn.json`. **Coverage decides the exit code**, not perfection: with
`unjudged / expected` within the `--min-coverage` threshold (default 0.95,
so up to 25 of 500 windows) it exits **0** and the run continues — a few
lost windows may not even be gems, and they remain reachable through a later
`--exclude` round (unjudged passages are not in `classified.jsonl`, so the
next fetch offers them again). It exits **3** only below the threshold, or
when a batch file has no return file at all (an extractor that never ran);
then re-judge exactly the listed windows — `extractor_prompt.py --indexes
3,7 --write-to …/batch-NNN.extract.r2.json`, one agent per batch — and
re-run assemble (with `--append` on a later round: it replaces that round's
earlier rows for the same windows, so a re-assembly after a re-judge never
stacks a second copy of the round's gems). **Nothing is hand-patched** — a
hand-edited return is an unverifiable quote.

Assemble, cluster and merge-prepare are three quick scripts with no
judgment between them: run them as ONE `&&`-chained shell command as soon
as the fan-out's receipts are in, with each stage's JSON summary redirected
to a file and only the FUNNEL lines on stderr reaching you; exit 3 from
assemble stops the chain by design. Spawn the merge agent in the same
assistant message that reads that command's result — every notify-then-act
gap between stages costs 20–60 s.

## Layer 4: cluster, then the sharded merge pass judges

The mechanical bulk — verbatim checking — is code; the judgment slice is ONE
small Claude pass over the clustered candidates.

**First collapse the repeats, locally:**

```bash
python3 <skill>/scripts/cluster_gems.py --in gems.jsonl
```

A long back catalogue answers the same question hundreds of times: one
channel's 172 gems held "BioShock is my favorite game" 29 times over. The script writes `gems-clustered.jsonl` beside the input — one line per claim, in
the same shape as a gem line, so there is exactly one format downstream — and
`gems-clustered.slim.jsonl`, the same lines without the window text (verdict,
video, start, date, members), which is what the merge pass reads: a
channel's clusters fit one agent's read that way.
Singletons pass through as clusters of 1. Each line adds `occurrences` and
`members` (`video_id`, `start`, `published` for every member, the
representative included), and the representative is the cluster's
highest-information member, so nothing the merge pass needs is left behind.

Merging is conservative on purpose: gems must share a life domain, a speaker
guess and a sensitivity call, their one-line claims must agree — including on
polarity ("has kids" never merges into "does not have kids") and on numbers
("has 2 cats" never merges into "has 3 cats") — and every
member must match every other member. Near-duplicates that fail any of those
stay separate — a missed merge costs a few tokens, a false merge would delete
a distinct fact. Do not hand-merge what the script left apart.

**The merge pass returns decisions, never records.** A script prepares the
input, each shard's agent returns a few kilobytes of judgment, and the same
script expands them into the ledger.

```bash
python3 <skill>/scripts/merge_pass.py prepare \
  --clustered <corpus>/gems-clustered.jsonl --format <label> --out <corpus> \
  [--existing tl-creator-profiles/<channel_id>-facts.jsonl --state <corpus>/merge-state.json] \
  [--shards N]
```

`prepare` numbers the clusters `c001…` and writes `merge-input.jsonl`: one
compact line per cluster the agent must judge — `c`, `domain`, `speaker`,
`tier`, `conf` (the extractor's confirmed/likely), `claim`, `quote`,
`title`, `published`, `videos` (distinct video_ids over the members), `occ`,
`ad_read` (every member inside a sponsor read), `anchor` (a host anchor on
some member), `format_hint`, `lang`, `notable`. No window text, no member
list. Before the agent sees anything it applies the parts of
`evidence-rules.md` no judgment is needed for: `guest` and `cohost` voices
are dropped (a second named voice is unattributable on any format);
`unclear` is dropped on interview and multi-host formats (a window's own
`format_hint` beats the channel label); on solo and faceless-scripted
channels `unclear` and `narration` stay in as the host, capped at
`unconfirmed`. Caps beat overrides: the agent can lower a confidence, never
lift a capped cluster to `confirmed`. Those never reach the agent and are counted as
`auto_dropped`.

**Shard by default.** With `--shards N` the input is split by life domain into
N files for N agents (folds never cross a domain, so a shard is a complete
judgment unit), spawned in ONE message like the extraction fan-out. Size N as
`clusters / 60`, floor 1, ceiling 6. One agent on 226 clusters measured 475 s
and is the single slowest pass in the pipeline after extraction.

**The biggest domain sets the floor.** Shards hold whole domains, so a channel
whose clusters pile into one domain cannot split below that domain's size. A
226-cluster solo tech channel packed 105 / 40 / 40 / 41 at `--shards 4`,
because `work` alone held 105 of the 226; raising N to 6 changed nothing.
Read the per-file line counts from `prepare`'s JSON before assuming more
shards will help, and expect the saving to be set by the largest shard rather
than by the average.

Each shard's reply is saved as its own `merge-decisions-r1-sN.json` and all of
them are passed to `expand` as repeated `--decisions` flags. `selected` is
**unioned** across the files rather than replaced, because each shard can only
nominate from the domains it saw; `expand` still owns the final count.

Each shard's agent reads its `merge-input-N.jsonl` (`merge-input.jsonl` when
there is one shard; plus the identity lane's findings when that lane ran) and
**never the windows, never a transcript**. It decides
what a script cannot, per `evidence-rules.md`:

- attribution reasoning the features cannot encode (recurrence never
  confirms on a multi-host channel; an ad-read fact must recur outside
  reads);
- deduplication across clusters the script left apart for good reason but
  which say the same thing about the same fact — a `fold`;
- the sensitivity tier, **re-tiered where the extractor missed an obvious
  case** — a stated allergy is `lifestyle`, not `none` and not `clinical`;
- **dropping any candidate whose claim asserts more than its quote
  supports** — or narrowing the claim to what the quote says; never the
  wider claim;
- superseded-fact resolution (latest wins, history kept), confidence
  overrides where a rule above changes the default, and its proposed
  `selected` picks.

It returns ONE JSON object as its final message — never a file of records:

```json
{"decisions": {
   "c001": {"action": "keep"},
   "c013": {"action": "fold", "target": "c012"},
   "c014": {"action": "drop", "reason": "claim asserts more than the quote supports"},
   "c015": {"action": "keep", "tier": "lifestyle"},
   "c016": {"action": "keep", "claim": "narrowed to what the quote says"},
   "c017": {"action": "keep", "confidence": "unconfirmed"},
   "c018": {"action": "keep", "supersedes": "c003"},
   "c019": {"action": "keep", "gloss": "English translation of a non-English quote"},
   "c040": {"action": "fold", "target": "f007"}},
 "selected": ["c012", "c001", "f007", "s1"],
 "facts": [{"ref": "s1", "provenance": "social", "claim": "runs a pottery studio",
            "domain": "work", "sensitivity": "none",
            "source_url": "https://instagram.com/…", "seen_date": "2026-09-02",
            "corroborates": "c012"}]}
```

Every cluster in the input appears exactly once. `facts` is the identity
lane's output when that lane ran: one record per `social`/`web` disclosure
(never a quote, never a video — lanes do not masquerade), with its URL and
seen-date; `corroborates` names the cluster or existing fact it confirms,
which lifts both to `confirmed` (cross-lane corroboration is the top tier —
a social or web fact alone stays `unconfirmed`, and the agent cannot declare
otherwise). A compact input line carrying `dropped_members: N` is a cluster
that gained a passage the last round dropped — it is asked again rather than
silently joining a fact. `fold`/`supersedes`
targets are kept clusters in the same domain, or — on a refresh — existing
`f…` facts. The orchestrator saves the reply as
`<corpus>/merge-decisions-rN.json` and runs

```bash
python3 <skill>/scripts/merge_pass.py expand \
  --clustered <corpus>/gems-clustered.jsonl --decisions <corpus>/merge-decisions-rN.json \
  --format <label> --channel <channel_id> --out <corpus>/facts.jsonl \
  [--existing … --state …] [--fallback-original]
```

`expand` validates first — totality, unknown ids, targets (a fold into a
cluster or an existing fact must stay in its domain), fold cycles, a
supersession that resolves to the fact itself or a cycle, enums, identity
records, and a narrowed claim that introduces a number token its quote and
cluster claim lack — and exits **3** with the offending ids and reasons. The
orchestrator re-asks the agent for exactly those ids ONCE and passes the
reply as a second `--decisions` file (later files override earlier ones per
id); on a second failure it runs with `--fallback-original`, which keeps the
cluster's own claim for the still-offending ids and names them in the
summary. Bounded: two asks, never a loop, never a hand edit.

Then it builds every record from the cluster's representative: claim
(narrowed if given), domain, quote, video, window `start`, the derived
`url`, `published`; **recurrence = distinct `video_id`s over the cluster
and everything folded into it**, never `occurrences`; confidence = the
agent's override, else the format-gated default — solo: the extractor's
`confirmed`→confirmed, `likely`→unconfirmed; interview/multi-host or a
window hinting interview/reaction: confirmed only with a host anchor; a
cluster entirely inside sponsor reads, or a solo `unclear`/narration voice,
caps at unconfirmed; the tier and its derived `sensitive` flag;
`superseded_by`; `members` (the passage keys the fact was built from);
`selected` — the agent's picks first, then filled to 20 across the active
ledger by confidence and recurrence, trimmed past 20 the same way. fact_ids
are `f001…` in cluster order on a fresh build. It writes `facts.jsonl`
(working file, no header), `merge-state.json`, and prints
`FUNNEL stage=merge clusters=… judged=… auto_dropped=… facts=… folded=…
dropped=… selected=… elapsed_s=…`.

### Incremental round

Decision `refresh` from `ledger_meta.py check` (round `N` = its `next_round`):

```bash
python3 <skill>/scripts/fetch_cues.py --channel <id> --host-terms "…" --round N \
  --since <latest_video_date> --exclude <corpus>/classified.jsonl
# fan out extractors over <corpus>/batches-rN only, then
python3 <skill>/scripts/assemble_extracts.py --batches <corpus>/batches-rN \
  --returns <corpus>/returns-rN --out <corpus> --append && \
python3 <skill>/scripts/cluster_gems.py --in <corpus>/gems.jsonl && \
python3 <skill>/scripts/merge_pass.py prepare --clustered <corpus>/gems-clustered.jsonl \
  --format <label> --existing tl-creator-profiles/<id>-facts.jsonl \
  --state <corpus>/merge-state.json --out <corpus>
# merge agents as usual, then expand with the same --existing --state, verify, and
python3 <skill>/scripts/ledger_meta.py write --channel <id> --from <corpus>/facts.jsonl.verified.jsonl --rounds N
```

Cost scales with the new uploads, not the corpus. `expand` starts from the
existing ledger rather than a blank page. `prepare --existing --state` maps every re-clustered cluster
by its **member keys** (`<video_id>:<window start>` — never the fact's
`start`, which the verifier rewrites): members all known to one existing
fact → additive, judgment carried, recurrence recomputed, not sent to the
agent; members known to two or more facts, or one that also carries a passage
dropped last round → re-judged, with those `f…` ids listed for the agent (a
kept re-judged cluster keeps the first inherited id and marks the others
superseded by it, their evidence pooled); every member
previously dropped → stays dropped; no known member → new. Only the new and
re-judged clusters reach the agent, along with a compact list of the
existing facts to fold into or supersede. `expand` keeps every fact_id,
continues numbering after the existing max, marks a superseded fact (never
deletes it), and asserts every existing fact is claimed by exactly one
cluster.

**Then verify in bulk, locally:**

```bash
python3 <skill>/scripts/verify_quotes.py --in facts.jsonl \
  --corpus tl-creator-profiles/.corpus/<channel_id>/corpus.jsonl.gz
```

Every transcript-provenance quote is located in the stored passages. Only
`match: "exact"` publishes, and its located timestamp is authoritative.
`partial` and `none` are flagged, never accepted: fix the quote to the
caption text the result shows, or drop the fact. A quote that needs more
than a mechanical fix goes back through the merge pass, not past it. Verified facts become `<channel_id>-facts.jsonl` via `ledger_meta.py write
--from`, which puts the build's meta record on line 1 of the same file per
`references/profile-spec.md`.

**The orchestrating context never sees raw transcripts.** It sees the fetch
summary, the extractors' receipt lines, the assemble summary, the merge
pass's decisions and the expand summary, and the rendered page.

## Entity expansion: a second round, not a re-scan

When a gem surfaces a new entity — "my dog Luna", a spouse's name, a company —
or the socials lane returns a name the first fetch did not have, deepen the
ledger with another additive round:

```bash
python3 <skill>/scripts/fetch_cues.py --channel <id> \
  --host-terms "…,Luna,<other new entities>" \
  --exclude <corpus>/<channel_id>/classified.jsonl
```

Passages already judged are skipped, so the round costs one fetch (seconds)
plus one extraction fan-out over genuinely new material. (A *refresh* round —
new uploads, not new terms — adds `--since <latest_video_date>` so the fetch
is bounded to what the ledger has not seen; measured live, an unbounded
`--exclude` round on a 283-video channel re-pulled 1,765 unjudged passages
and would have cost another full fan-out.) Confirmed entities
also feed CONNECT's connection probes and improve attribution (a fact tied to a
known family name anchors the host).

## The channel context brief

```bash
python3 <skill>/scripts/channel_context.py --channel <id> --corpus <corpus>/corpus.jsonl.gz \
  --per-video-out <corpus>/per-video.jsonl > <corpus>/context-full.json
# the model calls the label from context-full.json, then:
python3 <skill>/scripts/channel_context.py --from <corpus>/context-full.json \
  --format-label <label> --format-evidence "…" [--host-names "…"] [--known-facts "…"] \
  --write-context <corpus>/context.json
```

The second command writes the compact `context.json` every extractor prompt
takes; nothing about it is typed by hand.

After the fetch, format is measured rather than guessed: first-person
density, interview markers, question density, title hints. The corpus it reads
is now the fetched **passages**, not whole transcripts, so the densities are a
format hint on the material the profile is actually built from, never a
coverage census. A model
read of a small sample (3–5 videos' worth of windows) plus these stats calls
the label — solo / interview / multi-host / faceless-scripted — **with
evidence**. The label exists for two reasons only:

1. It is the attribution context handed to the classifier: interview means
   guest voices contaminate; solo means everything is the host.
2. Near-zero first-person density flags "likely faceless" early, so model
   tokens aren't spent on a channel with nothing to find.

Nothing exits early. A faceless channel with one personal Q&A upload still
surfaces it; the flag only reorders effort and sets expectations in the
profile header.

## Speed

Wall clock is the extraction fan-out plus the merge pass. Measured: the fetch
is **7–21 s on small channels and 36–54 s on channels with 700–1,200
cue-matched videos**, the local scripts (assemble, cluster, expand, verify)
are seconds, and the merge pass is one agent returning a few kilobytes of
decisions (260 s on a 273-cluster channel). The old five-turn extractor —
four Reads and a Write, each turn re-processing a growing context, ~115K
input tokens per agent — ran 2.3–8 minutes per agent and 10.7 minutes per
round with its re-spawns on Sydney Watson. The single-message extractor
(Layer 3) is one Read, one Write and a receipt: measured on the same channel
(2026-09-02, 20 agents × 25 windows, nothing else in flight) **262 s of wall
clock** with agents at 119–218 s each, 497/500 windows assembled, and the
coverage threshold (Layer 3b) let the run continue past the 3 unjudged
windows with no re-spawn round. The whole fresh PROFILE run was 706 s
(fetch 27 s, context brief 19 s, extraction 262 s, assemble→cluster→prepare
1 s, merge agent 280 s, expand→verify→write 16 s), 206 facts, every quote
exact — against 1,201 s before the change. Claude's share of a profile build is the fan-out
plus a handful of turns: the identity lane, the format call, one merge pass —
**one extractor per batch, one merge agent per shard, one optional socials lane**, not
one per window and not one per fact.

Every stage prints its own `elapsed_s` on its `FUNNEL` line, so "it was slow"
is always answerable with a stage name. Rounds are the knob that matters: one
round is 500 windows spread over every agent the host runs at once, and going
deeper means another `--exclude` round rather than a bigger cap.
