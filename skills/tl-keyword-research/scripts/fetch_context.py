#!/usr/bin/env python3
"""Fetch keyword-in-context evidence for candidate channels (for Haiku validation).

For each channel, pulls its top-scoring matching videos and extracts the text
window around each keyword occurrence, so a cheap classifier can judge whether
the channel uses the keyword in the INTENDED sense (e.g. financial "investing"
vs. "sports investing" / "investing in your faith").

`transcript` is stored as YouTube caption XML
(`<?xml ...?><transcript><text start=".." dur="..">cue</text>...`), so tags are
stripped and entities unescaped before windowing. We window whole `_source`
fields rather than ES highlight fragments (`tl db es --highlight`; see the `tl`
skill's SKILL.md). `title` / `summary` are windowed as-is.

Usage:
    fetch_context.py --channels 466311,199308 investing
    fetch_context.py --channels 5607 --samples 5 --window 200 "tiktok shop"
    fetch_context.py --operator AND --channels 5607 "tiktok shop" affiliate

Output (stdout): a JSON array, one object per channel:
    [{"channel_id","match_count","sampled","snippets":[
        {"video_id","title","field","keyword","text"}, ...]}, ...]
"""
import argparse
import html
import json
import re
import subprocess
import sys

DEFAULT_FIELDS = ["title", "summary", "transcript"]
ES_TIMEOUT = 90
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def run_es(body):
    proc = subprocess.run(
        ["tl", "db", "es", "-", "--json"],
        input=json.dumps(body), capture_output=True, text=True, timeout=ES_TIMEOUT,
    )
    if proc.returncode != 0:
        sys.stderr.write(
            f"tl db es failed (rc={proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()}\n"
        )
        sys.exit(proc.returncode or 1)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"could not parse tl db es output: {exc}\n")
        sys.exit(1)


def clean_text(value):
    """Strip XML/HTML tags, unescape entities, collapse whitespace.

    Caption text is sometimes double-escaped (e.g. `&amp;#39;`), so a single
    unescape leaves `&#39;` behind — unescape to a fixed point.
    """
    if not isinstance(value, str):
        return ""
    text = TAG_RE.sub(" ", value)
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return WS_RE.sub(" ", text).strip()


def windows(text, keyword, half, max_snips):
    """Up to `max_snips` non-overlapping ±`half`-char windows around `keyword`."""
    if not text or not keyword:
        return []
    hay, needle = text.lower(), keyword.lower()
    out, start, last_end = [], 0, -1
    while len(out) < max_snips:
        idx = hay.find(needle, start)
        if idx == -1:
            break
        lo, hi = max(0, idx - half), min(len(text), idx + len(keyword) + half)
        if lo <= last_end:  # overlaps previous window — skip ahead
            start = idx + len(keyword)
            continue
        snip = text[lo:hi].strip()
        out.append(("…" + snip if lo > 0 else snip) + ("…" if hi < len(text) else ""))
        last_end = hi
        start = hi
    return out


def build_query(channel_id, keywords, fields, operator, since, until, samples):
    clauses = [
        {"multi_match": {"query": kw, "type": "phrase", "fields": fields}}
        for kw in keywords
    ]
    bool_q = {"filter": [
        {"term": {"doc_type": "article"}},
        {"term": {"channel.id": channel_id}},
    ]}
    if operator == "AND":
        bool_q["must"] = clauses
    else:
        bool_q["should"] = clauses
        bool_q["minimum_should_match"] = 1
    if since or until:
        rng = {}
        if since:
            rng["gte"] = since
        if until:
            rng["lte"] = until
        bool_q["filter"].append({"range": {"publication_date": rng}})
    return {
        "size": samples,
        "track_total_hits": True,
        "query": {"bool": bool_q},
        "sort": [{"_score": "desc"}],
        "_source": ["id"] + fields,
    }


def channel_evidence(channel_id, keywords, fields, operator, since, until, samples, half, max_snips):
    env = run_es(build_query(channel_id, keywords, fields, operator, since, until, samples))
    snippets = []
    for row in env.get("results", []):
        vid = row.get("id") or row.get("_id")
        title = clean_text(row.get("title")) or None
        per_video = 0
        for field in fields:
            if per_video >= max_snips:
                break
            text = clean_text(row.get(field))
            if not text:
                continue
            for kw in keywords:
                for snip in windows(text, kw, half, max_snips - per_video):
                    snippets.append({"video_id": vid, "title": title, "field": field, "keyword": kw, "text": snip})
                    per_video += 1
                    if per_video >= max_snips:
                        break
                if per_video >= max_snips:
                    break
    return {
        "channel_id": channel_id,
        "match_count": env.get("total", 0),
        "sampled": len(env.get("results", [])),
        "snippets": snippets,
    }


def main():
    ap = argparse.ArgumentParser(description="Fetch keyword-in-context evidence per channel.")
    ap.add_argument("keywords", nargs="+", help="Keyword(s) to locate in context")
    ap.add_argument("--channels", required=True, help="Comma-separated channel ids")
    ap.add_argument("--operator", choices=["AND", "OR"], default="OR")
    ap.add_argument("--fields", default=",".join(DEFAULT_FIELDS),
                    help=f"Comma list of fields to search+extract (default: {','.join(DEFAULT_FIELDS)})")
    ap.add_argument("--samples", type=int, default=4, help="Videos sampled per channel (default 4)")
    ap.add_argument("--window", type=int, default=160, help="Context chars on each side of the keyword (default 160)")
    ap.add_argument("--max-snippets", type=int, default=3, help="Max snippets per video (default 3)")
    ap.add_argument("--since", help="publication_date >= YYYY-MM-DD")
    ap.add_argument("--until", help="publication_date <= YYYY-MM-DD")
    args = ap.parse_args()

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    if not fields:
        sys.exit("--fields must list at least one ES field")
    try:
        channel_ids = [int(c) for c in args.channels.split(",") if c.strip()]
    except ValueError:
        sys.exit("--channels must be comma-separated integer channel ids")
    if not channel_ids:
        sys.exit("provide at least one channel id via --channels")

    out = [
        channel_evidence(cid, args.keywords, fields, args.operator,
                         args.since, args.until, args.samples, args.window, args.max_snippets)
        for cid in channel_ids
    ]
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
