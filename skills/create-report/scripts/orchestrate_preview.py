#!/usr/bin/env python3
"""Full AI Report Builder orchestrator for the web UI.

Replaces execute_nl_search_lrr in nl_search_service.py.
Runs the complete pipeline: keyword research → main LLM config build → JSON output.

Django calls this subprocess, passes team_directory (from DB) and current_date.
The orchestrator owns the SYSTEM_PROMPT (system_prompt.txt) and keyword pipeline.

Usage:
    python3 orchestrate_preview.py \
        --prompt "gaming channels 100k+ English" \
        [--conversation '[{"role":"user","content":"..."}]'] \
        [--team-directory "## ThoughtLeaders Team Directory\\n..."] \
        [--campaign-config '{"report_title":"...", "filterset":{...}}']

Output (stdout): Raw config JSON matching execute_nl_search_lrr return shape.
Progress (stderr): Plain text lines for Django to forward to lrr.log_step.

Environment variables:
    OPENROUTER_API_KEY              (required)
    NL_SEARCH_LLM_MODEL            (optional, main config-building model)
    OPENROUTER_MAIN_MODEL          (optional, fallback)
    KEYWORD_RESEARCH_LLM_MODEL /
      OPENROUTER_SMALL_MODEL       (optional, keyword research model)
    ES_HOST / ELASTIC_SEARCH_URL   (required for keyword validation)
    ES_USERNAME / ELASTIC_SEARCH_USERNAME
    ES_PASSWORD / ELASTIC_SEARCH_PASSWORD
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from string import Template

# ── Model config ──────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MAIN_MODEL = (
    os.environ.get("NL_SEARCH_LLM_MODEL")
    or os.environ.get("OPENROUTER_MAIN_MODEL")
    or "anthropic/claude-opus-4-5"
)
KEYWORD_MODEL = (
    os.environ.get("KEYWORD_RESEARCH_LLM_MODEL")
    or os.environ.get("OPENROUTER_SMALL_MODEL")
    or "anthropic/claude-3.5-haiku"
)

SCRIPTS_DIR = Path(__file__).parent
PROMPTS_DIR = SCRIPTS_DIR.parent / "prompts"
DATA_DIR = SCRIPTS_DIR.parent / "data"
KW_SCRIPTS_DIR = SCRIPTS_DIR.parent.parent / "keyword-research" / "scripts"
SIMILAR_CHANNELS_SCRIPT = SCRIPTS_DIR / "find_similar_channels.py"
SIMILAR_CHANNELS_TIMEOUT = 60

# ── Time budget ──────────────────────────────────────────────────────────────
REVIEW_LOOP_TIME_BUDGET = 200  # Skip critic/judge if elapsed time exceeds this
KEYWORD_SUBPROCESS_TIMEOUT = 120  # Timeout for keyword validation/pruning subprocesses

# ── Keyword research system prompt (inline — matches nl_search_service.py) ────
KEYWORD_SYSTEM_PROMPT = """\
You are a keyword research assistant for an influencer marketing platform focused on YouTube.
Given a user's search query, your job is to suggest keywords that would help find relevant
YouTube channels and content.

You must respond with a JSON object in one of two shapes:

1. If the topic is clear enough to research:
{
  "action": "suggestions",
  "keywords": [
    {
      "text": "keyword or phrase",
      "category": "brand|subtopic|jargon|proper_noun|related_term",
      "reason": "Brief explanation of why this keyword is relevant"
    }
  ]
}

2. If the topic is genuinely too ambiguous to produce useful keywords:
{
  "action": "follow_up",
  "question": "Your clarifying question",
  "suggestions": [
    {"title": "Option A", "description": "What this option means"},
    {"title": "Option B", "description": "What this option means"}
  ]
}

Guidelines:
- Generate 10-20 suggested keywords. Prioritize specificity over breadth.
- Include: brand names, product names, proper nouns, jargon, sub-topics, content format terms.
- Do NOT include generic/obvious terms the user already mentioned.
- Each keyword should be 1-4 words. Focus on YouTube video titles, descriptions, channel descriptions.
- BIAS STRONGLY TOWARD ACTION: if you can produce even 5 reasonable keywords, do so. \
Only return "follow_up" when the query is so vague you literally cannot guess the niche \
(e.g. "find me some channels", "show me stuff"). Ambiguity about filter values \
(e.g. "high engagement", "popular", "big") is NOT a reason for follow_up — those are \
filter decisions, not keyword decisions. Produce keywords for the topic regardless.
- NEVER return "follow_up" when conversation history exists.
- If the user mentions a specific YouTube channel name, do NOT treat it as ambiguous.
"""

KEYWORD_REPORT_TYPES = {1, 2, 3}


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _do_openrouter_request(payload: dict, timeout: int = 120) -> str:
    """Send a single OpenRouter request, return raw content string."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=timeout) as resp:
        response = json.loads(resp.read())
    return response["choices"][0]["message"]["content"].strip()


def call_openrouter(messages: list[dict], model: str, json_mode: bool = True, max_tokens: int = 4096) -> str:
    """Call OpenRouter, return raw response content string.

    If the model doesn't support response_format (HTTP 400), retries without it.
    """
    if not OPENROUTER_API_KEY:
        raise EnvironmentError("OPENROUTER_API_KEY is not set")

    payload: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        try:
            return _do_openrouter_request(payload)
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            # Model doesn't support response_format — retry without it
            del payload["response_format"]

    return _do_openrouter_request(payload)


def parse_json_response(content: str) -> dict:
    """Parse JSON from LLM response, handling markdown code block wrapping."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Some models wrap JSON in ```json ... ```
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            return json.loads(content[start : end + 1])
        raise


# ── Conversation context builder ──────────────────────────────────────────────

def build_conversation_context(conversation: list[dict]) -> str:
    if not conversation:
        return ""
    parts = []
    for turn in conversation:
        if not isinstance(turn, dict):
            continue
        content = turn.get("content", "")
        if not content:
            continue
        role = turn.get("role", "")
        if role == "user":
            parts.append(f"Previous user message: {content}")
        elif role == "assistant":
            parts.append(f"Previous assistant response: {content}")
    return "\n".join(parts) + "\n" if parts else ""


# ── Sponsorship intent detection ─────────────────────────────────────────────

_SPONSORSHIP_KEYWORDS = {
    "pipeline", "deal", "deals", "adlink", "adlinks",
}

def _is_likely_sponsorship_query(prompt: str, conversation: list[dict] | None = None) -> bool:
    """Heuristic: returns True if the prompt (or any prior turn) is about sponsorship deals (report_type 8)."""
    all_text = prompt.lower()
    for turn in (conversation or []):
        all_text += " " + str(turn.get("content", "")).lower()
    words = set(all_text.split())
    return bool(words & _SPONSORSHIP_KEYWORDS)


_SIMILAR_CHANNEL_PATTERNS = (
    "similar to ", "channels like ", "creators like ", "youtubers like ",
    "channels similar", "creators similar", "youtubers similar",
    "similar channels", "similar creators", "similar youtubers",
)


def _is_likely_similar_channels_query(prompt: str, conversation: list[dict] | None = None) -> bool:
    """Heuristic: returns True if the prompt (or any prior turn) asks for similar channels."""
    all_text = prompt.lower()
    for turn in (conversation or []):
        all_text += " " + str(turn.get("content", "")).lower()
    return any(p in all_text for p in _SIMILAR_CHANNEL_PATTERNS)


# ── Stage 1: Keyword research ─────────────────────────────────────────────────

def research_keywords(
    prompt: str,
    conversation: list[dict],
    report_type: int,
) -> dict:
    """Run intent detection + ES validation + pruning.

    Returns:
        {
          "action": "suggestions" | "follow_up",
          "suggested_keywords": [...],
          "keyword_groups": [...],
          "keyword_operator": "OR",
          "follow_up_question": "...",
          "follow_up_suggestions": [...]
        }
    """
    if report_type not in KEYWORD_REPORT_TYPES:
        return {
            "action": "suggestions",
            "suggested_keywords": [],
            "keyword_groups": [],
            "keyword_operator": "OR",
        }

    has_history = bool(conversation)
    conversation_context = build_conversation_context(conversation)

    user_content = (
        f"{conversation_context}User's search query: {prompt}\n\n"
        "Suggest keywords that would help find relevant YouTube channels and content. "
        "Focus on SPECIFIC sub-topics, proper nouns, brand names, and niche terms."
    )
    if has_history:
        user_content += " Do not ask a follow-up question — the user has already provided context."

    emit_status("keywords", "Analyzing topic keywords...")

    content = call_openrouter(
        [
            {"role": "system", "content": KEYWORD_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        model=KEYWORD_MODEL,
    )
    kw_response = parse_json_response(content)

    if kw_response.get("action") == "follow_up":
        return {
            "action": "follow_up",
            "suggested_keywords": [],
            "keyword_groups": [],
            "keyword_operator": "OR",
            "follow_up_question": kw_response.get("question", "Could you be more specific?"),
            "follow_up_suggestions": kw_response.get("suggestions", []),
        }

    raw_keywords = kw_response.get("keywords", [])
    suggested_keywords = [
        {"text": k["text"], "category": k.get("category", ""), "reason": k.get("reason", "")}
        for k in raw_keywords
        if isinstance(k, dict) and k.get("text")
    ]
    keyword_texts = [k["text"] for k in suggested_keywords]

    if not keyword_texts:
        return {
            "action": "suggestions",
            "suggested_keywords": [],
            "keyword_groups": [],
            "keyword_operator": "OR",
        }

    # ES validation
    emit_status("keywords", f"Validating {len(keyword_texts)} keywords against Elasticsearch...")
    validated = _run_validate(keyword_texts)

    # Pruning
    emit_status("keywords", "Pruning and formatting keyword groups...")
    pruned = _run_prune(niche=prompt, validated=validated, report_type=report_type)

    return {
        "action": "suggestions",
        "suggested_keywords": suggested_keywords,
        "keyword_groups": pruned.get("keyword_groups", []),
        "keyword_operator": pruned.get("keyword_operator", "OR"),
    }


def _run_validate(keywords: list[str]) -> list[dict]:
    script = str(KW_SCRIPTS_DIR / "validate_keywords.py")
    result = subprocess.run(
        ["python3", script, "--keywords-json", json.dumps(keywords)],
        capture_output=True, text=True, env=os.environ.copy(),
        timeout=KEYWORD_SUBPROCESS_TIMEOUT,
    )
    if result.returncode != 0:
        emit_status("warning", f"ES validation failed: {result.stderr[:300]}")
        return []
    return json.loads(result.stdout)


def _run_prune(niche: str, validated: list[dict], report_type: int) -> dict:
    script = str(KW_SCRIPTS_DIR / "prune_keywords.py")
    result = subprocess.run(
        [
            "python3", script,
            "--niche", niche,
            "--validated-json", json.dumps(validated),
            "--report-type", str(report_type),
        ],
        capture_output=True, text=True, env=os.environ.copy(),
        timeout=KEYWORD_SUBPROCESS_TIMEOUT,
    )
    if result.returncode != 0:
        emit_status("warning", f"Pruning failed: {result.stderr[:300]}")
        return {"keyword_groups": [], "keyword_operator": "OR"}
    return json.loads(result.stdout)


# ── Status emitter ──────────────────────────────────────────────────────────

def emit_status(stage: str, message: str) -> None:
    """Emit a structured status line on stderr for Django to parse."""
    print(json.dumps({"stage": stage, "message": message}), file=sys.stderr)


# ── Report type heuristic ───────────────────────────────────────────────────

def _detect_report_type_hint(
    prompt: str,
    conversation: list[dict] | None,
    campaign_config: dict | None,
) -> int:
    """Heuristic report type detection for pre-config stages."""
    if campaign_config and isinstance(campaign_config.get("report_type"), int):
        return campaign_config["report_type"]
    if _is_likely_sponsorship_query(prompt, conversation):
        return 8
    text = prompt.lower()
    if any(w in text for w in ("video", "upload", "content", "clip", "article")):
        return 1
    if any(w in text for w in ("brand", "advertiser", "sponsor")):
        return 2
    return 3  # default: channels


# ── Stage 1.5: Sort strategy ───────────────────────────────────────────────

SORT_STRATEGY_SYSTEM_PROMPT = """\
You are a sort strategy advisor for an influencer marketing platform focused on YouTube.
Given a user's search query and the available sortable columns for a report type,
determine the single most relevant sort parameter.

Rules:
- Only recommend columns from the provided sortable list.
- "both" means ascending or descending; "asc" means ascending only; "desc" means descending only.
- For "best performance" or "top" queries: prefer views or reach metrics, descending.
- For "newest", "recent", or "latest" queries: prefer publication_date or send_date, descending.
- For "cheapest" or "budget" queries: prefer price-related fields, ascending.
- For "best engagement" queries: prefer likes, comments, or engagement-related metrics, descending.
- For "most sponsors" or "most branded": prefer sponsored_brands_count or brands_count, descending.
- For "evergreen" queries: prefer evergreenness metrics, descending.
- For general channel/creator discovery without explicit sort intent: default to views-related metrics descending.
- For sponsorship reports without explicit sort intent: default to send_date descending.
- For brand reports without explicit sort intent: default to doc_count (mentions) descending.

Respond with JSON:
{
  "sort_field": "<backend_code of the recommended column>",
  "sort_direction": "asc" or "desc",
  "reasoning": "One sentence explaining why this sort is best for the query"
}
"""


def get_sortable_columns(report_type: int) -> list[dict]:
    """Load sortable columns for a report type from static JSON data."""
    sortable_file = DATA_DIR / "sortable_columns.json"
    if not sortable_file.exists():
        return []
    try:
        all_columns = json.loads(sortable_file.read_text())
    except json.JSONDecodeError as exc:
        emit_status("warning", f"sortable_columns.json is malformed: {exc}")
        return []
    return all_columns.get(str(report_type), [])


def determine_sort_strategy(
    prompt: str,
    conversation: list[dict],
    report_type: int,
    sortable_columns: list[dict],
) -> dict | None:
    """Determine optimal sort for the query. Returns sort recommendation or None on failure."""
    if not sortable_columns:
        return None

    conversation_context = build_conversation_context(conversation)
    columns_summary = "\n".join(
        f"- {c['name']} (backend_code: {c['backend_code']}, sortability: {c['sortability']})"
        + (f" — {c['description']}" if c.get("description") else "")
        for c in sortable_columns
    )
    user_msg = (
        f"{conversation_context}User's search query: {prompt}\n\n"
        f"Report type: {report_type}\n\n"
        f"Available sortable columns:\n{columns_summary}"
    )

    emit_status("sort", "Determining optimal sort strategy...")
    try:
        content = call_openrouter(
            [
                {"role": "system", "content": SORT_STRATEGY_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            model=KEYWORD_MODEL,
        )
        result = parse_json_response(content)
        if "sort_field" not in result or "sort_direction" not in result:
            emit_status("warning", f"Sort strategy missing required keys: {list(result.keys())}")
            return None
        # Clamp direction to respect sortability constraint from column metadata
        col_meta = next((c for c in sortable_columns if c["backend_code"] == result["sort_field"]), None)
        if col_meta and col_meta.get("sortability") not in ("both", None):
            result["sort_direction"] = col_meta["sortability"]
        return result
    except Exception as exc:
        emit_status("warning", f"Sort strategy failed: {exc}")
        return None


# ── Stage 3: Multi-agent review loop ───────────────────────────────────────

CRITIC_SYSTEM_PROMPT = """\
You are a quality reviewer for an influencer marketing report configuration.
Review the generated config against the user's original request and identify concrete issues.

Check for these failure modes:
1. MISSING_ENTITY_FILTERS: Sponsorship reports (type 8) missing brand_names or channel_names \
when the user mentioned specific brands or channels by name.
2. OVER_CONSTRAINING: Too many narrow filters combined that will likely produce zero results \
(e.g., very narrow date range + rare keyword + small subscriber range + niche country).
3. KEYWORD_RELEVANCE: keyword_groups containing terms that don't match the user's intent, \
or entity names (brand/channel names) placed as keywords instead of the appropriate filter.
4. RESULT_COUNT_IGNORED: User requested a specific number of results (e.g., "top 50") \
but no limiting filters (reach_from, projected_views_from) were set to approximate that count.
5. SORT_MISMATCH: Sort field doesn't match the user's stated priority \
(e.g., user said "best engagement" but sort is by views or reach).
6. REPORT_TYPE_MISMATCH: Wrong report type for the query \
(e.g., user asked about specific videos but got a channels report type 3).
7. DATE_RANGE_MISSING: User implied a time period ("last 6 months", "recent", "this year") \
but no date filter (days_ago, start_date/end_date) was set.
8. BRAND_AS_KEYWORD: Brand names appearing in keyword_groups instead of brand_names/brands filter.
9. CHANNEL_AS_KEYWORD: Channel names appearing in keyword_groups instead of channel_names/channels filter.
10. MISSING_APPLY_AS: For multi_step_query, apply_as is missing or null in main_report. \
This is critical — without it the backend defaults to "exclude_channels" which may be the \
opposite of the user's intent. Check whether the user wants inclusion ("show me", "take", \
"from") or exclusion ("excluding", "not", "without").

Respond with JSON:
{
  "issues": [
    {
      "severity": "critical" or "warning",
      "category": "<category name from above>",
      "description": "What's wrong",
      "fix": "Specific instruction for what the generator should change"
    }
  ],
  "verdict": "pass" or "needs_revision"
}

If no issues found, return {"issues": [], "verdict": "pass"}.
Only set "needs_revision" if there are CRITICAL issues that would make the report \
unusable or produce wrong results. Warnings alone should still "pass".
Be conservative — false positives waste time and money on unnecessary revisions.
"""

JUDGE_SYSTEM_PROMPT = """\
You are a judge evaluating a critique of an AI-generated report configuration.
The critic has reviewed a config and found potential issues. Your job is to:
1. Validate each issue — is it a real problem or a false positive?
2. Decide whether the config should be accepted as-is or sent back for revision.

Be conservative: only send back for revision if there are genuine critical issues
that will cause the user to get wrong or empty results. Minor improvements are not
worth the latency cost of another LLM call.

Common false positives to watch for:
- Critic flagging missing brand/channel filters when the user didn't mention specific entities
- Critic flagging date range missing when the user genuinely wants all-time data
- Critic flagging sort mismatch when the chosen sort is a reasonable default
- Critic flagging over-constraining when the filters match what the user explicitly requested

Respond with JSON:
{
  "decision": "accept" or "revise",
  "valid_issues": [
    {
      "category": "...",
      "fix": "Specific instruction for the generator"
    }
  ],
  "reasoning": "Brief explanation of your decision"
}
"""


def critique_config(
    prompt: str,
    conversation: list[dict],
    config: dict,
    report_type: int,
    sort_strategy: dict | None,
) -> dict:
    """Critic agent: reviews config for known failure modes. Returns issues list and verdict."""
    conversation_context = build_conversation_context(conversation)
    sort_context = ""
    if sort_strategy:
        sort_context = (
            f"\nRecommended sort: {sort_strategy.get('sort_field', 'N/A')} "
            f"({sort_strategy.get('sort_direction', 'N/A')})"
        )

    user_msg = (
        f"Original user request: {prompt}\n"
        f"{conversation_context}"
        f"Report type: {report_type}{sort_context}\n\n"
        f"Generated config to review:\n{json.dumps(config, indent=2)}"
    )

    emit_status("critic", "Reviewing configuration quality...")
    content = call_openrouter(
        [
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=KEYWORD_MODEL,
    )
    return parse_json_response(content)


def judge_critique(
    prompt: str,
    config: dict,
    critique: dict,
) -> dict:
    """Judge agent: validates critique and decides accept/revise."""
    user_msg = (
        f"Original user request: {prompt}\n\n"
        f"Generated config:\n{json.dumps(config, indent=2)}\n\n"
        f"Critic's review:\n{json.dumps(critique, indent=2)}"
    )

    emit_status("judge", "Evaluating review feedback...")
    content = call_openrouter(
        [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=KEYWORD_MODEL,
    )
    return parse_json_response(content)


def revise_config(
    prompt: str,
    conversation: list[dict],
    original_config: dict,
    valid_issues: list[dict],
    keyword_result: dict,
    team_directory: str,
    campaign_config: dict | None,
    sort_strategy: dict | None,
) -> dict:
    """Re-run the generator with critic feedback to fix identified issues."""
    revision_addendum = (
        f"\n\n## REVISION REQUIRED\n"
        f"Your previous config had these issues that must be fixed:\n"
        f"{json.dumps(valid_issues, indent=2)}\n\n"
        f"Previous config for reference:\n"
        f"{json.dumps(original_config, indent=2)}\n\n"
        f"Fix the identified issues while preserving everything else that was correct."
    )

    system_prompt_tpl = (PROMPTS_DIR / "system_prompt.txt").read_text()
    system_msg = Template(system_prompt_tpl).safe_substitute(
        current_date=date.today().isoformat(),
        team_directory=team_directory or "",
        topic_insights="",
    )

    if campaign_config:
        system_msg += _build_edit_addendum(campaign_config)

    system_msg += revision_addendum

    conversation_context = build_conversation_context(conversation)
    user_msg = f"{conversation_context}User request: {prompt}"

    keyword_groups = keyword_result.get("keyword_groups", [])
    if keyword_groups:
        user_msg += (
            f"\n\nPre-validated keyword groups (ES-verified). "
            f"Use exactly as provided in filterset.keyword_groups — do not modify or regenerate:\n"
            f"{json.dumps(keyword_groups)}\n"
            f"keyword_operator: {keyword_result.get('keyword_operator', 'OR')}"
        )

    if sort_strategy:
        user_msg += (
            f"\n\nRecommended sort strategy (from analysis of available sortable columns):\n"
            f"Sort by: {sort_strategy.get('sort_field', 'unknown')} ({sort_strategy.get('sort_direction', 'desc')})\n"
            f"Reasoning: {sort_strategy.get('reasoning', '')}\n"
            f"Use this sort unless the user's intent clearly conflicts with it."
        )

    emit_status("revision", "Refining configuration based on review...")
    content = call_openrouter(
        [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        model=MAIN_MODEL,
        json_mode=True,
        max_tokens=8192,
    )
    return parse_json_response(content)


def run_review_loop(
    prompt: str,
    conversation: list[dict],
    llm_response: dict,
    report_type: int,
    sort_strategy: dict | None,
    keyword_result: dict,
    team_directory: str,
    campaign_config: dict | None,
    t0: float,
) -> dict:
    """Run Critic → Judge → (optional) Revise loop. Returns final config."""
    elapsed = time.time() - t0
    if elapsed > REVIEW_LOOP_TIME_BUDGET:
        emit_status("review", f"Skipping review loop (elapsed {elapsed:.0f}s > {REVIEW_LOOP_TIME_BUDGET}s budget)")
        return llm_response

    # Critic
    try:
        critique = critique_config(prompt, conversation, llm_response, report_type, sort_strategy)
    except Exception as exc:
        emit_status("warning", f"Critic failed: {exc}")
        return llm_response

    if critique.get("verdict") != "needs_revision":
        issues = critique.get("issues", [])
        if issues:
            warnings = [i for i in issues if i.get("severity") == "warning"]
            emit_status("review", f"Critic passed with {len(warnings)} warning(s)")
        else:
            emit_status("review", "Critic passed with no issues")
        return llm_response

    # Judge
    try:
        judgment = judge_critique(prompt, llm_response, critique)
    except Exception as exc:
        emit_status("warning", f"Judge failed: {exc}")
        return llm_response

    if judgment.get("decision") != "revise" or not judgment.get("valid_issues"):
        emit_status("review", f"Judge accepted config: {judgment.get('reasoning', '')}")
        return llm_response

    # Revise (max 1 round)
    valid_issues = judgment["valid_issues"]
    emit_status("review", f"Revising config to fix {len(valid_issues)} issue(s)...")
    try:
        revised = revise_config(
            prompt, conversation, llm_response, valid_issues,
            keyword_result, team_directory, campaign_config, sort_strategy,
        )
        return revised
    except Exception as exc:
        emit_status("warning", f"Revision failed: {exc}, using original config")
        return llm_response


# ── Stage 2: Main LLM config build ───────────────────────────────────────────

def _extract_similar_channel_names(prompt: str, conversation: list[dict] | None = None) -> list[str]:
    """Extract channel names from a similar-channels query.

    Looks for patterns like "similar to X", "like X", "creators like X" in the
    prompt and conversation, returning the channel name(s) found.
    """
    import re as _re
    all_text = prompt
    for turn in (conversation or []):
        content = turn.get("content", "")
        if turn.get("role") == "user" and content:
            all_text += " " + content

    # Match "similar to <name>", "like <name>", "creators like <name>", etc.
    patterns = [
        r"(?:similar to|like|channels like|creators like|youtubers like)\s+([A-Z][A-Za-z0-9' ]+?)(?:\s+(?:with|who|that|in|on|and|$))",
        r"(?:similar to|like|channels like|creators like|youtubers like)\s+(.+?)$",
    ]
    names: list[str] = []
    for pat in patterns:
        for match in _re.finditer(pat, all_text, _re.IGNORECASE | _re.MULTILINE):
            name = match.group(1).strip().rstrip(".,;")
            if name and len(name) > 2 and name.lower() not in ("the", "some", "these", "those"):
                names.append(name)
    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            unique.append(n)
    return unique


def build_config(
    prompt: str,
    conversation: list[dict],
    keyword_result: dict,
    team_directory: str,
    campaign_config: dict | None,
    sort_strategy: dict | None = None,
    is_similar_channels: bool = False,
) -> dict:
    """Call the main LLM with SYSTEM_PROMPT to produce the full campaign config."""
    system_prompt_tpl = (PROMPTS_DIR / "system_prompt.txt").read_text()
    system_msg = Template(system_prompt_tpl).safe_substitute(
        current_date=date.today().isoformat(),
        team_directory=team_directory or "",
        topic_insights="",
    )

    # Edit mode: append current config context
    if campaign_config:
        edit_addendum = _build_edit_addendum(campaign_config)
        system_msg += edit_addendum

    conversation_context = build_conversation_context(conversation)
    user_msg = f"{conversation_context}User request: {prompt}"

    # Similar channels: inject strong directive to use similar_to_channels
    if is_similar_channels:
        channel_names = _extract_similar_channel_names(prompt, conversation)
        names_json = json.dumps(channel_names) if channel_names else '["<channel name from prompt>"]'
        user_msg += (
            f"\n\n## SIMILAR CHANNELS QUERY — MANDATORY INSTRUCTIONS\n"
            f"This is a similar-channels query. You MUST:\n"
            f"1. Use similar_to_channels: {names_json} in the filterset\n"
            f"2. Do NOT include keyword_groups — vector similarity already captures topic relevance\n"
            f"3. Do NOT include days_ago — similar channels search is not time-bound\n"
            f"4. You may add other filters (language, reach_from, etc.) based on the user's request\n"
        )

    # Inject pre-validated keyword_groups if available
    keyword_groups = keyword_result.get("keyword_groups", [])
    if keyword_groups:
        user_msg += (
            f"\n\nPre-validated keyword groups (ES-verified). "
            f"Use exactly as provided in filterset.keyword_groups — do not modify or regenerate:\n"
            f"{json.dumps(keyword_groups)}\n"
            f"keyword_operator: {keyword_result.get('keyword_operator', 'OR')}\n"
            f"IMPORTANT: Since this report uses keywords, you MUST include a date filter "
            f"(days_ago: 730) in the filterset unless the user explicitly specified a different "
            f"timeframe. Keyword searches without date constraints cause ES timeouts."
        )

    # Inject sort recommendation if available
    if sort_strategy:
        user_msg += (
            f"\n\nRecommended sort strategy (from analysis of available sortable columns):\n"
            f"Sort by: {sort_strategy.get('sort_field', 'unknown')} ({sort_strategy.get('sort_direction', 'desc')})\n"
            f"Reasoning: {sort_strategy.get('reasoning', '')}\n"
            f"Use this sort unless the user's intent clearly conflicts with it."
        )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    # Retry once on JSON parse failure — transient LLM formatting errors
    last_exc: Exception | None = None
    for attempt in range(2):
        if attempt == 0:
            emit_status("generator", "Building report configuration...")
        else:
            emit_status("generator", "Retrying config build (JSON parse error)...")
        content = call_openrouter(messages, model=MAIN_MODEL, json_mode=True, max_tokens=8192)
        try:
            return parse_json_response(content)
        except json.JSONDecodeError as exc:
            last_exc = exc
            emit_status("warning", f"JSON parse error on attempt {attempt + 1}: {exc}")
    raise last_exc  # type: ignore[misc]


def _build_edit_addendum(campaign_config: dict) -> str:
    return (
        "\n\n## EDIT MODE — You are modifying an existing report, NOT creating a new one.\n"
        "The user has an existing report with the configuration below. They want to modify it.\n"
        "Your response must STILL use \"action\": \"create_report\" with the FULL updated configuration.\n"
        "Preserve ALL existing settings that the user did not ask to change.\n\n"
        f"### Current Report Configuration:\n{json.dumps(campaign_config, indent=2)}\n"
    )


# ── Stage 4: Resolve similar_to_channels ─────────────────────────────────────

def resolve_similar_channels(llm_response: dict) -> dict:
    """Post-process: if the LLM output contains similar_to_channels, resolve them.

    Calls find_similar_channels.py to run vector similarity search,
    then injects the resulting channel IDs into the filterset.
    """
    # Check both top-level filterset and multi_step_query's main_report
    action = llm_response.get("action", "")

    if action == "multi_step_query":
        filterset = llm_response.get("main_report", {}).get("filterset", {})
    elif action == "create_report":
        filterset = llm_response.get("filterset", {})
    else:
        return llm_response

    similar_to = filterset.get("similar_to_channels")
    if not similar_to or not isinstance(similar_to, list):
        return llm_response

    emit_status("similar_channels", f"Finding channels similar to {', '.join(similar_to)}...")

    try:
        result = subprocess.run(
            [
                "python3", str(SIMILAR_CHANNELS_SCRIPT),
                "--channel-names", json.dumps(similar_to),
                "--max-results", "50",
                "--min-score", "65",
            ],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=SIMILAR_CHANNELS_TIMEOUT,
        )
        if result.returncode != 0:
            emit_status("warning", f"Similar channels search failed: {result.stderr[:300]}")
            return llm_response

        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        emit_status("warning", "Similar channels search timed out")
        return llm_response
    except (json.JSONDecodeError, Exception) as exc:
        emit_status("warning", f"Similar channels search error: {exc}")
        return llm_response

    similar_channels = data.get("similar_channels", [])
    total_found = data.get("total_found", 0)
    source_channels = data.get("source_channels", [])

    if not similar_channels:
        unresolved = [s["channel_name"] for s in source_channels if not s.get("resolved")]
        if unresolved:
            emit_status("warning", f"Could not find channels: {', '.join(unresolved)}")
        else:
            emit_status("warning", "No similar channels found above the similarity threshold")
        return llm_response

    # Inject the similar channel IDs into the filterset as hardcoded channel filters
    channel_ids = [ch["channel_id"] for ch in similar_channels]
    existing_channels = filterset.get("channels", [])
    filterset["channels"] = existing_channels + channel_ids

    # Remove the similar_to_channels field — it's been resolved
    filterset.pop("similar_to_channels", None)

    # Attach metadata for Django's preview chip building
    resolved_names = [s["channel_name"] for s in source_channels if s.get("resolved")]
    llm_response["_similar_channels_meta"] = {
        "source_channels": resolved_names,
        "total_found": total_found,
        "top_matches": [
            {"channel_name": ch["channel_name"], "score": ch["score"]}
            for ch in similar_channels[:5]
        ],
    }

    emit_status("similar_channels", f"Found {total_found} similar channels")
    return llm_response


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    t0 = time.time()

    parser = argparse.ArgumentParser(description="AI Report Builder full orchestrator")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--conversation", default="[]")
    parser.add_argument("--team-directory", default="", dest="team_directory")
    parser.add_argument("--campaign-config", default=None, dest="campaign_config")
    args = parser.parse_args()

    try:
        conversation = json.loads(args.conversation)
    except json.JSONDecodeError:
        conversation = []

    campaign_config: dict | None = None
    if args.campaign_config:
        try:
            campaign_config = json.loads(args.campaign_config)
        except json.JSONDecodeError:
            pass

    is_followup_round = bool(conversation)

    # Detect report type hint from existing config (edit mode) or prompt heuristic
    report_type_hint = _detect_report_type_hint(args.prompt, conversation, campaign_config)

    # Stage 1: Keyword research — skip for sponsorship and similar-channels queries
    skip_keywords = False
    if report_type_hint == 8:
        emit_status("keywords", "Skipping keyword research (sponsorship query)...")
        skip_keywords = True
    elif _is_likely_similar_channels_query(args.prompt, conversation):
        emit_status("keywords", "Skipping keyword research (similar channels query)...")
        skip_keywords = True

    if skip_keywords:
        keyword_result = {
            "action": "suggestions",
            "suggested_keywords": [],
            "keyword_groups": [],
            "keyword_operator": "OR",
        }
    else:
        try:
            keyword_result = research_keywords(args.prompt, conversation, report_type_hint)
        except Exception as exc:
            emit_status("warning", f"Keyword research failed: {exc}")
            keyword_result = {
                "action": "suggestions",
                "suggested_keywords": [],
                "keyword_groups": [],
                "keyword_operator": "OR",
            }

    # If keyword research determined a follow-up is needed, return early
    if keyword_result.get("action") == "follow_up" and not is_followup_round:
        print(json.dumps({
            "action": "follow_up",
            "question": keyword_result.get("follow_up_question", "Could you be more specific?"),
            "suggestions": [
                {"title": s, "description": ""}
                if isinstance(s, str) else s
                for s in keyword_result.get("follow_up_suggestions", [])
            ],
        }))
        return

    # Stage 1.5: Sort strategy
    # Skip for type 8 hints — sponsorship queries default to -send_date, and the hint
    # may be wrong for multi_step_query where the main_report is a different type.
    # Skip in edit mode — preserve existing sort unless user explicitly asks to change it.
    is_edit_mode = campaign_config is not None
    skip_sort = report_type_hint == 8 or is_edit_mode
    sort_strategy: dict | None = None
    if not skip_sort:
        sortable_columns = get_sortable_columns(report_type_hint)
        sort_strategy = determine_sort_strategy(args.prompt, conversation, report_type_hint, sortable_columns)

    # Stage 2: Main LLM config build (Generator)
    is_similar = _is_likely_similar_channels_query(args.prompt, conversation)
    try:
        llm_response = build_config(
            prompt=args.prompt,
            conversation=conversation,
            keyword_result=keyword_result,
            team_directory=args.team_directory,
            campaign_config=campaign_config,
            sort_strategy=sort_strategy,
            is_similar_channels=is_similar,
        )
    except Exception as exc:
        emit_status("error", f"Main LLM call failed: {exc}")
        print(json.dumps({
            "action": "error",
            "message": "Sorry, the AI Report Builder is temporarily unavailable.",
        }))
        sys.exit(1)

    # Stage 3: Multi-agent review loop (Critic → Judge → optional Revise)
    action = llm_response.get("action", "")
    if action in ("create_report", "multi_step_query"):
        report_type = llm_response.get("report_type") or llm_response.get("main_report", {}).get("report_type")
        llm_response = run_review_loop(
            prompt=args.prompt,
            conversation=conversation,
            llm_response=llm_response,
            report_type=report_type or report_type_hint,
            sort_strategy=sort_strategy,
            keyword_result=keyword_result,
            team_directory=args.team_directory,
            campaign_config=campaign_config,
            t0=t0,
        )

    # Stage 4: Resolve similar_to_channels if present in the LLM output
    if action in ("create_report", "multi_step_query"):
        llm_response = resolve_similar_channels(llm_response)

    # Attach suggested_keywords to the response for Vue chips
    if keyword_result.get("suggested_keywords"):
        report_type = llm_response.get("report_type") or llm_response.get("main_report", {}).get("report_type")
        if report_type != 8:
            llm_response["suggested_keywords"] = keyword_result["suggested_keywords"]

    elapsed = time.time() - t0
    emit_status("done", f"Pipeline completed in {elapsed:.1f}s")
    print(json.dumps(llm_response))


if __name__ == "__main__":
    main()
