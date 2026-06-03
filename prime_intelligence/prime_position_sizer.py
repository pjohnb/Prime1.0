"""
PRIME v1.0 Position Sizer (Sprint 17 Item 2).

Short-side position sizing with tighter risk rules, plus the inverse (+5%)
short stop and short time stop. Pure/injectable so it is fully testable offline
(no Schwab calls in tests). Non-negotiable short risk constraints:

  * short size = short_size_multiplier (default 0.5) x equivalent long size;
  * hard cap at SHORT_MAX_POSITION_PCT (2%) of account value per short position;
  * short stop = current_price >= entry_price * (1 + short_stop_loss_pct) (+5%);
  * short time stop mirrors the long time stop (default 1950 min).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("prime_position_sizer")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Standard long sizing: target notional = account_value * LONG_POSITION_PCT.
LONG_POSITION_PCT = 0.04
# Hard cap for a single short position, regardless of multiplier.
SHORT_MAX_POSITION_PCT = 0.02

DEFAULT_SHORT_SIZE_MULTIPLIER = 0.5
DEFAULT_SHORT_STOP_LOSS_PCT = 0.05
DEFAULT_SHORT_TIME_STOP_MIN = 1950


def _read_short_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read short-side params from ops_config.json (runtime, no restart)."""
    if config_path is None:
        config_path = _PROJECT_ROOT / "ops_config.json"
    cfg = {
        "short_size_multiplier": DEFAULT_SHORT_SIZE_MULTIPLIER,
        "short_stop_loss_pct": DEFAULT_SHORT_STOP_LOSS_PCT,
        "short_time_stop_minutes": DEFAULT_SHORT_TIME_STOP_MIN,
    }
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            for k in cfg:
                if k in data and data[k] is not None:
                    cfg[k] = data[k]
    except Exception:
        pass
    return cfg


def calculate_long_size(account_value: float, price: float,
                        long_position_pct: float = LONG_POSITION_PCT) -> int:
    """Standard long sizing: shares for a target notional of account_value*pct."""
    if not account_value or not price or price <= 0:
        return 0
    notional = account_value * long_position_pct
    return int(notional // price)


def calculate_short_size(
    symbol: str,
    account: Optional[str],
    price: float,
    account_value: float = 0.0,
    buying_power: Optional[float] = None,
    short_size_multiplier: Optional[float] = None,
    long_position_pct: float = LONG_POSITION_PCT,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compute short position size = multiplier x equivalent long, capped at 2%.

    Returns {symbol, account, shares, notional, multiplier, capped, reason}.
    buying_power, when supplied, caps the long-equivalent notional (cannot size
    beyond available buying power). account_value drives the 2% hard cap.
    """
    if short_size_multiplier is None:
        short_size_multiplier = _read_short_config(config_path)["short_size_multiplier"]

    result = {"symbol": (symbol or "").upper(), "account": account, "shares": 0,
              "notional": 0.0, "multiplier": short_size_multiplier,
              "capped": False, "reason": ""}
    if not price or price <= 0 or account_value <= 0:
        result["reason"] = "invalid price or account_value"
        return result

    equivalent_long_notional = account_value * long_position_pct
    if buying_power is not None:
        equivalent_long_notional = min(equivalent_long_notional, max(buying_power, 0.0))

    short_notional = equivalent_long_notional * short_size_multiplier
    cap_notional = account_value * SHORT_MAX_POSITION_PCT
    if short_notional > cap_notional:
        short_notional = cap_notional
        result["capped"] = True
        result["reason"] = "hard-capped at {0:.0%} of account".format(SHORT_MAX_POSITION_PCT)

    shares = int(short_notional // price)
    result["shares"] = shares
    result["notional"] = round(shares * price, 2)
    return result


# ---------------------------------------------------------------------------
# Short stop / exit logic (inverse of the long -5% stop)
# ---------------------------------------------------------------------------

def short_stop_price(entry_price: float,
                     short_stop_loss_pct: float = DEFAULT_SHORT_STOP_LOSS_PCT) -> float:
    """Short stop sits ABOVE entry: entry * (1 + pct). Default +5%."""
    return round(float(entry_price or 0) * (1 + float(short_stop_loss_pct)), 4)


def short_stop_triggered(entry_price: float, current_price: float,
                         short_stop_loss_pct: float = DEFAULT_SHORT_STOP_LOSS_PCT) -> bool:
    """True when price has RISEN to/above the short stop (+5% adverse move)."""
    if not entry_price or entry_price <= 0 or not current_price:
        return False
    return current_price >= short_stop_price(entry_price, short_stop_loss_pct)


def evaluate_short_exit(
    entry_price: float,
    current_price: Optional[float],
    hold_minutes: int,
    short_stop_loss_pct: Optional[float] = None,
    short_time_stop_minutes: Optional[int] = None,
    take_profit_pct: float = 0.10,
    config_path: Optional[Path] = None,
) -> Tuple[bool, Optional[str], str]:
    """Short exit evaluation. Returns (should_close, trigger, reason).

    stop_loss: price rises >= +5% above entry (adverse for a short).
    take_profit: price falls >= 10% below entry (favourable for a short).
    time_stop: held >= short_time_stop_minutes.
    """
    cfg = _read_short_config(config_path)
    if short_stop_loss_pct is None:
        short_stop_loss_pct = cfg["short_stop_loss_pct"]
    if short_time_stop_minutes is None:
        short_time_stop_minutes = cfg["short_time_stop_minutes"]

    if current_price is None or not entry_price or entry_price <= 0:
        return False, None, "no current quote -- hold"

    move_pct = (current_price - entry_price) / entry_price  # +ve = price up = adverse
    if move_pct >= short_stop_loss_pct:
        return True, "short_stop_loss", "price {0:+.2%} >= +{1:.0%} short stop".format(
            move_pct, short_stop_loss_pct)
    if move_pct <= -take_profit_pct:
        return True, "short_take_profit", "price {0:+.2%} hit short target".format(move_pct)
    if hold_minutes >= short_time_stop_minutes:
        return True, "time_stop", "held {0}min -- time stop".format(hold_minutes)
    return False, None, "price {0:+.2%} hold {1}min -- hold".format(move_pct, hold_minutes)
