#!/usr/bin/env python3
"""Assemble extractor output files into the classified record, the gem list
and the candidate facts, validating the contract mechanically.

Each extractor agent produces ``<returns>/batch-NNN.extract.json``:
    {"batch": "NNN", "windows": N,
     "gems":     [{"i", "start", "anchor", "life_domain", "speaker_guess",
                   "sensitivity", "entity_corrections", "notable", "claim",
                   "quote_span": {"first", "last"}, "confidence"}],
     "not_gems": [{"i", "speaker_guess", "reason"}]}

Checks per batch: every index 0..N-1 exactly once; ``start`` and ``anchor``
match the window they claim (``start`` is a hard check; the five-word anchor
is advisory because extractors normalise punctuation); enums valid; the quote
span resolves to a contiguous 4-45-word substring of the window text, which
is cut mechanically so every quote is verbatim by construction. Windows an
extractor skipped, or whose verdict failed a check, are **unjudged**: they
stay out of every output file (so a later ``--exclude`` round can still pick
them up) and are listed in ``respawn.json`` in case a caller wants to
re-judge exactly those; nothing is hand-patched.

Usage: assemble_extracts.py --batches <dir> --returns <dir> --out <dir>
                            [--append] [--min-coverage 0.95]
Exit 0 when the assembled share of the expected windows is at least
``--min-coverage`` (a few unjudged windows are accepted and reported as
``unjudged=N``); exit 3 below the threshold, or whenever a batch file has no
return file at all (an extractor that never ran, not a few bad verdicts).
A batch may have several return files (the original plus subset re-judges
named batch-NNN.extract.r2.json …); later files override the indexes they
carry. ``--append`` adds a later round's rows to the existing files, replacing
any earlier rows for the same windows — so re-assembling a round after a
re-judge never stacks a second copy of its gems.
Outputs in <out>: classified.jsonl, gems.jsonl (cluster_gems.py input),
candidates.jsonl (verify_quotes.py input), respawn.json, one FUNNEL line.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import sys
import time

DOMAINS = {"origin", "family", "pets", "home", "work", "money", "health", "habits",
           "tastes", "beliefs", "relationships", "other"}
SPEAKERS = {"host", "guest", "cohost", "narration", "unclear"}
SENSITIVITY = {"none", "lifestyle", "clinical", "children", "location"}
WITHHELD = {"clinical", "children", "location"}   # excluded from connection angles by default
DEFAULT_MIN_COVERAGE = 0.95


def first5(t: str) -> str:
    return " ".join(t.split()[:5])


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _key(token: str) -> str:
    """A word's matching key: case-folded, punctuation stripped, letters of any
    script kept (windows come in any language)."""
    return "".join(ch for ch in token.casefold() if ch.isalnum())


def _words(s: str) -> list[str]:
    """Words with case and punctuation stripped — the matching key. Extractors
    add a full stop or a comma the captions never had; the cut itself always
    comes from the window text, so the quote stays verbatim by construction."""
    return [w for w in (_key(t) for t in s.split()) if w]


def _find_words(hay: list[str], needle: list[str], start: int = 0) -> int:
    n = len(needle)
    for k in range(start, len(hay) - n + 1):
        if hay[k:k + n] == needle:
            return k
    return -1


def extract_span(text: str, span: dict | None) -> str | None:
    first = (span or {}).get("first", "").strip()
    last = (span or {}).get("last", "").strip()
    if not first or not last:
        return None
    tokens = [(m.start(), m.end(), _key(m.group())) for m in re.finditer(r"\S+", text)]
    tokens = [t for t in tokens if t[2]]
    keys = [t[2] for t in tokens]
    f_words, l_words = _words(first), _words(last)
    if not f_words or not l_words:
        return None
    a = _find_words(keys, f_words)
    if a < 0:
        return None
    b = _find_words(keys, l_words, a)
    if b < 0:
        return None
    q = text[tokens[a][0]:tokens[b + len(l_words) - 1][1]]
    return q if 4 <= len(q.split()) <= 45 else None


def merge_return_files(efs: list[pathlib.Path], problems: list) -> tuple[dict, dict, set]:
    """Later files override the indexes they carry; within ONE file an index
    must appear exactly once — a duplicate is ambiguous and invalidates it."""
    merged_g: dict[int, dict] = {}
    merged_ng: dict[int, dict] = {}
    bad_idx: set[int] = set()
    for ef in efs:
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", ef.read_text(encoding="utf-8").strip())
        try:
            data = json.loads(raw)
        except Exception as e:
            problems.append(f"{ef.name} unparseable: {str(e)[:60]}")
            continue
        if not isinstance(data, dict):
            problems.append(f"{ef.name} envelope is not an object")
            continue
        g_list = data.get("gems")
        ng_list = data.get("not_gems")
        if not isinstance(g_list, list) or not isinstance(ng_list, list):
            problems.append(f"{ef.name} gems/not_gems missing or not lists")
            continue
        counts: dict[int, int] = {}
        for x in list(g_list) + list(ng_list):
            if isinstance(x, dict) and isinstance(x.get("i"), int):
                counts[x["i"]] = counts.get(x["i"], 0) + 1
        dups = {i for i, c in counts.items() if c > 1}
        bad_idx.update(dups)
        for x in g_list:
            if isinstance(x, dict) and isinstance(x.get("i"), int) and x["i"] not in dups:
                merged_ng.pop(x["i"], None)
                merged_g[x["i"]] = x
        for x in ng_list:
            if isinstance(x, dict) and isinstance(x.get("i"), int) and x["i"] not in dups:
                merged_g.pop(x["i"], None)
                merged_ng[x["i"]] = x
    return merged_g, merged_ng, bad_idx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", required=True)
    ap.add_argument("--returns", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--append", action="store_true", help="add this round's rows to existing "
                    "classified/gems/candidates files instead of replacing them")
    ap.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE,
                    help="assembled/expected share below which the exit code is 3 "
                         f"(default {DEFAULT_MIN_COVERAGE}; 1.0 = every window or nothing)")
    a = ap.parse_args()
    if not 0.0 <= a.min_coverage <= 1.0:
        print("--min-coverage must be between 0 and 1", file=sys.stderr)
        return 2
    t0 = time.monotonic()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, gems, cands, respawn, report = [], [], [], {}, {}
    missing_batches: list[str] = []
    for bf in sorted(glob.glob(os.path.join(a.batches, "batch-*.json"))):
        n = os.path.basename(bf)[6:9]
        wins = json.load(open(bf, encoding="utf-8"))
        efs = sorted(pathlib.Path(a.returns).glob(f"batch-{n}.extract*.json"))
        r = {"expected": len(wins), "file": bool(efs), "gems": 0, "problems": []}
        if not efs:
            respawn[n] = list(range(len(wins)))
            r["problems"].append("missing file")
            missing_batches.append(n)
            report[n] = r
            continue
        merged_g, merged_ng, bad_idx = merge_return_files(efs, r["problems"])
        if not merged_g and not merged_ng:
            respawn[n] = list(range(len(wins)))
            report[n] = r
            continue
        G = list(merged_g.values())
        NG = list(merged_ng.values())
        seen = [x.get("i") for x in G] + [x.get("i") for x in NG]
        for i in range(len(wins)):
            if seen.count(i) != 1:
                bad_idx.add(i)
        for x in NG:
            i = x.get("i")
            if i is None or i in bad_idx or not (0 <= i < len(wins)):
                continue
            if x.get("speaker_guess") not in SPEAKERS:
                bad_idx.add(i)
                continue
            rows.append({"window": wins[i], "verdict": {"i": i, "self_disclosure": False,
                         "speaker_guess": x.get("speaker_guess"), "notable": x.get("reason")}, "error": None})
        for v in G:
            i = v.get("i")
            if i is None or i in bad_idx or not (0 <= i < len(wins)):
                continue
            w = wins[i]
            problems = []
            if v.get("start") != w["start"]:
                problems.append("start")            # hard: the verdict is not about this window
            anchor_ok = norm(v.get("anchor")) == norm(first5(w["text"]))
            if v.get("speaker_guess") not in SPEAKERS:
                problems.append("speaker")
            if v.get("life_domain") not in DOMAINS:
                problems.append("domain")
            if v.get("sensitivity") not in SENSITIVITY:
                problems.append("sensitivity")
            q = extract_span(w["text"], v.get("quote_span"))
            if q is None:
                problems.append("span")
            if problems:
                bad_idx.add(i)
                r["problems"].append((i, problems))
                continue
            if not anchor_ok:
                r["anchor_soft_mismatch"] = r.get("anchor_soft_mismatch", 0) + 1
            v = dict(v)
            v["self_disclosure"] = True
            v["quote"] = q
            v["sensitive"] = v["sensitivity"] in WITHHELD
            rows.append({"window": w, "verdict": v, "error": None})
            if v["speaker_guess"] in ("host", "unclear"):
                r["gems"] += 1
                gems.append({"window": w, "verdict": v, "error": None})
                cands.append({"fact_id": f"b{n}-{i:03d}", "claim": v.get("claim"), "domain": v["life_domain"],
                              "provenance": "transcript", "quote": q, "video": w["id"], "start": w["start"],
                              "published": w.get("published"), "confidence": v.get("confidence"),
                              "sensitivity": v["sensitivity"], "sensitive": v["sensitive"],
                              "speaker_guess": v["speaker_guess"], "notable": v.get("notable"),
                              "entity_corrections": v.get("entity_corrections") or {}})
        if bad_idx:
            respawn[n] = sorted(bad_idx)
        report[n] = r
    # --append is idempotent: a round that is re-assembled after a re-judge
    # replaces its own earlier rows (same window id + start) instead of
    # stacking a second copy under them
    this_round = {(x["window"].get("id"), x["window"].get("start")) for x in rows}

    def _keep(line: str) -> bool:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return True
        w = obj.get("window")
        key = ((w.get("id"), w.get("start")) if isinstance(w, dict)
               else (obj.get("video"), obj.get("start")))      # candidates.jsonl rows
        return key not in this_round

    for name, items in (("classified.jsonl", rows), ("gems.jsonl", gems), ("candidates.jsonl", cands)):
        kept = ""
        if a.append and (out / name).exists():
            with open(out / name, encoding="utf-8") as fh:
                kept = "".join(ln for ln in fh if ln.strip() and _keep(ln))
        with open(out / name, "w", encoding="utf-8") as fh:
            fh.write(kept + "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items))
    (out / "respawn.json").write_text(json.dumps(respawn, indent=1), encoding="utf-8")
    expected = sum(r["expected"] for r in report.values())
    unjudged = sum(len(v) for v in respawn.values())
    raw_coverage = len(rows) / expected if expected else 1.0
    coverage = round(raw_coverage, 3)
    # A whole batch without a return file is an extractor that never ran,
    # never a coverage question; a few unjudged windows above the threshold
    # are accepted and reported, and stay reachable through respawn.json or
    # a later --exclude round. The threshold is compared unrounded.
    rc = 3 if (missing_batches or raw_coverage < a.min_coverage) else 0
    elapsed = round(time.monotonic() - t0, 1)
    print(json.dumps({"batches": len(report), "windows_expected": expected, "windows_assembled": len(rows),
                      "gems": len(gems), "unjudged_windows": unjudged, "coverage": coverage,
                      "min_coverage": a.min_coverage, "missing_batches": missing_batches,
                      "respawn": respawn,
                      "problems": {k: r["problems"] for k, r in report.items() if r["problems"]},
                      "anchor_soft_mismatches": sum(r.get("anchor_soft_mismatch", 0) for r in report.values()),
                      "out": str(out), "elapsed_s": elapsed, "exit": rc}, indent=1))
    print(f"FUNNEL stage=assemble windows_expected={expected} windows_assembled={len(rows)} "
          f"gems={len(gems)} unjudged={unjudged} coverage={coverage} elapsed_s={elapsed}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
