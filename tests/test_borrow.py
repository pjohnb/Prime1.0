"""
Sprint 17 Item 3 (Schwab borrow availability check) acceptance tests.

NON-NEGOTIABLE: no borrow = no signal. Covers check_borrow for borrowable
True/False, the fail-safe on API failure (treated as NOT borrowable), and
borrow_rate_pct storage on a signal. The scanner-level hard-block + ops_health
logging is covered in test_short_scanner.py (Item 1, which owns the scanner).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_trading.prime_schwab_borrow import check_borrow
from prime_data.prime_db import init_db, get_connection
from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup, get_signals


class TestCheckBorrow(unittest.TestCase):
    def test_borrowable_true_passes(self):
        out = check_borrow("AAPL", borrow_fn=lambda s: {"borrowable": True, "rate_pct": 1.25})
        self.assertTrue(out["borrowable"])
        self.assertEqual(out["rate_pct"], 1.25)
        self.assertEqual(out["source"], "schwab")
        self.assertEqual(out["symbol"], "AAPL")

    def test_borrowable_false_blocks(self):
        out = check_borrow("GME", borrow_fn=lambda s: {"borrowable": False, "rate_pct": 50.0})
        self.assertFalse(out["borrowable"])
        self.assertIsNone(out["rate_pct"])  # rate only populated when borrowable

    def test_api_failure_is_fail_safe(self):
        def boom(_):
            raise RuntimeError("schwab down")
        out = check_borrow("AAPL", borrow_fn=boom)
        self.assertFalse(out["borrowable"])  # fail-safe: never assume borrow
        self.assertEqual(out["source"], "unavailable")

    def test_default_lookup_is_fail_safe(self):
        # No token cache in the test env -> default lookup raises -> not borrowable.
        out = check_borrow("AAPL")
        self.assertFalse(out["borrowable"])
        self.assertEqual(out["source"], "unavailable")


class TestBorrowRateStorage(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_borrow.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_borrow_rate_pct_column_exists(self):
        with get_connection(self.db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(prime_signals)").fetchall()]
        self.assertIn("borrow_rate_pct", cols)

    def test_borrow_rate_stored_on_signal(self):
        insert_signal_dedup(
            symbol="AAPL", strategy="SHORT", scan_ts="2026-06-03T10:00:00",
            direction="SHORT", tier="STRONG", status="APPROVED",
            borrow_rate_pct=1.75, db_path=self.db)
        rows = get_signals(strategy="SHORT", db_path=self.db)
        self.assertEqual(rows[0]["borrow_rate_pct"], 1.75)


if __name__ == "__main__":
    unittest.main()
