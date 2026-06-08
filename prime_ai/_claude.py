"""
Shared Claude API helper for the prime_ai advisory package (Sprint 15).

Single place that knows the model, token budget, API-key source, and JSON
parsing. Every advisory module calls call_claude() and parse_json(); none
import anthropic directly. call_claude raises ClaudeUnavailable on any failure
so callers can apply a deterministic fallback (advisory is never blocking).

Sprint 26 Item 6: usage logging added — every call logs input/output token counts
and cost to prime_ai/prime_ai_usage.py without any caller changes.
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Per Sprint 15 work order.
CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1500


class ClaudeUnavailable(Exception):
    """Raised when the Claude API cannot be reached or returns no usable text."""


def get_api_key() -> Optional[str]:
    """Return ANTHROPIC_API_KEY from the environment (never hardcoded)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return key or None


def _detect_feature(system: str) -> str:
    """Infer the feature name from the system prompt text for usage tracking."""
    text = system.lower()
    if "position advisor" in text:
        return "Position Advisor"
    if "signal ranker" in text:
        return "Signal Ranker"
    if "briefing" in text:
        return "Briefing"
    if ("dk" in text or "dark" in text) and "pool" in text:
        return "DK Classifier"
    return "Other"


def call_claude(
    system: str,
    prompt: str,
    api_key: Optional[str] = None,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """Call claude-sonnet-4 and return the response text.

    Raises ClaudeUnavailable on missing key, missing library, or any API error.
    Token usage is logged automatically to prime_ai_usage.db.
    """
    if api_key is None:
        api_key = get_api_key()
    if not api_key:
        raise ClaudeUnavailable("no ANTHROPIC_API_KEY in environment")

    try:
        import anthropic
    except ImportError as e:
        raise ClaudeUnavailable(f"anthropic library not installed: {e}")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""
        if not text.strip():
            raise ClaudeUnavailable("empty response from Claude")

        # Sprint 26 Item 6: log usage; never raises (fire-and-forget).
        try:
            from prime_ai.prime_ai_usage import log_usage
            usage = getattr(resp, "usage", None)
            if usage is not None:
                log_usage(
                    feature=_detect_feature(system),
                    model=CLAUDE_MODEL,
                    input_tokens=int(getattr(usage, "input_tokens", 0)),
                    output_tokens=int(getattr(usage, "output_tokens", 0)),
                )
        except Exception:
            pass

        return text
    except ClaudeUnavailable:
        raise
    except Exception as e:
        raise ClaudeUnavailable(str(e))


def parse_json(text: str):
    """Parse a JSON object/array from Claude text, tolerating ``` fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the first {...} or [...] block in the text.
        m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        raise
