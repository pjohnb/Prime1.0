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


def _schwab_locate(client: Any, symbol: str) -> Dict[str, Any]:
    """Call the Schwab locate/availability endpoint via an authenticated client.

    Returns {borrowable, rate_pct}. Raises if the endpoint is unavailable or the
    response cannot be interpreted -- the caller maps any raise to borrowable=
    False (fail-safe). schwab-py has no stable public locate method, so we probe
    for a locate-style attribute on the SchwabClient or its underlying client;
    when absent, we raise (fail-safe) rather than assume borrow.
    """
    raw = getattr(client, "client", None)
    locate_fn = (getattr(client, "get_locate", None)
                 or getattr(client, "check_borrow", None)
                 or (getattr(raw, "get_locate", None) if raw else None))
    if locate_fn is None:
        raise RuntimeError("Schwab locate endpoint not available in client")
    resp = locate_fn(symbol)
    data = resp.json() if hasattr(resp, "json") else resp
    if not isinstance(data, dict):
        raise RuntimeError("unexpected Schwab locate response")
    borrowable = bool(data.get("borrowable", data.get("available", False)))
    rate = data.get("rate_pct", data.get("borrowRate"))
    return {"borrowable": borrowable, "rate_pct": rate}


def _schwab_borrow_lookup(symbol: str) -> Dict[str, Any]:
    """Live borrow/locate lookup via the authenticated SchwabClient (Sprint 18
    Item 2). Connects Schwab and calls the locate endpoint; any failure raises so
    the caller fails safe to borrowable=False. Never assumes borrow.
    """
    from pathlib import Path
    from prime_config.prime_config import get_config

    token_path = get_config().schwab_snapshot.schwab_token_path
    if not token_path or not Path(token_path).exists():
        raise RuntimeError("Schwab token cache absent -- cannot confirm borrow")

    from prime_trading.prime_schwab import SchwabClient
    client = SchwabClient()
    client.connect()
    return _schwab_locate(client, symbol)


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
