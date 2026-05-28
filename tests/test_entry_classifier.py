"""
Sprint 12 Item 3 (ML-16) acceptance tests -- Entry Timing Quality.
Covers each classification boundary, UNKNOWN on missing data, idempotent migration.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_entry_classifier import classify_entry
from prime_data.prime_db import get_connection, init_db
from prime_analytics.prime_signals_db import init_signals_table


class TestClassifyEntry(unittest.TestCase):

    def test_exhausted_high_rsi(self):
        data = {"sma_20": 180, "rsi": 80, "volume_ratio": 1.2, "low_20d": 170}
        self.assertEqual(classify_entry("AAPL", 185, "2026-05-27T10:00:00", data), "EXHAUSTED")

    def test_exhausted_price_extended(self):
        data = {"sma_20": 160, "rsi": 60, "volume_ratio": 1.2, "low_20d": 155}
        self.assertEqual(classify_entry("AAPL", 180, "2026-05-27T10:00:00", data), "EXHAUSTED")

    def test_early_near_low_low_rsi(self):
        data = {"sma_20": 185, "rsi": 30, "volume_ratio": 0.8, "low_20d": 170}
        self.assertEqual(classify_entry("AAPL", 170.5, "2026-05-27T10:00:00", data), "EARLY")

    def test_on_time_confirmed_momentum(self):
        data = {"sma_20": 180, "rsi": 55, "volume_ratio": 1.5, "low_20d": 170}
        self.assertEqual(classify_entry("AAPL", 185, "2026-05-27T10:00:00", data), "ON_TIME")

    def test_unknown_no_data(self):
        self.assertEqual(classify_entry("AAPL", 185, "2026-05-27T10:00:00", None), "UNKNOWN")

    def test_unknown_missing_rsi(self):
        data = {"sma_20": 180, "rsi": None, "volume_ratio": 1.2}
        self.assertEqual(classify_entry("AAPL", 185, "2026-05-27T10:00:00", data), "UNKNOWN")

    def test_unknown_missing_sma(self):
        data = {"sma_20": None, "rsi": 50, "volume_ratio": 1.2}
        self.assertEqual(classify_entry("AAPL", 185, "2026-05-27T10:00:00", data), "UNKNOWN")

    def test_unknown_zero_price(self):
        data = {"sma_20": 180, "rsi": 50, "volume_ratio": 1.2}
        self.assertEqual(classify_entry("AAPL", 0, "2026-05-27T10:00:00", data), "UNKNOWN")

    def test_on_time_boundary_rsi_40(self):
        data = {"sma_20": 180, "rsi": 40, "volume_ratio": 1.1, "low_20d": 170}
        self.assertEqual(classify_entry("AAPL", 182, "2026-05-27T10:00:00", data), "ON_TIME")

    def test_unknown_when_low_volume(self):
        data = {"sma_20": 180, "rsi": 50, "volume_ratio": 0.8, "low_20d": 170}
        self.assertEqual(classify_entry("AAPL", 185, "2026-05-27T10:00:00", data), "UNKNOWN")


class TestEntryTimingMigration(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_entry_timing.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_entry_timing_column_exists(self):
        init_signals_table(self.db)
        with get_connection(self.db) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(prime_signals)").fetchall()]
        self.assertIn("entry_timing", cols)

    def test_migration_idempotent(self):
        init_signals_table(self.db)
        init_signals_table(self.db)
        with get_connection(self.db) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(prime_signals)").fetchall()]
        self.assertIn("entry_timing", cols)


if __name__ == "__main__":
    unittest.main()
