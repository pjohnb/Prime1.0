"""
Item 1 (FIX-4) acceptance tests — price_at_scan validation.
Verifies that the v1.0 data layer enforces non-null, non-zero price_at_scan
for all trade records, fixing the v0.9 bug where scanners wrote 0.0.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    TradeRecordError,
    get_trade,
    init_db,
    insert_trade,
)


class TestPriceAtScanEnforced(unittest.TestCase):
    """AC 1.1 — price_at_scan non-null and non-zero for all trade records."""

    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "test_fix4.db"
        init_db(db_path=self.db)

    def _insert(self, price_at_scan, symbol="AAPL"):
        return insert_trade(
            strategy="UOA",
            symbol=symbol,
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=100,
            entry_time="2026-05-26T10:00:00",
            price_at_scan=price_at_scan,
            score=8.5,
            signal_source="test",
            db_path=self.db,
        )

    def test_valid_price_at_scan_accepted(self):
        log_id = self._insert(price_at_scan=185.50)
        record = get_trade(log_id, db_path=self.db)
        self.assertIsNotNone(record)
        self.assertEqual(record["price_at_scan"], 185.50)
        self.assertNotEqual(record["price_at_scan"], 0.0)

    def test_zero_price_at_scan_rejected(self):
        with self.assertRaises(TradeRecordError) as ctx:
            self._insert(price_at_scan=0.0)
        self.assertIn("price_at_scan must be > 0", str(ctx.exception))

    def test_none_price_at_scan_rejected(self):
        with self.assertRaises(TradeRecordError):
            self._insert(price_at_scan=None)

    def test_negative_price_at_scan_rejected(self):
        with self.assertRaises(TradeRecordError):
            self._insert(price_at_scan=-5.0)

    def test_multiple_records_all_have_valid_price(self):
        symbols = [("AAPL", 185.50), ("MSFT", 420.00), ("NVDA", 950.25)]
        log_ids = []
        for sym, price in symbols:
            log_ids.append(self._insert(price_at_scan=price, symbol=sym))

        for log_id in log_ids:
            record = get_trade(log_id, db_path=self.db)
            self.assertIsNotNone(record["price_at_scan"])
            self.assertGreater(record["price_at_scan"], 0)


class TestPriceAtScanCapturedByScanner(unittest.TestCase):
    """AC 1.2 — price_at_scan captured by scanner at signal time, stored via prime_data."""

    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "test_fix4_scanner.db"
        init_db(db_path=self.db)

    def test_scanner_signal_carries_price_through_data_layer(self):
        scanner_price = 192.75
        log_id = insert_trade(
            strategy="PEAD",
            symbol="GOOG",
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=50,
            entry_time="2026-05-26T09:45:00",
            price_at_scan=scanner_price,
            score=7.2,
            eps_beat_pct=15.3,
            signal_source="prime_scanners/prime_pead_scanner.py",
            db_path=self.db,
        )
        record = get_trade(log_id, db_path=self.db)
        self.assertEqual(record["price_at_scan"], scanner_price)
        self.assertEqual(record["strategy"], "PEAD")
        self.assertEqual(record["signal_source"], "prime_scanners/prime_pead_scanner.py")

    def test_all_strategy_types_store_price_at_scan(self):
        strategies = ["UOA", "PEAD", "MTS", "SRS", "PSA", "IDX"]
        for strat in strategies:
            log_id = insert_trade(
                strategy=strat,
                symbol=f"TEST_{strat}",
                direction="LONG",
                mode="PAPER",
                order_type="MARKET",
                shares=100,
                entry_time="2026-05-26T10:00:00",
                price_at_scan=100.0 + len(strat),
                score=5.0,
                db_path=self.db,
            )
            record = get_trade(log_id, db_path=self.db)
            self.assertGreater(
                record["price_at_scan"], 0,
                f"price_at_scan missing for strategy {strat}",
            )


if __name__ == "__main__":
    unittest.main()
