"""
Sprint 20 Item 1 (DK three-state core upgrade) acceptance tests.

Covers: CONFIRMING / NEUTRAL / NULLIFYING classification (PENDING retired),
dk_conviction populated on CONFIRMING/NULLIFYING rows, the PENDING->NEUTRAL
schema migration, and the `direction` field on DK feed prints.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db, get_connection
from prime_analytics.prime_signals_db import (
    init_signals_table, insert_signal, get_signals)
from prime_intelligence import prime_dk_trader as dkt
from prime_data import prime_dk_feed as feed


class _Base(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_dk3.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()


class TestThreeStateClassification(_Base):
    @patch("prime_intelligence.prime_dk_trader.get_dk_prints", return_value=[])
    @patch("prime_intelligence.prime_dk_trader.score_dk_signal")
    def test_confirming_neutral_nullifying(self, mock_dk, _mock_prints):
        mock_dk.side_effect = lambda s: {
            "AAA": {"dk_score": 80.0, "dk_status": "CONFIRMING", "detail": {}},
            "BBB": {"dk_score": 0.0, "dk_status": "NULLIFYING", "detail": {}},
            "CCC": {"dk_score": 10.0, "dk_status": "NEUTRAL", "detail": {}},
        }[s]
        # Seed non-DK signals so propagation has rows to stamp.
        for sym in ("AAA", "BBB", "CCC"):
            insert_signal(sym, "PSA", "2026-06-03 12:50", status="APPROVED", db_path=self.db)
        dkt.run_dk_trader_scan(symbols=["AAA", "BBB", "CCC"],
                               scan_ts="2026-06-03 12:50", db_path=self.db)
        dkt.propagate_dk_status(db_path=self.db)

        states = {r["symbol"]: r["dk_status"]
                  for r in get_signals(strategy="PSA", db_path=self.db)}
        self.assertEqual(states["AAA"], "CONFIRMING")
        self.assertEqual(states["BBB"], "NULLIFYING")
        self.assertEqual(states["CCC"], "NEUTRAL")  # neutral DK -> no row -> NEUTRAL

    def test_get_dk_status_helper(self):
        dkt._write_dk_row("AAA", {"dk_score": 75, "dk_status": "CONFIRMING", "detail": {}},
                          "SIGNAL", "2026-06-03 12:50", self.db)
        out = dkt.get_dk_status("AAA", self.db)
        self.assertEqual(out["dk_status"], "CONFIRMING")
        self.assertIsNotNone(out["dk_conviction"])
        self.assertEqual(dkt.get_dk_status("ZZZ", self.db)["dk_status"], "NEUTRAL")

    def test_conviction_on_confirming_and_nullifying(self):
        dkt._write_dk_row("AAA", {"dk_score": 90, "dk_status": "CONFIRMING", "detail": {}},
                          "SIGNAL", "2026-06-03 12:50", self.db)
        dkt._write_dk_row("BBB", {"dk_score": 0, "dk_status": "NULLIFYING", "detail": {}},
                          "NULLIFIER", "2026-06-03 12:50", self.db)
        rows = {r["symbol"]: r for r in get_signals(strategy="DK", db_path=self.db)}
        self.assertAlmostEqual(rows["AAA"]["dk_conviction"], 0.9, places=3)
        self.assertGreaterEqual(rows["BBB"]["dk_conviction"], 0.5)  # NULLIFYING floor

    def test_status_counts(self):
        for sym, st in (("AAA", "CONFIRMING"), ("BBB", "NULLIFYING"), ("CCC", "CONFIRMING")):
            insert_signal(sym, "PSA", "2026-06-03 12:50", status="APPROVED", db_path=self.db)
        dkt._write_dk_row("AAA", {"dk_score": 70, "dk_status": "CONFIRMING", "detail": {}},
                          "SIGNAL", "2026-06-03 12:50", self.db)
        dkt._write_dk_row("BBB", {"dk_score": 0, "dk_status": "NULLIFYING", "detail": {}},
                          "NULLIFIER", "2026-06-03 12:50", self.db)
        dkt.propagate_dk_status(db_path=self.db)
        counts = dkt.get_dk_status_counts(db_path=self.db)
        self.assertEqual(counts["CONFIRMING"], 1)
        self.assertEqual(counts["NULLIFYING"], 1)
        self.assertEqual(counts["NEUTRAL"], 1)  # CCC had no DK row


class TestPendingRetired(_Base):
    def test_default_dk_status_is_neutral(self):
        insert_signal("AAA", "PSA", "2026-06-03 12:50", status="APPROVED", db_path=self.db)
        row = get_signals(strategy="PSA", db_path=self.db)[0]
        self.assertEqual(row["dk_status"], "NEUTRAL")

    def test_migration_renames_pending_and_old_names(self):
        sid = insert_signal("AAA", "PSA", "2026-06-03 12:50", db_path=self.db)
        sid2 = insert_signal("BBB", "PSA", "2026-06-03 12:50", db_path=self.db)
        sid3 = insert_signal("CCC", "PSA", "2026-06-03 12:50", db_path=self.db)
        with get_connection(self.db) as conn:
            conn.execute("UPDATE prime_signals SET dk_status='PENDING' WHERE signal_id=?", (sid,))
            conn.execute("UPDATE prime_signals SET dk_status='CONFIRMED' WHERE signal_id=?", (sid2,))
            conn.execute("UPDATE prime_signals SET dk_status='NULLIFIED' WHERE signal_id=?", (sid3,))
            conn.commit()
        # Re-run migrations (idempotent) -> old names renamed to three-state.
        init_signals_table(self.db)
        states = {r["symbol"]: r["dk_status"] for r in get_signals(strategy="PSA", db_path=self.db)}
        self.assertEqual(states["AAA"], "NEUTRAL")
        self.assertEqual(states["BBB"], "CONFIRMING")
        self.assertEqual(states["CCC"], "NULLIFYING")

    def test_no_pending_after_scan(self):
        insert_signal("AAA", "PSA", "2026-06-03 12:50", status="APPROVED", db_path=self.db)
        dkt.propagate_dk_status(db_path=self.db)
        all_states = {r["dk_status"] for r in get_signals(db_path=self.db)}
        self.assertNotIn("PENDING", all_states)


class TestFeedDirection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).parent / "_test_dk3_sr"
        self.tmp.mkdir(exist_ok=True)
        self.f = self.tmp / "dk_prints_001.json"

    def tearDown(self):
        if self.f.exists():
            self.f.unlink()
        if self.tmp.exists() and not any(self.tmp.iterdir()):
            self.tmp.rmdir()

    def test_direction_in_prints(self):
        self.assertIn("direction", feed.PRINT_KEYS)
        self.f.write_text(json.dumps({"prints": [
            {"symbol": "AAA", "price": 100.0, "volume": 5000, "side": "BUY",
             "timestamp": "2026-06-03T10:00:00"},
            {"symbol": "AAA", "price": 100.0, "volume": 4000, "side": "SELL",
             "timestamp": "2026-06-03T10:01:00"},
        ]}))
        prints = feed.get_dk_prints(["AAA"], scan_results_dir=self.tmp)
        dirs = [p["direction"] for p in prints]
        self.assertEqual(dirs, ["LONG", "SHORT"])
        for p in prints:
            for k in feed.PRINT_KEYS:
                self.assertIn(k, p)


if __name__ == "__main__":
    unittest.main()
