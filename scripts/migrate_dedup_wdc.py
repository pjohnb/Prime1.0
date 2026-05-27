"""
CIL-095 Migration: Remove duplicate WDC entries from prime_trade_log.

Keeps the earliest entry per (symbol='WDC', strategy, status='OPEN') group.
Idempotent -- safe to run multiple times.

Usage:
    python scripts/migrate_dedup_wdc.py
"""

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import get_connection, init_db

logger = logging.getLogger(__name__)


def migrate_dedup_wdc(db_path=None):
    """Remove duplicate WDC rows, keeping the earliest entry per strategy group."""
    init_db(db_path)

    with get_connection(db_path) as conn:
        dupes = conn.execute(
            """SELECT symbol, strategy, COUNT(*) as cnt
               FROM prime_trade_log
               WHERE symbol='WDC' AND status='OPEN'
               GROUP BY symbol, strategy
               HAVING cnt > 1"""
        ).fetchall()

        if not dupes:
            logger.info("No duplicate WDC entries found. Nothing to do.")
            return {"removed": 0, "groups": 0}

        total_removed = 0
        for row in dupes:
            strategy = row["strategy"]
            keep = conn.execute(
                """SELECT log_id FROM prime_trade_log
                   WHERE symbol='WDC' AND strategy=? AND status='OPEN'
                   ORDER BY created_at ASC, entry_time ASC
                   LIMIT 1""",
                (strategy,),
            ).fetchone()

            if keep:
                cursor = conn.execute(
                    """DELETE FROM prime_trade_log
                       WHERE symbol='WDC' AND strategy=? AND status='OPEN'
                       AND log_id != ?""",
                    (strategy, keep["log_id"]),
                )
                removed = cursor.rowcount
                total_removed += removed
                logger.info("WDC/%s: kept %s, removed %d duplicates", strategy, keep["log_id"], removed)

        conn.commit()

    result = {"removed": total_removed, "groups": len(dupes)}
    logger.info("Migration complete: removed %d duplicate WDC rows across %d groups",
                total_removed, len(dupes))
    return result


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [MIGRATE] %(levelname)s %(message)s",
    )
    result = migrate_dedup_wdc()
    print(f"\nMigration complete: {result['removed']} duplicates removed "
          f"across {result['groups']} groups")


if __name__ == "__main__":
    main()
