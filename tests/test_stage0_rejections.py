"""
Sprint 12 Item 6 (CIL-STAGE0-TAB) acceptance tests -- Stage0 Rejections.
Covers rejection written on Stage0 fail, reason populated, DB query, upsert idempotent.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import get_connection, init_db, write_stage0_rejection
from prime_analytics.prime_signals_db import get_signals, init_signals_table
from prime_scanners.prime_psa_scanner import stage0_filter


class TestStage0Filter(unittest.TestCase):

    def test_price_below_min(self):
        r = stage0_filter("TEST", {"price": 2.0, "volume": 500000}, 5.0, 500.0, 100000)
        self.assertIsNotNone(r)
        self.assertIn("price", r.lower())

    def test_price_above_max(self):
        r = stage0_filter("TEST", {"price": 600.0, "volume": 500000}, 5.0, 500.0, 100000)
        self.assertIsNotNone(r)

    def test_volume_below_min(self):
        r = stage0_filter("TEST", {"price": 50.0, "volume": 5000}, 5.0, 500.0, 100000)
        self.assertIsNotNone(r)
        self.assertIn("volume", r.lower())

    def test_passes_stage0(self):
        r = stage0_filter("TEST", {"price": 50.0, "volume": 500000}, 5.0, 500.0, 100000)
        self.assertIsNone(r)


class TestStage0RejectionDB(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_stage0.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_rejection_written(self):
        sid = write_stage0_rejection("AAPL", "price 2.0 < 5.0", "2026-05-27T10:00:00",
                                     db_path=self.db)
        self.assertIsNotNone(sid)
        signals = get_signals(status="REJECTED_STAGE0", db_path=self.db)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "AAPL")

    def test_rejection_reason_populated(self):
        write_stage0_rejection("AAPL", "volume 5000 < 100000", "2026-05-27T10:00:00",
                               db_path=self.db)
        with get_connection(self.db) as conn:
            row = conn.execute(
                "SELECT rejection_reason, rejection_stage FROM prime_signals WHERE symbol='AAPL'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(dict(row)["rejection_reason"], "volume 5000 < 100000")
        self.assertEqual(dict(row)["rejection_stage"], "STAGE0")

    def test_rejection_columns_exist(self):
        with get_connection(self.db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(prime_signals)").fetchall()]
        self.assertIn("rejection_reason", cols)
        self.assertIn("rejection_stage", cols)

    def test_query_rejected_signals(self):
        write_stage0_rejection("AAPL", "price below", "2026-05-27T10:00:00", db_path=self.db)
        write_stage0_rejection("MSFT", "volume below", "2026-05-27T10:00:00", db_path=self.db)
        signals = get_signals(status="REJECTED_STAGE0", db_path=self.db)
        self.assertEqual(len(signals), 2)

    def test_none_reason_accepted(self):
        sid = write_stage0_rejection("AAPL", None, "2026-05-27T10:00:00", db_path=self.db)
        self.assertIsNotNone(sid)

    def test_multiple_scans_accumulate(self):
        write_stage0_rejection("AAPL", "price", "2026-05-27T10:00:00", db_path=self.db)
        write_stage0_rejection("AAPL", "volume", "2026-05-27T11:00:00", db_path=self.db)
        signals = get_signals(symbol="AAPL", status="REJECTED_STAGE0", db_path=self.db)
        self.assertEqual(len(signals), 2)


if __name__ == "__main__":
    unittest.main()
