"""
PRIME v1.0 MATA Sell — Unified Multi-Account Sell Allocation (Sprint 24 Item 3).

calculate_sell_allocation() distributes a sell quantity proportionally across
all accounts holding the symbol. Proportional to holdings; rounding surplus
goes to the account with the largest holding.

Example:
  Joint ...7926 holds 20 MSFT, Custodial ...0461 holds 16 MSFT.
  Selling 18 shares: Joint gets 10, Custodial gets 8.
  Ratio: 20/36 * 18 = 10.0, 16/36 * 18 = 8.0 — exact in this case.

Rounding: floor each allocation, then give leftover shares one-at-a-time to
accounts sorted by largest holding (most-shares-first).
"""

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def calculate_sell_allocation(
    symbol: str,
    total_qty: int,
    accounts_holdings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute proportional sell allocation across accounts.

    Parameters
    ----------
    symbol : str
        Ticker symbol being sold.
    total_qty : int
        Total shares to sell across all accounts.
    accounts_holdings : list of dict
        Each entry: {account: str, shares: int, account_hash: str (optional)}.
        Only accounts with shares > 0 are considered.

    Returns
    -------
    dict with keys:
        symbol           : str
        total_qty        : int  (requested)
        total_held       : int  (sum of all account holdings)
        allocations      : list of {account, account_hash, shares_held, sell_qty}
        allocated_total  : int  (sum of sell_qty — may be < total_qty if holdings insufficient)
        shortfall        : int  (total_qty - allocated_total)
    """
    symbol = (symbol or "").upper().strip()
    total_qty = int(total_qty)

    eligible = [
        a for a in accounts_holdings
        if int(a.get("shares", 0)) > 0
    ]

    total_held = sum(int(a.get("shares", 0)) for a in eligible)
    sellable   = min(total_qty, total_held)

    if not eligible or sellable <= 0:
        return {
            "symbol":         symbol,
            "total_qty":      total_qty,
            "total_held":     total_held,
            "allocations":    [],
            "allocated_total": 0,
            "shortfall":      total_qty,
        }

    # Proportional allocation (floor)
    allocs: List[Dict[str, Any]] = []
    for a in eligible:
        held = int(a.get("shares", 0))
        proportion = held / total_held
        sell = math.floor(proportion * sellable)
        allocs.append({
            "account":      a.get("account", ""),
            "account_hash": a.get("account_hash", ""),
            "shares_held":  held,
            "sell_qty":     sell,
        })

    # Distribute remainder to largest-holding accounts first
    remainder = sellable - sum(x["sell_qty"] for x in allocs)
    if remainder > 0:
        sorted_allocs = sorted(
            range(len(allocs)),
            key=lambda i: allocs[i]["shares_held"],
            reverse=True,
        )
        for idx in sorted_allocs:
            if remainder <= 0:
                break
            allocs[idx]["sell_qty"] += 1
            remainder -= 1

    allocated_total = sum(x["sell_qty"] for x in allocs)

    return {
        "symbol":          symbol,
        "total_qty":       total_qty,
        "total_held":      total_held,
        "allocations":     allocs,
        "allocated_total": allocated_total,
        "shortfall":       total_qty - allocated_total,
    }


def pct_to_shares(pct: float, total_held: int) -> int:
    """Convert a percentage of total holdings to a share count (floor)."""
    if pct <= 0 or total_held <= 0:
        return 0
    return max(1, math.floor(total_held * pct / 100.0))
