"""
PRIME v1.0 Index Options Scanner (IDX-OPT-001).

Directional calls/puts + defined-risk vertical spreads on SPY/QQQ/IWM.
European-style (cash-settled, no assignment risk).
DTE-based time stop mandatory: close at 7 DTE regardless of P&L.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_options.prime_options_pricer import black_scholes

logger = logging.getLogger(__name__)

TARGET_DTE_RANGE = (21, 35)
TIME_STOP_DTE = 7
TARGET_DELTA_DIRECTIONAL = 0.40
SPREAD_WIDTH = 5
MAX_LOSS_PCT = 0.01  # 1% of portfolio


def get_options_chain(
    symbol: str,
    dte_range: tuple = TARGET_DTE_RANGE,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Fetch options chain from Schwab API.

    Returns list of {strike, dte, iv, bid, ask, option_type, contract_symbol}.
    If unavailable, returns empty list (per tiebreaker: skip symbol, log warning).
    """
    if client is None:
        logger.debug("No Schwab client -- options chain unavailable for %s", symbol)
        return []

    try:
        chain = client.get_options_chain(symbol, dte_range=dte_range)
        return chain if chain else []
    except Exception as e:
        logger.warning("Options chain unavailable for %s: %s -- skipping", symbol, e)
        return []


def select_directional_leg(
    chain: List[Dict[str, Any]],
    direction: str,
    target_delta: float = TARGET_DELTA_DIRECTIONAL,
) -> Optional[Dict[str, Any]]:
    """Select a single leg closest to target delta.

    LONG direction -> CALL; SHORT direction -> PUT.
    """
    option_type = "CALL" if direction == "LONG" else "PUT"
    candidates = [c for c in chain if c.get("option_type", "").upper() == option_type]

    if not candidates:
        return None

    best = min(candidates, key=lambda c: abs(abs(c.get("delta", 0)) - target_delta))
    return best


def select_spread_legs(
    chain: List[Dict[str, Any]],
    direction: str,
    width: int = SPREAD_WIDTH,
) -> Optional[List[Dict[str, Any]]]:
    """Select vertical spread legs ($5 wide).

    LONG direction -> bull call spread; SHORT -> bear put spread.
    """
    long_leg = select_directional_leg(chain, direction)
    if not long_leg:
        return None

    long_strike = long_leg.get("strike", 0)
    option_type = "CALL" if direction == "LONG" else "PUT"
    candidates = [c for c in chain
                  if c.get("option_type", "").upper() == option_type]

    if direction == "LONG":
        short_strike_target = long_strike + width
    else:
        short_strike_target = long_strike - width

    short_candidates = [c for c in candidates
                        if abs(c.get("strike", 0) - short_strike_target) <= 1]
    if not short_candidates:
        return None

    short_leg = min(short_candidates,
                    key=lambda c: abs(c.get("strike", 0) - short_strike_target))
    return [long_leg, short_leg]


def score_option_signal(
    symbol: str,
    direction: str,
    uoa_score: float,
    chain: List[Dict[str, Any]],
    spot_price: float,
    portfolio_value: float = 100_000.0,
) -> Optional[Dict[str, Any]]:
    """Score an index option signal. Strategy selection based on UOA score.

    score >= 80 -> spread (defined risk); 60-79 -> directional only.
    Returns signal dict or None if no valid leg found.
    """
    if uoa_score >= 80:
        strategy_type = "SPREAD"
        legs = select_spread_legs(chain, direction)
        if not legs:
            legs_data = select_directional_leg(chain, direction)
            if legs_data:
                legs = [legs_data]
                strategy_type = "DIRECTIONAL"
            else:
                return None
    else:
        strategy_type = "DIRECTIONAL"
        leg = select_directional_leg(chain, direction)
        if not leg:
            return None
        legs = [leg]

    # Compute max loss
    if strategy_type == "SPREAD" and len(legs) == 2:
        debit = abs(legs[0].get("ask", 0) - legs[1].get("bid", 0))
        max_loss = debit * 100  # per contract
    else:
        max_loss = legs[0].get("ask", 0) * 100

    # Position sizing: max_loss <= 1% of portfolio
    max_contracts = max(1, int((portfolio_value * MAX_LOSS_PCT) / max_loss)) if max_loss > 0 else 1

    dte = legs[0].get("dte", 30)

    # Compute Greeks via pricer if not provided
    for leg in legs:
        if "delta" not in leg or leg.get("delta") is None:
            greeks = black_scholes(
                spot=spot_price,
                strike=leg.get("strike", spot_price),
                dte=dte,
                iv=leg.get("iv", 0.25),
                option_type=leg.get("option_type", "CALL"),
            )
            leg.update(greeks)

    legs_json = json.dumps([{
        "contract": l.get("contract_symbol", ""),
        "strike": l.get("strike", 0),
        "option_type": l.get("option_type", ""),
        "dte": l.get("dte", 0),
        "delta": l.get("delta", 0),
        "theta": l.get("theta", 0),
        "bid": l.get("bid", 0),
        "ask": l.get("ask", 0),
    } for l in legs])

    return {
        "symbol": symbol,
        "strategy": f"IDX_OPT_{strategy_type}",
        "direction": direction,
        "strategy_type": strategy_type,
        "legs": legs,
        "legs_json": legs_json,
        "max_loss": round(max_loss, 2),
        "max_contracts": max_contracts,
        "dte_at_entry": dte,
        "time_stop_dte": TIME_STOP_DTE,
        "breakeven": _calc_breakeven(legs, direction, strategy_type),
        "instrument_type": "INDEX_OPTION",
        "score": uoa_score,
    }


def _calc_breakeven(legs, direction, strategy_type):
    if not legs:
        return 0.0
    strike = legs[0].get("strike", 0)
    premium = legs[0].get("ask", 0)
    if strategy_type == "SPREAD" and len(legs) == 2:
        premium = abs(legs[0].get("ask", 0) - legs[1].get("bid", 0))
    if direction == "LONG":
        return round(strike + premium, 2)
    return round(strike - premium, 2)


def check_dte_time_stop(current_dte: int) -> bool:
    """Returns True if position should be closed due to DTE time stop."""
    return current_dte <= TIME_STOP_DTE
