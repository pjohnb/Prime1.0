"""
PRIME v1.0 Entry Timing Classifier (ML-Pattern-16).

Classifies each entry as EARLY, ON_TIME, EXHAUSTED, or UNKNOWN
based on momentum profile at entry time.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def classify_entry(
    symbol: str,
    entry_price: float,
    scan_ts: str,
    price_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Classify entry timing from momentum profile.

    price_data: optional dict with {sma_20, rsi, volume_ratio, low_20d}
    If unavailable, returns UNKNOWN per tiebreaker.
    """
    if not price_data or not entry_price or entry_price <= 0:
        return "UNKNOWN"

    sma_20 = price_data.get("sma_20")
    rsi = price_data.get("rsi")
    volume_ratio = price_data.get("volume_ratio", 1.0)
    low_20d = price_data.get("low_20d")

    if sma_20 is None or rsi is None:
        return "UNKNOWN"

    # EXHAUSTED: RSI > 75 OR price > 10% above 20-day SMA
    if rsi > 75:
        return "EXHAUSTED"
    if sma_20 > 0 and entry_price > sma_20 * 1.10:
        return "EXHAUSTED"

    # EARLY: price within 0.5% of 20-day low AND RSI < 35
    if low_20d and low_20d > 0:
        pct_from_low = (entry_price - low_20d) / low_20d
        if pct_from_low <= 0.005 and rsi < 35:
            return "EARLY"

    # ON_TIME: price above 20-day SMA AND RSI 40-65 AND volume > 1.0x avg
    if entry_price > sma_20 and 40 <= rsi <= 65 and volume_ratio > 1.0:
        return "ON_TIME"

    return "UNKNOWN"
