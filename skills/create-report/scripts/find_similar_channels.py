#!/usr/bin/env python3
"""Find channels similar to a given channel using vector similarity.

Uses Elasticsearch kNN search on topic description embeddings to find
channels with similar content/topics.

Usage:
    python3 find_similar_channels.py --channel-names '["Canterbury Cottage"]'
    python3 find_similar_channels.py --channel-names '["MrBeast", "MKBHD"]' --max-results 30 --min-score 65

Output: JSON to stdout:
    {
      "similar_channels": [
        {"channel_id": 123, "channel_name": "...", "score": 85},
        ...
      ],
      "source_channels": [
        {"channel_id": 456, "channel_name": "Canterbury Cottage", "resolved": true}
      ],
      "total_found": 42
    }

Env:
    TL_DATABASE_URI or DATABASE_URL  (PostgreSQL)
    ES_HOST or ELASTIC_SEARCH_URL    (Elasticsearch)
    ES_USERNAME / ES_PASSWORD
"""

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.request

# ── Database connection (shared utility) ─────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tl-data", "scripts"))
from db import get_connection

# ── Elasticsearch config ─────────────────────────────────────────────────────
ES_HOST = os.environ.get("ES_HOST", "") or os.environ.get("ELASTIC_SEARCH_URL", "")
ES_USER = os.environ.get("ES_USERNAME", "") or os.environ.get("ELASTIC_SEARCH_USERNAME", "")
ES_PASS = os.environ.get("ES_PASSWORD", "") or os.environ.get("ELASTIC_SEARCH_PASSWORD", "")

VECTOR_INDEX = "tl-vectors-channel-topic-descriptions"
VECTOR_DIMENSIONS = 1024
MIN_SIMILARITY = 0.5  # Absolute ES kNN similarity floor

# Channels active within last 90 days, VIDEO format, not music, not excluded countries
EXCLUDED_COUNTRIES = ("IN", "PK", "BD", "PH", "ID")


def _es_request(path: str, body: dict, timeout: int = 30) -> dict:
    """Execute an ES HTTP request and return parsed JSON."""
    if not ES_HOST:
        print("ERROR: ES_HOST not set", file=sys.stderr)
        sys.exit(1)

    url = f"{ES_HOST}/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    creds = base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return json.loads(resp.read())


# ── Step 1: Resolve channel names → IDs ─────────────────────────────────────

def resolve_channel_names(channel_names: list[str]) -> list[dict]:
    """Resolve channel names to IDs via PostgreSQL.

    Returns list of {"channel_id": int, "channel_name": str, "resolved": bool}.
    """
    results = []
    conn = get_connection(readonly=True)
    try:
        cur = conn.cursor()
        for name in channel_names:
            cur.execute(
                """
                SELECT id, channel_name
                FROM thoughtleaders_channel
                WHERE LOWER(channel_name) = LOWER(%s)
                  AND is_active = true
                ORDER BY reach DESC NULLS LAST
                LIMIT 1
                """,
                (name.strip(),),
            )
            row = cur.fetchone()
            if row:
                results.append({"channel_id": row[0], "channel_name": row[1], "resolved": True})
            else:
                results.append({"channel_id": None, "channel_name": name, "resolved": False})
                print(f"WARNING: Channel '{name}' not found in database", file=sys.stderr)
    finally:
        conn.close()
    return results


# ── Step 2: Get source channel's embedding vector from ES ────────────────────

def get_channel_vector(channel_id: int) -> list[float] | None:
    """Fetch the embedding vector for a channel from the ES vector index."""
    body = {
        "size": 1,
        "query": {"term": {"_id": str(channel_id)}},
        "_source": ["vector"],
    }
    try:
        resp = _es_request(f"{VECTOR_INDEX}/_search", body)
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            print(f"WARNING: No embedding vector found for channel {channel_id}", file=sys.stderr)
            return None
        return hits[0]["_source"]["vector"]
    except Exception as exc:
        print(f"ERROR: Failed to fetch vector for channel {channel_id}: {exc}", file=sys.stderr)
        return None


# ── Step 3: Run kNN search ───────────────────────────────────────────────────

def find_similar_by_vector(
    vector: list[float],
    source_channel_id: int,
    max_results: int = 50,
) -> list[dict]:
    """Run ES kNN search to find channels with similar topic embeddings.

    Returns list of {"channel_id": int, "score": float} sorted by score desc.
    """
    body = {
        "size": max_results + 1,
        "knn": {
            "field": "vector",
            "query_vector": vector,
            "k": max_results + 1,
            "num_candidates": min(max_results * 10, 10000),
            "similarity": MIN_SIMILARITY,
        },
        "_source": ["id"],
    }
    try:
        resp = _es_request(f"{VECTOR_INDEX}/_search", body, timeout=60)
    except Exception as exc:
        print(f"ERROR: kNN search failed: {exc}", file=sys.stderr)
        return []

    hits = resp.get("hits", {}).get("hits", [])
    results = []
    for hit in hits:
        cid = int(hit["_id"])
        if cid == source_channel_id:
            continue
        results.append({"channel_id": cid, "score": hit.get("_score", 0.0)})

    return results


# ── Step 4: Enrich results with channel data & filter ────────────────────────

def enrich_and_filter(
    similar: list[dict],
    min_score_pct: float,
    max_results: int,
    source_language: str | None = None,
) -> list[dict]:
    """Filter similar channels by activity, format, and score threshold.

    Fetches channel metadata from PostgreSQL and applies filters.
    Returns list of {"channel_id", "channel_name", "score"} with normalized 0-100 scores.
    """
    if not similar:
        return []

    channel_ids = [s["channel_id"] for s in similar]
    score_map = {s["channel_id"]: s["score"] for s in similar}

    conn = get_connection(readonly=True)
    try:
        cur = conn.cursor()
        # Fetch active channels with basic metadata
        placeholders = ",".join(["%s"] * len(channel_ids))
        query = f"""
            SELECT id, channel_name, language, format, content_category, country,
                   last_published, is_tl_channel, media_selling_network_join_date,
                   impression
            FROM thoughtleaders_channel
            WHERE id IN ({placeholders})
              AND is_active = true
              AND format = 4  -- VIDEO format
              AND last_published >= NOW() - INTERVAL '90 days'
        """
        params = channel_ids[:]

        # Language filter: match source channel's language if available
        if source_language:
            query += " AND language = %s"
            params.append(source_language)

        cur.execute(query, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    # Apply score normalization and filtering
    scored = []
    for row in rows:
        cid, name, lang, fmt, category, country, last_pub, is_tpp, msn_date, impression = row

        # Exclude music category (27) and certain countries
        if category == 27:
            continue
        if country in EXCLUDED_COUNTRIES:
            continue

        raw_score = score_map.get(cid, 0.0)

        # Impression penalty (channels with low PVs are less valuable)
        if impression is None or impression < 50000:
            raw_score *= 0.92
        elif impression < 100000:
            raw_score *= 0.95

        # TPP/MSN boost
        if is_tpp:
            raw_score *= 1.05
        if msn_date is not None:
            raw_score *= 1.05

        scored.append({"channel_id": cid, "channel_name": name, "raw_score": raw_score})

    if not scored:
        return []

    # Normalize to 0-100 percentage based on max score
    max_score = max(s["raw_score"] for s in scored)
    if max_score <= 0:
        return []

    result = []
    for s in scored:
        pct = int((s["raw_score"] / max_score) * 100)
        if pct > 100:
            pct = 100
        if pct < min_score_pct:
            continue
        result.append({
            "channel_id": s["channel_id"],
            "channel_name": s["channel_name"],
            "score": pct,
        })

    result.sort(key=lambda r: r["score"], reverse=True)
    return result[:max_results]


# ── Step 5: Get source channel's language for filtering ──────────────────────

def get_channel_language(channel_id: int) -> str | None:
    """Fetch the source channel's language from PostgreSQL."""
    conn = get_connection(readonly=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT language FROM thoughtleaders_channel WHERE id = %s", (channel_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Find channels similar to given channels via vector similarity")
    parser.add_argument("--channel-names", required=True, help="JSON array of channel name strings")
    parser.add_argument("--max-results", type=int, default=50, help="Max similar channels to return (default: 50)")
    parser.add_argument("--min-score", type=float, default=70.0, help="Min similarity score 0-100 (default: 70)")
    args = parser.parse_args()

    try:
        channel_names = json.loads(args.channel_names)
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON for --channel-names"}))
        sys.exit(1)

    if not channel_names:
        print(json.dumps({"similar_channels": [], "source_channels": [], "total_found": 0}))
        return

    # Resolve channel names to IDs
    source_channels = resolve_channel_names(channel_names)
    resolved = [s for s in source_channels if s["resolved"]]

    if not resolved:
        print(json.dumps({
            "similar_channels": [],
            "source_channels": source_channels,
            "total_found": 0,
            "warning": f"Could not resolve any channel names: {channel_names}",
        }))
        return

    # Collect similar channels from all source channels
    all_similar: dict[int, dict] = {}  # channel_id → best result

    for source in resolved:
        channel_id = source["channel_id"]
        print(f"Finding channels similar to {source['channel_name']} (ID: {channel_id})...", file=sys.stderr)

        vector = get_channel_vector(channel_id)
        if vector is None:
            continue

        language = get_channel_language(channel_id)
        raw_similar = find_similar_by_vector(vector, channel_id, max_results=args.max_results * 2)
        enriched = enrich_and_filter(raw_similar, args.min_score, args.max_results, source_language=language)

        for ch in enriched:
            cid = ch["channel_id"]
            if cid not in all_similar or ch["score"] > all_similar[cid]["score"]:
                all_similar[cid] = ch

    # Remove source channels from results
    source_ids = {s["channel_id"] for s in resolved}
    final = [ch for ch in all_similar.values() if ch["channel_id"] not in source_ids]
    final.sort(key=lambda r: r["score"], reverse=True)
    final = final[: args.max_results]

    print(json.dumps({
        "similar_channels": final,
        "source_channels": source_channels,
        "total_found": len(final),
    }))


if __name__ == "__main__":
    main()
