#!/usr/bin/env python3
"""Probe ES for keyword counts (documents AND distinct channels) + validation samples.

For each candidate, sends ONE `tl db es` query that returns:
  * the full document count (`track_total_hits`),
  * the distinct-channel count (a `cardinality` agg on the channel-identity
    field) — the real niche-size signal, since channel docs are duplicated
    across quarterly indexes so the raw document total over-counts them, and
  * up to N sample documents (`size`, top-scored, `collapse`d to distinct
    channels so one prolific channel can't flood them) with just the fields a
    relevance check needs (title/summary for topic search; channel name/AI topic
    description for channel search), emitted under friendly keys.

It then drops empty candidates (recording the reason), annotates which broader
phrases subsume each candidate (an OR-union redundancy hint, NOT a drop), and
prints a single JSON object with the survivors ranked by count.

The samples feed `select_keywords.py` (which builds the validation batch for the
`keyword-relevance-validator` Haiku sub-agent and applies its verdict) and the
main agent's second-pass keyword discovery. We return whole `_source` fields
rather than ES `highlight` fragments (available via `tl db es --highlight`; see
the `tl` skill's SKILL.md) because the samples need the full title/summary.

Usage:
    probe.py "tiktok shop" "tiktok affiliate" "tiktok ads"
    probe.py --level channel "cooking" "baking"
    probe.py --mode sqs '"tiktok shop" +(marketing|affiliate)' '"tiktok shop" -dropshipping'
    echo '["crypto","bitcoin"]' | probe.py
    echo '[{"label":"shop+amazon","sqs":"\"tiktok shop\" +amazon"}]' | probe.py --mode sqs

Output (stdout): a single JSON object — see OUTPUT CONTRACT at the bottom.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys

# Article-level (doc_type:article) text fields vs channel-level (doc_type:channel).
# `summary` holds the video's creator-written description (links, hashtags, promo)
# — not an AI summary. Article-level `description` is empty and `content` is
# podcast-only; neither belongs in these lists.
TOPIC_FIELDS = ["title", "summary", "transcript"]
# `description.domains` alongside plain `description`: the delivered report
# filter searches `description.domains`, which matches domain-shaped terms
# (e.g. "patreon.com") that plain `description` undercounts (URLs there
# tokenize into word pieces) — probing both keeps counts faithful to what
# the delivered filter will actually match.
CHANNEL_FIELDS = ["name", "description", "description.domains", "ai.description", "ai.topic_descriptions"]

# The channel-identity field per level — used both to `collapse` samples to
# distinct channels and to count distinct channels via a cardinality agg.
# Channel docs are written once per quarterly index, so a channel appears many
# times in the raw hit total; collapsing/cardinality on this field recovers the
# real per-channel picture. Topic docs nest it under `channel.id`; channel docs
# carry their own `id`.
COLLAPSE_FIELD = {"topic": "channel.id", "channel": "id"}

# Format = source platform; 4 = YouTube uploads. We ALWAYS scope to YouTube (that's
# the inventory we work with). Topic docs nest it under `channel.format`; channel
# docs carry their own `format`.
FORMAT_FIELD = {"topic": "channel.format", "channel": "format"}
YOUTUBE_FORMAT = 4

# content_type tags a video as longform / short / live. It exists ONLY on article
# (video) docs — channel docs have no content type. We default to longform (best
# signal for sponsorable content); "all" drops the filter.
CONTENT_TYPES = ("longform", "short", "live", "all")

# Sample fields returned per doc, as (ES _source path, friendly output key) pairs.
# The friendly keys are what select_keywords.py / the validator sub-agent read.
TOPIC_SAMPLE_FIELDS = [
    ("title", "title"), ("summary", "summary"),
    ("channel.id", "channel_id"), ("channel.content_category", "category"), ("url", "url"),
]
CHANNEL_SAMPLE_FIELDS = [
    ("name", "name"), ("ai.topic_descriptions", "topic"),
    ("ai.description", "channel_description"), ("id", "channel_id"),
]

DEFAULT_SAMPLES = 5
MAX_SAMPLES = 25
MAX_SAMPLE_TEXT = 500  # trim long text so samples stay light for the validator
PROBE_TIMEOUT = 90  # per-candidate ES probe, seconds
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_FIELD_RE = re.compile(r"^[\w.]+(\^\d+(\.\d+)?)?$")  # e.g. title, ai.description, title^3


def tokens(text):
    """Lowercased word tokens — used for lexical subsumption detection."""
    return _TOKEN_RE.findall(text.lower())


def is_contiguous_sublist(needle, haystack):
    """True if `needle` token list appears as a contiguous run inside `haystack`."""
    n, h = len(needle), len(haystack)
    if n == 0 or n >= h:
        return False
    return any(haystack[i:i + n] == needle for i in range(h - n + 1))


def valid_date(value):
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def build_body(candidate, *, fields, level, samples, source_paths, since, until,
               recency_cutoff=None, content_type="longform"):
    """Build one ES body that returns counts + sample docs for a candidate.

    `candidate` is a dict {"label", "value", "mode"}; mode is "phrase" (literal
    phrase across `fields`) or "sqs" (`value` passed to simple_query_string).
    `since`/`until` are applied only when given (topic level only — see main).

    The query is always scoped to YouTube uploads (`format` 4). At topic level it
    is also scoped to `content_type` (default "longform"; "all" drops that filter).
    `content_type` is ignored at channel level (channel docs have no content type).

    The body carries the count signals plus deduped samples:
      * `track_total_hits` → the raw document total (videos at topic level;
        channel docs — duplicated in the index — at channel level),
      * a `distinct_channels` cardinality agg on the channel-identity field →
        how many *distinct channels* the term reaches, and
      * `collapse` on the same field → samples are distinct channels, so one
        prolific channel can't flood every sample slot.

    Headline counts stay ALL-TIME. When recency is on, an extra agg measures
    *active* content WITHOUT re-scoping the query:
      * topic level (`recency_cutoff` given) → a `recent_window` filter-agg over
        `publication_date >= cutoff` with a nested distinct-channel cardinality;
      * channel level → an `active_window` filter-agg over `posts_per_90_days > 0`
        (channel docs have no date) with a nested distinct-channel cardinality.
    """
    doc_type = "article" if level == "topic" else "channel"
    collapse_field = COLLAPSE_FIELD[level]
    if candidate["mode"] == "sqs":
        match = {
            "simple_query_string": {
                "query": candidate["value"],
                "fields": fields,
                "default_operator": "and",
            }
        }
    else:
        match = {"multi_match": {"query": candidate["value"], "type": "phrase", "fields": fields}}

    # Always scope to YouTube uploads (format 4). At topic level also default to
    # longform videos (content_type is article-only; channel docs have none).
    filters = [
        {"term": {"doc_type": doc_type}},
        {"term": {FORMAT_FIELD[level]: YOUTUBE_FORMAT}},
    ]
    if level == "topic" and content_type and content_type != "all":
        filters.append({"term": {"content_type": content_type}})
    if since or until:
        date_range = {}
        if since:
            date_range["gte"] = since
        if until:
            date_range["lte"] = until
        filters.append({"range": {"publication_date": date_range}})

    aggs = {"distinct_channels": {"cardinality": {"field": collapse_field}}}
    if level == "topic" and recency_cutoff:
        aggs["recent_window"] = {
            "filter": {"range": {"publication_date": {"gte": recency_cutoff}}},
            "aggs": {"recent_channels": {"cardinality": {"field": collapse_field}}},
        }
    elif level == "channel" and recency_cutoff:
        # channel docs carry no date; "posting in the last 90 days" is the
        # liveness proxy. recency_cutoff is a sentinel here ("active"), not a date.
        aggs["active_window"] = {
            "filter": {"range": {"posts_per_90_days": {"gt": 0}}},
            "aggs": {"active_channels": {"cardinality": {"field": collapse_field}}},
        }

    return {
        "size": samples,
        "track_total_hits": True,
        "_source": source_paths,
        "collapse": {"field": collapse_field},
        "aggs": aggs,
        "query": {"bool": {"filter": filters, "must": [match]}},
    }


class ProbeError(Exception):
    """One candidate's ES probe failed (timeout, non-zero exit, bad JSON)."""


def run_es(body):
    """Run `tl db es - --json` with `body` on stdin; return parsed response.

    Raises ProbeError on failure so the caller can record the candidate and
    continue — one slow/heavy term must not abort a 60-candidate batch.
    """
    try:
        proc = subprocess.run(
            ["tl", "db", "es", "-", "--json"],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ProbeError(f"timed out after {PROBE_TIMEOUT}s")
    if proc.returncode != 0:
        raise ProbeError(
            f"tl db es failed (rc={proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"could not parse tl db es output: {exc}: {proc.stdout[:300]}")


def extract_total(data):
    total = data.get("total")
    if isinstance(total, int):
        return total
    hits = data.get("hits")
    if isinstance(hits, dict):
        ht = hits.get("total")
        if isinstance(ht, dict) and isinstance(ht.get("value"), int):
            return ht["value"]
        if isinstance(ht, int):
            return ht
    return 0


def _agg_root(data):
    return data.get("aggregations") or data.get("aggs") or {}


def extract_distinct(data):
    """Read the `distinct_channels` cardinality agg (distinct channels reached).

    Cardinality is HyperLogLog++ — approximate, but exact at the low counts that
    matter for a niche check. Returns 0 when the agg is absent (e.g. a stub
    response in tests that only sets `total`)."""
    dc = _agg_root(data).get("distinct_channels")
    if isinstance(dc, dict) and isinstance(dc.get("value"), (int, float)):
        return int(dc["value"])
    return 0


def extract_recent(data):
    """Topic-level recency: (recent_documents, recent_channels) from the
    `recent_window` filter-agg (docs published within the window + the distinct
    channels among them). (0, 0) when absent."""
    rw = _agg_root(data).get("recent_window")
    if not isinstance(rw, dict):
        return 0, 0
    docs = rw.get("doc_count", 0) or 0
    rc = rw.get("recent_channels", {})
    chans = rc.get("value", 0) if isinstance(rc, dict) else 0
    return int(docs), int(chans or 0)


def extract_active(data):
    """Channel-level recency: distinct channels posting in the last 90 days, from
    the `active_window` filter-agg. 0 when absent."""
    aw = _agg_root(data).get("active_window")
    if not isinstance(aw, dict):
        return 0
    ac = aw.get("active_channels", {})
    return int(ac.get("value", 0) or 0) if isinstance(ac, dict) else 0


def months_ago_iso(months):
    """ISO date `months` whole months before today (day clamped to <=28 to avoid
    month-length edge cases). Used as the topic-level recency cutoff."""
    today = datetime.date.today()
    y, m = today.year, today.month - months
    while m <= 0:
        m += 12
        y -= 1
    return datetime.date(y, m, min(today.day, 28)).isoformat()


def extract_samples(data, field_pairs):
    """Pull (es_path -> friendly_key) fields out of each returned row."""
    rows = data.get("results")
    if not isinstance(rows, list):
        hits = data.get("hits", {})
        rows = hits.get("hits", []) if isinstance(hits, dict) else []
    out = []
    for row in rows:
        src = row.get("_source", row)  # CLI flattens _source into the row
        sample = {}
        for path, key in field_pairs:
            if "." in path:
                top, sub = path.split(".", 1)
                val = src.get(top)
                value = val.get(sub) if isinstance(val, dict) else None
            else:
                value = src.get(path)
            if isinstance(value, str) and len(value) > MAX_SAMPLE_TEXT:
                value = value[:MAX_SAMPLE_TEXT]
            sample[key] = value
        out.append(sample)
    return out


def collect_candidates(argv_words):
    """Read candidates from argv (preferred) or stdin (JSON array / newlines).

    argv and stdin are not merged — if argv is non-empty, stdin is ignored.
    """
    if argv_words:
        return [w for w in argv_words if w.strip()]
    if sys.stdin.isatty():
        return []
    raw = sys.stdin.read().strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if isinstance(parsed, list):
        return parsed
    sys.exit("stdin JSON must be a list")


def normalize(raw_items, default_mode):
    """Turn raw argv/stdin items into {label, value, mode} dicts, deduped."""
    out, seen = [], set()
    for item in raw_items:
        if isinstance(item, dict):
            if "sqs" in item:
                value, mode = item["sqs"], "sqs"
            elif "phrase" in item:
                value, mode = item["phrase"], "phrase"
            elif "value" in item:
                value, mode = item["value"], item.get("mode", default_mode)
            elif "keyword" in item:
                value, mode = item["keyword"], item.get("mode", default_mode)
            else:
                sys.exit(f"candidate object missing value/sqs/phrase/keyword: {item}")
            value = str(value).strip()
            label = str(item.get("label", value)).strip() or value
        else:
            value = str(item).strip()
            label = value
            mode = default_mode
        if not value:
            continue
        key = (mode, value.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "value": value, "mode": mode})
    return out


def mark_subsumed(results, operator):
    """Annotate each candidate with the broader phrases that subsume it (info only).

    A *phrase* candidate A is subsumed by phrase candidate B when B's tokens are
    a contiguous run inside A's tokens — every doc matching phrase A also matches
    phrase B, so A adds ~0 new documents to an OR union *that keeps B*. We record
    this (`subsumed_by` = broader present phrases, shortest first); we do NOT drop
    A. Union-redundancy pruning runs AFTER intent validation (build_report.py),
    otherwise the broadest term always wins the union and the precise term gets
    dropped. Only meaningful for OR + phrase candidates (sqs carries its own
    Booleans, so it is excluded).
    """
    if operator != "OR":
        return
    phrase = [r for r in results if r["_mode"] == "phrase" and r["count"] > 0]
    tok = {r["keyword"]: tokens(r["keyword"]) for r in phrase}
    for a in phrase:
        broader = [b for b in phrase
                   if b is not a and is_contiguous_sublist(tok[b["keyword"]], tok[a["keyword"]])]
        broader.sort(key=lambda b: len(tok[b["keyword"]]))
        a["subsumed_by"] = [b["keyword"] for b in broader]


def annotate_recency(row, data, *, level, min_recent_channels, min_share):
    """Layer recency / active-content fields + a `stale` flag onto a result row.

    Headline `count` / `channels` stay all-time. Stale is ABSOLUTE-FIRST: a keyword
    is stale only when BOTH its recent (topic) / active (channel) distinct-channel
    count is below the floor AND its share of all-time channels is below the
    threshold — so a high-volume evergreen term (large recent reach, small share)
    is never mislabeled. Annotation only; staleness never drops a keyword here.
    """
    channels = row["channels"]
    if level == "topic":
        recent_docs, recent_n = extract_recent(data)
        share = (recent_n / channels) if channels else 0.0
        row["recent_documents"] = recent_docs
        row["recent_channels"] = recent_n
        row["recent_channel_share"] = round(share, 4)
        word = "recent"
    else:
        recent_n = extract_active(data)
        share = (recent_n / channels) if channels else 0.0
        row["active_channels"] = recent_n
        row["active_channel_share"] = round(share, 4)
        word = "active"
    below_floor = recent_n < min_recent_channels
    stale = below_floor and share < min_share
    row["stale"] = stale
    row["stale_reason"] = (
        f"{word}_channels {recent_n} < {min_recent_channels} and "
        f"{word}_share {share:.0%} < {min_share:.0%}" if stale else ""
    )
    # THIN: below the absolute floor but share rescued it from "stale" — a narrow
    # niche that's still proportionally active. Surface it; don't drop it.
    row["thin"] = below_floor and not stale


def main():
    ap = argparse.ArgumentParser(
        description="Probe ES for keyword counts + validation samples; drop empty, annotate subsumed."
    )
    ap.add_argument("keywords", nargs="*", help="Candidates (or pipe a JSON array on stdin)")
    ap.add_argument("--operator", choices=["AND", "OR"], default="OR",
                    help="How the caller will combine survivors downstream (default OR). "
                         "Echoed in output and drives subsumption annotation (OR only).")
    ap.add_argument("--level", choices=["topic", "channel"], default="topic",
                    help="topic = videos/articles (doc_type:article); "
                         "channel = whole channels (doc_type:channel). Default topic.")
    ap.add_argument("--mode", choices=["phrase", "sqs"], default="phrase",
                    help="How to interpret plain candidates: phrase match (default) "
                         "or simple_query_string (Boolean: + | - \"phrase\" () field^boost).")
    ap.add_argument("--fields", default=None,
                    help="Comma-separated ES fields to search (defaults by --level).")
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                    help=f"Sample docs returned per candidate (default {DEFAULT_SAMPLES}, max {MAX_SAMPLES}; 0 = count only).")
    ap.add_argument("--content-type", choices=list(CONTENT_TYPES), default="longform",
                    help="Topic-level video content type filter (default longform). "
                         "'all' drops the filter (include shorts + live). Ignored at "
                         "channel level (channel docs have no content type). "
                         "YouTube-only (format 4) is always enforced.")
    ap.add_argument("--since", help="publication_date >= YYYY-MM-DD (topic level only)")
    ap.add_argument("--until", help="publication_date <= YYYY-MM-DD (topic level only)")
    # Recency validation: headline counts stay ALL-TIME; this layers an extra
    # "is the content still active?" signal on the SAME query (no extra round-trip).
    ap.add_argument("--no-recency", action="store_true",
                    help="Skip the recency/active-content check (pure all-time counts).")
    ap.add_argument("--recency-months", type=int, default=12,
                    help="Topic-level recency window in months (default 12). "
                         "Channel level uses posts_per_90_days>0 (channel docs have no date).")
    ap.add_argument("--min-recent-channels", type=int, default=5,
                    help="STALE floor: fewer than this many recent/active distinct channels (default 5).")
    ap.add_argument("--min-recent-share", type=float, default=0.10,
                    help="Topic STALE share: recent_channels/channels below this AND below the floor → stale (default 0.10).")
    ap.add_argument("--min-active-share", type=float, default=0.33,
                    help="Channel STALE share: active_channels/channels below this AND below the floor → stale (default 0.33).")
    args = ap.parse_args()

    if args.recency_months < 1:
        sys.exit("--recency-months must be >= 1")

    # Date windowing only makes sense for articles — channel docs have no
    # publication_date, so a range filter there silently zeroes every count.
    if (args.since or args.until) and args.level != "topic":
        sys.exit("--since/--until apply only at --level topic (channel docs have no publication date)")
    for flag, val in (("--since", args.since), ("--until", args.until)):
        if val and not valid_date(val):
            sys.exit(f"{flag} must be YYYY-MM-DD, got {val!r}")

    fields = ([f.strip() for f in args.fields.split(",") if f.strip()] if args.fields
              else (TOPIC_FIELDS if args.level == "topic" else CHANNEL_FIELDS))
    if not fields:
        sys.exit("--fields must list at least one ES field")
    bad = [f for f in fields if not _FIELD_RE.match(f)]
    if bad:
        sys.exit(f"invalid --fields entries {bad}; use ES field paths, optionally boosted (e.g. title^3)")

    field_pairs = TOPIC_SAMPLE_FIELDS if args.level == "topic" else CHANNEL_SAMPLE_FIELDS
    source_paths = [p for p, _ in field_pairs]
    samples = min(MAX_SAMPLES, max(0, args.samples))

    candidates = normalize(collect_candidates(args.keywords), args.mode)
    if not candidates:
        sys.exit("provide at least one candidate (positional args or JSON array on stdin)")

    recency = not args.no_recency
    # Topic level needs a date cutoff; channel level uses a non-date sentinel so
    # build_body emits the posts_per_90_days "active" agg instead.
    recency_cutoff = None
    if recency:
        recency_cutoff = months_ago_iso(args.recency_months) if args.level == "topic" else "active"

    results, failed = [], []
    for cand in candidates:
        body = build_body(cand, fields=fields, level=args.level, samples=samples,
                          source_paths=source_paths, since=args.since, until=args.until,
                          recency_cutoff=recency_cutoff, content_type=args.content_type)
        try:
            data = run_es(body)
        except ProbeError as exc:
            sys.stderr.write(f"probe failed for {cand['value']!r}: {exc}\n")
            failed.append({"keyword": cand["value"], "label": cand["label"], "error": str(exc)})
            continue
        documents = extract_total(data)
        channels = extract_distinct(data)
        # `count` is the level-appropriate headline used for ranking/subsumption:
        # videos at topic level, distinct channels at channel level (where the raw
        # document total is inflated by index duplication). Both raw numbers are
        # always emitted as `documents` and `channels`.
        row = {
            "keyword": cand["value"],
            "label": cand["label"],
            "count": documents if args.level == "topic" else channels,
            "documents": documents,
            "channels": channels,
            "samples": extract_samples(data, field_pairs) if samples else [],
            "_mode": cand["mode"],
            "subsumed_by": [],
        }
        if recency:
            annotate_recency(row, data, level=args.level,
                             min_recent_channels=args.min_recent_channels,
                             min_share=(args.min_recent_share if args.level == "topic"
                                        else args.min_active_share))
        results.append(row)

    mark_subsumed(results, args.operator)

    # `count` is 0 exactly when the document total is 0 (a non-empty match always
    # reaches >=1 distinct channel), so it is the single no-match gate.
    recency_keys = (("recent_documents", "recent_channels", "recent_channel_share")
                    if args.level == "topic" else ("active_channels", "active_channel_share"))
    survivors, dropped = [], []
    for r in results:
        if r["count"] == 0:
            dropped.append({"keyword": r["keyword"], "count": 0, "reason": "no_matches"})
            continue
        kept = {"keyword": r["keyword"], "count": r["count"],
                "documents": r["documents"], "channels": r["channels"],
                "subsumed_by": r["subsumed_by"], "samples": r["samples"]}
        if recency:
            for k in (*recency_keys, "stale", "stale_reason", "thin"):
                kept[k] = r[k]
        survivors.append(kept)

    survivors.sort(key=lambda r: r["count"], reverse=True)

    # Echo the always-on scope so the caller (and user) sees what was filtered.
    scope = {"format": "youtube"}
    if args.level == "topic":
        scope["content_type"] = args.content_type
    out = {
        "operator": args.operator,
        "level": args.level,
        "fields": fields,
        "scope": scope,
        "keywords": survivors,
        "dropped": dropped,
        "failed": failed,
    }
    if recency:
        out["recency"] = ({"months": args.recency_months, "cutoff": recency_cutoff}
                          if args.level == "topic" else {"signal": "posts_per_90_days>0"})
    print(json.dumps(out, ensure_ascii=False))
    if failed and not results:
        sys.exit(1)  # every candidate failed — the batch itself is broken


# OUTPUT CONTRACT (stdout, single JSON object, no prose/fences):
# {
#   "operator": "OR" | "AND",
#   "level": "topic" | "channel",
#   "fields": [<es fields searched>],
#   "scope": {"format": "youtube"[, "content_type": "longform"|"short"|"live"|"all"]},  # always-on filters
#   "failed": [ {"keyword": "...", "label": "...", "error": "..."} ],  # probes that errored/timed out — retry individually
#   "keywords": [                       # all candidates with documents>0, sorted desc by count
#     {"keyword": "...",
#      "count": <int>,        # headline for ranking: documents at topic, channels at channel
#      "documents": <int>,    # raw doc total (videos at topic; channel docs at channel — inflated)
#      "channels": <int>,     # DISTINCT channels reached (cardinality agg) — the niche-size signal
#      "subsumed_by": ["<broader phrases present, shortest first>"],  # OR redundancy hint
#      # RECENCY fields (present unless --no-recency), level-specific:
#      #   topic:   "recent_documents", "recent_channels", "recent_channel_share"  (last --recency-months)
#      #   channel: "active_channels", "active_channel_share"                       (posts_per_90_days>0)
#      #   both:    "stale" (bool), "stale_reason" (str), "thin" (bool — below the
#      #            floor but proportionally active)  — annotations only, never dropped here
#      "samples": [{"title": "...", "summary": "...", "channel_id": ...}, ...]   # topic level
#      # channel level samples use keys: name, topic, channel_description, channel_id
#      # samples are COLLAPSED to distinct channels (channel.id/id), so no single
#      # channel floods them — read them to judge breadth and on-topic-ness.
#     }
#   ],
#   "dropped": [ {"keyword": "...", "count": 0, "reason": "no_matches"} ],
#   "recency": {"months": <int>, "cutoff": "YYYY-MM-DD"}   # topic; channel -> {"signal":"posts_per_90_days>0"}
# }
# Headline counts are ALL-TIME: topic documents==count (videos) + `channels` breadth;
# channel count==channels (real distinct count), `documents` = raw index-inflated total.
# Recency fields layer "is it still active?" on the SAME query (no extra round-trip):
# read recent_channels (topic) / active_channels (channel) — robust to evergreen volume.
# STALE is absolute-first: flagged only when recent/active channels < floor AND share <
# threshold, so high-volume evergreen terms aren't mislabeled. `subsumed_by`/`stale` are
# informational; union-redundancy pruning + stale handling happen after intent validation.
if __name__ == "__main__":
    main()
