"""
PRIME v1.0 AI Trade Factor Analyst (ML-Pattern-20).

Submits Trade Factor Evaluations to Claude via the Anthropic API
and receives structured advisory. Advisory is generated at signal time,
stored before the GUI opens.

Reference: PRIME Trade Intelligence Paper v1.0, Section 3.

Architectural rule: advisory is advisory only. Never blocks trade entry.
API failure returns graceful fallback. Model: claude-opus-4-5.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-opus-4-5-20250514"

SYSTEM_PROMPT = """You are the PRIME AI Trade Factor Analyst. Your role is to analyze
Trade Factor Evaluations and produce structured trading advisories.

You evaluate five factor categories:
1. Duration Classifiers (ST/MT/LT)
2. Entry Modifiers (IMMEDIATE_FULL/IMMEDIATE_HALF/WAIT/SCALED)
3. Exit Triggers (armed trigger conditions)
4. Nullifiers (CLEAR/SUSPECT/NULLIFIED from dark pool analysis)
5. Trade Maintenance (ongoing monitoring flags)

Respond ONLY with a valid JSON object matching the required schema.
No text outside the JSON structure."""

ADVISORY_SCHEMA = {
    "recommendation": "ENTER | MONITOR | PASS | NULLIFY",
    "conviction": "HIGH | MEDIUM | LOW",
    "duration_guidance": {"class": "ST|MT|LT", "rationale": "", "override": False},
    "entry_strategy": {"method": "", "trigger": "", "rationale": ""},
    "exit_framework": {"primary": "", "secondary": "", "time_stop": ""},
    "key_risks": [],
    "risk_narrative": "",
    "factor_interactions": "",
    "maintenance_flags": [],
    "confidence_note": "",
}


def _build_prompt(factor_eval: Dict[str, Any]) -> str:
    """Build the trade factor record section of the prompt."""
    return (
        f"Analyze the following Trade Factor Evaluation and produce a structured advisory.\n\n"
        f"TRADE FACTOR RECORD:\n"
        f"{json.dumps(factor_eval, indent=2)}\n\n"
        f"Respond with a JSON object containing these fields:\n"
        f"- recommendation: ENTER | MONITOR | PASS | NULLIFY\n"
        f"- conviction: HIGH | MEDIUM | LOW\n"
        f"- duration_guidance: {{class, rationale, override}}\n"
        f"- entry_strategy: {{method, trigger, rationale}}\n"
        f"- exit_framework: {{primary, secondary, time_stop}}\n"
        f"- key_risks: [string, string, string] (max 3)\n"
        f"- risk_narrative: 3-5 sentence plain English summary\n"
        f"- factor_interactions: 2-3 sentence description of non-obvious interactions\n"
        f"- maintenance_flags: [conditions to monitor]\n"
        f"- confidence_note: what would change this recommendation\n"
    )


def _fallback_advisory(factor_eval: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Generate deterministic fallback when Claude API is unavailable."""
    nullifier = factor_eval.get("nullifier", {})
    null_status = nullifier.get("status", "CLEAR")

    if null_status == "NULLIFIED":
        rec = "NULLIFY"
        conviction = "HIGH"
        narrative = f"Deterministic fallback (API unavailable: {reason}). Signal nullified by dark pool analysis."
    elif null_status == "SUSPECT":
        rec = "MONITOR"
        conviction = "LOW"
        narrative = f"Deterministic fallback (API unavailable: {reason}). Signal has suspect dark pool flags -- monitor only."
    else:
        score = factor_eval.get("signal_score", 0.0)
        if score >= 7.0:
            rec = "ENTER"
            conviction = "MEDIUM"
        else:
            rec = "MONITOR"
            conviction = "LOW"
        narrative = f"Deterministic fallback (API unavailable: {reason}). Score={score}, no nullifier flags."

    return {
        "recommendation": rec,
        "conviction": conviction,
        "duration_guidance": factor_eval.get("duration", {}),
        "entry_strategy": factor_eval.get("entry", {}),
        "exit_framework": {"primary": "stop_loss", "secondary": "price_target", "time_stop": "per duration class"},
        "key_risks": [f"Advisory generated without AI analysis: {reason}"],
        "risk_narrative": narrative,
        "factor_interactions": "Unable to assess -- deterministic fallback mode",
        "maintenance_flags": factor_eval.get("maintenance_flags", []),
        "confidence_note": "Full AI advisory unavailable; re-request when API is accessible",
        "_fallback": True,
        "_fallback_reason": reason,
    }


def generate_advisory(
    factor_eval: Dict[str, Any],
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate Claude advisory for a trade factor evaluation.

    Returns advisory dict. Never raises -- falls back to deterministic
    advisory on any API failure.
    """
    timestamp = datetime.utcnow().isoformat()

    if not api_key:
        advisory = _fallback_advisory(factor_eval, "no API key configured")
        advisory["timestamp"] = timestamp
        return advisory

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = _build_prompt(factor_eval)

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        advisory = json.loads(content)
        advisory["timestamp"] = timestamp
        advisory["_fallback"] = False
        return advisory

    except ImportError:
        advisory = _fallback_advisory(factor_eval, "anthropic library not installed")
        advisory["timestamp"] = timestamp
        return advisory
    except json.JSONDecodeError as e:
        logger.warning("Claude returned non-JSON response: %s", e)
        advisory = _fallback_advisory(factor_eval, f"invalid JSON response: {e}")
        advisory["timestamp"] = timestamp
        return advisory
    except Exception as e:
        logger.warning("Claude API call failed: %s", e)
        advisory = _fallback_advisory(factor_eval, str(e))
        advisory["timestamp"] = timestamp
        return advisory
