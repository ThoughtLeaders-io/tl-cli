#!/usr/bin/env python3
"""Shared Elasticsearch utilities for TL skills.

Used by: keyword-research/validate_keywords.py

Env: ES_HOST (or ELASTIC_SEARCH_URL), ES_USERNAME, ES_PASSWORD
"""
import base64
import json
import os
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

ES_HOST = os.environ.get("ES_HOST", "") or os.environ.get("ELASTIC_SEARCH_URL", "")
ES_USER = os.environ.get("ES_USERNAME", "") or os.environ.get("ELASTIC_SEARCH_USERNAME", "")
ES_PASS = os.environ.get("ES_PASSWORD", "") or os.environ.get("ELASTIC_SEARCH_PASSWORD", "")

CHANNEL_FORMAT_VIDEO = 4
MAX_WORKERS = 5


def get_es_indexes():
    """Build comma-separated quarterly ES indexes for the last 365 days."""
    indexes = []
    today = date.today()
    for months_ago in range(0, 13):
        y = today.year
        m = today.month - months_ago
        while m <= 0:
            m += 12
            y -= 1
        q = (m - 1) // 3 + 1
        idx = f"tl-platform-{y}-q{q}"
        if idx not in indexes:
            indexes.append(idx)
    return ",".join(indexes)


def es_search(index, body):
    """Execute an ES search request. Returns parsed JSON response."""
    if not ES_HOST:
        print("ERROR: ES_HOST not set", file=sys.stderr)
        sys.exit(1)

    url = f"{ES_HOST}/{index}/_search"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    creds = base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read())


def validate_keyword(keyword, es_index, sample_size=5):
    """Validate a single keyword against ES. Returns doc count + sample titles/channels."""
    body = {
        "size": sample_size,
        "track_total_hits": True,
        "_source": ["title", "channel.channel_name", "channel.id"],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"doc_type": "article"}},
                    {"term": {"channel.format": CHANNEL_FORMAT_VIDEO}},
                ],
                "should": [
                    {"match_phrase": {"title": keyword}},
                    {"match_phrase": {"summary": keyword}},
                ],
                "minimum_should_match": 1,
            }
        },
    }

    try:
        response = es_search(es_index, body)
    except Exception as exc:
        return {"keyword": keyword, "doc_count": -1, "samples": [], "error": str(exc)}

    total_raw = response.get("hits", {}).get("total", 0)
    doc_count = total_raw.get("value", 0) if isinstance(total_raw, dict) else int(total_raw)

    samples = []
    for hit in response.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        channel = src.get("channel", {})
        samples.append({
            "title": src.get("title", ""),
            "channel_name": channel.get("channel_name", ""),
        })

    return {"keyword": keyword, "doc_count": doc_count, "samples": samples}


def validate_keywords(keywords, sample_size=5):
    """Validate a list of keywords concurrently. Returns list of validation results."""
    es_index = get_es_indexes()
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(validate_keyword, kw, es_index, sample_size): kw for kw in keywords}
        for future in as_completed(futures):
            results.append(future.result())
    return results
