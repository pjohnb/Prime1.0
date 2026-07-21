"""
WO-PRIME-BACKEND-FIXES-01 Item A — SRS upsert-by-session tests.

Verifies that bridge_srs_result() uses upsert_signal_by_session() semantics:
a second SRS scan on the same calendar day updates the existing row rather
than inserting a duplicate (same root cause as CIL-41 for IDX/PEAD).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_analytics.prime_signals_db import init_signals_table, get_signals
from prime_data.prime_db import init_db
from prime_bridge.prime_signal_bridge import bridge_srs_result

_SCAN_RECOVERING = {
    "scan_time": "2026-07-20 07:35",
    "sectors": {
        "Technology": {
            "phase": "RECOVERING",
            "etf": "XLK",
            "metrics": {
                "close": 210.50,
                "chg_2d_pct": 1.2,
                "chg_5d_pct": 3.5,
            },
        }
    },
}

_SCAN_RECOVERING_LATER = {
    "scan_time": "2026-07-20 08:15",
    "sectors": {
        "Technology": {
            "phase": "RECOVERING",
            "etf": "XLK",
            "metrics": {
                "close": 211.00,
                "chg_2d_pct": 1.5,
                "chg_5d_pct": 3.7,
            },
        }
    },
}

_SCAN_NEXT_DAY = {
    "scan_time": "2026-07-21 07:35",
    "sectors": {
        "Technology": {
            "phase": "RECOVERING",
            "etf": "XLK",
            "metrics": {
                "close": 212.00,
                "chg_2d_pct": 0.8,
                "chg_5d_pct": 2.9,
            },
        }
    },
}


class TestSrsDedup(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_srs_dedup.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _srs_signals(self):
        return [s for s in get_signals(limit=500, db_path=self.db) if s["strategy"] == "SRS"]

    def test_srs_second_run_same_day_does_not_add_rows(self):
        bridge_srs_result(_SCAN_RECOVERING, db_path=self.db)
        bridge_srs_result(_SCAN_RECOVERING_LATER, db_path=self.db)
        rows = self._srs_signals()
        self.assertEqual(len(rows), 1, "Second SRS run same day must not add a duplicate row")

    def test_srs_second_run_updates_scan_ts(self):
        bridge_srs_result(_SCAN_RECOVERING, db_path=self.db)
        bridge_srs_result(_SCAN_RECOVERING_LATER, db_path=self.db)
        rows = self._srs_signals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "XLK")
        self.assertIn("08:15", rows[0]["scan_ts"], "scan_ts should be updated to the later run")

    def test_srs_different_day_creates_new_row(self):
        bridge_srs_result(_SCAN_RECOVERING, db_path=self.db)
        bridge_srs_result(_SCAN_NEXT_DAY, db_path=self.db)
        rows = self._srs_signals()
        self.assertEqual(len(rows), 2, "SRS scans on different calendar days must produce separate rows")

    def test_srs_non_recovering_phase_not_inserted(self):
        scan = {
            "scan_time": "2026-07-20 07:35",
            "sectors": {
                "Technology": {"phase": "LEADING", "etf": "XLK", "metrics": {}},
                "Energy": {"phase": "LAGGING", "etf": "XLE", "metrics": {}},
            },
        }
        n = bridge_srs_result(scan, db_path=self.db)
        self.assertEqual(n, 0, "Non-RECOVERING phases must not be inserted")
        self.assertEqual(len(self._srs_signals()), 0)

    def test_srs_first_run_inserts_row(self):
        n = bridge_srs_result(_SCAN_RECOVERING, db_path=self.db)
        self.assertEqual(n, 1)
        rows = self._srs_signals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy"], "SRS")
        self.assertEqual(rows[0]["symbol"], "XLK")
        self.assertEqual(rows[0]["tier"], "RECOVERING")
        self.assertEqual(rows[0]["direction"], "LONG")


if __name__ == "__main__":
    unittest.main()
