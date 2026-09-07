#!/usr/bin/env python3
"""Collapse repeated gems into one cluster per claim, before the fact pass.

A back-catalogue channel says the same thing hundreds of times: "BioShock is
my favorite game" lands in thirty different videos, and the classifier keeps
every copy because every copy really is self-disclosure. Handing all thirty to
the fact pass makes an expensive model re-read and re-dedupe them by hand.
This script does the mechanical half locally: same claim in, one cluster out,
with the repeats kept as evidence rather than thrown away.

Usage:
    cluster_gems.py --in gems.jsonl [--out gems-clustered.jsonl]

Input: ``gems.jsonl`` from the classification stage — one
``{"window": …, "verdict": …}`` object per line.

Output (``--out``, default ``gems-clustered.jsonl`` beside the input): one
cluster per line, in the same shape as the input line, so downstream consumers
handle exactly ONE format. Each line is the cluster's representative gem (the
highest-information member: strongest ``rank_score``, then longest window
text) with two fields merged in:

* ``occurrences`` — how many gems the cluster holds (1 for a singleton, which
  passes through unchanged apart from these two fields).
* ``members`` — ``{video_id, start, published, in_sponsor_read, host_anchor}``
  for every member, the representative included, so recurrence can be counted
  over **distinct videos** — and the ad-read and anchor questions answered per
  member — without going back to the raw gems.

Clustering is deliberately conservative: a false merge silently deletes a
distinct fact, a missed merge only costs a few tokens. Two gems may merge only
when they share a life domain, a speaker guess and a sensitivity call; only
when their claims agree about polarity (a denial never merges into an
assertion) and about numbers (a claim differing only in a count is a different
claim); only
when their one-line claims agree; and only when either the claims are
near-identical or the recurring phrases (failing that, the window texts)
overlap heavily on content words. Every member must match every other member
(complete linkage), so near-misses cannot chain two unrelated claims together,
and the result does not depend on the order the gems arrive in.

Summary JSON on stdout and one ``FUNNEL`` line on stderr for the run report;
the clustered file holds the detail. Exit 0.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

# Content-word overlap between two recurring phrases. 0.6 keeps the three
# phrasings of one claim together ("bioshock favorite game all" /
# "bioshock favorite game ever" / "bioshock's favorite game all" all sit at
# 0.75) while leaving distinct claims that merely share a frame apart
# ("what's favorite ice cream" vs "what's favorite movie favorite", 0.4).
PHRASE_THRESHOLD = 0.6

# Window texts are ~50 words of surrounding chatter, so even genuine repeats of
# one claim usually overlap only ~0.1. This is the near-verbatim safety net for
# repeats the scan never tagged with a phrase: on real runs the highest genuine
# pair sits at ~0.86 and the highest false pair at ~0.28, so 0.55 is well clear
# of both.
TEXT_THRESHOLD = 0.55

# The claim gate. A recurring phrase belongs to the WINDOW, not to the claim:
# three windows can all repeat "bioshock favorite game" while disclosing the
# channel's age, its 2011 Skyrim videos and some old Dragon Ball uploads. Word
# overlap between the two one-line claims is what separates a real repeat from
# that coincidence — the false pairs sit at 0.0–0.11, genuine ones above 0.25.
CLAIM_GATE = 0.25

# Near-identical claims are the same fact even when the scan tagged no phrase
# and the surrounding chatter shares nothing.
CLAIM_THRESHOLD = 0.8

STOPWORDS = frozenset("""
a about all also an and any are as at be been being but can could did do does
down for from get getting go going gonna got had has have he her here his how
i if im in into is it its just know like me my no not of off on or other our
out really said say she should so some that the their them then there these
they this those to too up us very was we were what when who why will with
would you your
""".split())

_WORD = re.compile(r"[a-z0-9']+")

# Polarity and quantity are dropped by content_words() — "no"/"not" are
# stopwords and digits are folded in with everything else — so "has children"
# and "does not have children", or "has 2 cats" and "has 3 cats", reach the
# similarity test as the same bag of words. Both would be false merges that
# delete a distinct fact, so they are refused before any similarity is scored.
_NEGATION = re.compile(
    r"n't\b|\b(?:no|not|never|none|nor|neither|nobody|nothing|nowhere|"
    r"without|cannot|cant)\b")

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def funnel(**fields) -> None:
    """One machine-parseable stage line for the run report (stderr)."""
    print("FUNNEL " + " ".join(f"{k}={v}" for k, v in fields.items()),
          file=sys.stderr)


def content_words(text: str) -> frozenset[str]:
    """Lowercase content words: no stopwords, no possessives, no 1-2 letter noise."""
    out = set()
    for raw in _WORD.findall((text or "").lower()):
        word = raw.strip("'")
        if word.endswith("'s"):
            word = word[:-2]
        word = word.replace("'", "")
        if len(word) >= 3 and word not in STOPWORDS:
            out.add(word)
    return frozenset(out)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def raw_claim(gem: dict) -> str:
    """The text whose polarity and quantities identify the claim.

    The one-line claim when the classifier wrote one (it always does for a
    kept gem); the window text is the fallback so a claimless gem is still
    compared on something it actually said.
    """
    notable = str((gem.get("verdict") or {}).get("notable") or "").strip()
    return notable or str((gem.get("window") or {}).get("text") or "")


def negated(text: str) -> bool:
    """Whether the claim is a denial — word-bounded markers, contractions too."""
    return bool(_NEGATION.search((text or "").lower()))


def numbers(text: str) -> frozenset[str]:
    """Numeric tokens as written: two claims differing only in a count differ."""
    return frozenset(_NUMBER.findall(text or ""))


def compatible(a: dict, b: dict) -> bool:
    """False when the two claims disagree about polarity or quantity.

    "has children" / "does not have children" and "has 2 cats" / "has 3 cats"
    survive content_words() as identical word sets, so this runs before any
    similarity is scored and vetoes the merge outright.
    """
    a_text, b_text = raw_claim(a), raw_claim(b)
    return (negated(a_text) == negated(b_text)
            and numbers(a_text) == numbers(b_text))


def block_key(gem: dict) -> tuple:
    """Gems in different blocks never merge, whatever their words say.

    A different life domain is a different fact; a different speaker is a
    different person; and a sensitive gem must never be absorbed into a
    non-sensitive one, because the sensitivity call travels with the cluster.
    """
    verdict = gem.get("verdict") or {}
    return (str(verdict.get("life_domain") or ""),
            str(verdict.get("speaker_guess") or ""),
            bool(verdict.get("sensitive")))


def similar(a: dict, b: dict) -> bool:
    """True only when two gems confidently carry the same claim."""
    if not compatible(a, b):
        # contradictory polarity or different numbers — the same words do not
        # make the same fact
        return False
    a_claim = content_words((a.get("verdict") or {}).get("notable"))
    b_claim = content_words((b.get("verdict") or {}).get("notable"))
    if a_claim and b_claim:
        claim = jaccard(a_claim, b_claim)
        if claim >= CLAIM_THRESHOLD:
            return True
        if claim < CLAIM_GATE:
            # the words disagree about what was disclosed — never merge, no
            # matter how alike the surrounding transcript looks
            return False

    a_phrase = content_words((a.get("window") or {}).get("recurring_phrase"))
    b_phrase = content_words((b.get("window") or {}).get("recurring_phrase"))
    if a_phrase and b_phrase:
        if jaccard(a_phrase, b_phrase) >= PHRASE_THRESHOLD:
            return True
    a_text = content_words((a.get("window") or {}).get("text"))
    b_text = content_words((b.get("window") or {}).get("text"))
    return jaccard(a_text, b_text) >= TEXT_THRESHOLD


def sort_key(gem: dict) -> tuple:
    """Stable read order: the input file's order never decides a merge."""
    window = gem.get("window") or {}
    return (str(window.get("video_id") or ""),
            float(window.get("start") or 0.0),
            str(window.get("id") or ""))


def representative_key(gem: dict) -> tuple:
    """Highest information first: strongest rank, then longest text."""
    window = gem.get("window") or {}
    return (-float(window.get("rank_score") or 0.0),
            -len(str(window.get("text") or "")),
            sort_key(gem))


def member_ref(gem: dict) -> dict:
    window = gem.get("window") or {}
    # in_sponsor_read and host_anchor travel per member, not just on the
    # representative: the merge pass caps an ad-read-only cluster at
    # `unconfirmed` and confirms a shared-voice cluster only on an anchor, and
    # both are questions about EVERY member, not about the one that happened
    # to rank highest.
    return {"video_id": window.get("video_id"),
            "start": window.get("start"),
            "published": window.get("published"),
            "in_sponsor_read": bool(window.get("in_sponsor_read")),
            "host_anchor": bool(window.get("host_anchor"))}


def cluster(gems: list[dict]) -> list[list[dict]]:
    """Complete-linkage clustering inside each block, deterministic."""
    blocks: dict[tuple, list[list[dict]]] = {}
    order: list[tuple] = []
    for gem in sorted(gems, key=sort_key):
        key = block_key(gem)
        if key not in blocks:
            blocks[key] = []
            order.append(key)
        bucket = blocks[key]
        for group in bucket:
            # every member must match — a near-miss cannot chain two claims
            if all(similar(gem, member) for member in group):
                group.append(gem)
                break
        else:
            bucket.append([gem])

    clusters: list[list[dict]] = []
    for key in order:
        clusters.extend(_coalesce(blocks[key]))
    return clusters


def _coalesce(bucket: list[list[dict]]) -> list[list[dict]]:
    """Fold together groups that only stayed apart because of arrival order.

    A gem joins the first group it fully matches, so two groups can end up
    mutually compatible once both have finished filling. Merging to a fixpoint
    (still complete-linkage: every cross pair must match) makes the output
    independent of the order gems were read in.
    """
    changed = True
    while changed:
        changed = False
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                if all(similar(x, y) for x in bucket[i] for y in bucket[j]):
                    bucket[i] = bucket[i] + bucket[j]
                    del bucket[j]
                    changed = True
                    break
            if changed:
                break
    return bucket


# the merge pass judges verdicts, not passages: the slim view keeps what it
# needs and drops the window text, so one agent can read a whole channel's
# clusters instead of a 400 KB file
SLIM_WINDOW = ("id", "video_id", "title", "published", "start", "language",
               "format_hint", "in_sponsor_read", "host_anchor", "recurrence_videos")
SLIM_VERDICT_DROP = ("i", "anchor", "quote_span", "self_disclosure", "start")


def slim_line(line: dict) -> dict:
    w = line.get("window") or {}
    v = line.get("verdict") or {}
    return {"window": {k: w.get(k) for k in SLIM_WINDOW if k in w},
            "verdict": {k: val for k, val in v.items() if k not in SLIM_VERDICT_DROP},
            "occurrences": line.get("occurrences"),
            "members": line.get("members")}


def build_line(group: list[dict]) -> dict:
    ordered = sorted(group, key=representative_key)
    line = dict(ordered[0])
    line["occurrences"] = len(ordered)
    line["members"] = [member_ref(g) for g in sorted(group, key=sort_key)]
    return line


def main() -> None:
    started = time.monotonic()
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True,
                    help="gems.jsonl from the classification stage")
    ap.add_argument("--out", default=None,
                    help="default: gems-clustered.jsonl beside the input")
    a = ap.parse_args()

    in_path = pathlib.Path(a.infile)
    out_path = (pathlib.Path(a.out) if a.out
                else in_path.parent / "gems-clustered.jsonl")

    gems: list[dict] = []
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                gems.append(json.loads(line))

    groups = cluster(gems)
    lines = [build_line(g) for g in groups]
    # biggest recurrence first, then a stable identity tie-break
    lines.sort(key=lambda ln: (-ln["occurrences"], sort_key(ln)))

    with open(out_path, "w", encoding="utf-8") as fout:
        for ln in lines:
            fout.write(json.dumps(ln, ensure_ascii=False, default=str) + "\n")
    slim_path = out_path.with_name(out_path.stem + ".slim.jsonl")
    with open(slim_path, "w", encoding="utf-8") as fout:
        for ln in lines:
            fout.write(json.dumps(slim_line(ln), ensure_ascii=False, default=str) + "\n")

    merged = len(gems) - len(lines)
    largest = max((ln["occurrences"] for ln in lines), default=0)
    repeated = sum(1 for ln in lines if ln["occurrences"] > 1)
    elapsed = round(time.monotonic() - started, 1)
    print(json.dumps({
        "gems": len(gems),
        "clusters": len(lines),
        "merged": merged,
        "repeated_clusters": repeated,
        "largest_cluster": largest,
        "elapsed_s": elapsed,
        "clustered_file": str(out_path),
        "slim_file": str(slim_path),
        "note": ("one line per claim; `occurrences` and `members` carry the "
                 "recurrence evidence — count distinct videos, not members"),
    }, indent=1))
    funnel(stage="cluster", gems=len(gems), clusters=len(lines),
           merged=merged, elapsed_s=elapsed)


if __name__ == "__main__":
    main()
