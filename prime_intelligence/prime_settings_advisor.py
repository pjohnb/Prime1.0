"""
PRIME v1.0 AI Settings Advisor (ML-Pattern-18).

Analyzes recent scan performance and suggests parameter adjustments.
Reads ops_config.json (read-only reference) and scan history from
prime_signals. Display only -- no auto-write to ops_config.json.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6-20250514"

SETTINGS_SYSTEM_PROMPT = """You are the PRIME AI Settings Advisor. Analyze scan performance
metrics and current scanner configuration, then suggest parameter adjustments.

Consider: approval rate, win rate by tier, signal-to-trade conversion,
rejection reasons, and any patterns that suggest parameter tuning.

Respond ONLY with a valid JSON object matching the required schema.
No text outside the JSON structure."""


def _read_ops_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read ops_config.json as read-only reference. Returns empty dict on failure."""
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "ops_config.json"
    try:
        if config_path.exists():
            return json.loads(config_path.read_text())
    except Exception as e:
        logger.warning("Failed to read ops_config.json: %s", e)
    return {}


def _build_settings_prompt(
    scan_history: List[Dict[str, Any]],
    current_config: Dict[str, Any],
) -> str:
    total = len(scan_history)
    traded = sum(1 for s in scan_history if s.get("trade_id"))
    conversion = (traded / total * 100) if total > 0 else 0

    scores = [s.get("score", 0) for s in scan_history if s.get("score")]
    avg_score = sum(scores) / len(scores) if scores else 0

    by_strategy: Dict[str, int] = {}
    for s in scan_history:
        strat = s.get("strategy", "Unknown")
        by_strategy[strat] = by_strategy.get(strat, 0) + 1

    metrics = {
        "total_signals": total,
        "traded_signals": traded,
        "conversion_rate_pct": round(conversion, 1),
        "avg_score": round(avg_score, 2),
        "signals_by_strategy": by_strategy,
    }

    return (
        "Analyze scan performance and suggest parameter adjustments.\n\n"
        f"SCAN PERFORMANCE METRICS:\n{json.dumps(metrics, indent=2)}\n\n"
        f"CURRENT CONFIG:\n{json.dumps(current_config, indent=2)}\n\n"
        "Respond with a JSON object:\n"
        '{"suggestions": [{"parameter": "...", "current_value": "...", '
        '"suggested_value": "...", "rationale": "..."}], '
        '"performance_assessment": "...", "confidence": "HIGH|MEDIUM|LOW"}'
    )


def _fallback_settings(
    scan_history: List[Dict[str, Any]],
    current_config: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    """Deterministic fallback when Claude API is unavailable."""
    total = len(scan_history)
    traded = sum(1 for s in scan_history if s.get("trade_id"))
    conversion = (traded / total * 100) if total > 0 else 0

    suggestions = []

    if total > 0 and conversion < 10:
        suggestions.append({
            "parameter": "score_threshold",
            "current_value": "varies by scanner",
            "suggested_value": "lower by 0.5",
            "rationale": f"Conversion rate is {conversion:.0f}% -- thresholds may be too restrictive",
        })

    if total == 0:
        suggestions.append({
            "parameter": "scan_schedule",
            "current_value": "see ops_config.json",
            "suggested_value": "increase frequency",
            "rationale": "No scan history available -- ensure scanners are running",
        })

    if not suggestions:
        suggestions.append({
            "parameter": "[no changes]",
            "current_value": "--",
            "suggested_value": "--",
            "rationale": "Current settings appear reasonable based on available data",
        })

    return {
        "suggestions": suggestions,
        "performance_assessment": f"Deterministic fallback ({reason})",
        "confidence": "LOW",
        "_fallback": True,
        "_fallback_reason": reason,
        "timestamp": datetime.utcnow().isoformat(),
    }


def get_settings_suggestions(
    scan_history: List[Dict[str, Any]],
    current_config: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get AI-powered settings suggestions via Claude API.

    ops_config.json is read-only -- suggestions are advisory, no auto-write.
    Never raises -- falls back to deterministic suggestions on failure.
    """
    if current_config is None:
        current_config = _read_ops_config(config_path)

    timestamp = datetime.utcnow().isoformat()

    if not api_key:
        result = _fallback_settings(scan_history, current_config, "no API key configured")
        result["timestamp"] = timestamp
        return result

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_settings_prompt(scan_history, current_config)

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=SETTINGS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        result = json.loads(content)
        result["timestamp"] = timestamp
        result["_fallback"] = False
        return result

    except ImportError:
        result = _fallback_settings(scan_history, current_config, "anthropic library not installed")
        result["timestamp"] = timestamp
        return result
    except json.JSONDecodeError as e:
        logger.warning("Claude returned non-JSON for settings: %s", e)
        result = _fallback_settings(scan_history, current_config, f"invalid JSON: {e}")
        result["timestamp"] = timestamp
        return result
    except Exception as e:
        logger.warning("Claude API call failed for settings: %s", e)
        result = _fallback_settings(scan_history, current_config, str(e))
        result["timestamp"] = timestamp
        return result
