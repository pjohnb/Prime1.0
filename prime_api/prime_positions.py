"""
PRIME v1.0 Position management helpers (Sprint 16 Item 5).

Pure, side-effect-free helpers that enrich open positions for the Lovable UI
Positions tab: unrealized P&L (direction-aware), stop alert badges
(GREEN/AMBER/RED), and human-readable hold time with a time-stop highlight.

No DB access here -- the route layer fetches positions via prime_db.py and the
UI renders the enriched fields. Defaults: stop_loss_pct=-5%, time_stop=1950 min.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_STOP_LOSS_PCT = -5.0      # percent move against entry that defines the stop
DEFAULT_TIME_STOP_MIN = 1950      # minutes held after which a position is flagged
STOP_AMBER_BAND = 0.01            # within 1% of the stop price -> AMBER

# Sprint 17 Item 5: short positions use a +5% stop (price rising = adverse).
DEFAULT_SHORT_STOP_LOSS_PCT = 0.05  # fraction above entry (inverse of long -5%)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_short_params(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read short_stop_loss_pct / short_time_stop_minutes from ops_config.json."""
    if config_path is None:
        config_path = _PROJECT_ROOT / "ops_config.json"
    out = {"short_stop_loss_pct": DEFAULT_SHORT_STOP_LOSS_PCT,
           "short_time_stop_minutes": DEFAULT_TIME_STOP_MIN}
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            for k in out:
                if data.get(k) is not None:
                    out[k] = data[k]
    except Exception:
        pass
    return out


def short_stop_price(entry_price: float,
                     short_stop_loss_pct: float = DEFAULT_SHORT_STOP_LOSS_PCT) -> float:
    """Short stop sits ABOVE entry: entry * (1 + pct). Default +5%."""
    return round(float(entry_price or 0) * (1 + float(short_stop_loss_pct)), 4)


def short_stop_badge(entry_price: float, current_price: float,
                     short_stop_loss_pct: float = DEFAULT_SHORT_STOP_LOSS_PCT) -> str:
    """Short stop alert: RED (price at/above entry*1.05), AMBER (within 1% of the
    stop), else GREEN (price below / comfortably away from the stop)."""
    entry_price = float(entry_price or 0)
    current_price = float(current_price or 0)
    if entry_price <= 0 or current_price <= 0:
        return "GREEN"
    stop = short_stop_price(entry_price, short_stop_loss_pct)
    if current_price >= stop:
        return "RED"
    if current_price >= stop * (1 - STOP_AMBER_BAND):
        return "AMBER"
    return "GREEN"


def compute_pnl(entry_price: float, current_price: float, shares: float,
                direction: str = "LONG") -> Dict[str, Any]:
    """Direction-aware unrealized P&L. Returns {pnl_dollars, pnl_pct, color}."""
    entry_price = float(entry_price or 0)
    current_price = float(current_price or 0)
    shares = float(shares or 0)
    if (direction or "LONG").upper() == "SHORT":
        pnl_dollars = (entry_price - current_price) * shares
    else:
        pnl_dollars = (current_price - entry_price) * shares
    pnl_pct = (pnl_dollars / (entry_price * shares) * 100.0) if entry_price and shares else 0.0
    color = "green" if pnl_dollars > 0 else ("red" if pnl_dollars < 0 else "flat")
    return {"pnl_dollars": round(pnl_dollars, 2), "pnl_pct": round(pnl_pct, 2),
            "color": color}


def compute_stop_price(entry_price: float, stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
                       direction: str = "LONG") -> float:
    """Stop price from entry and stop_loss_pct (negative = adverse move).

    LONG  stop sits below entry: entry * (1 + pct/100).
    SHORT stop sits above entry: entry * (1 - pct/100).
    """
    entry_price = float(entry_price or 0)
    pct = float(stop_loss_pct)
    if (direction or "LONG").upper() == "SHORT":
        return round(entry_price * (1 - pct / 100.0), 4)
    return round(entry_price * (1 + pct / 100.0), 4)


def stop_badge(entry_price: float, current_price: float, direction: str = "LONG",
               stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT) -> str:
    """Stop alert: 'RED' (breached), 'AMBER' (within 1% of stop), else 'GREEN'."""
    entry_price = float(entry_price or 0)
    current_price = float(current_price or 0)
    if entry_price <= 0 or current_price <= 0:
        return "GREEN"
    stop = compute_stop_price(entry_price, stop_loss_pct, direction)
    if (direction or "LONG").upper() == "SHORT":
        if current_price >= stop:
            return "RED"
        if current_price >= stop * (1 - STOP_AMBER_BAND):
            return "AMBER"
        return "GREEN"
    # LONG
    if current_price <= stop:
        return "RED"
    if current_price <= stop * (1 + STOP_AMBER_BAND):
        return "AMBER"
    return "GREEN"


def format_hold_time(entry_time: Optional[str], now: Optional[datetime] = None) -> str:
    """Human-readable hold time, e.g. '2d 4h', '5h 12m', '7m'. '--' if unknown."""
    mins = hold_minutes(entry_time, now)
    if mins is None:
        return "--"
    days, rem = divmod(mins, 1440)
    hours, minutes = divmod(rem, 60)
    if days > 0:
        return "{0}d {1}h".format(days, hours)
    if hours > 0:
        return "{0}h {1}m".format(hours, minutes)
    return "{0}m".format(minutes)


def hold_minutes(entry_time: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    """Whole minutes a position has been held. None if entry_time unparseable."""
    if not entry_time:
        return None
    try:
        start = datetime.fromisoformat(str(entry_time))
    except (TypeError, ValueError):
        return None
    now = now or datetime.now()
    return max(int((now - start).total_seconds() // 60), 0)


def enrich_position(position: Dict[str, Any], current_price: Optional[float] = None,
                    now: Optional[datetime] = None,
                    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
                    time_stop_min: int = DEFAULT_TIME_STOP_MIN,
                    config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return a copy of `position` with P&L, stop badge, and hold-time fields.

    Direction-aware (Sprint 17 Item 5): SHORT positions use inverse P&L, a +5%
    short stop (price rising = adverse), the short time stop, and the short stop
    badge. current_price falls back to the last known price when no live quote
    is supplied.
    """
    out = dict(position)
    entry = position.get("entry_price") or position.get("price_at_scan") or 0.0
    last_known = position.get("entry_price") or position.get("price_at_scan") or 0.0
    price = current_price if current_price else last_known
    direction = (position.get("direction") or "LONG").upper()
    shares = position.get("shares", 0)

    pnl = compute_pnl(entry, price, shares, direction)
    held = hold_minutes(position.get("entry_time"), now)

    if direction == "SHORT":
        sp = _read_short_params(config_path)
        short_pct = sp["short_stop_loss_pct"]
        out["stop_price"] = short_stop_price(entry, short_pct)
        out["stop_badge"] = short_stop_badge(entry, price, short_pct)
        effective_time_stop = sp["short_time_stop_minutes"]
    else:
        out["stop_price"] = compute_stop_price(entry, stop_loss_pct, direction)
        out["stop_badge"] = stop_badge(entry, price, direction, stop_loss_pct)
        effective_time_stop = time_stop_min

    out["current_price"] = round(float(price), 4) if price else 0.0
    out["unrealized_pnl"] = pnl["pnl_dollars"]
    out["unrealized_pnl_pct"] = pnl["pnl_pct"]
    out["pnl_color"] = pnl["color"]
    out["direction"] = direction
    out["hold_time"] = format_hold_time(position.get("entry_time"), now)
    out["hold_minutes"] = held if held is not None else 0
    out["time_stop_min"] = effective_time_stop
    out["time_stop_exceeded"] = bool(held is not None and held >= effective_time_stop)
    # Sprint 22 Item 2: DK status per position (graceful: NEUTRAL when unavailable).
    try:
        from prime_intelligence.prime_dk_trader import get_dk_status
        dk = get_dk_status(position.get("symbol") or "")
        out["dk_status"] = dk["dk_status"]
        out["dk_conviction"] = dk["dk_conviction"]
    except Exception:
        out.setdefault("dk_status", "NEUTRAL")
        out.setdefault("dk_conviction", None)
    return out
