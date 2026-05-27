"""
PRIME v1.0 Schwab Position Importer (CIL-099).

Imports live Schwab holdings into prime_trade_log and reconciles against
OPEN records. Ghost trades auto-closed, new positions auto-imported,
qty mismatches flagged for manual review.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


class BrokerClient(Protocol):
    def get_positions(self) -> List[Dict[str, Any]]: ...


def get_schwab_positions(client: BrokerClient) -> List[Dict[str, Any]]:
    """Call Schwab API, return normalized list of {symbol, qty, avg_cost}."""
    raw_positions = client.get_positions()
    normalized = []
    for pos in raw_positions:
        instrument = pos.get("instrument", {})
        if instrument.get("assetType") != "EQUITY":
            continue
        symbol = instrument.get("symbol", "").upper()
        if not symbol:
            continue
        qty = int(pos.get("longQuantity", 0)) - int(pos.get("shortQuantity", 0))
        avg_cost = pos.get("averagePrice", 0.0)
        normalized.append({"symbol": symbol, "qty": qty, "avg_cost": avg_cost})
    return normalized


def reconcile_positions(
    schwab_positions: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compare Schwab positions to OPEN trade_log records.

    Returns summary with auto_closed, auto_imported, flagged, unchanged lists.
    """
    from prime_data.prime_db import (
        close_trade_reconcile,
        get_open_positions,
        insert_trade,
        log_ops_event,
    )

    result = {
        "auto_closed": [],
        "auto_imported": [],
        "flagged": [],
        "unchanged": [],
        "errors": [],
    }

    schwab_by_symbol = {p["symbol"]: p for p in schwab_positions}
    open_trades = get_open_positions(db_path=db_path)
    open_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for t in open_trades:
        sym = t["symbol"].upper()
        open_by_symbol.setdefault(sym, []).append(t)

    all_symbols = set(schwab_by_symbol.keys()) | set(open_by_symbol.keys())

    for symbol in all_symbols:
        schwab_pos = schwab_by_symbol.get(symbol)
        open_records = open_by_symbol.get(symbol, [])

        if schwab_pos and not open_records:
            try:
                log_id = insert_trade(
                    strategy="SCHWAB_IMPORT",
                    symbol=symbol,
                    direction="LONG" if schwab_pos["qty"] > 0 else "SHORT",
                    mode="LIVE",
                    order_type="MARKET",
                    shares=abs(schwab_pos["qty"]),
                    entry_time=datetime.utcnow().isoformat(),
                    price_at_scan=schwab_pos["avg_cost"] if schwab_pos["avg_cost"] > 0 else 0.01,
                    entry_price=schwab_pos["avg_cost"],
                    trade_source="SCHWAB_IMPORT",
                    db_path=db_path,
                )
                result["auto_imported"].append({"symbol": symbol, "log_id": log_id,
                                                "qty": schwab_pos["qty"]})
                log_ops_event("SCHWAB_IMPORT", "prime_schwab_importer",
                              symbol=symbol, detail=f"Auto-imported {symbol} qty={schwab_pos['qty']}",
                              db_path=db_path)
            except Exception as e:
                result["errors"].append({"symbol": symbol, "error": str(e)})
            continue

        if not schwab_pos and open_records:
            for rec in open_records:
                try:
                    close_trade_reconcile(rec["log_id"], "SCHWAB_RECONCILE", db_path=db_path)
                    result["auto_closed"].append({"symbol": symbol, "log_id": rec["log_id"]})
                    log_ops_event("SCHWAB_RECONCILE", "prime_schwab_importer",
                                  symbol=symbol,
                                  detail=f"Ghost trade {rec['log_id']} auto-closed",
                                  db_path=db_path)
                except Exception as e:
                    result["errors"].append({"symbol": symbol, "error": str(e)})
            continue

        if schwab_pos and open_records:
            total_open_shares = sum(abs(r.get("shares", 0)) for r in open_records)
            schwab_qty = abs(schwab_pos["qty"])

            if total_open_shares == schwab_qty:
                result["unchanged"].append({"symbol": symbol})
            else:
                result["flagged"].append({
                    "symbol": symbol,
                    "schwab_qty": schwab_qty,
                    "prime_qty": total_open_shares,
                    "reason": "QTY_MISMATCH",
                })
                log_ops_event("SCHWAB_QTY_MISMATCH", "prime_schwab_importer",
                              symbol=symbol,
                              detail=f"Schwab={schwab_qty} vs PRIME={total_open_shares}",
                              severity="WARN", db_path=db_path)

    return result
