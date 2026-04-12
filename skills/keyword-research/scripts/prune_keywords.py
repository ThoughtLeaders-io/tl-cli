#!/usr/bin/env python3
"""Stage 4: Prune keywords and format as filterset-ready keyword_groups.

LLM reviews ES validation results and removes keywords that are too broad,
redundant, or off-niche. Assigns content_fields per keyword.

Usage:
    python3 prune_keywords.py \
      --niche "Mechanical keyboard enthusiast content..." \
      --validated-json '[{"keyword": "Cherry MX Red", "doc_count": 450, "samples": [...]}]' \
      --report-type 3

Output: JSON to stdout with keyword_groups and keyword_operator.
Env: OPENROUTER_API_KEY
"""
import argparse
import json
import os
import ssl
import sys
import urllib.request

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DEFAULT_MODEL = (
    os.environ.get("KEYWORD_RESEARCH_LLM_MODEL")
    or os.environ.get("OPENROUTER_SMALL_MODEL")
    or "anthropic/claude-3.5-haiku"
)

SYSTEM_PROMPT = """\
You are a search quality analyst for a YouTube influencer marketing platform.

You will receive a niche definition and a list of candidate keywords, each with ES validation data:
- doc_count: how many YouTube videos matched
- sample_titles: actual video titles that matched
- sample_channels: channel names that matched

Your job:
1. PRUNE keywords that are bad for this niche
2. ASSIGN the best content_fields for each surviving keyword
3. Return a filterset-ready keyword_groups list

Respond with JSON:
{{"keyword_groups": [{{"text": "keyword", "content_fields": ["title", "summary"], "exclude": false}}], "keyword_operator": "OR"}}

Pruning rules — remove if ANY apply:

Too General: High doc_count AND sample titles show unrelated niches. High doc_count alone is \
NOT a reason to remove if samples are on-niche.

Redundant: Samples very similar to another keyword's. Doesn't add different content. Keep the \
one with better results.

Off-Niche: Sample titles show content NOT in the defined niche.

Zero Results: doc_count is 0 or < 5. Exception: keep very specific brand/product names.

Entity-Type Suffix: Keywords ending with a word that describes the creator/channel/platform \
rather than the content (e.g. "creators", "channels", "YouTubers", "influencers", "streamers"). \
These words don't appear in video titles. Strip the suffix and keep the searchable core term \
(e.g. "programming tutorial creators" → "programming tutorial").

content_fields assignment for report_type {report_type}:
{content_fields_guidance}

NEVER include "transcript" unless extremely specific technical term.

keyword_operator: Use "OR" (default). Use "AND" only for intersection of distinct concepts.

Aim to keep 30-60 keywords. Quality over quantity. Preserve exact keyword text.
"""


def get_content_fields_guidance(report_type):
    if report_type == 3:
        return (
            'Channels reports (type 3):\n'
            '- Brand/product names, proper nouns: ["title", "summary"]\n'
            '- Niche descriptors, sub-topics: ["title", "summary", "channel_description", "channel_topic_description"]\n'
            '- Very specific jargon: ["title", "summary"]'
        )
    return '- Default to ["title", "summary"] for all keywords'


def call_openrouter(system_prompt, user_prompt, model):
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "https://app.thoughtleaders.io",
            "X-Title": "TL Keyword Research",
        },
    )

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        response = json.loads(resp.read())

    content = response["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    # Try to extract just the JSON object if LLM returned extra text
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Find the first { and last } to extract the JSON object
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end != -1:
            return json.loads(content[start:end + 1])
        print(f"ERROR: Failed to parse LLM response as JSON", file=sys.stderr)
        print(f"Raw response: {content[:500]}", file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description="Stage 4: Prune keywords and format as keyword_groups")
    parser.add_argument("--niche", required=True, help="Niche definition from Stage 1")
    parser.add_argument("--validated-json", required=True, help="JSON array of validated keywords from Stage 3")
    parser.add_argument("--report-type", type=int, default=3, choices=[1, 2, 3, 8], help="Report type (default: 3)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    validated = json.loads(args.validated_json)

    # Filter out failed validations
    valid_results = [v for v in validated if v.get("doc_count", -1) >= 0]
    if not valid_results:
        print(json.dumps({"keyword_groups": [], "keyword_operator": "OR", "error": "No valid ES results"}))
        return

    # Build summaries for LLM
    kw_summaries = []
    for v in valid_results:
        kw_summaries.append({
            "keyword": v["keyword"],
            "doc_count": v["doc_count"],
            "sample_titles": [s["title"] for s in v.get("samples", [])][:5],
            "sample_channels": list({s["channel_name"] for s in v.get("samples", []) if s.get("channel_name")})[:5],
        })

    content_fields_guidance = get_content_fields_guidance(args.report_type)
    system = SYSTEM_PROMPT.format(report_type=args.report_type, content_fields_guidance=content_fields_guidance)

    user_prompt = json.dumps({
        "niche": args.niche,
        "report_type": args.report_type,
        "candidates": kw_summaries,
    }, indent=2)

    result = call_openrouter(system, user_prompt, args.model)

    # Ensure all keyword_groups have required fields
    for group in result.get("keyword_groups", []):
        if "content_fields" not in group:
            group["content_fields"] = ["title", "summary"]
        if "exclude" not in group:
            group["exclude"] = False

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
