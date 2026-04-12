#!/usr/bin/env python3
"""Stage 3: Validate candidate keywords against Elasticsearch.

Queries ES for each keyword individually, returns doc count + sample titles/channels.

Usage:
    python3 validate_keywords.py --keywords-json '["Cherry MX Red", "hot-swap PCB"]'
    python3 validate_keywords.py --keywords-json '["gaming", "esports"]' --sample-size 3

Output: JSON array to stdout with validation results per keyword.
Env: ES_HOST, ES_USERNAME, ES_PASSWORD
"""
import argparse
import json
import os
import sys

# ES utilities live in tl-data/scripts/es_utils.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tl-data", "scripts"))
from es_utils import validate_keywords


def main():
    parser = argparse.ArgumentParser(description="Stage 3: Validate keywords against ES")
    parser.add_argument("--keywords-json", required=True, help="JSON array of keyword strings")
    parser.add_argument("--sample-size", type=int, default=5, help="Samples per keyword (default: 5)")
    args = parser.parse_args()

    keywords = json.loads(args.keywords_json)
    if not keywords:
        print(json.dumps([]))
        return

    results = validate_keywords(keywords, sample_size=args.sample_size)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
