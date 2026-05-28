"""
PRIME v1.0 Open Position Review (Sprint 13 Item 1).

Evaluates open positions against stop/exit criteria. Closes positions
where criteria met via close_trade_with_fill(). Documents the decision
in prime_trade_log.notes.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_STOP_PCT = 0.05  # 5% stop loss
DEFAULT_TARGET_PCT = 0.05  # 5% target
SMA_BREAK_FLAG_PCT = 0.02  # 2% below SMA flags thesis as fragile


def evaluate_position(
    position: Dict[str, Any],
    current_price: float,
    sma_20: Optional[float] = None,
) -> Dict[str, Any]:
    """Evaluate a single open position against stop/exit rules.

    Returns {decision, reason, pnl_pct, action}.
    decision: KEEP | CLOSE | FLAG
    action: HOLD | CLOSE_STOP | CLOSE_TARGET | CLOSE_THESIS_BROKEN
    """
    symbol = position.get("symbol", "???")
    entry_price = position.get("entry_price") or position.get("price_at_scan", 0)
    direction = position.get("direction", "LONG").upper()

    if not entry_price or entry_price <= 0:
        return {
            "symbol": symbol,
            "decision": "FLAG",
            "reason": "No valid entry price",
            "pnl_pct": 0.0,
            "action": "HOLD",
        }

    if direction == "SHORT":
        pnl_pct = (entry_price - current_price) / entry_price
    else:
        pnl_pct = (current_price - entry_price) / entry_price

    # Stop loss check
    if pnl_pct <= -DEFAULT_STOP_PCT:
        return {
            "symbol": symbol,
            "decision": "CLOSE",
            "reason": f"Stop hit: {pnl_pct:.1%} <= -{DEFAULT_STOP_PCT:.0%}",
            "pnl_pct": round(pnl_pct, 4),
            "action": "CLOSE_STOP",
        }

    # Target check
    if pnl_pct >= DEFAULT_TARGET_PCT:
        return {
            "symbol": symbol,
            "decision": "CLOSE",
            "reason": f"Target hit: {pnl_pct:.1%} >= {DEFAULT_TARGET_PCT:.0%}",
            "pnl_pct": round(pnl_pct, 4),
            "action": "CLOSE_TARGET",
        }

    # SMA break thesis check
    if sma_20 is not None and direction == "LONG":
        if current_price < sma_20 * (1 - SMA_BREAK_FLAG_PCT):
            return {
                "symbol": symbol,
                "decision": "FLAG",
                "reason": f"Price ${current_price:.2f} broke below SMA20 ${sma_20:.2f} -- thesis fragile",
                "pnl_pct": round(pnl_pct, 4),
                "action": "HOLD",
            }

    return {
        "symbol": symbol,
        "decision": "KEEP",
        "reason": f"Within bounds: {pnl_pct:.1%}",
        "pnl_pct": round(pnl_pct, 4),
        "action": "HOLD",
    }


def review_positions(
    open_positions: List[Dict[str, Any]],
    price_data: Dict[str, Dict[str, Any]],
    db_path: Optional[Path] = None,
    apply_closes: bool = False,
) -> Dict[str, Any]:
    """Review all open positions. Optionally apply CLOSE actions.

    price_data: dict of {symbol: {current_price, sma_20}}
    apply_closes: if True, call close_trade_with_fill() for CLOSE decisions
    """
    reviews = []
    closes_applied = []
    flags = []

    for pos in open_positions:
        symbol = pos.get("symbol", "???")
        data = price_data.get(symbol, {})
        current_price = data.get("current_price")
        sma_20 = data.get("sma_20")

        if not current_price:
            missing = {
                "symbol": symbol,
                "decision": "FLAG",
                "reason": "No current price data",
                "pnl_pct": 0.0,
                "action": "HOLD",
            }
            reviews.append(missing)
            flags.append(missing)
            continue

        review = evaluate_position(pos, current_price, sma_20)
        reviews.append(review)

        if review["decision"] == "FLAG":
            flags.append(review)

        if apply_closes and review["decision"] == "CLOSE":
            try:
                from prime_data.prime_db import close_trade_with_fill
                result = close_trade_with_fill(
                    log_id=pos["log_id"],
                    fill_price=current_price,
                    fill_qty=pos.get("shares", 0),
                    close_ts=datetime.utcnow().isoformat(),
                    exit_reason=review["action"],
                    db_path=db_path,
                )
                if result:
                    closes_applied.append({
                        "symbol": symbol,
                        "log_id": pos["log_id"],
                        "fill_price": current_price,
                        "realized_pnl": result["realized_pnl"],
                        "action": review["action"],
                    })
            except Exception as e:
                logger.error("Failed to close %s: %s", symbol, e)

    return {
        "reviews": reviews,
        "closes_applied": closes_applied,
        "flags": flags,
        "review_ts": datetime.utcnow().isoformat(),
        "total_reviewed": len(reviews),
        "kept": sum(1 for r in reviews if r["decision"] == "KEEP"),
        "closed": len(closes_applied),
        "flagged": len(flags),
    }
