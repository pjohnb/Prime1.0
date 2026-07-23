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
    except Exception as e:
        # AUDIT-043: log so a malformed ops_config.json isn't indistinguishable
        # from "no override configured" — same silent-failure shape as AUDIT-001.
        logger.warning("MATA short-multiplier config parse error: %s", e)
    return DEFAULT_SHORT_SIZE_MULTIPLIER


def load_accounts(config_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load MATA account profiles from ops_config.json ("mata_accounts"). [] if none."""
    if config_path is None:
        config_path = _PROJECT_ROOT / "ops_config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            accts = data.get("mata_accounts", [])
            if not isinstance(accts, list):
                return []
            if not accts:
                logger.warning(
                    "MATA: mata_accounts is empty in ops_config.json — MATA routing will produce 0 orders"
                )
            else:
                total_weight = sum(float(a.get("weight", 0)) for a in accts if isinstance(a, dict))
                if abs(total_weight - 100.0) > 1.0:
                    logger.warning(
                        "MATA: mata_accounts weights sum to %.1f%% (expected 100%%) — "
                        "MATA allocation may be incorrect",
                        total_weight,
                    )
            return accts
    except Exception as e:
        # AUDIT-043: log so a malformed/unreadable ops_config.json doesn't
        # silently reproduce AUDIT-001 symptoms (empty MATA routing, no clue why).
        logger.warning("MATA config parse error: %s", e)
    return []


def allocate_trade(
    symbol: str,
    direction: str,
    base_shares: int,
    price: float,
    accounts: List[Dict[str, Any]],
    short_size_multiplier: Optional[float] = None,
    config_path: Optional[Path] = None,
    use_weights: bool = False,
) -> Dict[str, Any]:
    """Allocate a trade across accounts, direction-aware.

    base_shares is the long-equivalent share count. For SHORT, IRAs are excluded,
    capacity is margin_available, and base_shares is scaled by short_size_multiplier.

    use_weights=True (CIL-NEW-13: All Accounts profile): distributes proportionally
    by each account's 'weight' field rather than greedy largest-capacity-first fill.
    Residual shares (from capacity clips) are redistributed to remaining accounts.

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

    if use_weights:
        # CIL-NEW-13: proportional by weight — used by the All Accounts profile.
        # CALC-MATA-1: `weight or 1` silently coerced an explicit weight=0 (or a
        # missing/None weight) back to 1, letting a deliberately-excluded account
        # still receive shares. Filter those out before computing total_weight.
        weighted: List[tuple] = []
        for a in eligible:
            w_raw = a.get("weight")
            try:
                w = float(w_raw) if w_raw is not None else 0.0
            except (TypeError, ValueError):
                w = 0.0
            if w <= 0:
                logger.warning(
                    "MATA: account %s has weight=%s — excluded from allocation",
                    a.get("name"), w_raw,
                )
                continue
            weighted.append((a, w))

        total_weight = sum(w for _, w in weighted)
        allocs: List[Dict[str, Any]] = []
        if total_weight > 0:
            for a, w in weighted:
                ideal = target_shares * w / total_weight
                floor_take = int(ideal)  # floor — ideal is always >= 0 here
                cap_dollars = float(a.get(capacity_field, 0) or 0)
                cap_shares = int(cap_dollars // price) if price > 0 else floor_take
                take = min(floor_take, cap_shares) if cap_shares > 0 else 0
                allocs.append({
                    "account": a.get("name"), "type": a.get("type"),
                    "_take": take, "_cap": cap_shares,
                    "_fractional": ideal - floor_take,
                })
            remaining = target_shares - sum(s["_take"] for s in allocs)
            # CALC-MATA-2: largest-remainder method — hand out the leftover
            # shares ONE AT A TIME, in descending fractional-shortfall order,
            # not whichever account happens to appear first in ops_config.json.
            # (Awarding the whole remainder to the top-ranked slot in one shot
            # would just relocate the list-order bias to the sort key instead
            # of removing it.) Capacity can force more than one pass — e.g. if
            # a top-ranked account is already at its cap — so loop until either
            # the remainder is exhausted or no slot has headroom left.
            ranked = sorted(allocs, key=lambda s: s["_fractional"], reverse=True)
            while remaining > 0:
                gave_any = False
                for slot in ranked:
                    if remaining <= 0:
                        break
                    if slot["_take"] < slot["_cap"]:
                        slot["_take"] += 1
                        remaining -= 1
                        gave_any = True
                if not gave_any:
                    break  # every account is at cap — remainder can't be placed
        result["allocations"] = [
            {"account": s["account"], "type": s["type"],
             "shares": s["_take"], "notional": round(s["_take"] * price, 2)}
            for s in allocs if s["_take"] > 0
        ]
        if result["allocations"] and target_shares > 0:
            logger.info(
                "MATA allocation: %s",
                ", ".join(
                    f"{a['account']}={a['shares']} ({100 * a['shares'] / target_shares:.1f}%)"
                    for a in result["allocations"]
                ),
            )
    else:
        # Legacy greedy fill: largest capacity first.
        remaining = target_shares
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
