"""
PRIME v1.0 MATA -- Multi-Account Trade Allocation (Sprint 17 Item 4).

allocate_trade() distributes a trade across configured accounts. Sprint 17 makes
it direction-aware for SHORT:

  * Rollover IRA accounts are EXCLUDED from short routing (Design Principle 4 --
    IRAs cannot hold short positions);
  * short allocation respects margin_available (not buying_power);
  * short share counts apply short_size_multiplier automatically.

LONG routing is unchanged: all accounts eligible, sized against buying_power.

Account profile (dict): {name, type, buying_power, margin_available, weight}.
`type` containing "IRA" (e.g. "ROLLOVER_IRA") marks an IRA. allocate_trade()
takes accounts explicitly so it is fully testable; load_accounts() reads the
optional ops_config.json "mata_accounts" list for live use.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prime_mata")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHORT_SIZE_MULTIPLIER = 0.5


def is_ira(account: Dict[str, Any]) -> bool:
    """True if the account is an IRA (cannot hold shorts)."""
    return "IRA" in str(account.get("type", "")).upper()


def _short_multiplier(config_path: Optional[Path]) -> float:
    if config_path is None:
        config_path = _PROJECT_ROOT / "ops_config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            v = data.get("short_size_multiplier")
            if v is not None:
                return float(v)
    except Exception:
        pass
    return DEFAULT_SHORT_SIZE_MULTIPLIER


def load_accounts(config_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load MATA account profiles from ops_config.json ("mata_accounts"). [] if none."""
    if config_path is None:
        config_path = _PROJECT_ROOT / "ops_config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            accts = data.get("mata_accounts", [])
            return accts if isinstance(accts, list) else []
    except Exception:
        pass
    return []


def allocate_trade(
    symbol: str,
    direction: str,
    base_shares: int,
    price: float,
    accounts: List[Dict[str, Any]],
    short_size_multiplier: Optional[float] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Allocate a trade across accounts, direction-aware.

    base_shares is the long-equivalent share count. For SHORT, IRAs are excluded,
    capacity is margin_available, and base_shares is scaled by short_size_multiplier.
    Returns {symbol, direction, target_shares, capacity_field, allocations,
    allocated_shares, excluded_ira}.
    """
    direction = (direction or "LONG").upper()
    is_short = direction == "SHORT"
    if short_size_multiplier is None:
        short_size_multiplier = _short_multiplier(config_path)

    capacity_field = "margin_available" if is_short else "buying_power"
    excluded_ira: List[str] = []
    eligible: List[Dict[str, Any]] = []
    for a in accounts:
        if is_short and is_ira(a):
            excluded_ira.append(a.get("name"))
            continue
        eligible.append(a)

    target_shares = int(base_shares * short_size_multiplier) if is_short else int(base_shares)

    result = {
        "symbol": (symbol or "").upper(),
        "direction": direction,
        "target_shares": target_shares,
        "capacity_field": capacity_field,
        "allocations": [],
        "allocated_shares": 0,
        "excluded_ira": excluded_ira,
    }
    if target_shares <= 0 or price <= 0 or not eligible:
        return result

    # Capacity (in shares) per eligible account from the direction-appropriate field.
    remaining = target_shares
    # Largest capacity first so the trade fills predictably.
    ranked = sorted(eligible, key=lambda a: float(a.get(capacity_field, 0) or 0), reverse=True)
    for a in ranked:
        if remaining <= 0:
            break
        cap_dollars = float(a.get(capacity_field, 0) or 0)
        cap_shares = int(cap_dollars // price)
        if cap_shares <= 0:
            continue
        take = min(cap_shares, remaining)
        result["allocations"].append({
            "account": a.get("name"),
            "type": a.get("type"),
            "shares": take,
            "notional": round(take * price, 2),
        })
        remaining -= take

    result["allocated_shares"] = sum(x["shares"] for x in result["allocations"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    demo = [
        {"name": "Joint Brokerage", "type": "BROKERAGE", "buying_power": 100000, "margin_available": 50000},
        {"name": "Rollover IRA", "type": "ROLLOVER_IRA", "buying_power": 80000, "margin_available": 0},
    ]
    print("LONG:", allocate_trade("AAPL", "LONG", 100, 100.0, demo))
    print("SHORT:", allocate_trade("AAPL", "SHORT", 100, 100.0, demo))
