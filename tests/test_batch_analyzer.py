"""
Sprint 12 Item 2 (ML-15) acceptance tests -- Batch Entry Analysis.
Covers concentration detection, correlation flagging, batch_score range,
empty batch, DB persistence, batch_id tagging.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import get_latest_batch_summary, init_db
from prime_analytics.prime_signals_db import init_signals_table
from prime_intelligence.prime_batch_analyzer import analyze_batch


class TestBatchAnalyzer(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_batch.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_empty_batch(self):
        result = analyze_batch([], db_path=self.db)
        self.assertEqual(result["signal_count"], 0)
        self.assertEqual(result["batch_score"], 100.0)
        self.assertIsNotNone(result["batch_id"])

    def test_concentration_detection(self):
        signals = [
            {"symbol": "AAPL", "price_at_scan": 185},
            {"symbol": "MSFT", "price_at_scan": 415},
            {"symbol": "NVDA", "price_at_scan": 800},
        ]
        result = analyze_batch(signals, db_path=self.db)
        self.assertTrue(result["concentration_breach"])
        self.assertEqual(result["max_sector"], "Technology")

    def test_no_concentration_breach(self):
        signals = [
            {"symbol": "AAPL", "price_at_scan": 185},
            {"symbol": "JPM", "price_at_scan": 200},
            {"symbol": "UNH", "price_at_scan": 500},
        ]
        result = analyze_batch(signals, db_path=self.db)
        self.assertFalse(result["concentration_breach"])

    def test_correlation_flagging(self):
        signals = [
            {"symbol": "AAPL", "price_at_scan": 185},
            {"symbol": "MSFT", "price_at_scan": 415},
        ]
        result = analyze_batch(signals, db_path=self.db)
        self.assertTrue(len(result["correlation_flags"]) >= 1)

    def test_batch_score_range(self):
        signals = [{"symbol": f"SYM{i}", "price_at_scan": 100} for i in range(5)]
        result = analyze_batch(signals, db_path=self.db)
        self.assertGreaterEqual(result["batch_score"], 0)
        self.assertLessEqual(result["batch_score"], 100)

    def test_batch_score_penalized_for_concentration(self):
        all_tech = [
            {"symbol": "AAPL", "price_at_scan": 185},
            {"symbol": "MSFT", "price_at_scan": 415},
            {"symbol": "NVDA", "price_at_scan": 800},
        ]
        diversified = [
            {"symbol": "AAPL", "price_at_scan": 185},
            {"symbol": "JPM", "price_at_scan": 200},
            {"symbol": "XOM", "price_at_scan": 110},
        ]
        tech_result = analyze_batch(all_tech, db_path=self.db)
        div_result = analyze_batch(diversified, db_path=self.db)
        self.assertLess(tech_result["batch_score"], div_result["batch_score"])

    def test_aggregate_risk(self):
        signals = [
            {"symbol": "AAPL", "price_at_scan": 185, "shares": 100},
        ]
        result = analyze_batch(signals, portfolio_value=100_000, db_path=self.db)
        self.assertGreater(result["aggregate_risk"], 0)

    def test_db_persistence(self):
        analyze_batch([{"symbol": "AAPL", "price_at_scan": 185}], db_path=self.db)
        summary = get_latest_batch_summary(db_path=self.db)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["signal_count"], 1)

    def test_batch_id_generated(self):
        result = analyze_batch([{"symbol": "AAPL", "price_at_scan": 185}], db_path=self.db)
        self.assertIsNotNone(result["batch_id"])
        self.assertTrue(len(result["batch_id"]) > 10)

    def test_persistence_idempotent(self):
        r1 = analyze_batch([{"symbol": "AAPL", "price_at_scan": 185}], db_path=self.db)
        r2 = analyze_batch([{"symbol": "MSFT", "price_at_scan": 415}], db_path=self.db)
        latest = get_latest_batch_summary(db_path=self.db)
        self.assertEqual(latest["batch_id"], r2["batch_id"])


if __name__ == "__main__":
    unittest.main()
