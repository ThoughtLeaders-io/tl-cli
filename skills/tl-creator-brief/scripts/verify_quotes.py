#!/usr/bin/env python3
"""Verbatim-verify every candidate quote against the local corpus, in bulk.

The mechanical half of what the confirmation wave used to do: each
transcript-provenance candidate fact's quote is located in its video's stored
captions by ``locate`` below, the one quote matcher. Only an **exact**
contiguous match auto-accepts; ``partial`` and ``none`` are flagged, never
accepted — a shared opening with a different tail is how a fabricated quote
gets a real timestamp. The judgment half (sensitivity, ambiguous voices,
superseded facts) stays with the single model pass; this script never judges.

Usage:
    verify_quotes.py --in candidates.jsonl \\
        --corpus tl-creator-profiles/.corpus/<id>/corpus.jsonl.gz

Input is read through ``store_io``: one candidate per line, and a ledger's
meta header (first line, ``schema: tl-creator-meta/*``) is not a candidate —
it is carried over to the output file unchanged. Candidates with
``provenance: "transcript"`` (or no provenance but a ``video`` field) need
``quote`` and ``video`` (the corpus ref, ``<channel_id>:<video_id>``). Other
provenances pass through unverified — social/web facts are not quotes and
never dress as them.

Output (``--out``, default ``<in>.verified.jsonl``): every input line with a
``verify`` object merged in:

* ``{"match": "exact", "start": ..., "url": "...&t=<s>s", "found": true}`` —
  the authoritative timestamp; it overrides whatever the candidate carried.
* ``{"match": "partial", "found": false, "matched_prefix", "unmatched_tail",
  "cue"}`` — fix the quote to the caption text or drop the fact.
* ``{"match": "none", "found": false}`` — the quote does not publish.
* ``{"match": "n/a"}`` — non-transcript provenance, passed through.

Exit 0 when every transcript quote matched exactly, 1 otherwise. Summary JSON
on stdout and one ``FUNNEL`` line on stderr for the run report; the verified
file holds the detail.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "_shared"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from store_io import open_corpus, read_ledger, write_ledger  # sibling module


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def locate(cues: list[tuple[float, str]], quote: str,
           hint_start: float | None = None) -> dict:
    """Find the quote in the normalized cue stream; say how much matched.

    A quote can occur more than once in a video (a catchphrase, a repeated
    line on a multi-voice upload). With ``hint_start`` — the candidate's
    claimed timestamp — the exact match nearest that time wins, so
    verification never silently relocates a fact to an earlier occurrence
    spoken by someone else. Without a hint, the first occurrence wins.
    """
    parts, owner = [], []
    for i, (_, text) in enumerate(cues):
        n = _norm(text)
        if not n:
            continue
        if parts:
            parts.append(" ")
            owner.append(i)
        parts.append(n)
        owner.extend([i] * len(n))
    hay = "".join(parts)
    needle = _norm(quote)
    if not needle:
        return {"match": "none"}

    starts = []
    pos = hay.find(needle)
    while pos >= 0:
        cue = cues[owner[pos]]
        starts.append((int(cue[0]), cue[1]))
        pos = hay.find(needle, pos + 1)
    if starts:
        if hint_start is not None:
            starts.sort(key=lambda s: abs(s[0] - hint_start))
        start, cue_text = starts[0]
        return {"match": "exact", "start": start, "cue": cue_text,
                "occurrences": len(starts)}

    # Longest word-prefix of the quote that IS present, reported as partial —
    # never as a verification of the whole quote.
    words = needle.split()
    best = None
    for n in range(len(words) - 1, 3, -1):
        prefix = " ".join(words[:n])
        pos = hay.find(prefix)
        if pos >= 0:
            cue = cues[owner[pos]]
            best = {"match": "partial", "start": int(cue[0]), "cue": cue[1],
                    "matched_prefix": prefix,
                    "unmatched_tail": " ".join(words[n:])}
            break
    return best or {"match": "none"}


def funnel(**fields) -> None:
    """One machine-parseable stage line for the run report (stderr)."""
    print("FUNNEL " + " ".join(f"{k}={v}" for k, v in fields.items()),
          file=sys.stderr)


def load_cues(corpus: pathlib.Path) -> dict[str, list[tuple[float, str]]]:
    out: dict[str, list[tuple[float, str]]] = {}
    with open_corpus(corpus) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            v = json.loads(line)
            out[str(v.get("id"))] = [(float(c[0]), c[1])
                                     for c in v.get("cues") or []]
    if not out:
        sys.exit(f"empty corpus at {corpus}")
    return out


def main() -> None:
    started = time.monotonic()
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True,
                    help="candidate facts, one JSON object per line")
    ap.add_argument("--corpus", required=True,
                    help="corpus.jsonl.gz from fetch_cues.py "
                         "(a plain .jsonl corpus is read too)")
    ap.add_argument("--out", default=None,
                    help="default: <in>.verified.jsonl")
    a = ap.parse_args()

    in_path = pathlib.Path(a.infile)
    out_path = pathlib.Path(a.out) if a.out else in_path.with_suffix(
        in_path.suffix + ".verified.jsonl")
    cues_by_video = load_cues(pathlib.Path(a.corpus))

    counts = {"exact": 0, "partial": 0, "none": 0, "n/a": 0}
    header, candidates = read_ledger(in_path)
    verified: list[dict] = []
    for fact in candidates:
        provenance = fact.get("provenance") or (
            "transcript" if fact.get("video") else "n/a")
        if provenance != "transcript":
            fact["verify"] = {"match": "n/a"}
            counts["n/a"] += 1
        else:
            video = str(fact.get("video") or "")
            quote = fact.get("quote") or ""
            cues = cues_by_video.get(video)
            if cues is None:
                # a wrong ref is a broken candidate, not a coverage gap
                hit = {"match": "none",
                       "error": f"video {video!r} not in corpus"}
            elif not cues:
                hit = {"match": "none",
                       "error": "video has no stored transcript"}
            elif not quote:
                hit = {"match": "none", "error": "empty quote"}
            else:
                hint = fact.get("start")
                hit = locate(cues, quote,
                             hint_start=float(hint)
                             if isinstance(hint, (int, float)) else None)
            verify = {"match": hit["match"],
                      "found": hit["match"] == "exact"}
            if hit["match"] != "none":
                verify["start"] = hit["start"]
                vid = video.split(":")[-1]
                verify["url"] = (f"https://www.youtube.com/watch"
                                 f"?v={vid}&t={hit['start']}s")
                verify["cue"] = hit["cue"]
            if hit["match"] == "partial":
                verify["matched_prefix"] = hit["matched_prefix"]
                verify["unmatched_tail"] = hit["unmatched_tail"]
                verify["warning"] = ("partial match: fix the quote to "
                                     "the caption text or drop the "
                                     "fact; never publish as verbatim")
            if hit["match"] == "exact":
                # the located timestamp is authoritative
                fact["start"] = hit["start"]
                fact["url"] = verify["url"]
                if hit.get("occurrences", 1) > 1:
                    verify["occurrences"] = hit["occurrences"]
            if "error" in hit:
                verify["error"] = hit["error"]
            fact["verify"] = verify
            counts[hit["match"]] += 1
        # candidates come from an agent's file: a value json cannot encode
        # (a date, say) is stringified here rather than crashing the writer
        verified.append(json.loads(json.dumps(fact, ensure_ascii=False,
                                              default=str)))
    write_ledger(out_path, header, verified)

    failed = counts["partial"] + counts["none"]
    elapsed = round(time.monotonic() - started, 1)
    print(json.dumps({
        "candidates": sum(counts.values()),
        "elapsed_s": elapsed,
        "exact": counts["exact"],
        "partial": counts["partial"],
        "none": counts["none"],
        "passed_through_non_transcript": counts["n/a"],
        "verified_file": str(out_path),
        "note": ("only exact matches publish as verbatim; partial/none must "
                 "be fixed to the caption text or dropped"),
    }, indent=1))
    funnel(stage="verify", candidates=sum(counts.values()),
           verified=counts["exact"], rejected=failed,
           passed_through=counts["n/a"], elapsed_s=elapsed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
