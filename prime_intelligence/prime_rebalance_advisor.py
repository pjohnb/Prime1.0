"""
PRIME v1.0 AI Portfolio Rebalance Advisor (ML-Pattern-17).

Surfaces advisory rebalancing suggestions via Claude API call.
Advisory only -- no order generation, no auto-execute path.
Falls back to cached/deterministic suggestions on API failure.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from prime_intelligence.prime_portfolio_factor import (
    evaluate_portfolio_risk,
    get_rebalance_suggestions,
    sector_map,
)

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6-20250514"

REBALANCE_SYSTEM_PROMPT = """You are the PRIME AI Portfolio Rebalance Advisor. Analyze the
portfolio snapshot and provide structured rebalancing suggestions.

Consider: sector concentration limits (40% max), correlation risk,
position sizing, and current market conditions implied by the data.

Respond ONLY with a valid JSON object matching the required schema.
No text outside the JSON structure."""


def _build_rebalance_prompt(snapshot: Dict[str, Any]) -> str:
    return (
        "Analyze this portfolio snapshot and suggest rebalancing actions.\n\n"
        f"PORTFOLIO SNAPSHOT:\n{json.dumps(snapshot, indent=2)}\n\n"
        "Respond with a JSON object:\n"
        '{"suggestions": [{"symbol": "...", "action": "REDUCE|INCREASE|HOLD|EXIT", '
        '"rationale": "...", "urgency": "HIGH|MEDIUM|LOW"}], '
        '"portfolio_assessment": "...", "concentration_warnings": [...]}'
    )


def _fallback_suggestions(
    snapshot: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    """Deterministic fallback when Claude API is unavailable."""
    risk = snapshot.get("risk", {})
    suggestions = []

    if risk.get("concentration_breach"):
        max_sector = risk.get("max_sector", "Unknown")
        suggestions.append({
            "symbol": f"[{max_sector} sector]",
            "action": "REDUCE",
            "rationale": f"Sector concentration breach: {max_sector} "
                         f"at {risk.get('max_sector_weight', 0):.0%}",
            "urgency": "HIGH",
        })

    for flag in risk.get("correlation_flags", []):
        suggestions.append({
            "symbol": "[correlated group]",
            "action": "REDUCE",
            "rationale": flag,
            "urgency": "MEDIUM",
        })

    if not suggestions:
        suggestions.append({
            "symbol": "[portfolio]",
            "action": "HOLD",
            "rationale": "No concentration breaches or correlation risks detected",
            "urgency": "LOW",
        })

    return {
        "suggestions": suggestions,
        "portfolio_assessment": f"Deterministic fallback ({reason})",
        "concentration_warnings": risk.get("correlation_flags", []),
        "_fallback": True,
        "_fallback_reason": reason,
        "timestamp": datetime.utcnow().isoformat(),
    }


def build_portfolio_snapshot(
    open_positions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a snapshot dict suitable for the rebalance advisor prompt."""
    risk = evaluate_portfolio_risk(open_positions)
    positions_summary = []
    for pos in open_positions:
        sym = pos.get("symbol", "???")
        positions_summary.append({
            "symbol": sym,
            "shares": pos.get("shares", 0),
            "entry_price": pos.get("entry_price") or pos.get("price_at_scan", 0),
            "current_price": pos.get("current_price", 0),
            "sector": sector_map(sym),
            "trade_source": pos.get("trade_source", "PAPER"),
        })

    return {
        "positions": positions_summary,
        "risk": risk,
        "position_count": risk["position_count"],
        "total_market_value": risk["total_market_value"],
    }


def get_ai_rebalance_suggestions(
    portfolio_snapshot: Dict[str, Any],
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Get AI-powered rebalancing suggestions via Claude API.

    Returns structured suggestions dict. Never raises -- falls back
    to deterministic suggestions on any API failure.
    """
    timestamp = datetime.utcnow().isoformat()

    if not api_key:
        result = _fallback_suggestions(portfolio_snapshot, "no API key configured")
        result["timestamp"] = timestamp
        return result

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_rebalance_prompt(portfolio_snapshot)

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=REBALANCE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        result = json.loads(content)
        result["timestamp"] = timestamp
        result["_fallback"] = False
        return result

    except ImportError:
        result = _fallback_suggestions(portfolio_snapshot, "anthropic library not installed")
        result["timestamp"] = timestamp
        return result
    except json.JSONDecodeError as e:
        logger.warning("Claude returned non-JSON for rebalance: %s", e)
        result = _fallback_suggestions(portfolio_snapshot, f"invalid JSON: {e}")
        result["timestamp"] = timestamp
        return result
    except Exception as e:
        logger.warning("Claude API call failed for rebalance: %s", e)
        result = _fallback_suggestions(portfolio_snapshot, str(e))
        result["timestamp"] = timestamp
        return result
