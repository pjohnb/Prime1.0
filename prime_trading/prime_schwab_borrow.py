"""
PRIME v1.0 Schwab Borrow Availability Check (Sprint 17 Item 3).

Hard gate for short selling: a short candidate that cannot be confirmed
borrowable is dropped before it ever enters prime_signals (Design Principle 1,
"no borrow = no signal").

check_borrow(symbol) -> {symbol, borrowable: bool, rate_pct: float|None,
                         source: "schwab"|"unavailable"}

FAIL-SAFE: any failure (Schwab not connected, API error, unexpected shape) maps
to borrowable=False / source="unavailable". We NEVER assume borrow is available.
A live-feed lookup is injectable (borrow_fn) so tests run without Schwab.
"""

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("prime_schwab_borrow")


def _schwab_borrow_lookup(symbol: str) -> Dict[str, Any]:
    """Best-effort live borrow/locate lookup via Schwab.

    schwab-py does not expose a stable locate endpoint, so this attempts a
    connection and raises if a borrow verdict cannot be obtained -- the caller
    treats any raise as borrowable=False (fail-safe). When Schwab later exposes
    locate data, populate borrowable/rate_pct here behind the same return shape.
    """
    from prime_config.prime_config import get_config
    from pathlib import Path

    token_path = get_config().schwab_snapshot.schwab_token_path
    if not token_path or not Path(token_path).exists():
        raise RuntimeError("Schwab token cache absent -- cannot confirm borrow")

    # No stable Schwab locate API yet -> cannot confirm borrow. Fail safe.
    raise RuntimeError("Schwab borrow/locate lookup not available")


def check_borrow(
    symbol: str,
    borrow_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return borrow availability for `symbol`. Never raises (fail-safe)."""
    sym = (symbol or "").upper()
    fn = borrow_fn or _schwab_borrow_lookup
    try:
        result = fn(sym) or {}
        borrowable = bool(result.get("borrowable"))
        rate = result.get("rate_pct")
        return {
            "symbol": sym,
            "borrowable": borrowable,
            "rate_pct": (rate if borrowable else None),
            "source": "schwab",
        }
    except Exception as e:
        logger.warning("borrow check failed for %s: %s -- fail-safe to not borrowable",
                       sym, e)
        return {"symbol": sym, "borrowable": False, "rate_pct": None,
                "source": "unavailable"}
