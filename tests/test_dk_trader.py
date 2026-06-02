"""
Sprint 15 Item 1 (DK Trader) acceptance tests.

Covers SIGNAL/NULLIFIER classification, DK strategy-row writes, dk_status
propagation (CONFIRMED/NULLIFIED/PENDING), and nullifier suppression of
another strategy's output.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db, get_connection
from prime_analytics.prime_signals_db import init_signals_table, insert_signal, get_signals
from prime_intelligence import prime_dk_trader as dkt


class _Base(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_dk_trader.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()


class TestClassify(_Base):
    def test_confirming_is_signal(self):
        self.assertEqual(dkt.classify_dk({"dk_status": "CONFIRMING"}), "SIGNAL")

    def test_nullifying_is_nullifier(self):
        self.assertEqual(dkt.classify_dk({"dk_status": "NULLIFYING"}), "NULLIFIER")

    def test_neutral_and_unavailable_none(self):
        self.assertIsNone(dkt.classify_dk({"dk_status": "NEUTRAL"}))
        self.assertIsNone(dkt.classify_dk({"dk_status": "UNAVAILABLE"}))


class TestScanWrites(_Base):
    @patch("prime_intelligence.prime_dk_trader.score_dk_signal")
    def test_signal_and_nullifier_rows_written(self, mock_dk):
        mock_dk.side_effect = lambda sym: {
            "SPY": {"dk_score": 70.0, "dk_status": "CONFIRMING", "detail": {}},
            "QQQ": {"dk_score": 0.0, "dk_status": "NULLIFYING", "detail": {"reason": "short spike"}},
            "IWM": {"dk_score": 0.0, "dk_status": "NEUTRAL", "detail": {}},
        }[sym]
        summary = dkt.run_dk_trader_scan(symbols=["SPY", "QQQ", "IWM"],
                                         scan_ts="2026-06-02 10:00", db_path=self.db)
        self.assertEqual(summary["scanned"], 3)
        self.assertEqual(summary["signals"], ["SPY"])
        self.assertEqual(summary["nullifiers"], ["QQQ"])
        self.assertEqual(summary["neutral"], ["IWM"])

        dk_rows = get_signals(strategy="DK", db_path=self.db)
        self.assertEqual(len(dk_rows), 2)
        spy = next(r for r in dk_rows if r["symbol"] == "SPY")
        self.assertEqual(spy["tier"], "SIGNAL")
        self.assertEqual(spy["status"], "APPROVED")
        self.assertEqual(spy["dk_status"], "CONFIRMING")
        self.assertEqual(spy["dk_score"], 70.0)
        qqq = next(r for r in dk_rows if r["symbol"] == "QQQ")
        self.assertEqual(qqq["tier"], "NULLIFIER")
        self.assertEqual(qqq["status"], "NULLIFIER")


class TestPropagateAndSuppress(_Base):
    def _seed_dk(self):
        # DK SIGNAL on SPY, DK NULLIFIER on QQQ
        dkt._write_dk_row("SPY", {"dk_score": 70, "dk_status": "CONFIRMING", "detail": {}},
                          "SIGNAL", "2026-06-02 10:00", self.db)
        dkt._write_dk_row("QQQ", {"dk_score": 0, "dk_status": "NULLIFYING", "detail": {}},
                          "NULLIFIER", "2026-06-02 10:00", self.db)

    def test_propagate_dk_status(self):
        insert_signal("SPY", "UOA", "2026-06-02 10:00", status="APPROVED", db_path=self.db)
        insert_signal("QQQ", "PEAD", "2026-06-02 10:00", status="APPROVED", db_path=self.db)
        insert_signal("TSLA", "MTS", "2026-06-02 10:00", status="APPROVED", db_path=self.db)
        self._seed_dk()
        counts = dkt.propagate_dk_status(db_path=self.db)
        self.assertEqual(counts["CONFIRMED"], 1)
        self.assertEqual(counts["NULLIFIED"], 1)
        self.assertEqual(counts["PENDING"], 1)
        byc = {r["symbol"]: r["dk_status"] for r in get_signals(db_path=self.db) if r["strategy"] != "DK"}
        self.assertEqual(byc["SPY"], "CONFIRMED")
        self.assertEqual(byc["QQQ"], "NULLIFIED")
        self.assertEqual(byc["TSLA"], "PENDING")

    def test_nullifier_suppresses_other_strategy(self):
        # A UOA approved signal on QQQ should be suppressed by the DK NULLIFIER
        insert_signal("QQQ", "UOA", "2026-06-02 10:00", status="APPROVED", db_path=self.db)
        insert_signal("SPY", "UOA", "2026-06-02 10:00", status="APPROVED", db_path=self.db)
        self._seed_dk()
        result = dkt.apply_nullifier_suppression(db_path=self.db)
        self.assertEqual(result["suppressed"], 1)
        self.assertIn("QQQ", result["symbols"])
        rows = {r["symbol"]: r["status"] for r in get_signals(strategy="UOA", db_path=self.db)}
        self.assertEqual(rows["QQQ"], "SUPPRESSED")
        self.assertEqual(rows["SPY"], "APPROVED")  # not nullified


if __name__ == "__main__":
    unittest.main()
