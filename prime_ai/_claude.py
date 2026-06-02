"""
Shared Claude API helper for the prime_ai advisory package (Sprint 15).

Single place that knows the model, token budget, API-key source, and JSON
parsing. Every advisory module calls call_claude() and parse_json(); none
import anthropic directly. call_claude raises ClaudeUnavailable on any failure
so callers can apply a deterministic fallback (advisory is never blocking).
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


def call_claude(
    system: str,
    prompt: str,
    api_key: Optional[str] = None,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """Call claude-sonnet-4 and return the response text.

    Raises ClaudeUnavailable on missing key, missing library, or any API error.
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
