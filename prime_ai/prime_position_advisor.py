"""
PRIME v1.0 AI Position Advisory (Sprint 15 Item 2).

For every OPEN trade in prime_trade_log, Claude returns a HOLD / TRIM / EXIT
recommendation with plain-English reasoning. Advisory is advisory only -- it
never blocks or mutates trades, and degrades gracefully (recommendation
"UNAVAILABLE") when the API is unreachable.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_ai import _claude

logger = logging.getLogger(__name__)

VALID_RECS = ("HOLD", "TRIM", "EXIT")

SYSTEM_PROMPT = """You are the PRIME AI Position Advisor. Given a single open
trading position, decide whether to HOLD, TRIM, or EXIT it. Weigh unrealized
P&L, hold time, the originating strategy, sector exposure, dark-pool status,
batch and entry-timing quality. Be concise and concrete.

DK three-state context (use to adjust urgency):
  dk_status CONFIRMING + dk_conviction >= 0.7 = institutional dark-pool money
    agrees with your position direction -- positive signal, lean HOLD.
  dk_status NEUTRAL = no dark-pool signal either way -- no adjustment.
  dk_status NULLIFYING = institutional dark-pool money is moving AGAINST your
    position -- warning signal; consider tightening stop or exiting.

DIRECTION MATTERS. The position has a "direction" field:
- LONG: profit when price RISES; a rising price is favourable. EXIT = sell.
- SHORT: profit when price FALLS; a RISING price is ADVERSE (loss). For a SHORT,
  speak in short terms -- say "cover" rather than "sell", and treat a rising
  price as the risk to manage, not good news.
Unrealized P&L is already computed direction-correctly; interpret it as given.

Respond ONLY with a JSON object, no prose outside it:
{"symbol": str, "recommendation": "HOLD|TRIM|EXIT", "confidence": "HIGH|MEDIUM|LOW",
 "reasoning": "one or two sentences", "suggested_action": "short imperative"}"""


def _hold_minutes(entry_time: Optional[str]) -> Optional[int]:
    if not entry_time:
        return None
    try:
        ts = datetime.fromisoformat(entry_time)
        return int((datetime.now() - ts).total_seconds() // 60)
    except (TypeError, ValueError):
        return None


def build_context(position: Dict[str, Any]) -> Dict[str, Any]:
    """Build the per-position payload sent to Claude.

    Sprint 20 Item 4: dk_conviction added alongside dk_status so Claude can
    calibrate how strongly the dark-pool signal opposes or supports the position.
    Graceful degradation: missing DK data defaults to NEUTRAL.
    """
    entry = position.get("entry_price") or position.get("price_at_scan")
    return {
        "symbol": position.get("symbol"),
        "strategy": position.get("strategy"),
        "direction": position.get("direction"),
        "entry_price": entry,
        "current_price": position.get("current_price"),
        "shares": position.get("shares"),
        "hold_minutes": position.get("hold_minutes") or _hold_minutes(position.get("entry_time")),
        "unrealized_pnl_pct": position.get("pnl_pct"),
        "sector": position.get("sector"),
        "dk_status": position.get("dk_status") or "NEUTRAL",
        "dk_conviction": position.get("dk_conviction"),
        "batch_score": position.get("batch_score"),
        "entry_timing": position.get("entry_timing"),
    }


def _fallback(symbol: str, reason: str) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "recommendation": "UNAVAILABLE",
        "confidence": "LOW",
        "reasoning": f"AI advisory unavailable: {reason}",
        "suggested_action": "Review manually",
        "_fallback": True,
    }


def advise_one(position: Dict[str, Any], api_key: Optional[str] = None) -> Dict[str, Any]:
    """Return a recommendation dict for one position. Never raises."""
    symbol = position.get("symbol", "?")
    ctx = build_context(position)
    import json
    prompt = ("Analyze this open position and respond with the JSON object:\n"
              + json.dumps(ctx, indent=2, default=str))
    try:
        text = _claude.call_claude(SYSTEM_PROMPT, prompt, api_key=api_key)
        data = _claude.parse_json(text)
        rec = str(data.get("recommendation", "")).upper()
        if rec not in VALID_RECS:
            return _fallback(symbol, f"unexpected recommendation '{rec}'")
        return {
            "symbol": data.get("symbol", symbol),
            "recommendation": rec,
            "confidence": str(data.get("confidence", "MEDIUM")).upper(),
            "reasoning": data.get("reasoning", ""),
            "suggested_action": data.get("suggested_action", ""),
            "_fallback": False,
        }
    except Exception as e:
        logger.warning("position advisory failed for %s: %s", symbol, e)
        return _fallback(symbol, str(e))


def advise_positions(
    db_path: Optional[Path] = None,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Advise on all OPEN positions. Returns one dict per position (possibly empty)."""
    from prime_data.prime_db import get_open_positions
    positions = get_open_positions(db_path=db_path)
    if api_key is None:
        api_key = _claude.get_api_key()
    return [advise_one(p, api_key=api_key) for p in positions]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for a in advise_positions():
        print(a["symbol"], a["recommendation"], "-", a["reasoning"])
