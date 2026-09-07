#!/usr/bin/env python3
"""The merge pass as a compact decision contract: the agent judges, this script
materialises the ledger.

The old merge pass asked one agent to compose the whole ledger — ~220 full fact
records, ~118 KB in a single write — of which roughly three quarters was
verbatim copying of fields the clustered file already held. It cost 21 minutes
of a 33-minute run. Everything mechanical in that output is derivable here:
fact ids, urls, recurrence over distinct videos, the confidence default, the
sensitivity boolean, the `selected` pick. What is left for a model is the
judgment a script cannot make — attribution, folds, narrowing an over-reaching
claim, supersession — and that fits in a few kilobytes of decisions.

Two subcommands:

``prepare``
    Numbers the clusters ``c001…`` in file order and writes one compact line
    per cluster the agent must judge (no window text, no member list), plus —
    on a refresh — a compact view of the existing ledger to fold into or
    supersede. The deterministic half of ``references/evidence-rules.md`` runs
    here: guest windows never reach the agent, and neither do cohost/unclear
    windows on a shared-voice format. On a refresh the previous round's
    ``merge-state.json`` decides which clusters are genuinely new; the rest
    carry their judgment forward untouched.

``expand``
    Validates the returned decisions like ``assemble_extracts.py`` validates
    extractor returns — every judged cluster placed exactly once, targets that
    exist, enums, fold chains that terminate, no number in a narrowed claim
    that is not in the evidence — and exits 3 with the offending ids so the
    orchestrator re-asks for exactly those as a patch decisions file, never
    hand-patches. Then it builds the facts, rewrites ``merge-state.json`` so
    the next round can inherit, and prints the FUNNEL line.

Usage:
    merge_pass.py prepare --clustered <corpus>/gems-clustered.jsonl
        --format <solo|interview|multi_host|faceless_scripted>
        [--existing <profiles>/<id>-facts.jsonl]
        [--state <corpus>/merge-state.json] [--shards N] --out <corpus>

    merge_pass.py expand --clustered <corpus>/gems-clustered.jsonl
        --decisions <file> [--decisions <patch> …]
        [--existing <profiles>/<id>-facts.jsonl]
        [--state <corpus>/merge-state.json]
        --format <label> --channel <id> --out <corpus>/facts.jsonl
        [--fallback-original]

The decisions file (what the agent returns) is ONE object:

    {"decisions": {
        "c013": {"action": "fold", "target": "f007"},
        "c014": {"action": "drop", "reason": "claim asserts more than the quote"},
        "c015": {"action": "keep", "tier": "lifestyle", "claim": "narrowed claim",
                 "confidence": "unconfirmed", "supersedes": "f003",
                 "gloss": "English translation of a non-English quote"}},
     "selected": ["c015", "f001"],
     "facts": [{"ref": "s1", "provenance": "social", "claim": "runs a pottery studio",
                "domain": "work", "sensitivity": "none",
                "source_url": "https://instagram.com/…", "seen_date": "2026-09-02",
                "corroborates": "c012"}]}

The optional top-level ``facts`` list is the identity lane's way into the
ledger: social/web facts carry ``source_url`` and ``seen_date`` instead of a
quote, a video and a start, are numbered after the clusters, and publish at
``unconfirmed`` unless ``corroborates`` names a kept cluster or an existing
fact — cross-lane corroboration is the top tier in ``evidence-rules.md``, so
it lifts BOTH facts to ``confirmed``. An optional ``ref`` is what ``selected``
can name them by.

Every field of a ``keep`` other than ``action`` is optional. ``target`` and
``supersedes`` may name a kept cluster (``c*``) or, on a refresh, an existing
fact (``f*``); a fold target must share the domain. Later ``--decisions``
files override earlier ones per cluster id, which is how the exit-3 re-ask
lands as a small patch.

Exit 0 on success, 3 on a contract violation (with the offending ids on
stdout), 2 on a usage error.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from store_io import read_ledger, write_ledger  # noqa: E402

DOMAINS = {"origin", "family", "pets", "home", "work", "money", "health",
           "habits", "tastes", "beliefs", "relationships", "other"}
# The socials lane writes its own `facts` records rather than judging clusters,
# so it reaches for domain words the enum does not carry ("hobbies", "gear").
# Measured on a live run: 5 of 9 identity facts used a near-miss label and the
# whole expand failed, costing a hand-written patch. These are the near-misses
# with exactly one sensible target; anything else still fails loudly with the
# allowed list. The aliasing is reported, never silent.
DOMAIN_ALIASES = {
    "hobbies": "habits", "hobby": "habits", "interests": "habits",
    "routine": "habits", "routines": "habits",
    # An ambiguous label lands in the enum's own catch-all rather than a
    # guessed specific domain: "other" is honest, a wrong "work" is not.
    "identity": "other", "personal": "other", "misc": "other",
    "gear": "other", "stuff": "other",
    "career": "work", "job": "work", "business": "work",
    "location": "home", "residence": "home",
    "fitness": "health", "wellness": "health",
    "food": "tastes", "preferences": "tastes",
    "partner": "relationships", "marriage": "relationships",
    "childhood": "origin", "background": "origin", "history": "origin",
    "finances": "money",
}
SENSITIVITY = {"none", "lifestyle", "clinical", "children", "location"}
WITHHELD = {"clinical", "children", "location"}
CONFIDENCE = {"confirmed", "unconfirmed"}
# Sensitivity near-misses. An alias may only ever move a value to a MORE
# protective tier: raising "medical" to `clinical` withholds a fact that would
# otherwise have been usable, which is the safe direction to be wrong in.
# Nothing aliases INTO "none", because that would strip protection from a fact
# the lane was trying to flag.
SENSITIVITY_ALIASES = {
    "medical": "clinical", "health": "clinical", "diagnosis": "clinical",
    "condition": "clinical", "illness": "clinical",
    "kids": "children", "child": "children", "kid": "children",
    "address": "location", "geo": "location", "where": "location",
    "personal": "lifestyle", "private": "lifestyle",
}
# Confidence near-misses. The extractor stage upstream grades a passage
# `confirmed` / `likely` / `unconfirmed`, and the merge agent carries its
# vocabulary downstream into a field that holds only two of those words. Every
# alias here resolves DOWNWARD to `unconfirmed`: a near-miss must never promote
# a fact to `confirmed`, because cross-lane corroboration is the only thing
# that earns the top tier (`evidence-rules.md`).
CONFIDENCE_ALIASES = {
    "likely": "unconfirmed", "probable": "unconfirmed",
    "possible": "unconfirmed", "unclear": "unconfirmed",
    "uncertain": "unconfirmed", "unverified": "unconfirmed",
    "partial": "unconfirmed", "weak": "unconfirmed",
}
ACTIONS = {"keep", "fold", "drop"}
# The identity lane. `evidence-rules.md`: lanes never masquerade as each other,
# so a social/web fact names its source and seen-date and carries no quote,
# video or start — those belong to the transcript lane alone.
IDENTITY_PROVENANCE = {"social", "web"}
IDENTITY_REQUIRED = ("claim", "domain", "sensitivity", "source_url", "seen_date")
IDENTITY_BANNED = ("quote", "video", "start", "url")
FORMATS = {"solo", "interview", "multi_host", "faceless_scripted"}

# Formats where one voice holds the transcript, so an unattributed window is
# the host's (capped at unconfirmed) rather than unattributable.
SINGLE_VOICE = {"solo", "faceless_scripted"}

# A window's own hint outranks the channel label for that cluster: a solo
# channel's one interview upload is still an interview.
_HINT_NON_SOLO = re.compile(r"interview|collab|reaction", re.I)

# Facts the connections page leads with. The agent proposes, the script owns
# the final count (revision 3 of the plan): never a contract violation.
SELECTED_TARGET = 20

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def funnel(**fields) -> None:
    print("FUNNEL " + " ".join(f"{k}={v}" for k, v in fields.items()),
          file=sys.stderr)


def read_jsonl(path: pathlib.Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{n}: not JSON ({exc})") from exc
    return out


def write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str,
                                separators=(",", ":")) + "\n")


# --------------------------------------------------------------------------- #
# cluster accessors — one place that knows the clustered line's shape
# --------------------------------------------------------------------------- #
def cid(index: int) -> str:
    return f"c{index + 1:03d}"


def member_key(member: dict) -> str:
    """The identity a refresh inherits by.

    Never the fact's own (video, start): ``verify_quotes.py`` rewrites a
    fact's start to the located timestamp, so only the cluster member's own
    offset survives a round.
    """
    return f"{member.get('video_id')}:{member.get('start')}"


def members_of(line: dict) -> list[dict]:
    members = line.get("members")
    if isinstance(members, list) and members:
        return [m for m in members if isinstance(m, dict)]
    w = line.get("window") or {}
    return [{"video_id": w.get("video_id"), "start": w.get("start"),
             "published": w.get("published")}]


def member_keys(line: dict) -> list[str]:
    return [member_key(m) for m in members_of(line)]


def distinct_videos(line: dict) -> list[str]:
    seen: list[str] = []
    for m in members_of(line):
        vid = m.get("video_id")
        if vid and vid not in seen:
            seen.append(vid)
    return seen


def _member_flag(line: dict, field: str, mode: str) -> bool:
    """``all``/``any`` over a boolean the members carry.

    ``cluster_gems.py`` writes ``in_sponsor_read`` and ``host_anchor`` onto
    every member ref. A clustered file written before that change has them
    only on the representative window: a singleton then answers from the
    window, and a multi-member cluster answers conservatively (an unknown
    member is not an ad read, and is not an anchor).
    """
    members = members_of(line)
    known = [bool(m.get(field)) for m in members if field in m]
    if not known:
        window = line.get("window") or {}
        if field in window and len(members) <= 1:
            return bool(window.get(field))
        return bool(window.get(field)) if mode == "any" else False
    if len(known) != len(members) and mode == "all":
        return False
    return all(known) if mode == "all" else any(known)


def ad_read_only(line: dict) -> bool:
    return _member_flag(line, "in_sponsor_read", "all")


def anchored(line: dict) -> bool:
    return _member_flag(line, "host_anchor", "any")


def cluster_claim(line: dict) -> str:
    v = line.get("verdict") or {}
    return str(v.get("claim") or v.get("notable") or "").strip()


def effective_format(line: dict, fmt: str) -> str:
    """The channel format, unless this cluster's own window says otherwise."""
    hint = str((line.get("window") or {}).get("format_hint") or "")
    if hint and _HINT_NON_SOLO.search(hint):
        return "interview"
    return fmt


def compact(line: dict, index: int) -> dict:
    """One line of ``merge-input.jsonl``: everything the judgment needs and
    nothing else — no window text, no member list."""
    w = line.get("window") or {}
    v = line.get("verdict") or {}
    return {
        "c": cid(index),
        "domain": v.get("life_domain"),
        "speaker": v.get("speaker_guess"),
        "tier": v.get("sensitivity"),
        "conf": v.get("confidence"),
        "claim": cluster_claim(line),
        "quote": v.get("quote"),
        "title": w.get("title"),
        "published": w.get("published"),
        "videos": len(distinct_videos(line)),
        "occ": line.get("occurrences") or len(members_of(line)),
        "ad_read": ad_read_only(line),
        "anchor": anchored(line),
        "format_hint": w.get("format_hint"),
        "lang": w.get("language"),
        "notable": v.get("notable"),
    }


# --------------------------------------------------------------------------- #
# the deterministic pre-judgment (evidence-rules + the refresh state)
# --------------------------------------------------------------------------- #
def auto_drop_reason(line: dict, fmt: str) -> str | None:
    """The drops ``evidence-rules.md`` makes without a model.

    ``guest`` is another voice on any format. ``cohost`` names a second voice
    too, so it never publishes as the host. ``unclear`` is an honest answer on
    a shared-voice format and drops there; on a single-voice format it (and
    ``narration``) publishes as the host, capped at ``unconfirmed``.
    """
    speaker = str((line.get("verdict") or {}).get("speaker_guess") or "")
    fmt = effective_format(line, fmt)
    if speaker == "guest":
        return "speaker guest"
    if speaker == "cohost":
        return "speaker cohost"
    if speaker == "unclear" and fmt not in SINGLE_VOICE:
        return f"speaker unclear on {fmt} format"
    return None


def load_state(path: pathlib.Path | None) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    members = data.get("members") if isinstance(data, dict) else None
    if not isinstance(members, dict):
        return {}
    return {k: v for k, v in members.items() if isinstance(v, dict)}


def plan(clusters: list[dict], fmt: str, state: dict[str, dict],
         existing_ids: set[str]) -> list[dict]:
    """One record per cluster saying who judges it.

    ``judge`` — sent to the agent (new, or re-judged because its members now
    span several existing facts, or because one of them was dropped last
    round). ``additive`` — every known member belongs to ONE existing fact and
    none was dropped, so the judgment carries and only recurrence changes.
    ``auto_dropped`` — a deterministic evidence-rules drop. ``carry_dropped``
    — every member was dropped last round and nothing new joined.
    """
    out: list[dict] = []
    for i, line in enumerate(clusters):
        rec: dict = {"c": cid(i), "index": i, "known": [], "fact_id": None,
                     "reason": None}
        reason = auto_drop_reason(line, fmt)
        if reason:
            rec["status"] = "auto_dropped"
            rec["reason"] = reason
            out.append(rec)
            continue
        keys = member_keys(line)
        known_facts: list[str] = []
        dropped = 0
        unknown = 0
        for key in keys:
            entry = state.get(key)
            if not entry:
                unknown += 1
                continue
            fid = entry.get("fact") or entry.get("folded")
            if fid and fid in existing_ids:
                if fid not in known_facts:
                    known_facts.append(fid)
            elif "dropped" in entry:
                dropped += 1
            else:
                unknown += 1
        rec["known"] = known_facts
        rec["dropped_members"] = dropped
        if len(known_facts) == 1 and not dropped:
            rec["status"] = "additive"
            rec["fact_id"] = known_facts[0]
        elif known_facts:
            # Either the members now span several facts, or one of them was
            # dropped last round and the rest were kept. Both are a changed
            # picture, and carrying the old judgment forward would silently
            # resurrect a dropped window or merge two facts — the agent
            # decides, with the ids it is deciding between.
            rec["status"] = "judge"
            rec["rejudge"] = True
        elif dropped and not unknown:
            rec["status"] = "carry_dropped"
            rec["reason"] = "every member was dropped in an earlier round"
        else:
            rec["status"] = "judge"
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# prepare
# --------------------------------------------------------------------------- #
def shard_rows(rows: list[dict], shards: int) -> list[list[dict]]:
    """Pack whole life domains into ``shards`` files.

    Folds only happen inside a domain, so a domain split across two agents
    would make a fold impossible to express. Biggest domain into the emptiest
    shard, deterministically.
    """
    by_domain: dict[str, list[dict]] = {}
    for row in rows:
        by_domain.setdefault(str(row.get("domain") or ""), []).append(row)
    buckets: list[list[dict]] = [[] for _ in range(shards)]
    order = sorted(by_domain.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for _, group in order:
        target = min(range(shards), key=lambda i: (len(buckets[i]), i))
        buckets[target].extend(group)
    for bucket in buckets:
        bucket.sort(key=lambda r: r["c"])
    return buckets


def existing_line(fact: dict, rejudge: bool) -> dict:
    return {"f": fact.get("fact_id"), "domain": fact.get("domain"),
            "tier": fact.get("sensitivity"), "claim": fact.get("claim"),
            "recurrence": fact.get("recurrence"),
            "rejudge": bool(rejudge)}


def cmd_prepare(a: argparse.Namespace) -> int:
    t0 = time.monotonic()
    clusters = read_jsonl(pathlib.Path(a.clustered))
    out_dir = pathlib.Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    existing_facts: list[dict] = []
    if a.existing:
        _, existing_facts = read_ledger(a.existing)
    existing_ids = {str(f.get("fact_id")) for f in existing_facts}

    state_path = pathlib.Path(a.state) if a.state else out_dir / "merge-state.json"
    state = load_state(state_path if a.existing or a.state else None)

    records = plan(clusters, a.format, state, existing_ids)
    rows: list[dict] = []
    for rec in records:
        if rec["status"] != "judge":
            continue
        row = compact(clusters[rec["index"]], rec["index"])
        if rec["known"]:
            row["known"] = rec["known"]
        if rec.get("dropped_members"):
            row["dropped_members"] = rec["dropped_members"]
        rows.append(row)

    files: list[str] = []
    if a.shards and a.shards > 1:
        for n, bucket in enumerate(shard_rows(rows, a.shards), 1):
            path = out_dir / f"merge-input-{n}.jsonl"
            write_jsonl(path, bucket)
            files.append(str(path))
    else:
        path = out_dir / "merge-input.jsonl"
        write_jsonl(path, rows)
        files.append(str(path))

    if existing_facts:
        rejudge_ids = {f for rec in records if rec["status"] == "judge"
                       for f in rec["known"]}
        path = out_dir / "merge-existing.jsonl"
        write_jsonl(path, [existing_line(f, str(f.get("fact_id")) in rejudge_ids)
                           for f in existing_facts])
        files.append(str(path))

    counts = {"clusters": len(clusters),
              "judged": sum(1 for r in records if r["status"] == "judge"),
              "rejudge": sum(1 for r in records if r.get("rejudge")),
              "additive": sum(1 for r in records if r["status"] == "additive"),
              "auto_dropped": sum(1 for r in records if r["status"] == "auto_dropped"),
              "carry_dropped": sum(1 for r in records if r["status"] == "carry_dropped"),
              "existing_facts": len(existing_facts)}
    elapsed = round(time.monotonic() - t0, 1)
    print(json.dumps({**counts, "files": files, "format": a.format,
                      "elapsed_s": elapsed,
                      "note": ("one line per cluster the agent must judge; "
                               "auto-dropped and additive clusters never "
                               "reach it")}, indent=1))
    funnel(stage="merge-prepare", elapsed_s=elapsed, **counts)
    return 0


# --------------------------------------------------------------------------- #
# expand
# --------------------------------------------------------------------------- #
def load_decisions(paths: list[str]) -> tuple[dict[str, dict], list[str],
                                              list[dict], list[str]]:
    """Merge the decision files. Later files override earlier ones per id,
    which is what makes the exit-3 re-ask a small patch instead of a rewrite.

    ``selected`` is a **union** across files, in first-seen order, because a
    sharded merge returns one file per shard and each shard can only nominate
    from the domains it saw: replacing would let the last shard read silently
    decide the whole page. ``expand`` still owns the final count.

    The identity-lane ``facts`` list stays a whole-document field, so a later
    file that carries one replaces it and a file that omits it leaves the
    earlier answer standing. An explicitly empty ``facts`` is a violation
    rather than a silent wipe of the lane, and a file that changes nothing at
    all is a violation too: a patch that no-ops is the failure mode that
    costs a stage a manual diagnosis."""
    decisions: dict[str, dict] = {}
    selected: list[str] = []
    identity: list[dict] = []
    problems: list[str] = []
    identity_seen = False
    for p in paths:
        path = pathlib.Path(p)
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "",
                     path.read_text(encoding="utf-8").strip())
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: not JSON ({exc})")
            continue
        if not isinstance(data, dict):
            problems.append(f"{path.name}: envelope is not an object")
            continue
        got = data.get("decisions")
        if not isinstance(got, dict):
            problems.append(f"{path.name}: no `decisions` object")
            continue
        touched = False
        for key, value in got.items():
            if not isinstance(value, dict):
                problems.append(f"{path.name}: decision for {key} is not an object")
                continue
            decisions[str(key)] = value
            touched = True
        if isinstance(data.get("selected"), list):
            for x in data["selected"]:
                if str(x) not in selected:
                    selected.append(str(x))
            touched = True
        if "facts" in data:
            if not isinstance(data["facts"], list):
                problems.append(f"{path.name}: `facts` is not a list")
            elif not data["facts"] and identity_seen:
                problems.append(
                    f"{path.name}: `facts` is empty, which would drop the "
                    f"{len(identity)} identity-lane fact(s) an earlier file "
                    "carried; omit the key to leave them standing")
            else:
                identity = [x for x in data["facts"] if isinstance(x, dict)]
                if len(identity) != len(data["facts"]):
                    problems.append(f"{path.name}: a `facts` entry is not an object")
                identity_seen = True
                touched = True
        if not touched:
            problems.append(
                f"{path.name}: carries no decisions, no `selected` and no "
                "`facts`, so it changes nothing; a patch file must say what "
                "it is patching")
    return decisions, selected, identity, problems


def _alias_field(rec: dict, field: str, allowed: set[str],
                 aliases: dict[str, str], label: str) -> str | None:
    """Rewrite one near-miss enum value in place. Returns a
    ``label.field: was -> now`` note, or None when there was nothing to do."""
    got = rec.get(field)
    if not isinstance(got, str) or got in allowed:
        return None
    now = aliases.get(got.strip().lower())
    if not now:
        return None
    rec[field] = now
    return f"{label}.{field}: {got} -> {now}"


def alias_enums(decisions: dict[str, dict], identity: list[dict]) -> list[str]:
    """Rewrite near-miss enum values to the enum, in place, before ``validate``
    sees them. Returns one ``ref.field: was -> now`` note per rewrite so the
    caller can report them: a correction nobody sees is how a lane keeps
    getting it wrong. A value with no single sensible target is left alone for
    ``validate`` to reject with the allowed list.

    Two stages reach for words the enums do not carry, and prose in the prompt
    has not stopped either. The socials lane writes its own ``facts`` records
    rather than judging clusters, so it invents domain labels ("hobbies",
    "gear", "history"). The merge agent carries the extractor's confidence
    vocabulary downstream ("likely"), which cost a measured run a 123 s
    re-ask. Both are near-misses with exactly one sensible target, so they are
    normalised here rather than argued with in the prompt."""
    notes: list[str] = []
    # Only `confidence` is aliased on the decision path, and deliberately not
    # `tier`: the merge agent judges against a rubric that states the five
    # tiers, and its tier vocabulary has never drifted, so a wrong tier there
    # should still fail loudly. The confidence field is different — it is the
    # extractor's own vocabulary arriving one stage late.
    for key, dec in sorted(decisions.items()):
        if not isinstance(dec, dict):
            continue
        note = _alias_field(dec, "confidence", CONFIDENCE,
                            CONFIDENCE_ALIASES, key)
        if note:
            notes.append(note)
    for i, rec in enumerate(identity):
        if not isinstance(rec, dict):
            continue
        label = rec.get("ref") or f"facts[{i}]"
        for field, allowed, aliases in (
                ("domain", DOMAINS, DOMAIN_ALIASES),
                ("sensitivity", SENSITIVITY, SENSITIVITY_ALIASES),
                ("confidence", CONFIDENCE, CONFIDENCE_ALIASES)):
            note = _alias_field(rec, field, allowed, aliases, label)
            if note:
                notes.append(note)
    return notes


def numbers_in(text: str) -> list[str]:
    return _NUMBER.findall(text or "")


def resolve_fold(start: str, folds: dict[str, str]) -> tuple[str | None, bool]:
    """Follow a fold chain to its terminal. Returns (terminal, cycle)."""
    seen = {start}
    node = start
    while node in folds:
        node = folds[node]
        if node in seen:
            return None, True
        seen.add(node)
    return node, False


def validate(records: list[dict], clusters: list[dict], decisions: dict[str, dict],
             existing: dict[str, dict], identity: list[dict],
             fallback: bool) -> tuple[dict, dict, list[dict]]:
    """The contract check. Returns (violations, folds, fallbacks)."""
    violations: dict[str, list[str]] = {}
    existing_ids = set(existing)

    def bad(key: str, why: str) -> None:
        violations.setdefault(key, []).append(why)

    judged = {r["c"]: r for r in records if r["status"] == "judge"}
    by_cid = {cid(r["index"]): clusters[r["index"]] for r in records}

    for key in decisions:
        if key not in judged:
            bad(key, "unknown id: not a cluster this round asked about")

    for key in judged:
        if key not in decisions:
            bad(key, "missing decision")

    kept: dict[str, dict] = {}
    folds: dict[str, str] = {}
    for key, dec in decisions.items():
        if key not in judged:
            continue
        action = dec.get("action")
        if action not in ACTIONS:
            bad(key, f"action must be one of {sorted(ACTIONS)}, got {action!r}")
            continue
        if action == "keep":
            kept[key] = dec
        elif action == "fold":
            target = str(dec.get("target") or "")
            if not target:
                bad(key, "fold without a target")
            else:
                folds[key] = target

    for key, dec in kept.items():
        tier = dec.get("tier")
        if tier is not None and tier not in SENSITIVITY:
            bad(key, f"tier must be one of {sorted(SENSITIVITY)}, got {tier!r}")
        conf = dec.get("confidence")
        if conf is not None and conf not in CONFIDENCE:
            bad(key, f"confidence must be one of {sorted(CONFIDENCE)}, got {conf!r}")

    # fold targets: a c* target must be a kept cluster in the same domain, an
    # f* target must exist in the ledger we are refreshing.
    for key, target in folds.items():
        if target.startswith("f"):
            if target not in existing_ids:
                bad(key, f"fold target {target} is not in --existing")
            else:
                src = (by_cid[key].get("verdict") or {}).get("life_domain")
                dst = (existing[target] or {}).get("domain")
                if src != dst:
                    bad(key, f"fold across domains ({src} -> {dst})")
            continue
        if target not in kept:
            bad(key, f"fold target {target} is not a kept cluster")
            continue
        src = (by_cid[key].get("verdict") or {}).get("life_domain")
        dst = (by_cid[target].get("verdict") or {}).get("life_domain")
        if src != dst:
            bad(key, f"fold across domains ({src} -> {dst})")

    for key in folds:
        _, cycle = resolve_fold(key, {k: v for k, v in folds.items()
                                      if not v.startswith("f")})
        if cycle:
            bad(key, "fold chain is a cycle")

    for key, dec in kept.items():
        sup = dec.get("supersedes")
        if sup is None:
            continue
        sup = str(sup)
        if sup.startswith("f"):
            if sup not in existing_ids:
                bad(key, f"supersedes target {sup} is not in --existing")
        elif sup not in kept:
            bad(key, f"supersedes target {sup} is not a kept cluster")

    # A narrowed claim may only narrow: non-empty, and every number in it has
    # to come from the quote or the cluster's own claim. A cheap tripwire, not
    # a proof — but it is the one that catches an invented figure.
    fallbacks: list[dict] = []
    for key, dec in kept.items():
        if "claim" not in dec:
            continue
        claim = str(dec.get("claim") or "").strip()
        line = by_cid[key]
        # Token comparison, never substring: "has 3 dogs" must not pass on a
        # quote that says "13 dogs".
        evidence = set(numbers_in((line.get("verdict") or {}).get("quote"))) \
            | set(numbers_in(cluster_claim(line)))
        why = None
        if not claim:
            why = "narrowed claim is empty"
        else:
            new = [n for n in numbers_in(claim) if n not in evidence]
            if new:
                why = ("narrowed claim introduces numbers absent from the "
                       f"quote and the cluster claim: {', '.join(new)}")
        if why is None:
            continue
        if fallback:
            fallbacks.append({"c": key, "reason": why})
        else:
            bad(key, why)

    # The identity lane. A social/web fact names its source instead of quoting
    # a transcript, so it is checked on entirely different fields — and it may
    # never carry the transcript lane's, because lanes never masquerade.
    refs: set[str] = set()
    for i, rec in enumerate(identity):
        label = str(rec.get("ref") or f"facts[{i}]")
        if label in refs:
            bad(label, "duplicate ref")
        refs.add(label)
        if rec.get("provenance") not in IDENTITY_PROVENANCE:
            bad(label, "provenance must be one of "
                       f"{sorted(IDENTITY_PROVENANCE)}, got "
                       f"{rec.get('provenance')!r}")
        for field in IDENTITY_REQUIRED:
            if not str(rec.get(field) or "").strip():
                bad(label, f"{field} is required on an identity-lane fact")
        for field in IDENTITY_BANNED:
            if rec.get(field) is not None:
                bad(label, f"an identity-lane fact carries no {field}")
        if rec.get("domain") is not None and rec.get("domain") not in DOMAINS:
            bad(label, f"domain must be one of {sorted(DOMAINS)}, "
                       f"got {rec.get('domain')!r}")
        if rec.get("sensitivity") is not None and rec.get("sensitivity") not in SENSITIVITY:
            bad(label, f"sensitivity must be one of {sorted(SENSITIVITY)}, "
                       f"got {rec.get('sensitivity')!r}")
        conf = rec.get("confidence")
        if conf is not None and conf not in CONFIDENCE:
            bad(label, f"confidence must be one of {sorted(CONFIDENCE)}, got {conf!r}")
        corr = rec.get("corroborates")
        if corr is None:
            continue
        corr = str(corr)
        if corr.startswith("f"):
            if corr not in existing_ids:
                bad(label, f"corroborates target {corr} is not in --existing")
        elif corr not in kept:
            bad(label, f"corroborates target {corr} is not a kept cluster")

    return violations, folds, fallbacks


def default_confidence(line: dict, fmt: str) -> str:
    """The format-gated default (revision 5 of the plan).

    Single-voice format: the extractor's own call carries. Shared-voice
    format, or a window hinting interview/reaction: only a host anchor
    confirms — recurrence alone never does, because on a multi-host channel
    both hosts recur.
    """
    extractor = str((line.get("verdict") or {}).get("confidence") or "")
    base = "confirmed" if extractor == "confirmed" else "unconfirmed"
    if effective_format(line, fmt) in SINGLE_VOICE:
        return base
    return "confirmed" if (base == "confirmed" and anchored(line)) else "unconfirmed"


def capped_confidence(line: dict, fmt: str, override: str | None) -> str:
    """Agent override beats the default; the evidence-rules caps beat both.

    An ad-read-only cluster and an unattributed (unclear/narration) window are
    capped at ``unconfirmed`` by ``evidence-rules.md``, so an override cannot
    promote them — that cap is the rule the model is not allowed to overrule.
    """
    value = override if override in CONFIDENCE else default_confidence(line, fmt)
    speaker = str((line.get("verdict") or {}).get("speaker_guess") or "")
    if ad_read_only(line) or speaker in ("unclear", "narration"):
        return "unconfirmed"
    return value


def fact_from_cluster(line: dict, fact_id: str, dec: dict, fmt: str, channel: str,
                      videos: list[str], keys: list[str],
                      use_original_claim: bool) -> dict:
    v = line.get("verdict") or {}
    w = line.get("window") or {}
    claim = cluster_claim(line)
    if not use_original_claim and str(dec.get("claim") or "").strip():
        claim = str(dec["claim"]).strip()
    tier = dec.get("tier") if dec.get("tier") in SENSITIVITY else v.get("sensitivity")
    tier = tier if tier in SENSITIVITY else "none"
    vid = w.get("video_id")
    start = w.get("start")
    fact = {
        "fact_id": fact_id,
        "claim": claim,
        "domain": v.get("life_domain"),
        "provenance": "transcript",
        "quote": v.get("quote"),
        "video": f"{channel}:{vid}",
        "start": start,
        "url": f"https://www.youtube.com/watch?v={vid}&t={start}s",
        "published": w.get("published"),
        "recurrence": len(videos),
        "confidence": capped_confidence(line, fmt, dec.get("confidence")),
        "sensitivity": tier,
        "sensitive": tier in WITHHELD,
        "superseded_by": None,
        "selected": False,
        "members": keys,
    }
    if str(dec.get("gloss") or "").strip():
        fact["gloss"] = str(dec["gloss"]).strip()
    return fact


def fail(violations: dict[str, list[str]]) -> int:
    """Exit 3 with the offending ids, so the orchestrator re-asks the agent
    for exactly those and never hand-patches the ledger."""
    print(json.dumps({"error": "decision contract violated",
                      "violations": {k: v for k, v in sorted(violations.items())},
                      "hint": ("re-ask the agent for exactly these ids and "
                               "pass the answer as another --decisions file; "
                               "a second failure runs expand "
                               "--fallback-original")}, indent=1))
    return 3


def identity_fact(rec: dict, fact_id: str) -> dict:
    """A social/web fact: its source and seen-date stand where the transcript
    lane's quote, video and start would be. Alone it is never `confirmed` —
    only cross-lane corroboration lifts it, and that is applied once both
    sides of the pair exist."""
    tier = rec.get("sensitivity") if rec.get("sensitivity") in SENSITIVITY else "none"
    fact = {
        "fact_id": fact_id,
        "claim": str(rec.get("claim") or "").strip(),
        "domain": rec.get("domain"),
        "provenance": rec.get("provenance"),
        "source_url": str(rec.get("source_url") or "").strip(),
        "seen_date": str(rec.get("seen_date") or "").strip(),
        "recurrence": 1,
        "confidence": "unconfirmed",
        "sensitivity": tier,
        "sensitive": tier in WITHHELD,
        "superseded_by": None,
        "selected": False,
        "members": [],
    }
    if str(rec.get("gloss") or "").strip():
        fact["gloss"] = str(rec["gloss"]).strip()
    return fact


def rank_key(fact: dict) -> tuple:
    """Strongest first: confirmed before unconfirmed, then recurrence, then
    the fact id so the pick never depends on dict order."""
    return (0 if fact.get("confidence") == "confirmed" else 1,
            -int(fact.get("recurrence") or 0),
            str(fact.get("fact_id")))


def cmd_expand(a: argparse.Namespace) -> int:
    t0 = time.monotonic()
    clusters = read_jsonl(pathlib.Path(a.clustered))
    out_path = pathlib.Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing_facts: list[dict] = []
    if a.existing:
        _, existing_facts = read_ledger(a.existing)
    existing_by_id = {str(f.get("fact_id")): dict(f) for f in existing_facts}
    existing_ids = set(existing_by_id)

    state_path = pathlib.Path(a.state) if a.state else out_path.parent / "merge-state.json"
    prior_state = load_state(state_path if (a.existing or a.state) else None)

    records = plan(clusters, a.format, prior_state, existing_ids)
    by_c = {r["c"]: r for r in records}

    decisions, agent_selected, identity, problems = load_decisions(a.decisions)
    enum_aliases = alias_enums(decisions, identity)
    violations, folds, fallbacks = validate(records, clusters, decisions,
                                            existing_by_id, identity,
                                            a.fallback_original)
    if problems:
        violations.setdefault("_files", []).extend(problems)
    if violations:
        return fail(violations)

    fallback_ids = {f["c"] for f in fallbacks}
    kept = [r["c"] for r in records
            if r["status"] == "judge" and decisions[r["c"]].get("action") == "keep"]
    kept_set = set(kept)

    # Every folded cluster resolves to the terminal it lands on: a c* chain
    # ends at a kept cluster, an f* target ends at an existing fact.
    terminal: dict[str, str] = {}
    for key in folds:
        node, seen = key, {key}
        while node in folds:
            node = folds[node]
            if node.startswith("f") or node in seen:
                break
            seen.add(node)
        terminal[key] = node

    # fact ids: fresh builds number in cluster order; a refresh continues
    # after the ledger's highest id so an existing fact never changes id.
    next_num = 0
    for fid in existing_ids:
        m = re.fullmatch(r"f(\d+)", fid)
        if m:
            next_num = max(next_num, int(m.group(1)))

    assigned: dict[str, str] = {}          # cluster id -> fact id
    for key in kept:
        rec = by_c[key]
        if rec["status"] == "judge" and rec["known"]:
            # a re-judged cluster keeps the first fact id it inherited
            reuse = rec["known"][0]
            if reuse not in assigned.values():
                assigned[key] = reuse
                continue
        next_num += 1
        assigned[key] = f"f{next_num:03d}"

    additive = [r for r in records if r["status"] == "additive"]
    for rec in additive:
        assigned[rec["c"]] = rec["fact_id"]

    # identity-lane facts are numbered after the clusters
    identity_ids: list[str] = []
    identity_by_ref: dict[str, str] = {}
    for i, rec in enumerate(identity):
        next_num += 1
        fact_id = f"f{next_num:03d}"
        identity_ids.append(fact_id)
        identity_by_ref[str(rec.get("ref") or f"facts[{i}]")] = fact_id

    # Supersession is checked again once ids are resolved: a re-judged cluster
    # that reuses f003 may not also claim to supersede f003, and two facts may
    # not supersede each other.
    sup_edges: dict[str, str] = {}
    owner = {fact_id: key for key, fact_id in assigned.items()}
    post: dict[str, list[str]] = {}
    for key in kept:
        raw = decisions[key].get("supersedes")
        if raw is None:
            continue
        target = assigned.get(str(raw), str(raw))
        if target == assigned[key]:
            post.setdefault(key, []).append(
                f"supersedes resolves to the fact itself ({target})")
            continue
        sup_edges[assigned[key]] = target
    for src in sup_edges:
        node, seen = src, {src}
        while node in sup_edges:
            node = sup_edges[node]
            if node in seen:
                post.setdefault(owner.get(src, src), []).append(
                    "supersedes chain is a cycle")
                break
            seen.add(node)
    if post:
        return fail(post)

    # members that land on each fact: the cluster's own, everything folded in,
    # and (on a refresh) whatever the existing fact already carried.
    keys_by_fact: dict[str, list[str]] = {}
    videos_by_fact: dict[str, list[str]] = {}

    def add_evidence(fact_id: str, line: dict) -> None:
        keys = keys_by_fact.setdefault(fact_id, [])
        vids = videos_by_fact.setdefault(fact_id, [])
        for m in members_of(line):
            key = member_key(m)
            if key not in keys:
                keys.append(key)
            vid = m.get("video_id")
            if vid and vid not in vids:
                vids.append(vid)

    def seed_from_existing(fact_id: str, into: str | None = None) -> None:
        prior = existing_by_id.get(fact_id) or {}
        target = into or fact_id
        keys = keys_by_fact.setdefault(target, [])
        vids = videos_by_fact.setdefault(target, [])
        for key in prior.get("members") or []:
            if key not in keys:
                keys.append(key)
            vid = str(key).rsplit(":", 1)[0]
            if vid and vid not in vids:
                vids.append(vid)
        vid = str(prior.get("video") or "").split(":")[-1]
        if vid and vid not in vids:
            vids.append(vid)

    for key, fact_id in assigned.items():
        if fact_id in existing_ids:
            seed_from_existing(fact_id)
        add_evidence(fact_id, clusters[by_c[key]["index"]])

    folded_into: dict[str, list[str]] = {}
    for key, target in terminal.items():
        fact_id = assigned.get(target) if not target.startswith("f") else target
        if not fact_id:
            continue
        if fact_id in existing_ids and fact_id not in keys_by_fact:
            seed_from_existing(fact_id)
        add_evidence(fact_id, clusters[by_c[key]["index"]])
        folded_into.setdefault(fact_id, []).append(key)

    # A re-judged cluster inherited several fact ids and kept one of them.
    # The others must not stay active: their evidence pools into the fact that
    # was kept and they are marked superseded by it — history stays, a stale
    # duplicate does not. A fact the decisions already fold into or supersede
    # explicitly is left to that decision, and one that another cluster owns
    # this round is not ours to retire.
    explicit = {str(t) for t in terminal.values() if str(t).startswith("f")}
    explicit |= {str(decisions[k].get("supersedes")) for k in kept
                 if decisions[k].get("supersedes") is not None}
    owned = set(assigned.values())
    reconciled: dict[str, str] = {}
    for key in kept:
        rec = by_c[key]
        for old in rec.get("known") or []:
            if old == assigned[key] or old in explicit or old in owned:
                continue
            if old in reconciled:
                continue
            seed_from_existing(old, into=assigned[key])
            reconciled[old] = assigned[key]

    # ---- build the facts -------------------------------------------------- #
    facts: list[dict] = []
    fact_index: dict[str, dict] = {}
    for key in kept:
        fact_id = assigned[key]
        line = clusters[by_c[key]["index"]]
        fact = fact_from_cluster(line, fact_id, decisions[key], a.format,
                                 a.channel, videos_by_fact.get(fact_id, []),
                                 keys_by_fact.get(fact_id, []),
                                 key in fallback_ids)
        facts.append(fact)
        fact_index[fact_id] = fact

    for rec in additive:
        fact_id = rec["fact_id"]
        if fact_id in fact_index:
            # an existing fact whose members now sit in several re-clustered
            # clusters is one fact, not one per cluster: its evidence was
            # already pooled above
            continue
        prior = dict(existing_by_id.get(fact_id) or {})
        prior["recurrence"] = len(videos_by_fact.get(fact_id, []))
        prior["members"] = keys_by_fact.get(fact_id, [])
        facts.append(prior)
        fact_index[fact_id] = prior

    for fact_id, prior in existing_by_id.items():
        if fact_id in fact_index:
            continue
        carried = dict(prior)
        if fact_id in keys_by_fact:      # something folded into it this round
            seed_from_existing(fact_id)
            carried["members"] = keys_by_fact[fact_id]
            carried["recurrence"] = len(videos_by_fact[fact_id])
        facts.append(carried)
        fact_index[fact_id] = carried

    for rec, fact_id in zip(identity, identity_ids):
        fact = identity_fact(rec, fact_id)
        facts.append(fact)
        fact_index[fact_id] = fact

    # cross-lane corroboration is the top tier in `evidence-rules.md`: a
    # transcript fact and an identity-lane fact that name the same thing lift
    # each other to `confirmed`.
    corroborated: list[list[str]] = []
    for rec, fact_id in zip(identity, identity_ids):
        raw = rec.get("corroborates")
        if raw is None:
            continue
        target = assigned.get(str(raw), str(raw))
        if target not in fact_index:
            continue
        fact_index[fact_id]["confidence"] = "confirmed"
        fact_index[target]["confidence"] = "confirmed"
        corroborated.append([fact_id, target])

    # a re-judged cluster's other inherited facts stay as history
    for old, new in reconciled.items():
        if old in fact_index:
            fact_index[old]["superseded_by"] = new

    # supersession: the newer fact marks the older, which stays as history.
    for key in kept:
        sup = decisions[key].get("supersedes")
        if sup is None:
            continue
        sup = str(sup)
        target = assigned.get(sup, sup)
        if target in fact_index:
            fact_index[target]["superseded_by"] = assigned[key]

    facts.sort(key=lambda f: str(f.get("fact_id")))

    # ---- selected: the agent proposes, the script owns the count ---------- #
    active = [f for f in facts if not f.get("superseded_by")]
    active_ids = {str(f["fact_id"]) for f in active}
    picked: list[str] = []
    ignored: list[str] = []
    # the agent names c* ids, f* ids, and an identity fact's own `ref`
    pick_map = {**assigned, **identity_by_ref}
    for raw in agent_selected:
        fact_id = pick_map.get(raw, raw)
        if fact_id in active_ids and fact_id not in picked:
            picked.append(fact_id)
        elif fact_id not in active_ids:
            ignored.append(raw)
    if len(picked) > SELECTED_TARGET:
        ranked = sorted((fact_index[p] for p in picked), key=rank_key)
        picked = [str(f["fact_id"]) for f in ranked[:SELECTED_TARGET]]
    if len(picked) < SELECTED_TARGET:
        for fact in sorted(active, key=rank_key):
            if len(picked) >= SELECTED_TARGET:
                break
            if str(fact["fact_id"]) not in picked:
                picked.append(str(fact["fact_id"]))
    chosen = set(picked)
    for fact in facts:
        fact["selected"] = str(fact.get("fact_id")) in chosen

    write_ledger(out_path, None, facts)

    # ---- state: what the next round inherits by --------------------------- #
    members_state: dict[str, dict] = dict(prior_state)
    for key, fact_id in assigned.items():
        for mkey in member_keys(clusters[by_c[key]["index"]]):
            members_state[mkey] = {"fact": fact_id}
    for key, target in terminal.items():
        fact_id = assigned.get(target) if not target.startswith("f") else target
        if not fact_id:
            continue
        for mkey in member_keys(clusters[by_c[key]["index"]]):
            members_state[mkey] = {"folded": fact_id}
    for rec in records:
        if rec["status"] in ("auto_dropped", "carry_dropped"):
            for mkey in member_keys(clusters[rec["index"]]):
                members_state.setdefault(mkey, {"dropped": rec["reason"]})
    for key, dec in decisions.items():
        if dec.get("action") != "drop":
            continue
        reason = str(dec.get("reason") or "dropped by the merge pass")
        for mkey in member_keys(clusters[by_c[key]["index"]]):
            members_state[mkey] = {"dropped": reason}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(
        {"schema": "tl-creator-merge-state/v1", "channel": a.channel,
         "format": a.format, "facts": len(facts),
         "members": members_state}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    dropped = sum(1 for k, d in decisions.items() if d.get("action") == "drop")
    auto_dropped = sum(1 for r in records
                       if r["status"] in ("auto_dropped", "carry_dropped"))
    elapsed = round(time.monotonic() - t0, 1)
    summary = {
        "clusters": len(clusters),
        "judged": sum(1 for r in records if r["status"] == "judge"),
        "auto_dropped": auto_dropped,
        "additive": len(additive),
        "facts": len(facts),
        "new_facts": len(kept_set),
        "folded": len(terminal),
        "dropped": dropped,
        "selected": len(chosen),
        "identity_facts": len(identity_ids),
        "enum_aliases": enum_aliases,
        "corroborated": corroborated,
        "reconciled": reconciled,
        "superseded": sum(1 for f in facts if f.get("superseded_by")),
        "claim_fallbacks": fallbacks,
        "selected_ignored": ignored,
        "facts_file": str(out_path),
        "state_file": str(state_path),
        "elapsed_s": elapsed,
    }
    print(json.dumps(summary, indent=1))
    funnel(stage="merge", clusters=len(clusters), judged=summary["judged"],
           auto_dropped=auto_dropped, additive=len(additive),
           facts=len(facts), folded=len(terminal), dropped=dropped,
           selected=len(chosen), identity_facts=len(identity_ids),
           enum_aliases=len(enum_aliases), elapsed_s=elapsed)
    if enum_aliases:
        print("near-miss enum values aliased: "
              + "; ".join(enum_aliases), file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="write the compact judgment input")
    p.add_argument("--clustered", required=True)
    p.add_argument("--format", required=True, choices=sorted(FORMATS))
    p.add_argument("--existing", default=None, help="the ledger being refreshed")
    p.add_argument("--state", default=None,
                   help="merge-state.json from the previous round "
                        "(default: <out>/merge-state.json)")
    p.add_argument("--shards", type=int, default=1,
                   help="split the input across N agents, whole domains only")
    p.add_argument("--out", required=True, help="directory for merge-input*.jsonl")
    p.set_defaults(fn=cmd_prepare)

    e = sub.add_parser("expand", help="validate decisions and build the facts")
    e.add_argument("--clustered", required=True)
    e.add_argument("--decisions", action="append", required=True,
                   help="repeatable; later files override earlier ones per id")
    e.add_argument("--existing", default=None)
    e.add_argument("--state", default=None,
                   help="default: merge-state.json beside --out")
    e.add_argument("--format", required=True, choices=sorted(FORMATS))
    e.add_argument("--channel", required=True, help="channel id, for `video`")
    e.add_argument("--out", required=True, help="facts.jsonl to write")
    e.add_argument("--fallback-original", action="store_true",
                   help="use the cluster's own claim for claims that fail the "
                        "tripwire instead of exiting 3")
    e.set_defaults(fn=cmd_expand)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
