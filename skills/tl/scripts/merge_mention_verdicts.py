#!/usr/bin/env python3
"""Merge and contract-check `sponsorship-mention-validator` batch verdicts.

The validator agent is run as a fan-out: the caller splits candidate
(video, brand) items into batches, launches one agent per batch, and gets one
JSON array of verdicts back per batch. This script is the caller's other half:

* it checks every verdict array against the agent's output contract (same
  `i` set as the input, one `matches` row per mention with complete `m`
  indices, output field vocabulary, one deciding quote, length limits), so a
  malformed or truncated batch is caught before it is merged;
* it merges the batches into one array ordered by `i`, prints label and
  confidence counts, and lists the items that should get a full-context
  second pass (`unclear`, or `low` confidence);
* with `--gold`, it scores the merged verdicts against a labelled file, which
  is how prompt edits to the agent are checked against the repo's fixture.

Usage:
    merge_mention_verdicts.py --input candidates.json \
        --verdicts batch-1.json batch-2.json ... \
        [--merged merged.json] [--gold gold.json] [--lenient]

`--input` is the full candidate array the batches were cut from (or one
batch, if you are checking a single run). `--verdicts` takes every batch
output. Contract violations exit 1 unless `--lenient` is given.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

LABELS = ("paid_read", "sponsor_credit", "affiliate_or_link_only", "organic", "unclear")
CONFIDENCES = ("high", "medium", "low")
ROLES = ("read", "disclosure", "offer", "link", "passing")
INPUT_FIELDS = ("transcript", "summary", "description", "title", "hashtags")
OUTPUT_FIELDS = ("transcript", "description", "title", "hashtags")
FIELD_TRANSLATION = {"summary": "description"}
VERDICT_KEYS = {"i", "label", "confidence", "evidence_field", "matches", "note"}
MATCH_KEYS = {"m", "field", "role", "quote"}
QUOTE_MAX_WORDS = 15
NOTE_MAX_WORDS = 12
SECOND_PASS_CONFIDENCE = "low"


def _words(text: str) -> int:
    return len(str(text).split())


def output_field(input_field: str) -> str:
    """Translate an input mention field to the output vocabulary."""
    return FIELD_TRANSLATION.get(input_field, input_field)


def check_items(items: object) -> list[str]:
    """Validate the caller's input array. Returns a list of violations."""
    errs: list[str] = []
    if not isinstance(items, list):
        return ["input: must be a JSON array of items"]
    if not items:
        return ["input: the candidate array is empty (nothing to judge)"]
    seen: set = set()
    for pos, item in enumerate(items):
        where = f"input[{pos}]"
        if not isinstance(item, dict):
            errs.append(f"{where}: must be an object")
            continue
        i = item.get("i")
        if not isinstance(i, int) or isinstance(i, bool):
            errs.append(f"{where}: `i` must be an integer")
        elif i in seen:
            errs.append(f"{where}: duplicate `i`={i} (indices must be unique across batches)")
        else:
            seen.add(i)
        for key in ("brand", "aliases", "mentions"):
            if key not in item:
                errs.append(f"{where}: missing `{key}`")
        aliases = item.get("aliases")
        if isinstance(aliases, list) and item.get("brand") and item["brand"] not in aliases:
            errs.append(f"{where}: `aliases` must include the brand name itself")
        mentions = item.get("mentions")
        if not isinstance(mentions, list) or not mentions:
            errs.append(f"{where}: `mentions` must be a non-empty array")
            continue
        for m, mention in enumerate(mentions):
            if not isinstance(mention, dict):
                errs.append(f"{where}.mentions[{m}]: must be an object")
                continue
            field = mention.get("field")
            if field not in INPUT_FIELDS:
                errs.append(f"{where}.mentions[{m}]: `field` must be one of {INPUT_FIELDS}, got {field!r}")
            if not mention.get("snippet"):
                errs.append(f"{where}.mentions[{m}]: missing `snippet`")
    return errs


def check_verdicts(items: list[dict], verdicts: object, *, partial: bool = False) -> list[str]:
    """Validate one verdict array against the items it was produced from.

    With ``partial=True`` the verdicts may cover a subset of ``items`` (one
    batch of a larger input); every `i` must still exist in ``items`` and
    appear at most once. Without it, the verdicts must cover every item.
    """
    errs: list[str] = []
    if not isinstance(verdicts, list):
        return ["output: must be a JSON array (no prose, no markdown fence)"]
    by_i = {item["i"]: item for item in items if isinstance(item, dict) and "i" in item}
    seen: set = set()
    for pos, v in enumerate(verdicts):
        where = f"output[{pos}]"
        if not isinstance(v, dict):
            errs.append(f"{where}: must be an object")
            continue
        i = v.get("i")
        if not isinstance(i, int) or isinstance(i, bool) or i not in by_i:
            errs.append(f"{where}: `i`={i!r} is not an input item")
            continue
        if i in seen:
            errs.append(f"{where}: duplicate verdict for `i`={i}")
            continue
        seen.add(i)
        where = f"output[i={i}]"
        missing = VERDICT_KEYS - set(v)
        extra = set(v) - VERDICT_KEYS
        if missing:
            errs.append(f"{where}: missing keys {sorted(missing)}")
        if extra:
            errs.append(f"{where}: extra keys {sorted(extra)}")
        if v.get("label") not in LABELS:
            errs.append(f"{where}: `label` must be one of {LABELS}, got {v.get('label')!r}")
        if v.get("confidence") not in CONFIDENCES:
            errs.append(f"{where}: `confidence` must be one of {CONFIDENCES}, got {v.get('confidence')!r}")
        if v.get("evidence_field") not in OUTPUT_FIELDS:
            errs.append(f"{where}: `evidence_field` must be one of {OUTPUT_FIELDS}, got {v.get('evidence_field')!r}")
        note = v.get("note")
        if "note" in v and (not isinstance(note, str) or not note.strip()):
            errs.append(f"{where}: `note` must be non-empty text")
        elif "note" in v and _words(note) > NOTE_MAX_WORDS:
            errs.append(f"{where}: `note` is {_words(note)} words, max {NOTE_MAX_WORDS}")

        mentions = by_i[i].get("mentions") or []
        matches = v.get("matches")
        if not isinstance(matches, list):
            errs.append(f"{where}: `matches` must be an array")
            continue
        if len(matches) != len(mentions):
            errs.append(f"{where}: {len(matches)} matches for {len(mentions)} mentions (one row per mention)")
        quotes = 0
        fields_seen: set = set()
        for pos_m, row in enumerate(matches):
            rw = f"{where}.matches[{pos_m}]"
            if not isinstance(row, dict):
                errs.append(f"{rw}: must be an object")
                continue
            extra_m = set(row) - MATCH_KEYS
            if extra_m:
                errs.append(f"{rw}: extra keys {sorted(extra_m)}")
            m = row.get("m")
            if isinstance(m, bool) or m != pos_m:
                errs.append(f"{rw}: `m` must be {pos_m} (0..n-1 in input order), got {m!r}")
            field = row.get("field")
            if field not in OUTPUT_FIELDS:
                hint = " (write `description`, not `summary`)" if field == "summary" else ""
                errs.append(f"{rw}: `field` must be one of {OUTPUT_FIELDS}, got {field!r}{hint}")
            elif isinstance(m, int) and 0 <= m < len(mentions):
                expected = output_field(mentions[m].get("field"))
                if field != expected:
                    errs.append(f"{rw}: `field` is {field!r} but mention {m} was found in {expected!r}")
                fields_seen.add(field)
            if row.get("role") not in ROLES:
                errs.append(f"{rw}: `role` must be one of {ROLES}, got {row.get('role')!r}")
            if "quote" in row:
                quotes += 1
                quote = row["quote"]
                if not isinstance(quote, str) or not quote.strip():
                    errs.append(f"{rw}: `quote` must be non-empty text")
                elif _words(quote) > QUOTE_MAX_WORDS:
                    errs.append(f"{rw}: `quote` is {_words(quote)} words, max {QUOTE_MAX_WORDS}")
        if matches and quotes != 1:
            errs.append(f"{where}: exactly one match row must carry `quote`, found {quotes}")
        if v.get("evidence_field") in OUTPUT_FIELDS and fields_seen and v["evidence_field"] not in fields_seen:
            errs.append(f"{where}: `evidence_field` {v['evidence_field']!r} matches no row in `matches`")
    if not partial:
        unjudged = sorted(set(by_i) - seen)
        if unjudged:
            errs.append(f"output: no verdict for input `i` {unjudged} (same length as the input, same `i` values)")
    return errs


def merge_verdicts(items: list[dict], batches: list[object]) -> tuple[list[dict], list[str]]:
    """Check every batch and merge them into one array ordered by `i`."""
    errs = check_items(items)
    merged: dict[int, dict] = {}
    for n, batch in enumerate(batches, start=1):
        batch_errs = check_verdicts(items, batch, partial=True)
        errs.extend(f"batch {n}: {e}" for e in batch_errs)
        if not isinstance(batch, list):
            continue
        for v in batch:
            if isinstance(v, dict) and isinstance(v.get("i"), int):
                if v["i"] in merged:
                    errs.append(f"batch {n}: `i`={v['i']} was already judged by an earlier batch")
                else:
                    merged[v["i"]] = v
    unjudged = sorted({it["i"] for it in items if isinstance(it, dict) and "i" in it} - set(merged))
    if unjudged:
        errs.append(f"merged: no verdict for input `i` {unjudged}")
    return [merged[i] for i in sorted(merged)], errs


def score_against_gold(verdicts: list[dict], gold: list[dict]) -> dict:
    """Score labels against a gold file (`label`, optional `also_ok`)."""
    by_i = {v["i"]: v for v in verdicts}
    misses = []
    evidence_mismatches = []
    for g in gold:
        v = by_i.get(g["i"])
        accepted = {g["label"], *g.get("also_ok", [])}
        got = v.get("label") if v else None
        if got not in accepted:
            misses.append({"i": g["i"], "expected": g["label"], "also_ok": g.get("also_ok", []),
                           "got": got, "why": g.get("why", "")})
        elif v and "evidence_field" in g and v.get("evidence_field") != g["evidence_field"]:
            evidence_mismatches.append({"i": g["i"], "expected": g["evidence_field"], "got": v.get("evidence_field")})
    return {"total": len(gold), "correct": len(gold) - len(misses), "misses": misses,
            "evidence_mismatches": evidence_mismatches}


def second_pass_items(verdicts: list[dict]) -> list[int]:
    return sorted(v["i"] for v in verdicts
                  if v.get("label") == "unclear" or v.get("confidence") == SECOND_PASS_CONFIDENCE)


def _load(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="the candidate items array the batches were cut from")
    ap.add_argument("--verdicts", required=True, nargs="+", help="one or more agent output files")
    ap.add_argument("--merged", help="write the merged, `i`-ordered verdict array here")
    ap.add_argument("--gold", help="score the merged labels against this gold file")
    ap.add_argument("--lenient", action="store_true", help="report contract violations but exit 0")
    args = ap.parse_args(argv)

    items = _load(args.input)
    batches = [_load(p) for p in args.verdicts]
    merged, errs = merge_verdicts(items, batches)

    labels = Counter(v.get("label") for v in merged)
    confidences = Counter(v.get("confidence") for v in merged)
    print(f"items: {len(items)}  verdicts: {len(merged)}  batches: {len(batches)}")
    print("labels:      " + ", ".join(f"{k}={labels[k]}" for k in LABELS if labels[k]))
    print("confidence:  " + ", ".join(f"{k}={confidences[k]}" for k in CONFIDENCES if confidences[k]))
    second = second_pass_items(merged)
    if second:
        print(f"second pass: {len(second)} item(s) (unclear or low confidence): {second}")

    rc = 0
    if args.gold:
        score = score_against_gold(merged, _load(args.gold))
        print(f"gold: {score['correct']}/{score['total']} correct")
        for miss in score["misses"]:
            alt = f" (also ok: {', '.join(miss['also_ok'])})" if miss["also_ok"] else ""
            print(f"  MISS i={miss['i']}: expected {miss['expected']}{alt}, got {miss['got']}  -- {miss['why']}")
        for mm in score["evidence_mismatches"]:
            print(f"  evidence_field i={mm['i']}: expected {mm['expected']}, got {mm['got']}")
        if score["misses"]:
            rc = 1

    if errs:
        print(f"contract violations: {len(errs)}", file=sys.stderr)
        for e in errs:
            print(f"  {e}", file=sys.stderr)
        if not args.lenient:
            rc = 1
    else:
        print("contract: ok")

    if args.merged:
        if errs and not args.lenient:
            print(f"merged NOT written ({args.merged}): fix the contract violations first", file=sys.stderr)
        else:
            Path(args.merged).write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"merged -> {args.merged}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
