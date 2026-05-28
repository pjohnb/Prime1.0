"""
PRIME v1.0 Index UOA Scanner (CIL-PRIME-IDX-001).

SPY/QQQ/IWM directional trades with index-calibrated Tier thresholds.
Thresholds are higher than single-name UOA due to index volume norms.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

INDEX_WATCHLIST = ["SPY", "QQQ", "IWM"]

# Index-calibrated Tier thresholds (pre-decided tiebreaker)
INDEX_TIERS = {
    "SPY": {"tier1_vol": 200_000, "tier1_sizzle": 3.0, "tier2_vol": 100_000, "tier2_sizzle": 2.0},
    "QQQ": {"tier1_vol": 200_000, "tier1_sizzle": 3.0, "tier2_vol": 100_000, "tier2_sizzle": 2.0},
    "IWM": {"tier1_vol": 100_000, "tier1_sizzle": 3.0, "tier2_vol": 50_000, "tier2_sizzle": 2.0},
}

# Direction thresholds
CP_RATIO_LONG = 1.3
CP_RATIO_SHORT = 0.77

# A-B-C-D factor set for index ETFs
INDEX_EXIT_TARGETS = {
    "SPY": {"target_pct": 1.5, "stop_pct": 0.75},
    "QQQ": {"target_pct": 1.5, "stop_pct": 0.75},
    "IWM": {"target_pct": 2.0, "stop_pct": 1.0},
}


def classify_tier(
    symbol: str,
    option_volume: int,
    sizzle: float,
) -> Optional[str]:
    """Classify index signal into Tier 1, Tier 2, or None."""
    thresholds = INDEX_TIERS.get(symbol.upper())
    if not thresholds:
        return None

    if option_volume >= thresholds["tier1_vol"] and sizzle >= thresholds["tier1_sizzle"]:
        return "TIER_1"
    if option_volume >= thresholds["tier2_vol"] and sizzle >= thresholds["tier2_sizzle"]:
        return "TIER_2"
    return None


def classify_direction(call_put_ratio: float) -> str:
    """Determine direction from call/put ratio."""
    if call_put_ratio > CP_RATIO_LONG:
        return "LONG"
    if call_put_ratio < CP_RATIO_SHORT:
        return "SHORT"
    return "NEUTRAL"


def build_index_factors(
    symbol: str,
    direction: str,
    tier: str,
    price: float,
) -> Dict[str, Any]:
    """Build A-B-C-D factor set for an index ETF signal."""
    exits = INDEX_EXIT_TARGETS.get(symbol.upper(), {"target_pct": 1.5, "stop_pct": 0.75})

    target_price = round(price * (1 + exits["target_pct"] / 100), 2) if direction == "LONG" else \
                   round(price * (1 - exits["target_pct"] / 100), 2)
    stop_price = round(price * (1 - exits["stop_pct"] / 100), 2) if direction == "LONG" else \
                 round(price * (1 + exits["stop_pct"] / 100), 2)

    return {
        "duration": {"class": "ST", "confidence": "HIGH",
                     "rationale": "Index ETF: SHORT_TERM only (1-5 days)"},
        "entry": {"method": "IMMEDIATE_FULL", "trigger": "",
                  "rationale": "Index ETF: single tranche only"},
        "exit_triggers": [
            {"type": "PRICE_TARGET", "status": "ARMED",
             "value": str(target_price),
             "description": f"{exits['target_pct']}% target at ${target_price}"},
            {"type": "STOP_LOSS", "status": "ARMED",
             "value": str(stop_price),
             "description": f"{exits['stop_pct']}% stop at ${stop_price}"},
            {"type": "TIME_STOP", "status": "ARMED",
             "value": "5 trading days",
             "description": "Index ETF time stop: 5 trading days"},
        ],
        "nullifier": {"status": "CLEAR", "flags": [], "rationale": ""},
        "maintenance_flags": [
            "Monitor VIX for regime shift",
            f"Index ETF {tier} signal -- {direction}",
        ],
    }


def scan_index_uoa(
    market_data: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Scan index watchlist for UOA signals.

    market_data: list of {symbol, option_volume, sizzle, call_put_ratio, price}
    If None, returns empty (no live data).
    """
    if not market_data:
        return []

    signals = []
    for data in market_data:
        symbol = data.get("symbol", "").upper()
        if symbol not in INDEX_WATCHLIST:
            continue

        volume = data.get("option_volume", 0)
        sizzle = data.get("sizzle", 0.0)
        cp_ratio = data.get("call_put_ratio", 1.0)
        price = data.get("price", 0.0)

        tier = classify_tier(symbol, volume, sizzle)
        if tier is None:
            continue

        direction = classify_direction(cp_ratio)
        if direction == "NEUTRAL":
            continue

        score = 80.0 if tier == "TIER_1" else 65.0
        factors = build_index_factors(symbol, direction, tier, price)

        signals.append({
            "symbol": symbol,
            "strategy": "UOA_INDEX",
            "direction": direction,
            "tier": tier,
            "score": score,
            "price_at_scan": price,
            "option_volume": volume,
            "sizzle": sizzle,
            "call_put_ratio": cp_ratio,
            "factors": factors,
            "instrument_type": "INDEX_ETF",
        })

    return signals
