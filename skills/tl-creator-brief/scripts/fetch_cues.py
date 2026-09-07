#!/usr/bin/env python3
"""Layer 1+2 in one query: pull only the transcript passages around
first-person cue phrases, straight from Elasticsearch highlights.

This is the model layer's only retrieval flow — there is no local
full-transcript scan any more. One boolean ``should`` of ``match_phrase``
clauses (references/cue-phrases.txt plus the host terms) selects the videos;
ES ``highlight`` returns the passages around each hit, with the timed-text
``start`` attributes intact, so a 5,000-video channel costs a few dozen small
queries (7-21 s measured, any channel size) instead of a full transcript
download.

Usage:
    fetch_cues.py --channel <id> [--host-terms "a,b"] [--out <root>]
                  [--max-windows 500] [--batch-size 25]
                  [--exclude <classified.jsonl>] [--since <YYYY-MM-DD>]

Writes ``<out>/<channel_id>/``: ``windows.jsonl.gz`` (every passage, ranked),
``batches/batch-NNN.json`` (the capped model-layer batches, 25 windows each)
and ``corpus.jsonl.gz`` — the same store shape ``verify_quotes.py`` and
``quote_timestamp.py`` read, holding the fetched passages as cues, so both run
unchanged. Once the cap is taken, the kept windows' real ad-read spans are
looked up (``sponsor_spans.py``) and ``in_sponsor_read`` is decided from them;
the regex heuristic the windows were built with is the fallback when that
lookup fails, and ``sponsor_source`` in the summary says which one decided.
One JSON summary on stdout, one FUNNEL line on stderr.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import html
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "_shared"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sponsor_spans  # noqa: E402  — sibling script
import tl_data  # noqa: E402
from channel_context import TITLE_SECOND_VOICE  # noqa: E402  — sibling script, one home for title hints

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PHRASES = HERE.parent / "references" / "cue-phrases.txt"
SPONSOR_RX = re.compile(
    r"\b(sponsored by|sponsor(?:ing)? (?:of )?(?:this|today'?s) (?:video|episode)|"
    r"use (?:my |the )?code|promo code|link in (?:the|my) description|"
    r"\d\d?% off|free trial|check them out)\b", re.I)
TAG_RX = re.compile(r"<[^>]+>")
START_RX = re.compile(r'start="([\d.]+)"')
EM_RX = re.compile(r"<em>(.*?)</em>", re.S)
YEARS = list(range(2005, time.gmtime().tm_year + 2))   # YouTube launch → next year
CUE_RX = re.compile(r'<text start="([\d.]+)"[^>]*>')
NON_EN_WINDOWS_PER_VIDEO = 3
NON_EN_WINDOW_WORDS = 80
WINDOW_SPAN = 30        # seconds a passage is assumed to occupy from its start


GENERIC_TERMS = ["i", "my", "me", "i'm", "i've", "i'd", "i'll", "myself", "we", "our"]
GENERIC_BOOST = 0.15
RECURRING_PREFIX = "~"          # a phrase that fires in most uploads (greeting, sign-off)
RECURRING_CAP = 12              # max windows any recurring-bit phrase may supply to the cap
PHRASE_CAP_SHARE = 0.08         # no single phrase supplies more than this share of the cap
WEAK_CUES = {"i love", "i hate", "i think that", "my life", "my own", "i always", "i never",
             "personally i", "my favorite", "my favourite", "i believe", "i want", "i play",
             "i watch", "i read", "i listen to", "i can't stand", "my story", "my journey",
             "i once", "i remember", "i personally", "in my experience", "i live", "my home",
             "my whole life", "i've always", "i've never", "i used to", "i'm from", "we launch"}
EXTRA_GENERIC = ["i've always", "i've never", "i always", "i never", "i used to", "i remember",
                 "i personally", "for me personally", "i can only speak for myself", "in my experience",
                 "i live", "i'm from", "my home", "my life", "my whole life", "my own"]


def load_phrases(path: pathlib.Path) -> tuple[list[str], set[str]]:
    out: list[str] = []
    recurring: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(RECURRING_PREFIX):
            line = line[1:].strip()
            recurring.add(line.lower())
        out.append(line)
    for g in EXTRA_GENERIC:
        if g not in out:
            out.append(g)
    return out, recurring


# a highlight fragment can start inside a doubly-escaped caption entity
# ("&amp;#39;s" cut to "amp;#39;s" or ";#39;s"); the entity is unrecoverable
# by unescaping, so the stub is resolved by hand — #39 is the apostrophe the
# captions actually meant, anything else is dropped
_PARTIAL_ENTITY_RX = re.compile(r"^\s*(?:&?amp;)?;?#(\d+);")
# ... or inside a timed-text tag, leaving `start="138" dur="3.78">`,
# `="664.399" dur="2.801">` or a bare `>` in front of the first words
_PARTIAL_TAG_RX = re.compile(r'^\s*(?:(?:[\w-]*=)?"[^"]*"\s*)*>\s*')


def _fix_partial_entity(t: str) -> str:
    t = _PARTIAL_TAG_RX.sub("", t, count=1)
    m = _PARTIAL_ENTITY_RX.match(t)
    if not m:
        return t
    code = int(m.group(1))
    rest = t[m.end():]
    return ("'" + rest) if code == 39 else rest.lstrip()


def _tidy(t: str) -> str:
    t = TAG_RX.sub(" ", t)
    t = html.unescape(html.unescape(t))
    return re.sub(r"\s+", " ", t).strip()


def clean(frag: str) -> tuple[str, list[str], float | None, list[str], list[list]]:
    """Return (text, distinct hits, first start, raw hits, cue pieces).

    ``pieces`` keeps each timed-text cue as ``[start, text]`` so the passage
    corpus stores the same per-cue shape the verifier expects and a quote
    from the tail of a passage verifies to its own second, not the passage's
    first. Text before the first ``<text>`` tag belongs to a cue whose tag the
    fragment boundary cut; it is attached to the first cue.
    """
    hits = [re.sub(r"\s+", " ", html.unescape(html.unescape(TAG_RX.sub(" ", m)))).strip().lower()
            for m in EM_RX.findall(frag)]
    body = frag.replace("<em>", "").replace("</em>", "")
    parts = CUE_RX.split(body)
    pieces: list[list] = []
    for k in range(1, len(parts) - 1, 2):
        t = _tidy(parts[k + 1])
        if t:
            pieces.append([round(float(parts[k]), 2), t])
    pre = _tidy(parts[0]) if parts else ""
    if pre and pieces:
        pieces[0][1] = pre + " " + pieces[0][1]
    if pieces:
        pieces[0][1] = _fix_partial_entity(pieces[0][1])
    text = " ".join(pc[1] for pc in pieces)
    start = pieces[0][0] if pieces else None
    return text, sorted(set(h for h in hits if h)), start, hits, pieces


def format_hint(title: str | None) -> str | None:
    """Per-video hint the rubric lets override the channel label."""
    t = title or ""
    for fmt, rx in TITLE_SECOND_VOICE.items():  # reaction first, then collab
        if rx.search(t):
            return fmt
    return None


ENGLISH_CODES = {"en", "asr-en"}


def is_english(code: str | None) -> bool:
    """``en``, ``en-US``, ``asr-en`` … are all English captions; missing is
    treated as English, as the old scan did."""
    if not code or code in ("None", ""):
        return True
    c = code.lower()
    return c in ENGLISH_CODES or c.startswith("en-") or c.endswith("-en")


def census(channel: int) -> tuple[int, dict[str, int]]:
    """Transcript-bearing uploads for the coverage ratio, split by language."""
    body = {"size": 0, "track_total_hits": True,
            "query": {"bool": {"filter": [{"term": {"doc_type": "article"}},
                                          {"term": {"channel.id": channel}},
                                          {"exists": {"field": "transcript"}}]}},
            "aggs": {"lang": {"terms": {"field": "transcript_language", "size": 50}}}}
    data = tl_data._tl_json(["db", "es", "-", "--json"], input_text=json.dumps(body))
    total = int((data or {}).get("total") or 0)
    langs: dict[str, int] = {}
    buckets = (((data or {}).get("aggregations") or {}).get("lang") or {}).get("buckets") or []
    for b in buckets:
        langs[str(b.get("key"))] = int(b.get("doc_count") or 0)
    return total, langs


def latest_upload(channel: int) -> str | None:
    """Date of the channel's newest upload at fetch time. The meta record
    keeps it so a later run can count uploads since with one query."""
    body = {"size": 1, "_source": ["id", "publication_date"],
            "sort": [{"publication_date": "desc"}],
            "query": {"bool": {"filter": [{"term": {"doc_type": "article"}},
                                          {"term": {"channel.id": channel}}]}}}
    data = tl_data._tl_json(["db", "es", "-", "--json"], input_text=json.dumps(body))
    rows = (data or {}).get("results") or []
    if not rows:
        return None
    return (rows[0].get("publication_date") or "")[:10] or None


def fetch_non_english(channel: int, size: int = 40, since: str | None = None) -> list[dict]:
    """Uploads whose captions are not English carry no English cue phrase, so
    the cue query cannot see them. Pull their transcripts and stride-sample a
    few windows per video into the same schema; the extractor judges them in
    the source language (rubric), and they enter the cap at a flat score."""
    docs: list[dict] = []
    after = None
    while True:
        body = {"size": size,
                "_source": ["id", "title", "publication_date", "transcript_language",
                            "content_type", "duration", "transcript"],
                "query": {"bool": {"filter": [{"term": {"doc_type": "article"}},
                                              {"term": {"channel.id": channel}},
                                              {"exists": {"field": "transcript"}},
                                              {"exists": {"field": "transcript_language"}}]
                                   + ([{"range": {"publication_date": {"gt": since}}}] if since else []),
                                   "must_not": [{"terms": {"transcript_language": sorted(ENGLISH_CODES)}}]}},
                "sort": [{"publication_date": "desc"}, {"id": "asc"}]}
        if after:
            body["search_after"] = after
        rows = tl_data.cli_rows(["db", "es", "-", "--json"], input_text=json.dumps(body))
        docs.extend(r for r in rows if not is_english(r.get("transcript_language")))
        if len(rows) < size:
            return docs
        last = rows[-1]
        after = [last.get("publication_date"), last.get("id")]
        if not after[0] or not after[1]:
            return docs


def sample_windows(cue_list: list, per_video: int = NON_EN_WINDOWS_PER_VIDEO,
                   words: int = NON_EN_WINDOW_WORDS) -> list[list[list]]:
    """Evenly spaced runs of consecutive cues, each about ``words`` long."""
    runs: list[list[list]] = []
    cur: list[list] = []
    n = 0
    for st, t in cue_list:
        cur.append([round(float(st), 2), t])
        n += len(t.split())
        if n >= words:
            runs.append(cur)
            cur = []
            n = 0
    if cur and n >= 8:
        runs.append(cur)
    if len(runs) <= per_video:
        return runs
    step = len(runs) / per_video
    return [runs[int(i * step)] for i in range(per_video)]


def date_range(year: int, since: str | None) -> dict:
    """The year bucket's publication window, cut down to uploads after
    ``since`` when a refresh round only wants what is new."""
    lo = f"{year}-01-01"
    rng = {"gte": lo, "lt": f"{year + 1}-01-01"}
    if since and since >= lo:
        rng = {"gt": since, "lt": f"{year + 1}-01-01"}
    return {"range": {"publication_date": rng}}


def query_body(channel: int, phrases: list[str], year: int, size: int,
               fragment_size: int, fragments: int, after: list | None,
               since: str | None = None) -> dict:
    should = [{"match_phrase": {"transcript": {"query": p, "boost": 2.0}}} for p in phrases]
    should += [{"match": {"transcript": {"query": g, "boost": GENERIC_BOOST}}} for g in GENERIC_TERMS]
    body = {
        "size": size,
        "_source": ["id", "title", "publication_date", "transcript_language",
                    "content_type", "duration"],
        "query": {"bool": {
            "filter": [
                {"term": {"doc_type": "article"}},
                {"term": {"channel.id": channel}},
                {"exists": {"field": "transcript"}},
                date_range(year, since),
            ],
            "must": [{"bool": {"should": should, "minimum_should_match": 1}}],
        }},
        "sort": [{"publication_date": "desc"}, {"id": "asc"}],
        "highlight": {"fields": {"transcript": {
            "fragment_size": fragment_size, "number_of_fragments": fragments,
            "order": "score"}}},
    }
    if after:
        body["search_after"] = after
    return body


def fetch_year(channel: int, phrases: list[str], year: int, size: int,
               fragment_size: int, fragments: int, since: str | None = None) -> list[dict]:
    docs: list[dict] = []
    after = None
    while True:
        body = query_body(channel, phrases, year, size, fragment_size, fragments, after, since)
        rows = tl_data.cli_rows(["db", "es", "-", "--json", "--highlight"],
                                input_text=json.dumps(body))
        docs.extend(rows)
        if len(rows) < size:
            return docs
        last = rows[-1]
        after = [last.get("publication_date"), last.get("id")]
        if not after[0] or not after[1]:
            return docs


def apply_sponsor_spans(kept: list[dict]) -> str:
    """Re-decide ``in_sponsor_read`` for the kept windows from real ad-read spans.

    The windows are built with a regex heuristic already in the flag, because a
    span lookup over every passage in the catalogue would cost more than the
    cap it feeds. Once the cap is taken, the kept windows name a few hundred
    videos at most, so the spans are looked up for real: a window overlaps an
    ad read when ``[start, start + WINDOW_SPAN]`` meets a sponsored span padded
    by ``SPONSOR_PAD`` on both sides.

    The lookup is authoritative when it succeeds — it replaces the heuristic
    rather than joining it. When it fails, the heuristic stays exactly as it
    was. Returns the source used, which the summary records as
    ``sponsor_source`` so a reader always knows which of the two decided.
    """
    refs = sorted({w["id"] for w in kept})
    if not refs:
        return "none"
    try:
        segments = sponsor_spans.sponsor_segments(refs)
    except BaseException as exc:              # noqa: BLE001 — reported, not raised
        print(f"sponsor-span lookup failed ({type(exc).__name__}: "
              f"{str(exc)[:120]}) — keeping the regex heuristic", file=sys.stderr)
        return "regex_fallback"
    pad = sponsor_spans.SPONSOR_PAD
    for w in kept:
        segs = segments.get(w["id"]) or []
        lo, hi = w["start"], w["start"] + WINDOW_SPAN
        w["in_sponsor_read"] = any(lo <= e + pad and hi >= s - pad
                                   for s, e in segs)
    return "brand_mentions"


DEFAULT_AGENT_CAP = 20      # concurrent subagents the host runs when nothing says otherwise
MIN_BATCH_SIZE = 5          # below this the per-agent overhead outweighs the parallelism


def env_agent_cap() -> int:
    """How many extractor agents can run at once: $CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS
    when the host sets it, else the default of 20. Garbage falls back with a note."""
    raw = os.environ.get("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "").strip()
    if not raw:
        return DEFAULT_AGENT_CAP
    try:
        n = int(raw)
    except ValueError:
        print(f"CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS={raw!r} is not an integer; "
              f"using {DEFAULT_AGENT_CAP}", file=sys.stderr)
        return DEFAULT_AGENT_CAP
    return n if n >= 1 else DEFAULT_AGENT_CAP


def derived_batch_size(windows: int, agent_cap: int) -> int:
    """The batch size that spreads the kept windows over every agent the host
    allows in one wave: ceil(windows / cap), never below MIN_BATCH_SIZE."""
    if windows <= 0:
        return MIN_BATCH_SIZE
    return max(MIN_BATCH_SIZE, -(-windows // max(1, agent_cap)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", type=int, required=True)
    ap.add_argument("--out", default="tl-creator-profiles/.corpus",
                    help="PARENT directory for the corpus; the channel id is appended, "
                         "so the run writes <out>/<channel>/. Passing a path that already "
                         "ends in the channel id nests it twice")
    ap.add_argument("--phrases", default=str(DEFAULT_PHRASES))
    ap.add_argument("--host-terms", default="")
    ap.add_argument("--max-windows", type=int, default=500)
    ap.add_argument("--batch-size", type=int, default=None,
                    help="windows per batch file (one extractor each); default: enough batches "
                         "to use every concurrent agent the host allows, "
                         "ceil(windows / $CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS), cap 20 when unset")
    ap.add_argument("--reserve", type=int, default=0,
                    help="agent slots held by other lanes during the fan-out "
                         "(1 when the socials lane is on). Batches are sized "
                         "against cap minus this, so the last extractor is "
                         "not rejected and relaunched a wave later")
    ap.add_argument("--fragment-size", type=int, default=900)
    ap.add_argument("--fragments-per-doc", type=int, default=10)
    ap.add_argument("--per-video-cap", type=int, default=8)
    ap.add_argument("--page-size", type=int, default=150)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--round", type=int, default=1, help="additive round number; round N writes "
                    "batches-rN/ and clears returns-rN/, merges its passages into the existing "
                    "corpus.jsonl.gz, and keeps earlier rounds' artifacts intact")
    ap.add_argument("--exclude", default="", help="classified.jsonl from earlier rounds; "
                    "passages already judged (same video, start within 30 s) are skipped, so a "
                    "second round deepens the ledger instead of repeating it")
    ap.add_argument("--since", default="", help="only uploads published after this date "
                    "(YYYY-MM-DD) — a refresh round passes the ledger's latest_video_date so "
                    "its cost scales with the new uploads, not the catalogue")
    a = ap.parse_args()
    t0 = time.monotonic()
    since = a.since.strip() or None

    phrases, recurring = load_phrases(pathlib.Path(a.phrases))
    host_terms = [t.strip() for t in a.host_terms.split(",") if t.strip()]
    host_lc = {t.lower() for t in host_terms}
    all_phrases = phrases + host_terms

    docs: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        futs = [pool.submit(fetch_year, a.channel, all_phrases, y, a.page_size,
                            a.fragment_size, a.fragments_per_doc, since)
                for y in YEARS if not since or y >= int(since[:4])]
        for f in futs:
            docs.extend(f.result())
    queries_note = f"{len(YEARS)} year buckets"
    try:
        videos_with_transcript, langs = census(a.channel)
    except Exception as exc:  # census is reporting, never a gate
        videos_with_transcript, langs = 0, {}
        print(f"census failed: {exc}", file=sys.stderr)
    try:
        latest_video_date = latest_upload(a.channel)
    except Exception as exc:  # reporting, never a gate
        latest_video_date = None
        print(f"latest-upload lookup failed: {exc}", file=sys.stderr)
    non_en_docs: list[dict] = []
    non_en_failed = False
    non_en_total = sum(n for k, n in langs.items() if not is_english(k))
    if non_en_total:
        try:
            non_en_docs = fetch_non_english(a.channel, since=since)
        except Exception as exc:
            non_en_failed = True
            print(f"non-English fetch failed: {exc}", file=sys.stderr)
    if non_en_failed:
        # the round did not read everything after the old watermark, so it
        # must not advance it: the summary carries no latest_video_date and the
        # ledger header keeps the previous round's, so the next --since refresh
        # re-covers the uploads this round missed
        latest_video_date = None

    done: dict[str, list[int]] = {}
    if a.exclude and pathlib.Path(a.exclude).exists():
        for line in open(a.exclude, encoding="utf-8"):
            if line.strip():
                w = json.loads(line).get("window") or {}
                done.setdefault(w.get("id", ""), []).append(int(w.get("start", 0)))
    windows: list[dict] = []
    corpus: dict[str, dict] = {}
    for d in docs:
        vid = d.get("id") or d.get("_id")
        if not vid:
            continue
        frags = (d.get("highlight") or {}).get("transcript") or []
        entry = corpus.setdefault(vid, {
            "id": vid, "title": d.get("title"), "publication_date": d.get("publication_date"),
            "views": None, "duration": d.get("duration"), "content_type": d.get("content_type"),
            "transcript_language": d.get("transcript_language"), "cues": []})
        seen_starts: list[float] = []
        for frag in frags:
            text, hits, start, raw_hits, pieces = clean(frag)
            if start is None or len(text.split()) < 8:
                continue
            if any(abs(start - s) < 30 for s in seen_starts):
                continue
            if any(abs(start - s) < 30 for s in done.get(vid, [])):
                continue
            seen_starts.append(start)
            generic = {g.lower() for g in GENERIC_TERMS}
            cue_hits = [h for h in hits if h not in host_lc and h not in generic]
            anchor_hits = [h for h in hits if h in host_lc]
            density = sum(1 for h in raw_hits if h in generic)
            specific = [h for h in cue_hits if h not in recurring]
            rec = [h for h in cue_hits if h in recurring]
            entry["cues"].extend(pieces)
            heur = round(min(sum(0.5 if h in WEAK_CUES else 1.0 for h in specific), 4)
                         + 0.5 * min(len(rec), 1) + 2 * len(anchor_hits)
                         + GENERIC_BOOST * min(density, 12), 2)
            score = heur
            windows.append({
                "id": vid, "video_id": vid.split(":", 1)[-1], "title": d.get("title"),
                "language": d.get("transcript_language"), "format_hint": format_hint(d.get("title")),
                "published": (d.get("publication_date") or "")[:10],
                "start": int(start), "text": text,
                "cues_fired": cue_hits, "host_anchor": bool(anchor_hits),
                "host_anchor_terms": [[h, "strong"] for h in anchor_hits],
                "entity_hits": [], "weak_anchor": False, "stage_direction": False,
                "boilerplate": False,
                "in_sponsor_read": bool(SPONSOR_RX.search(text)),
                "recurrence_videos": 0, "recurring_phrase": None,
                "rank_score": score,
                "_specific": specific, "_recurring": rec,
            })

    import corpus_io  # sibling; parses the timed-text XML into [start, text] cues
    for d in non_en_docs:
        vid = d.get("id") or d.get("_id")
        if not vid or vid in corpus:
            continue
        cue_list = corpus_io.cues(d.get("transcript"))
        entry = corpus.setdefault(vid, {
            "id": vid, "title": d.get("title"), "publication_date": d.get("publication_date"),
            "views": None, "duration": d.get("duration"), "content_type": d.get("content_type"),
            "transcript_language": d.get("transcript_language"), "cues": []})
        for run in sample_windows(cue_list):
            start = run[0][0]
            if any(abs(start - s_) < 30 for s_ in done.get(vid, [])):
                continue
            text = " ".join(t for _, t in run)
            entry["cues"].extend(run)
            windows.append({
                "id": vid, "video_id": vid.split(":", 1)[-1], "title": d.get("title"),
                "language": d.get("transcript_language"), "format_hint": format_hint(d.get("title")),
                "published": (d.get("publication_date") or "")[:10],
                "start": int(start), "text": text, "cues_fired": [], "host_anchor": False,
                "host_anchor_terms": [], "entity_hits": [], "weak_anchor": False,
                "stage_direction": False, "boilerplate": False,
                "in_sponsor_read": bool(SPONSOR_RX.search(text)),
                "recurrence_videos": 0, "recurring_phrase": None, "rank_score": 1.0,
                "_specific": [], "_recurring": [],
            })
    windows.sort(key=lambda w: (-w["rank_score"], w["published"]))
    per_video: dict[str, int] = {}
    per_phrase: dict[str, int] = {}
    phrase_cap = max(RECURRING_CAP, int(a.max_windows * PHRASE_CAP_SHARE))
    kept: list[dict] = []
    # Spread ties across the channel's history: within the same score, take one
    # passage per year in turn instead of newest-first, so a profile spans the
    # back catalogue rather than the last twelve months.
    pos_in_year: dict[str, int] = {}
    order: list[tuple[float, int, int]] = []
    for i, w in enumerate(windows):
        y = w["published"][:4]
        pos_in_year[y] = pos_in_year.get(y, 0) + 1
        order.append((-w["rank_score"], pos_in_year[y], i))
    order.sort()
    def eligible(w):
        n = per_video.get(w["id"], 0)
        if n >= a.per_video_cap:
            return False
        strong = [c for c in w["_specific"] if c not in WEAK_CUES]
        if w["_recurring"] and len(strong) < 2 and any(
                per_phrase.get(c, 0) >= RECURRING_CAP for c in w["_recurring"]):
            return False
        cues = w["_specific"] or w["_recurring"]
        if cues and all(per_phrase.get(c, 0) >= phrase_cap for c in cues):
            return False
        return True
    for _, _, i in order:
        w = windows[i]
        if not eligible(w):
            continue
        for c in (w["_specific"] or w["_recurring"]) + w["_recurring"]:
            per_phrase[c] = per_phrase.get(c, 0) + 1
        per_video[w["id"]] = per_video.get(w["id"], 0) + 1
        kept.append(w)
        if len(kept) >= a.max_windows:
            break
    for w in windows:
        w.pop("_specific", None)
        w.pop("_recurring", None)
    sponsor_source = apply_sponsor_spans(kept)

    out = pathlib.Path(a.out) / str(a.channel)
    suffix = "" if a.round <= 1 else f"-r{a.round}"
    bdir = out / f"batches{suffix}"
    rdir = out / f"returns{suffix}"
    bdir.mkdir(parents=True, exist_ok=True)
    if a.round <= 1:
        # a first round is a fresh build: nothing from an earlier build's
        # later rounds may leak into this one's counts or its passage store
        for stale in list(out.glob("fetch-r*.json")) + list(out.glob("windows-r*.jsonl.gz")) + [
                out / n for n in ("classified.jsonl", "gems.jsonl", "gems-clustered.jsonl",
                                  "candidates.jsonl", "respawn.json")]:
            if stale.exists():
                stale.unlink()
        for d in list(out.glob("batches-r*")) + list(out.glob("returns-r*")):
            for f in d.glob("*"):
                f.unlink()
            d.rmdir()
        if (out / "corpus.jsonl.gz").exists():
            (out / "corpus.jsonl.gz").unlink()
    for old in bdir.glob("batch-*.json"):
        old.unlink()
    if rdir.exists():                      # a return for a batch that no longer exists is stale
        for old in rdir.glob("batch-*.extract*.json"):
            old.unlink()
    with gzip.open(out / f"windows{suffix}.jsonl.gz", "wt", encoding="utf-8") as fh:
        for w in windows:
            fh.write(json.dumps(w, ensure_ascii=False) + "\n")
    corpus_path = out / "corpus.jsonl.gz"
    if corpus_path.exists():               # merge, never replace: earlier rounds must still verify
        with gzip.open(corpus_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                e = json.loads(line)
                cur = corpus.get(e["id"])
                if cur is None:
                    corpus[e["id"]] = e
                else:
                    have = {round(float(c[0]), 2) for c in cur["cues"]}
                    cur["cues"].extend(c for c in e["cues"] if round(float(c[0]), 2) not in have)
    with gzip.open(corpus_path, "wt", encoding="utf-8") as fh:
        for e in corpus.values():
            e["cues"].sort(key=lambda c: c[0])
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    agent_cap = env_agent_cap()
    # Every running agent counts against the host's cap, so a lane in flight
    # during the fan-out costs one extractor slot: with 20 batches for a cap of
    # 20 and the socials lane running, the 20th extractor was rejected and
    # relaunched a wave later. Size the batches against what is actually free.
    usable_cap = max(1, agent_cap - max(0, a.reserve))
    batch_size = a.batch_size or derived_batch_size(len(kept), usable_cap)
    batches = []
    for i in range(0, len(kept), batch_size):
        p = bdir / f"batch-{i // batch_size:03d}.json"
        p.write_text(json.dumps(kept[i:i + batch_size], ensure_ascii=False))
        batches.append(str(p))
    elapsed = round(time.monotonic() - t0, 1)
    summary = {
        "channel": a.channel, "round": a.round, "phrases": len(all_phrases), "queries": queries_note,
        "videos_with_transcript": videos_with_transcript, "languages": langs,
        "non_english_videos_sampled": len(non_en_docs),
        "videos_matched": len(corpus), "passages": len(windows),
        "windows_batched": len(kept), "videos_in_batches": len(per_video),
        "batch_size": batch_size, "agent_cap": agent_cap,
        "reserved_slots": max(0, a.reserve), "usable_cap": usable_cap,
        "sponsor_flagged": sum(1 for w in kept if w["in_sponsor_read"]),
        "sponsor_source": sponsor_source,
        "batches": batches, "returns_dir": str(rdir),
        "windows_file": str(out / f"windows{suffix}.jsonl.gz"),
        "corpus": str(corpus_path), "latest_video_date": latest_video_date,
        "non_english_fetch_failed": non_en_failed,
        "elapsed_s": elapsed,
    }
    summary_path = out / f"fetch{suffix}.json"   # ledger_meta.py reads these per round
    summary["summary_file"] = str(summary_path)
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print(f"FUNNEL stage=fetch_cues round={a.round} videos_with_transcript={videos_with_transcript} "
          f"videos_matched={len(corpus)} non_english_sampled={len(non_en_docs)} passages={len(windows)} "
          f"windows_capped={len(kept)} batches={len(batches)} batch_size={batch_size} "
          f"agent_cap={agent_cap} sponsor_source={sponsor_source} elapsed_s={elapsed}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
