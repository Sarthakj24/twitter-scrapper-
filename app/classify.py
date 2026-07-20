"""Hiring classification: a cheap keyword pre-filter, then an LLM for anything
that isn't obviously non-hiring.

The LLM provider is selectable via LLM_PROVIDER (config): "groq" (default, free
tier, OpenAI-compatible — called with httpx, no extra dependency) or
"anthropic". Both are held to the same strict-JSON contract, so the rest of the
pipeline doesn't care which one ran.

The pre-filter's job is only to discard tweets that *clearly* aren't hiring so
we don't spend a model call on every timeline item. Anything ambiguous is
passed through, as specified.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from .config import config

# Broad set — presence of ANY of these means "not clearly non-hiring", so we let
# the model make the real call. Kept deliberately generous to avoid false skips.
_HIRING_HINTS = (
    "hiring", "we're hiring", "were hiring", "now hiring", "join our team",
    "join the team", "join us", "open role", "open position", "open roles",
    "job opening", "job opportunity", "opportunity", "vacancy", "vacancies",
    "recruit", "recruiting", "we are looking for", "looking for a",
    "looking to hire", "apply", "application", "career", "careers",
    "job alert", "job post", "position", "role:", "roles", "headcount",
    "dm me your resume", "send your resume", "send your cv", "referral",
    "founding engineer", "we need", "come work", "backfill", "seeking a",
    "onsite", "remote role", "full-time", "part-time", "internship", "intern",
    "wfh", "ctc", "salary", "stipend", "notice period",
)

# The JSON contract the model must return. NOTE: the original spec was truncated
# mid-schema (…"company": str|null,). This is a faithful reconstruction; adjust
# the fields here and in the prompt below if you want a different shape.
CLASSIFICATION_KEYS = ("is_hiring", "role", "company", "location", "seniority", "apply_url", "summary")

_SYSTEM = (
    "You classify whether a single tweet is a hiring / job-opportunity post "
    "(someone advertising an open role, seeking candidates, or pointing to a "
    "job application). Retweets and quote-tweets of a job post count. General "
    "career advice, 'I got a new job' announcements, and layoffs do NOT count.\n\n"
    "Respond with ONLY a JSON object, no prose, no markdown fences, matching:\n"
    '{"is_hiring": bool, "role": str|null, "company": str|null, '
    '"location": str|null, "seniority": str|null, "apply_url": str|null, '
    '"summary": str|null}\n'
    "Use null for any field you cannot determine. 'summary' is a short (<=140 "
    "char) human-readable one-liner describing the opportunity, or null if "
    "is_hiring is false."
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def keyword_prefilter(text: str) -> bool:
    """Return True if the tweet should go to the model (i.e. it's NOT clearly
    non-hiring). Return False to skip cheaply."""
    low = text.lower()
    return any(h in low for h in _HIRING_HINTS)


def _empty_result() -> dict[str, Any]:
    return {k: (False if k == "is_hiring" else None) for k in CLASSIFICATION_KEYS}


def _extract_url(tweet: dict[str, Any]) -> Optional[str]:
    """Best-effort apply link from the tweet's expanded entities."""
    for u in tweet.get("entities", {}).get("urls", []) or []:
        if u.get("expanded_url"):
            return u["expanded_url"]
    return None


# --- provider backends: each returns the model's raw text (expected JSON) -----

def _complete_groq(text: str) -> str:
    resp = httpx.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {config.groq_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.groq_model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": text},
            ],
            "max_tokens": 300,
            "temperature": 0,
            # JSON mode; the system prompt contains the word "JSON" as required.
            "response_format": {"type": "json_object"},
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _complete_anthropic(text: str) -> str:
    from anthropic import Anthropic  # lazy: only imported if this provider is used

    client = Anthropic(api_key=config.anthropic_api_key)
    msg = client.messages.create(
        model=config.claude_model,
        max_tokens=300,
        system=_SYSTEM,
        messages=[{"role": "user", "content": text}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def _complete(text: str) -> str:
    if config.llm_provider == "anthropic":
        return _complete_anthropic(text)
    return _complete_groq(text)


def classify(tweet: dict[str, Any]) -> dict[str, Any]:
    """Full classification for one tweet. Cheap skip if the pre-filter rejects."""
    text = tweet.get("text", "") or ""

    if not keyword_prefilter(text):
        return _empty_result()

    raw = _complete(text).strip()

    # Be forgiving if the model wraps JSON in fences despite instructions.
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"): raw.rfind("}") + 1]

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Treat unparseable output as "not hiring" rather than crash the run.
        return _empty_result()

    result = _empty_result()
    for k in CLASSIFICATION_KEYS:
        if k in parsed:
            result[k] = parsed[k]
    result["is_hiring"] = bool(result.get("is_hiring"))

    # Fill apply_url from tweet entities if the model didn't surface one.
    if result["is_hiring"] and not result.get("apply_url"):
        result["apply_url"] = _extract_url(tweet)

    return result
