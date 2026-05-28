"""
Sprint 10 Item 1 (CIL-PRIME-DK-001) acceptance tests -- Dark Pool Scanner.
Covers DK scoring, get_nullifier_flags integration, FINRA unavailable graceful,
prime_signals schema migration, scanner entry point.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_analytics.prime_signals_db import init_signals_table, insert_signal, get_signals
from prime_data.prime_db import get_connection, init_db
from prime_intelligence.prime_dark_pool import score_dk_signal, get_nullifier_flags
from prime_intelligence.prime_dk_scanner import run_dk_scan


class TestScoreDkSignal(unittest.TestCase):
    """AC: DK score combines three sources correctly."""

    @patch("prime_intelligence.prime_dk_data.get_finra_ats_volume")
    @patch("prime_intelligence.prime_dk_data.get_tape_prints")
    @patch("prime_intelligence.prime_dk_data.get_short_volume")
    def test_all_unavailable_returns_unavailable(self, mock_short, mock_tape, mock_ats):
        mock_ats.return_value = None
        mock_tape.return_value = None
        mock_short.return_value = None
        result = score_dk_signal("AAPL")
        self.assertEqual(result["dk_status"], "UNAVAILABLE")
        self.assertIsNone(result["dk_score"])

    @patch("prime_intelligence.prime_dk_data.get_finra_ats_volume")
    @patch("prime_intelligence.prime_dk_data.get_tape_prints")
    @patch("prime_intelligence.prime_dk_data.get_short_volume")
    def test_high_ats_plus_blocks_confirming(self, mock_short, mock_tape, mock_ats):
        mock_ats.return_value = {"ats_pct": 50, "ats_rising": True}
        mock_tape.return_value = [
            {"ts": "2026-05-27T10:00:00", "size": 15000, "price": 185.0, "mid_offset": 0.02},
        ]
        mock_short.return_value = {"short_pct": 20, "short_avg_20d": 18}
        result = score_dk_signal("AAPL")
        self.assertEqual(result["dk_status"], "CONFIRMING")
        self.assertGreaterEqual(result["dk_score"], 50)

    @patch("prime_intelligence.prime_dk_data.get_finra_ats_volume")
    @patch("prime_intelligence.prime_dk_data.get_tape_prints")
    @patch("prime_intelligence.prime_dk_data.get_short_volume")
    def test_short_spike_nullifying_override(self, mock_short, mock_tape, mock_ats):
        mock_ats.return_value = {"ats_pct": 60, "ats_rising": True}
        mock_tape.return_value = [{"ts": "t", "size": 20000, "price": 185, "mid_offset": 0.01}]
        mock_short.return_value = {"short_pct": 45, "short_avg_20d": 20}
        result = score_dk_signal("AAPL")
        self.assertEqual(result["dk_status"], "NULLIFYING")
        self.assertEqual(result["dk_score"], 0.0)

    @patch("prime_intelligence.prime_dk_data.get_finra_ats_volume")
    @patch("prime_intelligence.prime_dk_data.get_tape_prints")
    @patch("prime_intelligence.prime_dk_data.get_short_volume")
    def test_low_ats_no_blocks_neutral(self, mock_short, mock_tape, mock_ats):
        mock_ats.return_value = {"ats_pct": 30, "ats_rising": False}
        mock_tape.return_value = []
        mock_short.return_value = {"short_pct": 15, "short_avg_20d": 14}
        result = score_dk_signal("AAPL")
        self.assertEqual(result["dk_status"], "NEUTRAL")
        self.assertEqual(result["dk_score"], 0.0)

    @patch("prime_intelligence.prime_dk_data.get_finra_ats_volume")
    @patch("prime_intelligence.prime_dk_data.get_tape_prints")
    @patch("prime_intelligence.prime_dk_data.get_short_volume")
    def test_block_print_cap_at_50(self, mock_short, mock_tape, mock_ats):
        mock_ats.return_value = None
        mock_tape.return_value = [
            {"ts": f"t{i}", "size": 12000, "price": 185, "mid_offset": 0.01}
            for i in range(5)
        ]
        mock_short.return_value = None
        result = score_dk_signal("AAPL")
        self.assertLessEqual(result["dk_score"], 50)


class TestGetNullifierFlagsIntegration(unittest.TestCase):
    """AC: get_nullifier_flags returns correct structure for each dk_status."""

    @patch("prime_intelligence.prime_dark_pool.score_dk_signal")
    def test_nullifying_overrides_clear(self, mock_dk):
        mock_dk.return_value = {
            "dk_score": 0.0,
            "dk_status": "NULLIFYING",
            "detail": {"reason": "Short spike"},
        }
        result = get_nullifier_flags("AAPL")
        self.assertEqual(result["status"], "NULLIFIED")
        self.assertTrue(result["nullified"])
        self.assertEqual(result["dk_status"], "NULLIFYING")

    @patch("prime_intelligence.prime_dark_pool.score_dk_signal")
    def test_confirming_annotates_clear(self, mock_dk):
        mock_dk.return_value = {
            "dk_score": 60.0,
            "dk_status": "CONFIRMING",
            "detail": {},
        }
        result = get_nullifier_flags("AAPL")
        self.assertEqual(result["status"], "CLEAR")
        self.assertIn("CONFIRMING", result["rationale"])
        self.assertEqual(result["dk_score"], 60.0)

    @patch("prime_intelligence.prime_dark_pool.score_dk_signal")
    def test_unavailable_passes_through(self, mock_dk):
        mock_dk.return_value = {
            "dk_score": None,
            "dk_status": "UNAVAILABLE",
            "detail": {"reason": "no data"},
        }
        result = get_nullifier_flags("AAPL")
        self.assertEqual(result["dk_status"], "UNAVAILABLE")
        self.assertFalse(result["nullified"])

    def test_returns_all_required_fields(self):
        result = get_nullifier_flags("AAPL")
        for key in ("status", "flags", "flag_count", "rationale",
                     "nullified", "dk_score", "dk_status", "dk_detail"):
            self.assertIn(key, result)


class TestDkScannerEntryPoint(unittest.TestCase):
    """AC: DK scanner runs and updates prime_signals."""

    @patch("prime_intelligence.prime_dk_scanner.score_dk_signal")
    def test_run_dk_scan_returns_summary(self, mock_dk):
        mock_dk.return_value = {
            "dk_score": 60.0,
            "dk_status": "CONFIRMING",
            "detail": {"ats_pct": 50},
        }
        result = run_dk_scan(symbols=["AAPL", "MSFT"])
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(len(result["confirming"]), 2)

    @patch("prime_intelligence.prime_dk_scanner.score_dk_signal")
    def test_scan_handles_errors_gracefully(self, mock_dk):
        mock_dk.side_effect = [
            {"dk_score": 60, "dk_status": "CONFIRMING", "detail": {}},
            RuntimeError("data error"),
        ]
        result = run_dk_scan(symbols=["AAPL", "MSFT"])
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(len(result["errors"]), 1)


class TestSignalsSchemaMigration(unittest.TestCase):
    """AC: prime_signals schema migration is idempotent."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_dk_migrate.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_migration_adds_dk_columns(self):
        init_signals_table(self.db)
        with get_connection(self.db) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(prime_signals)").fetchall()]
        self.assertIn("dk_score", cols)
        self.assertIn("dk_status", cols)

    def test_migration_idempotent(self):
        init_signals_table(self.db)
        init_signals_table(self.db)  # second call should not error
        with get_connection(self.db) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(prime_signals)").fetchall()]
        self.assertIn("dk_score", cols)

    def test_insert_with_dk_fields(self):
        init_signals_table(self.db)
        sid = insert_signal("AAPL", "UOA", "2026-05-27T10:00:00", db_path=self.db)
        with get_connection(self.db) as conn:
            conn.execute(
                "UPDATE prime_signals SET dk_score=75.0, dk_status='CONFIRMING' WHERE signal_id=?",
                (sid,),
            )
            conn.commit()
            row = conn.execute("SELECT dk_score, dk_status FROM prime_signals WHERE signal_id=?",
                               (sid,)).fetchone()
        self.assertEqual(dict(row)["dk_score"], 75.0)
        self.assertEqual(dict(row)["dk_status"], "CONFIRMING")


if __name__ == "__main__":
    unittest.main()
