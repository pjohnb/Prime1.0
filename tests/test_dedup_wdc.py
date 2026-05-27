"""
CIL-095 Duplicate WDC Entries tests.
Covers: upsert_signal deduplication, migration idempotency.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    get_connection,
    get_open_trades,
    init_db,
    insert_trade,
    upsert_signal,
)
from scripts.migrate_dedup_wdc import migrate_dedup_wdc


class TestUpsertSignal(unittest.TestCase):
    """upsert_signal() deduplication logic."""

    def setUp(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test_dedup.db"
        init_db(db_path=self.db_path)

    def test_first_insert_succeeds(self):
        log_id = upsert_signal(
            strategy="UOA", symbol="WDC",
            scan_date="2026-05-27T12:00:00",
            price_at_scan=50.0, score=7.5,
            db_path=self.db_path,
        )
        self.assertIsNotNone(log_id)

    def test_duplicate_skipped(self):
        log_id1 = upsert_signal(
            strategy="UOA", symbol="WDC",
            scan_date="2026-05-27T12:00:00",
            price_at_scan=50.0, score=7.5,
            db_path=self.db_path,
        )
        log_id2 = upsert_signal(
            strategy="UOA", symbol="WDC",
            scan_date="2026-05-27T14:00:00",
            price_at_scan=51.0, score=8.0,
            db_path=self.db_path,
        )
        self.assertIsNotNone(log_id1)
        self.assertIsNone(log_id2)

        trades = get_open_trades(db_path=self.db_path)
        wdc_trades = [t for t in trades if t["symbol"] == "WDC"]
        self.assertEqual(len(wdc_trades), 1)

    def test_different_strategy_not_duplicate(self):
        log_id1 = upsert_signal(
            strategy="UOA", symbol="WDC",
            scan_date="2026-05-27T12:00:00",
            price_at_scan=50.0, db_path=self.db_path,
        )
        log_id2 = upsert_signal(
            strategy="PEAD", symbol="WDC",
            scan_date="2026-05-27T12:00:00",
            price_at_scan=50.0, db_path=self.db_path,
        )
        self.assertIsNotNone(log_id1)
        self.assertIsNotNone(log_id2)

    def test_different_day_not_duplicate(self):
        log_id1 = upsert_signal(
            strategy="UOA", symbol="WDC",
            scan_date="2026-05-27T12:00:00",
            price_at_scan=50.0, db_path=self.db_path,
        )
        log_id2 = upsert_signal(
            strategy="UOA", symbol="WDC",
            scan_date="2026-05-28T12:00:00",
            price_at_scan=51.0, db_path=self.db_path,
        )
        self.assertIsNotNone(log_id1)
        self.assertIsNotNone(log_id2)

    def test_different_symbol_not_duplicate(self):
        log_id1 = upsert_signal(
            strategy="UOA", symbol="WDC",
            scan_date="2026-05-27T12:00:00",
            price_at_scan=50.0, db_path=self.db_path,
        )
        log_id2 = upsert_signal(
            strategy="UOA", symbol="AAPL",
            scan_date="2026-05-27T12:00:00",
            price_at_scan=190.0, db_path=self.db_path,
        )
        self.assertIsNotNone(log_id1)
        self.assertIsNotNone(log_id2)


class TestMigrateDedup(unittest.TestCase):
    """Migration script idempotency and correctness."""

    def setUp(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test_migrate.db"
        init_db(db_path=self.db_path)

    def test_removes_duplicates_keeps_earliest(self):
        id1 = insert_trade(
            strategy="UOA", symbol="WDC", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-25T10:00:00", price_at_scan=50.0,
            db_path=self.db_path,
        )
        insert_trade(
            strategy="UOA", symbol="WDC", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-25T14:00:00", price_at_scan=51.0,
            db_path=self.db_path,
        )
        insert_trade(
            strategy="UOA", symbol="WDC", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-26T10:00:00", price_at_scan=52.0,
            db_path=self.db_path,
        )

        result = migrate_dedup_wdc(db_path=self.db_path)
        self.assertEqual(result["removed"], 2)

        trades = get_open_trades(db_path=self.db_path)
        wdc_trades = [t for t in trades if t["symbol"] == "WDC"]
        self.assertEqual(len(wdc_trades), 1)

    def test_idempotent_run(self):
        insert_trade(
            strategy="UOA", symbol="WDC", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-25T10:00:00", price_at_scan=50.0,
            db_path=self.db_path,
        )
        insert_trade(
            strategy="UOA", symbol="WDC", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-25T14:00:00", price_at_scan=51.0,
            db_path=self.db_path,
        )

        result1 = migrate_dedup_wdc(db_path=self.db_path)
        self.assertEqual(result1["removed"], 1)

        result2 = migrate_dedup_wdc(db_path=self.db_path)
        self.assertEqual(result2["removed"], 0)

    def test_no_duplicates_no_change(self):
        insert_trade(
            strategy="UOA", symbol="WDC", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-25T10:00:00", price_at_scan=50.0,
            db_path=self.db_path,
        )
        result = migrate_dedup_wdc(db_path=self.db_path)
        self.assertEqual(result["removed"], 0)

    def test_non_wdc_untouched(self):
        insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-25T10:00:00", price_at_scan=190.0,
            db_path=self.db_path,
        )
        insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-25T14:00:00", price_at_scan=191.0,
            db_path=self.db_path,
        )
        result = migrate_dedup_wdc(db_path=self.db_path)
        self.assertEqual(result["removed"], 0)

        trades = get_open_trades(db_path=self.db_path)
        aapl_trades = [t for t in trades if t["symbol"] == "AAPL"]
        self.assertEqual(len(aapl_trades), 2)


if __name__ == "__main__":
    unittest.main()
