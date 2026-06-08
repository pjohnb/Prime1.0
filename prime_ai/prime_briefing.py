"""
PRIME v1.0 AI Briefing Panel (Sprint 15 Item 4).

A single Claude call produces a portfolio briefing for the Lovable Dashboard:
a headline, position/signal summaries, sector/strategy concentration warnings,
and recommended actions. Aggregates deterministic counts first (which are also
the graceful-fallback payload), then asks Claude for the narrative on top.
"""

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_ai import _claude

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the PRIME AI Briefing Analyst. Given a portfolio
snapshot (open positions, today's signal quality, DK dark-pool activity, and
concentration metrics), write a concise trading-desk briefing.

DK three-state context:
  CONFIRMING = institutional dark-pool buying aligns with signal direction.
  NEUTRAL    = no significant dark-pool activity.
  NULLIFYING = institutional dark-pool selling works against the signal.
Flag open positions with NULLIFYING DK as elevated risk.

Respond ONLY with a JSON object, no prose outside it:
{"headline": "one punchy sentence",
 "positions_summary": "one sentence",
 "signals_summary": "one sentence",
 "dk_summary": "DK: N confirming, N neutral, N nullifying today",
 "concentration_warnings": [str, ...],
 "recommended_actions": [str, ...]}"""


def _aggregate(db_path: Optional[Path]) -> Dict[str, Any]:
    """Deterministic portfolio snapshot used both for the prompt and fallback.

    Sprint 26 Item 1: positions are enriched so SCHWAB_IMPORT entries have
    realistic unrealized_pnl_pct and hold_minutes in the briefing context.
    """
    from prime_data.prime_db import get_open_positions
    from prime_analytics.prime_signals_db import get_signals
    from prime_api.prime_positions import enrich_position

    raw_positions = get_open_positions(db_path=db_path)
    positions = [enrich_position(p, current_price=None) for p in raw_positions]
    signals = get_signals(limit=500, db_path=db_path)

    today = datetime.now().strftime("%Y-%m-%d")
    todays = [s for s in signals if str(s.get("scan_ts", "")).startswith(today)] or signals

    tiers = Counter((s.get("tier") or "").upper() for s in todays)
    # Sprint 20 Item 4: three-state DK counts (PENDING/CONFIRMED/NULLIFIED retired).
    dk_counts = Counter((s.get("dk_status") or "NEUTRAL").upper() for s in todays)

    # Flag open positions carrying a NULLIFYING DK verdict (elevated risk).
    nullifying_positions = [
        p.get("symbol") for p in positions
        if (p.get("dk_status") or "NEUTRAL").upper() == "NULLIFYING"
    ]

    strat_counts = Counter(p.get("strategy") for p in positions)
    warnings: List[str] = []
    total = len(positions)
    for strat, n in strat_counts.items():
        if total and n / total >= 0.5 and total >= 2:
            warnings.append(f"{strat} is {round(n / total * 100)}% of open positions ({n}/{total})")
    if nullifying_positions:
        warnings.append("DK NULLIFYING on open positions: " + ", ".join(nullifying_positions))

    return {
        "open_position_count": total,
        "positions": [
            {"symbol": p.get("symbol"), "strategy": p.get("strategy"),
             "trade_source": p.get("trade_source", "PAPER"),
             "shares": p.get("shares"), "entry_price": p.get("entry_price"),
             "current_price": p.get("current_price"),
             "unrealized_pnl_pct": p.get("unrealized_pnl_pct"),
             "hold_minutes": p.get("hold_minutes"),
             "dk_status": p.get("dk_status") or "NEUTRAL",
             "dk_conviction": p.get("dk_conviction")}
            for p in positions
        ],
        "signal_quality": {
            "strong": tiers.get("STRONG", 0),
            "watch": tiers.get("WATCH", 0),
            "nullifying": dk_counts.get("NULLIFYING", 0),
            "total_today": len(todays),
        },
        "dk_activity": {
            "confirming": dk_counts.get("CONFIRMING", 0),
            "neutral": dk_counts.get("NEUTRAL", 0),
            "nullifying": dk_counts.get("NULLIFYING", 0),
        },
        "concentration_warnings": warnings,
    }


def _fallback(snapshot: Dict[str, Any], reason: str) -> Dict[str, Any]:
    n = snapshot["open_position_count"]
    sq = snapshot["signal_quality"]
    dk = snapshot.get("dk_activity", {})
    if n == 0 and sq["total_today"] == 0:
        headline = "No active positions or signals yet -- run a scan to populate the briefing."
    else:
        headline = f"{n} open position(s); {sq['strong']} strong / {sq['watch']} watch signals today."
    # Sprint 20 Item 4: include DK summary in fallback (graceful degradation).
    dk_summary = ("DK: {confirming} confirming, {neutral} neutral, "
                  "{nullifying} nullifying today").format(
        confirming=dk.get("confirming", 0),
        neutral=dk.get("neutral", 0),
        nullifying=dk.get("nullifying", 0),
    )
    return {
        "headline": headline,
        "positions_summary": f"{n} open position(s).",
        "signals_summary": (f"{sq['strong']} STRONG, {sq['watch']} WATCH, "
                            f"{sq['nullifying']} nullifying today."),
        "dk_summary": dk_summary,
        "concentration_warnings": snapshot["concentration_warnings"],
        "recommended_actions": [],
        "_fallback": True,
        "_fallback_reason": reason,
    }


def generate_briefing(
    db_path: Optional[Path] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Produce the dashboard briefing. Never raises; falls back deterministically."""
    snapshot = _aggregate(db_path)
    if api_key is None:
        api_key = _claude.get_api_key()

    prompt = ("Write the briefing JSON for this portfolio snapshot:\n"
              + json.dumps(snapshot, indent=2, default=str))
    try:
        text = _claude.call_claude(SYSTEM_PROMPT, prompt, api_key=api_key)
        data = _claude.parse_json(text)
        if not isinstance(data, dict) or "headline" not in data:
            return _fallback(snapshot, "malformed briefing")
        # Ensure all expected fields exist; backfill from the snapshot.
        data.setdefault("concentration_warnings", snapshot["concentration_warnings"])
        data.setdefault("recommended_actions", [])
        # Sprint 20 Item 4: backfill dk_summary from snapshot when Claude omits it.
        if "dk_summary" not in data:
            dk = snapshot.get("dk_activity", {})
            data["dk_summary"] = ("DK: {confirming} confirming, {neutral} neutral, "
                                  "{nullifying} nullifying today").format(
                confirming=dk.get("confirming", 0),
                neutral=dk.get("neutral", 0),
                nullifying=dk.get("nullifying", 0),
            )
        data["_fallback"] = False
        data["snapshot"] = snapshot
        return data
    except Exception as e:
        logger.warning("briefing generation failed: %s", e)
        out = _fallback(snapshot, str(e))
        out["snapshot"] = snapshot
        return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    b = generate_briefing()
    print(b["headline"])
    for a in b.get("recommended_actions", []):
        print(" -", a)
