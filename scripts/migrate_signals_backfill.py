"""
Migration: backfill prime_signals from existing prime_trade_log entries.

Idempotent -- skips entries that already have a matching signal record.
Run: python scripts/migrate_signals_backfill.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_analytics.prime_signals_db import init_signals_table, insert_signal, get_signals
from prime_data.prime_db import get_connection, init_db
from prime_intelligence.prime_portfolio_factor import sector_map


def migrate(db_path=None):
    """Backfill prime_signals from prime_trade_log. Returns count of new records."""
    init_db(db_path)
    init_signals_table(db_path)

    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prime_trade_log ORDER BY entry_time ASC"
        ).fetchall()

    inserted = 0
    for row in rows:
        r = dict(row)
        existing = get_signals(
            strategy=r["strategy"],
            symbol=r["symbol"],
            db_path=db_path,
        )
        already = any(
            s.get("trade_id") == r["log_id"] for s in existing
        )
        if already:
            continue

        insert_signal(
            symbol=r["symbol"],
            strategy=r["strategy"],
            scan_ts=r["entry_time"],
            entry_price=r.get("entry_price") or r.get("price_at_scan", 0),
            score=r.get("score", 0),
            sector=sector_map(r["symbol"]),
            status="TRADED" if r["status"] == "CLOSED" else "OPEN",
            trade_id=r["log_id"],
            direction=r.get("direction", "LONG"),
            factors=r.get("trade_factors", "{}"),
            db_path=db_path,
        )
        inserted += 1

    return inserted


if __name__ == "__main__":
    count = migrate()
    print(f"Migration complete: {count} signal records backfilled.")
