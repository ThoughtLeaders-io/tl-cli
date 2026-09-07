#!/usr/bin/env python3
"""Resolve any channel reference into a channel record + recent videos.

Accepts: YouTube handle (@x), full URL, channel name, numeric channel.id,
or ``adlink:<id>`` (resolves channel + the specific sponsored video).

Returns a dict:
    {
      "channel": {...thoughtleaders_channel row...},
      "longform": [ {id, video_id, title, publication_date, views, likes,
                     comments, duration} ... ]  # newest first
      "shorts":   [ ... same shape ... ],
      "focus_video": {video_id, ...} | None,   # set when adlink/video given
      "focus_adlink": {...} | None,
    }
"""
from __future__ import annotations

import json
import sys

import _io_utf8  # noqa: F401  (side effect: forces UTF-8 stdout/stderr on Windows)

import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[2] / "_shared"))
import tl_data

import channel_lookup

VIDEO_FETCH = 30  # how many recent of each content_type to pull


def _es_recent(channel_id: int, content_type: str, size: int) -> list[dict]:
    body = {
        "size": size,
        "_source": [
            "id",
            "title",
            "publication_date",
            "views",
            "likes",
            "comments",
            "duration",
            "content_type",
            "url",
        ],
        "query": {
            "bool": {
                "must": [
                    {"term": {"doc_type": "article"}},
                    {"term": {"channel.id": channel_id}},
                    {"term": {"content_type": content_type}},
                ]
            }
        },
        "sort": [{"publication_date": {"order": "desc"}}],
    }
    out = []
    for s in tl_data.db_es(body):
        vid = str(s.get("id", ""))
        out.append(
            {
                "id": vid,
                "video_id": vid.split(":", 1)[1] if ":" in vid else vid,
                "title": s.get("title", ""),
                "publication_date": s.get("publication_date"),
                "views": s.get("views") or 0,
                "likes": s.get("likes") or 0,
                "comments": s.get("comments") or 0,
                "duration": s.get("duration") or 0,
                "content_type": s.get("content_type", content_type),
                "url": s.get("url"),
            }
        )
    return out


def _resolve_adlink(adlink_id: int) -> tuple[dict, dict]:
    rows = tl_data.db_pg(
        "SELECT a.id, a.publish_status, a.publish_date, a.scheduled_date, "
        "a.price, a.cost, a.article_id, a.url, s.channel_id "
        "FROM thoughtleaders_adlink a "
        "JOIN thoughtleaders_adspot s ON a.ad_spot_id = s.id "
        f"WHERE a.id = {int(adlink_id)} LIMIT 1"
    )
    if not rows:
        raise tl_data.DataError(f"adlink {adlink_id} not found")
    adlink = rows[0]
    article_id = adlink.get("article_id") or ""
    video_id = article_id.split(":", 1)[1] if ":" in article_id else article_id
    adlink["video_id"] = video_id or None
    return adlink, {"id": adlink["channel_id"]}


def resolve(ref: str) -> dict:
    ref = str(ref).strip()
    focus_adlink = None
    focus_video_id = None

    if ref.lower().startswith("adlink:"):
        adlink_id = int(ref.split(":", 1)[1])
        focus_adlink, ch_hint = _resolve_adlink(adlink_id)
        focus_video_id = focus_adlink.get("video_id")
        channel = channel_lookup.channels_show(ch_hint["id"])
    else:
        channel = channel_lookup.channels_show(ref)

    cid = int(channel["id"])
    longform = _es_recent(cid, "longform", VIDEO_FETCH)
    shorts = _es_recent(cid, "short", VIDEO_FETCH)

    focus_video = None
    if focus_video_id:
        focus_video = next(
            (v for v in longform + shorts if v["video_id"] == focus_video_id),
            {"video_id": focus_video_id, "id": f"{cid}:{focus_video_id}"},
        )

    return {
        "channel": channel,
        "longform": longform,
        "shorts": shorts,
        "focus_video": focus_video,
        "focus_adlink": focus_adlink,
    }


if __name__ == "__main__":
    data = resolve(sys.argv[1])
    ch = data["channel"]
    print(
        f"{ch['channel_name']} (id={ch['id']}, subs={ch.get('subscribers')}, "
        f"cat={ch.get('content_category')}, lang={ch.get('language')})"
    )
    print(f"longform={len(data['longform'])} shorts={len(data['shorts'])}")
    if data["focus_video"]:
        print("focus_video=", json.dumps(data["focus_video"], default=str))
