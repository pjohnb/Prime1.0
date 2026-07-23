"""
WO-PRIME-AUDIT-FIX-06 — AUDIT-031: UOA upsert-by-session tests.

Verifies that persist_uoa_signals() (direct persist path) and
bridge_uoa_result()/bridge_uoa_rows() (bridge adapters) use upsert semantics:
a second run on the same calendar day updates the existing row rather than
inserting a duplicate, even when the two runs use distinct scan_ts values
(e.g. an 08:00 deep scan and a 12:40 cron run).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_analytics.prime_signals_db import init_signals_table, get_signals
from prime_data.prime_db import init_db
from prime_scanners.prime_uoa_scanner import persist_uoa_signals
from prime_bridge.prime_signal_bridge import bridge_uoa_result

_AAPL_SIGNAL = {
    "symbol": "AAPL", "tier": "STRONG", "sizzle_index": 6.2, "group": "Top50",
    "direction": "LONG", "call_put_ratio": 3.1, "total_volume": 120000,
    "price_at_scan": 195.0,
}


class TestUoaScannerDedup(unittest.TestCase):
    """AUDIT-031: prime_uoa_scanner.persist_uoa_signals direct persist path."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_uoa_dedup.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _uoa_signals(self):
        return [s for s in get_signals(limit=500, db_path=self.db) if s["strategy"] == "UOA"]

    def test_two_distinct_same_day_runs_do_not_add_rows(self):
        persist_uoa_signals([_AAPL_SIGNAL], "2026-07-16 08:00:00", db_path=self.db)
        persist_uoa_signals([_AAPL_SIGNAL], "2026-07-16 12:40:00", db_path=self.db)
        rows = self._uoa_signals()
        self.assertEqual(len(rows), 1, "Two same-day runs at different times must not duplicate")

    def test_second_run_updates_scan_ts(self):
        persist_uoa_signals([_AAPL_SIGNAL], "2026-07-16 08:00:00", db_path=self.db)
        persist_uoa_signals([_AAPL_SIGNAL], "2026-07-16 12:40:00", db_path=self.db)
        rows = self._uoa_signals()
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertIn("12:40", rows[0]["scan_ts"])

    def test_different_calendar_day_creates_new_row(self):
        persist_uoa_signals([_AAPL_SIGNAL], "2026-07-16 08:00:00", db_path=self.db)
        persist_uoa_signals([_AAPL_SIGNAL], "2026-07-17 08:00:00", db_path=self.db)
        rows = self._uoa_signals()
        self.assertEqual(len(rows), 2, "Different calendar days must produce separate rows")


class TestUoaBridgeDedup(unittest.TestCase):
    """AUDIT-031: prime_signal_bridge.bridge_uoa_result JSON-adapter path."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_uoa_bridge_dedup.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _data(self, scan_time):
        return {
            "scan_time": scan_time,
            "signals": [
                {"symbol": "AAPL", "tier": "STRONG", "sizzle_index": 6.2,
                 "group": "Top50", "direction": "LONG", "call_put_ratio": 3.1,
                 "total_volume": 120000, "price_at_scan": 195.0},
            ],
        }

    def _uoa_signals(self):
        return [s for s in get_signals(limit=500, db_path=self.db) if s["strategy"] == "UOA"]

    def test_two_distinct_same_day_runs_do_not_add_rows(self):
        bridge_uoa_result(self._data("2026-07-16 08:00:00"), db_path=self.db)
        bridge_uoa_result(self._data("2026-07-16 12:40:00"), db_path=self.db)
        rows = self._uoa_signals()
        self.assertEqual(len(rows), 1, "Two same-day runs at different times must not duplicate")

    def test_different_calendar_day_creates_new_row(self):
        bridge_uoa_result(self._data("2026-07-16 08:00:00"), db_path=self.db)
        bridge_uoa_result(self._data("2026-07-17 08:00:00"), db_path=self.db)
        rows = self._uoa_signals()
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
