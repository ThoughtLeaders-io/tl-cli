#!/usr/bin/env python3
"""A brand's past sponsorship reads: what creators have said about it on camera.

Returns sponsored mentions the platform detected across YouTube — whoever
brokered them — **newest first**: a product changes, and the current
description of it is the one being sold.

Every snippet is re-checked per mention: only ``type == "sponsored"`` AND
``field == "transcript"`` counts, so an organic mention earlier in a video can
never displace the real sponsored read.

**Never returns price, cost, rate cards or performance grades.** None of them
say what the product is, and the output can be forwarded.

Usage:
    brand_reads.py --brand <id>
    brand_reads.py --brand <id> --brand <old-id> --max 15   # after a rebrand

Output (stdout): one JSON object.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "_shared"))
import tl_data

PAD = 25  # seconds before a detected mention, since the read starts earlier

# The detector sometimes records that a mention exists without capturing what
# was said, as a bare placeholder. That is not a read.
PLACEHOLDER = re.compile(r"^\(?\s*(in|found in)\s+(the\s+)?"
                         r"(transcript|description|title)\s*\)?\.?$", re.I)


def mention_videos(brand_ids: list[int], max_videos: int) -> list[dict]:
    """Videos carrying a sponsored mention of the brand, newest first."""
    return tl_data.db_es({
        "size": max_videos,
        "query": {"bool": {"should": [
            {"term": {"sponsored_brand_mentions": str(b)}} for b in brand_ids
        ], "minimum_should_match": 1}},
        "_source": ["id", "title", "channel.id", "channel.name",
                    "publication_date"],
        "sort": [{"publication_date": "desc"}],
    })


def mention_snippets(brand_ids: list[int], max_videos: int) -> dict:
    """The detected ad-read snippet per video, from the nested mention field."""
    rows = tl_data.db_es({
        "size": max_videos,
        "query": {"nested": {"path": "brand_mentions", "query": {"bool": {
            "must": [
                {"terms": {"brand_mentions.id": [str(b) for b in brand_ids]}},
                {"term": {"brand_mentions.type": "sponsored"}},
                # constrain to spoken reads HERE, not only in Python after
                # paging: otherwise description-only rows can fill the page
                # and hide older videos whose read has words
                {"term": {"brand_mentions.field": "transcript"}},
            ]}}}},
        "_source": ["id", "title", "publication_date", "channel.id",
                    "channel.name", "brand_mentions"],
        "sort": [{"publication_date": "desc"}],
    })
    out: dict[str, dict] = {}
    wanted = {str(b) for b in brand_ids}
    for r in rows:
        mentions = r.get("brand_mentions") or []
        if isinstance(mentions, dict):
            mentions = [mentions]
        for m in mentions:
            # Re-check every condition per mention: the video-level query
            # matched the DOC, but this list holds ALL of the video's
            # mentions — organic ones and other brands' included.
            if str(m.get("id")) not in wanted:
                continue
            if m.get("type") != "sponsored":
                continue
            if m.get("field") != "transcript":
                continue  # a summary (description) hit is the affiliate link, not speech
            key = str(r.get("id"))
            if key in out:
                continue
            words = (m.get("snippet") or "").strip()
            if PLACEHOLDER.match(words):
                words = ""
            chan = r.get("channel") if isinstance(r.get("channel"), dict) else {}
            out[key] = {
                "snippet": words,
                "entity_as_heard": m.get("entity"),
                "start": m.get("start_ts"),
                "end": m.get("end_ts"),
                # meta, so a spoken read whose video fell off the
                # mention_videos page still becomes a full row
                "title": r.get("title"),
                "published": str(r.get("publication_date") or "")[:10],
                "channel_id": chan.get("id") or r.get("channel.id"),
                "channel_name": chan.get("name") or r.get("channel.name"),
            }
    return out


def channel_names(channel_ids: list[int]) -> dict[int, str]:
    ids = sorted({int(c) for c in channel_ids if c})
    if not ids:
        return {}
    rows = tl_data.db_pg("SELECT id, channel_name FROM thoughtleaders_channel "
                        f"WHERE id IN ({','.join(str(i) for i in ids)})")
    return {int(r["id"]): r.get("channel_name") for r in rows if r.get("id")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", type=int, action="append", required=True,
                    help="brand id from `tl brands find`; repeat for a rebrand")
    ap.add_argument("--max", type=int, default=10,
                    help="reads returned, newest descriptive first (default 10)")
    a = ap.parse_args()

    videos = mention_videos(a.brand, max(a.max * 3, 30))
    snippets = mention_snippets(a.brand, max(a.max * 3, 30))

    chan_ids = []
    for v in videos:
        c = v.get("channel") if isinstance(v.get("channel"), dict) else {}
        cid = c.get("id") or v.get("channel.id")
        if cid:
            chan_ids.append(cid)
    names = channel_names(chan_ids)

    reads, seen = [], set()
    for v in videos:
        key = str(v.get("id") or "")
        seen.add(key)
        snip = snippets.get(key) or {}
        vid = key.split(":")[-1]
        start = snip.get("start")
        url = f"https://www.youtube.com/watch?v={vid}"
        if isinstance(start, (int, float)) and start > 0:
            url += f"&t={max(int(start) - PAD, 0)}s"
        chan = v.get("channel") if isinstance(v.get("channel"), dict) else {}
        cid = chan.get("id") or v.get("channel.id")
        reads.append({
            "id": key,
            "video_id": vid,
            "title": v.get("title"),
            "published": str(v.get("publication_date") or "")[:10],
            "channel_id": cid,
            "channel_name": (chan.get("name") or v.get("channel.name")
                             or (names.get(int(cid)) if cid else None)),
            "read_words": snip.get("snippet") or None,
            "entity_as_heard": snip.get("entity_as_heard"),
            "start": start,
            "url": url,
        })

    # Spoken reads whose video fell off the mention_videos page still count.
    for key, snip in snippets.items():
        if key in seen:
            continue
        seen.add(key)
        vid = key.split(":")[-1]
        start = snip.get("start")
        url = f"https://www.youtube.com/watch?v={vid}"
        if isinstance(start, (int, float)) and start > 0:
            url += f"&t={max(int(start) - PAD, 0)}s"
        reads.append({
            "id": key,
            "video_id": vid,
            "title": snip.get("title"),
            "published": snip.get("published"),
            "channel_id": snip.get("channel_id"),
            "channel_name": snip.get("channel_name"),
            "read_words": snip.get("snippet") or None,
            "entity_as_heard": snip.get("entity_as_heard"),
            "start": start,
            "url": url,
        })

    # A read whose words we actually have outranks a bare row; newest first
    # within each group.
    reads.sort(key=lambda r: r["published"] or "", reverse=True)
    reads.sort(key=lambda r: 0 if r["read_words"] else 1)
    kept = reads[:a.max]
    with_words = sum(1 for r in kept if r["read_words"])

    print(json.dumps({
        "brand_ids": a.brand,
        "mention_videos_found": len(videos),
        "reads_returned": len(kept),
        "reads_with_spoken_words": with_words,
        "usage_note": ("read the words to learn what the product is, newest "
                       "first — an old read can describe a product that no "
                       "longer exists. A read with no words describes nothing. "
                       "If no read has words at all, use the brand's website "
                       "or their own brief instead."),
        "excluded_by_design": ["price", "cost", "rate cards",
                               "performance grades"],
        "reads": kept,
    }, indent=1, default=str))


if __name__ == "__main__":
    main()
