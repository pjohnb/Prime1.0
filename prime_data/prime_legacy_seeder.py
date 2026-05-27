"""
PRIME v1.0 Legacy Position Seeder (CIL-102).

Idempotent script to seed pre-PRIME positions as LEGACY in prime_trade_log.
Scope: Joint Brokerage (...7926) only.

Usage:
    python prime_data/prime_legacy_seeder.py
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    get_connection,
    get_open_trades,
    init_db,
    insert_trade,
    update_trade_source,
)

logger = logging.getLogger(__name__)

JOINT_ACCOUNT = "7926"

LEGACY_POSITIONS: List[Dict[str, Any]] = [
    {"symbol": "GLD",  "strategy": "Pre-PRIME Investment", "shares": 10,  "cost_basis": 309.67},
    {"symbol": "NIO",  "strategy": "Pre-PRIME Investment", "shares": 100, "cost_basis": 3.645},
    {"symbol": "TJX",  "strategy": "Pre-PRIME Investment", "shares": 20,  "cost_basis": 158.3275},
    {"symbol": "MSFT", "strategy": "Pre-PRIME Investment", "shares": 36,  "cost_basis": 424.345},
    {"symbol": "DDOG", "strategy": "Pre-PRIME Investment", "shares": 2,   "cost_basis": 202.135},
]


def seed_legacy_positions(db_path: Optional[Path] = None) -> Dict[str, List[str]]:
    """Upsert legacy positions into prime_trade_log. Idempotent.

    If a PAPER record for a symbol exists with status OPEN, updates it to LEGACY.
    If a LEGACY record already exists, skips. Otherwise inserts new LEGACY record.

    Returns dict with 'inserted', 'updated', and 'skipped' symbol lists.
    """
    init_db(db_path)
    result = {"inserted": [], "updated": [], "skipped": []}

    open_trades = get_open_trades(db_path=db_path)
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for t in open_trades:
        sym = t["symbol"].upper()
        if sym not in by_symbol:
            by_symbol[sym] = t
        elif t.get("trade_source", "PAPER") == "PAPER":
            by_symbol[sym] = t

    for pos in LEGACY_POSITIONS:
        symbol = pos["symbol"].upper()
        existing = by_symbol.get(symbol)

        if existing and existing.get("trade_source") == "LEGACY":
            logger.info("SKIP %s: already LEGACY", symbol)
            result["skipped"].append(symbol)
            continue

        if existing and existing.get("trade_source", "PAPER") == "PAPER":
            update_trade_source(existing["log_id"], "LEGACY", db_path=db_path)
            with get_connection(db_path) as conn:
                conn.execute(
                    "UPDATE prime_trade_log SET strategy=?, shares=?, entry_price=?, account=? WHERE log_id=?",
                    (pos["strategy"], pos["shares"], pos["cost_basis"], JOINT_ACCOUNT, existing["log_id"]),
                )
                conn.commit()
            logger.info("UPDATED %s: PAPER -> LEGACY (log_id=%s)", symbol, existing["log_id"])
            result["updated"].append(symbol)
            continue

        insert_trade(
            strategy=pos["strategy"],
            symbol=symbol,
            direction="LONG",
            mode="LIVE",
            order_type="MARKET",
            shares=pos["shares"],
            entry_time=datetime.utcnow().isoformat(),
            price_at_scan=pos["cost_basis"],
            entry_price=pos["cost_basis"],
            account=JOINT_ACCOUNT,
            notes="Pre-PRIME position seeded by CIL-102",
            trade_source="LEGACY",
            db_path=db_path,
        )
        logger.info("INSERTED %s as LEGACY", symbol)
        result["inserted"].append(symbol)

    return result


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [LEGACY-SEEDER] %(levelname)s %(message)s",
    )
    logger.info("Starting legacy position seeder (CIL-102)...")
    result = seed_legacy_positions()
    print(f"\nLegacy seeder complete:")
    print(f"  Inserted: {result['inserted']}")
    print(f"  Updated:  {result['updated']}")
    print(f"  Skipped:  {result['skipped']}")


if __name__ == "__main__":
    main()
