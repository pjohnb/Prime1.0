"""
PRIME v1.0 Single-Name Equity Options (OPT-001).

Directional calls/puts on UOA Tier 1 single-name signals.
American-style -- early assignment risk must be flagged.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from prime_options.prime_options_pricer import black_scholes

logger = logging.getLogger(__name__)

TARGET_DELTA = 0.35  # slightly lower than index (more room for single-name vol)
TARGET_DTE_RANGE = (21, 45)
TIME_STOP_DTE = 10  # longer than index given single-name vol
MAX_LOSS_PCT = 0.0075  # 0.75% of portfolio (tighter than index)


def select_directional_leg(
    chain: List[Dict[str, Any]],
    direction: str,
    target_delta: float = TARGET_DELTA,
) -> Optional[Dict[str, Any]]:
    """Select single leg closest to target delta for single-name."""
    option_type = "CALL" if direction == "LONG" else "PUT"
    candidates = [c for c in chain if c.get("option_type", "").upper() == option_type]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(abs(c.get("delta", 0)) - target_delta))


def check_early_assignment_risk(
    option_type: str,
    strike: float,
    spot: float,
    dte: int,
    ex_div_dte: Optional[int] = None,
) -> Dict[str, Any]:
    """Check early assignment risk for American-style equity options.

    Flag if ITM + DTE < 14 + ex-dividend within DTE window.
    """
    is_itm = (option_type.upper() == "CALL" and spot > strike) or \
             (option_type.upper() == "PUT" and spot < strike)

    ex_div_risk = ex_div_dte is not None and 0 < ex_div_dte <= dte

    if is_itm and dte < 14 and ex_div_risk:
        return {
            "risk": True,
            "flag": "ASSIGNMENT_RISK",
            "reason": f"ITM ({option_type} strike={strike}, spot={spot:.2f}), "
                      f"DTE={dte}<14, ex-div in {ex_div_dte}d",
        }

    if is_itm and dte < 14:
        return {
            "risk": True,
            "flag": "ASSIGNMENT_WATCH",
            "reason": f"ITM + DTE={dte}<14 -- monitor for early exercise",
        }

    return {"risk": False, "flag": "", "reason": ""}


def score_equity_option_signal(
    symbol: str,
    direction: str,
    uoa_score: float,
    chain: List[Dict[str, Any]],
    spot_price: float,
    portfolio_value: float = 100_000.0,
    ex_div_dte: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Score a single-name equity option signal.

    Directional only this sprint -- no spreads.
    Returns signal dict or None if no valid leg.
    """
    leg = select_directional_leg(chain, direction)
    if not leg:
        return None

    dte = leg.get("dte", 30)
    strike = leg.get("strike", spot_price)
    option_type = leg.get("option_type", "CALL")

    # Compute Greeks if not provided
    if "delta" not in leg or leg.get("delta") is None:
        greeks = black_scholes(
            spot=spot_price,
            strike=strike,
            dte=dte,
            iv=leg.get("iv", 0.30),
            option_type=option_type,
        )
        leg.update(greeks)

    # Early assignment check
    assignment = check_early_assignment_risk(option_type, strike, spot_price, dte, ex_div_dte)

    max_loss = leg.get("ask", 0) * 100
    max_contracts = max(1, int((portfolio_value * MAX_LOSS_PCT) / max_loss)) if max_loss > 0 else 1

    legs_json = json.dumps([{
        "contract": leg.get("contract_symbol", ""),
        "strike": strike,
        "option_type": option_type,
        "dte": dte,
        "delta": leg.get("delta", 0),
        "theta": leg.get("theta", 0),
        "bid": leg.get("bid", 0),
        "ask": leg.get("ask", 0),
    }])

    return {
        "symbol": symbol,
        "strategy": "EQUITY_OPT_DIRECTIONAL",
        "direction": direction,
        "strategy_type": "DIRECTIONAL",
        "legs": [leg],
        "legs_json": legs_json,
        "max_loss": round(max_loss, 2),
        "max_contracts": max_contracts,
        "dte_at_entry": dte,
        "time_stop_dte": TIME_STOP_DTE,
        "instrument_type": "EQUITY_OPTION",
        "score": uoa_score,
        "assignment_risk": assignment,
        "greeks": {
            "delta": leg.get("delta", 0),
            "theta": leg.get("theta", 0),
            "gamma": leg.get("gamma", 0),
            "vega": leg.get("vega", 0),
        },
    }


def check_dte_time_stop(current_dte: int) -> bool:
    """Returns True if position should be closed due to DTE time stop (10 DTE)."""
    return current_dte <= TIME_STOP_DTE
