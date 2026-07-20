"""
WO-PRIME-PEAD-DEDUP-01 — PEAD upsert-by-session tests.

Verifies that persist_pead_signals() uses upsert semantics: a second run
on the same calendar day updates the existing row rather than inserting a
duplicate (same root cause as CIL-41 for IDX, fixed in commit 1cc43e0).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_analytics.prime_signals_db import init_signals_table, get_signals
from prime_data.prime_db import init_db
from prime_scanners.prime_pead_scanner import persist_pead_signals

_UNH_SIGNAL = {
    "symbol": "UNH",
    "score": 72.0,
    "direction": "LONG",
    "guidance_flag": "BEAT_RAISE",
    "finnhub_guidance_available": True,
    "approved": True,
    "confidence_level": "HIGH",
    "price_at_scan": 500.0,
    "surprise_pct": 5.2,
}


class TestPeadDedup(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_pead_dedup.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _pead_signals(self, db=None):
        return [
            s for s in get_signals(limit=500, db_path=db or self.db)
            if s["strategy"] == "PEAD"
        ]

    def test_pead_second_run_same_day_does_not_add_rows(self):
        persist_pead_signals([_UNH_SIGNAL], "2026-07-20 03:17:00", db_path=self.db)
        persist_pead_signals([_UNH_SIGNAL], "2026-07-20 03:45:00", db_path=self.db)
        rows = self._pead_signals()
        self.assertEqual(len(rows), 1)

    def test_pead_second_run_updates_scan_ts_not_duplicates(self):
        persist_pead_signals([_UNH_SIGNAL], "2026-07-20 03:17:00", db_path=self.db)
        persist_pead_signals([_UNH_SIGNAL], "2026-07-20 03:45:00", db_path=self.db)
        rows = self._pead_signals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "UNH")
        self.assertIn("03:45", rows[0]["scan_ts"])

    def test_pead_different_day_creates_new_row(self):
        persist_pead_signals([_UNH_SIGNAL], "2026-07-20 03:17:00", db_path=self.db)
        persist_pead_signals([_UNH_SIGNAL], "2026-07-21 03:17:00", db_path=self.db)
        rows = self._pead_signals()
        self.assertEqual(len(rows), 2)

    def test_pead_persist_returns_count(self):
        n = persist_pead_signals([_UNH_SIGNAL], "2026-07-20 03:17:00", db_path=self.db)
        self.assertEqual(n, 1)

    def test_pead_unapproved_signal_not_written(self):
        unapproved = {**_UNH_SIGNAL, "approved": False, "score": 30.0}
        n = persist_pead_signals([unapproved], "2026-07-20 03:17:00", db_path=self.db)
        self.assertEqual(n, 0)
        self.assertEqual(len(self._pead_signals()), 0)


if __name__ == "__main__":
    unittest.main()
