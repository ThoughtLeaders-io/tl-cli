#!/usr/bin/env python3
"""Build a niche-matched peer baseline for engagement ratios.

Strategy: ``tl channels similar`` for niche peers; fall back to a Postgres
cohort query (same content_category + language, active, reach ±50%,
recently published). Aggregate each peer's recent longform like:view and
comment:view rates into a baseline. Cached to references/peer-cohort-cache.json
keyed by (content_category, language, reach_bucket) so we don't re-spend
credits on every run.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import _io_utf8  # noqa: F401  (side effect: forces UTF-8 stdout/stderr on Windows)

import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[2] / "_shared"))
import tl_data

import channel_lookup

CACHE = Path(__file__).resolve().parent.parent / "references" / "peer-cohort-cache.json"
CACHE_TTL_DAYS = 30
MAX_PEERS = 12


def _reach_bucket(reach: int) -> str:
    for hi, label in [
        (10_000, "<10k"),
        (50_000, "10-50k"),
        (150_000, "50-150k"),
        (500_000, "150-500k"),
        (1_000_000, "500k-1m"),
        (5_000_000, "1-5m"),
    ]:
        if reach < hi:
            return label
    return "5m+"


def _cache_key(channel: dict) -> str:
    return (
        f"{channel.get('content_category')}|{channel.get('language')}|"
        f"{_reach_bucket(channel.get('subscribers') or 0)}"
    )


def _load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")


def _peer_ids_via_cli(channel_id: int) -> list[int]:
    try:
        rows = channel_lookup.channels_similar(channel_id, limit=MAX_PEERS * 2)
    except (tl_data.DataError, tl_data.CliUnavailable):
        return []
    ids = []
    for r in rows:
        cid = r.get("id") or r.get("channel_id")
        if cid:
            ids.append(int(cid))
    return ids


def _peer_ids_via_pg(channel: dict) -> list[int]:
    reach = channel.get("subscribers") or 0
    lo, hi = int(reach * 0.5), int(reach * 1.5) or 10_000
    rows = tl_data.db_pg(
        "SELECT id FROM thoughtleaders_channel "
        f"WHERE content_category = {int(channel['content_category'])} "
        f"AND language = '{channel.get('language', 'en')}' "
        "AND is_active = true "
        f"AND subscribers BETWEEN {lo} AND {hi} "
        "AND last_published > (CURRENT_DATE - INTERVAL '60 days') "
        f"AND id != {int(channel['id'])} "
        "ORDER BY subscribers DESC LIMIT 25"
    )
    return [int(r["id"]) for r in rows]


def _peer_longform_rates(channel_id: int) -> dict | None:
    body = {
        "size": 10,
        "_source": ["views", "likes", "comments"],
        "query": {
            "bool": {
                "must": [
                    {"term": {"doc_type": "article"}},
                    {"term": {"channel.id": channel_id}},
                    {"term": {"content_type": "longform"}},
                ]
            }
        },
        "sort": [{"publication_date": {"order": "desc"}}],
    }
    hits = tl_data.db_es(body)
    v = sum(h.get("views") or 0 for h in hits)
    if v < 5000 or len(hits) < 3:
        return None
    likes = sum(h.get("likes") or 0 for h in hits)
    cmts = sum(h.get("comments") or 0 for h in hits)
    return {"like_rate": likes / v, "comment_rate": cmts / v, "videos": len(hits)}


def get_baseline(channel: dict, *, refresh: bool = False) -> dict:
    """Return {like_rate_median, comment_rate_median, like_rate_p25,
    comment_rate_p25, n_peers, source, cached}."""
    key = _cache_key(channel)
    cache = _load_cache()
    entry = cache.get(key)
    if entry and not refresh:
        age_days = (time.time() - entry.get("_ts", 0)) / 86400
        if age_days < CACHE_TTL_DAYS:
            entry = dict(entry)
            entry["cached"] = True
            return entry

    cid = int(channel["id"])
    peer_ids = _peer_ids_via_cli(cid)
    source = "cli-similar"
    if not peer_ids:
        peer_ids = _peer_ids_via_pg(channel)
        source = "pg-cohort"

    like_rates, comment_rates = [], []
    used = 0
    for pid in peer_ids:
        if used >= MAX_PEERS:
            break
        r = _peer_longform_rates(pid)
        if r:
            like_rates.append(r["like_rate"])
            comment_rates.append(r["comment_rate"])
            used += 1

    if not like_rates:
        # last-resort generic floor for English educational/tech content
        result = {
            "like_rate_median": 0.02,
            "comment_rate_median": 0.0025,
            "like_rate_p25": 0.008,
            "comment_rate_p25": 0.001,
            "n_peers": 0,
            "source": "fallback-generic",
            "cached": False,
        }
    else:
        result = {
            "like_rate_median": statistics.median(like_rates),
            "comment_rate_median": statistics.median(comment_rates),
            "like_rate_p25": _pct(like_rates, 25),
            "comment_rate_p25": _pct(comment_rates, 25),
            "n_peers": used,
            "source": source,
            "cached": False,
        }

    entry = dict(result)
    entry["_ts"] = time.time()
    cache[key] = entry
    _save_cache(cache)
    return result


def _pct(values: list[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * (p / 100)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


if __name__ == "__main__":
    import sys

    import resolve_channel

    ch = resolve_channel.resolve(sys.argv[1])["channel"]
    print(json.dumps(get_baseline(ch, refresh="--refresh" in sys.argv), indent=2, default=str))
