"""
Sprint 16 Item 4 (DK feed -- mature strategy + stub interface) acceptance tests.

Covers:
  * prime_dk_feed.get_dk_prints stub returns correctly-shaped data (PRINT_KEYS),
    filters by symbol/date, and the abstraction-layer interface contract.
  * score_dk_prints matured classification using volume_ratio, price_proximity,
    and repeat_activity for SIGNAL / NULLIFIER / NEUTRAL.
  * prime_dk_trader integrates the matured verdict (NULLIFIER override, SIGNAL
    upgrade) while leaving the legacy composite path intact.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data import prime_dk_feed as feed
from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table, get_signals
from prime_intelligence.prime_dark_pool import score_dk_prints
from prime_intelligence import prime_dk_trader as dkt


class TestDkFeedStub(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).parent / "_test_scan_results"
        self.tmp.mkdir(exist_ok=True)
        self._files = []

    def tearDown(self):
        for f in self._files:
            if f.exists():
                f.unlink()
        if self.tmp.exists() and not any(self.tmp.iterdir()):
            self.tmp.rmdir()

    def _write(self, name, payload):
        p = self.tmp / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        self._files.append(p)
        return p

    def test_empty_when_no_files(self):
        self.assertEqual(feed.get_dk_prints(["AAPL"], scan_results_dir=self.tmp), [])

    def test_returns_correctly_shaped_data(self):
        self._write("dk_prints_001.json", {"prints": [
            {"symbol": "AAPL", "price": 185.0, "volume": 15000,
             "timestamp": "2026-06-03T14:30:00", "venue": "ATS"},
        ]})
        out = feed.get_dk_prints(["AAPL"], scan_results_dir=self.tmp)
        self.assertEqual(len(out), 1)
        rec = out[0]
        for k in feed.PRINT_KEYS:
            self.assertIn(k, rec)
        self.assertEqual(rec["symbol"], "AAPL")
        self.assertEqual(rec["volume"], 15000)
        self.assertIsInstance(rec["price"], float)

    def test_filters_by_symbol(self):
        self._write("dk_prints_002.json", [
            {"symbol": "AAPL", "price": 185.0, "volume": 10000, "timestamp": "2026-06-03T10:00:00"},
            {"symbol": "MSFT", "price": 400.0, "volume": 12000, "timestamp": "2026-06-03T10:00:00"},
        ])
        out = feed.get_dk_prints(["MSFT"], scan_results_dir=self.tmp)
        self.assertEqual({r["symbol"] for r in out}, {"MSFT"})

    def test_filters_by_date(self):
        self._write("dk_prints_003.json", [
            {"symbol": "AAPL", "price": 185.0, "volume": 10000, "timestamp": "2026-06-03T10:00:00"},
            {"symbol": "AAPL", "price": 186.0, "volume": 11000, "timestamp": "2026-06-02T10:00:00"},
        ])
        out = feed.get_dk_prints(["AAPL"], date="2026-06-03", scan_results_dir=self.tmp)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["timestamp"].startswith("2026-06-03"))

    def test_accepts_legacy_size_ts_keys(self):
        self._write("dk_prints_004.json", [
            {"symbol": "AAPL", "price": 185.0, "size": 9000, "ts": "2026-06-03T10:00:00"},
        ])
        out = feed.get_dk_prints(["AAPL"], scan_results_dir=self.tmp)
        self.assertEqual(out[0]["volume"], 9000)
        self.assertEqual(out[0]["timestamp"], "2026-06-03T10:00:00")

    def test_string_symbol_accepted(self):
        self.assertEqual(feed.get_dk_prints("AAPL", scan_results_dir=self.tmp), [])


class TestScoreDkPrints(unittest.TestCase):
    def test_empty_prints_neutral(self):
        out = score_dk_prints([])
        self.assertEqual(out["verdict"], None)
        self.assertEqual(out["repeat_activity"], 0)

    def test_signal_accumulation_near_price(self):
        # 5 prints all within 0.5% of 100.0, heavy dark volume -> SIGNAL
        prints = [{"symbol": "AAA", "price": 100.0 + (i % 2) * 0.1, "volume": 20000}
                  for i in range(5)]
        out = score_dk_prints(prints, reference_price=100.0, total_volume=100000)
        self.assertGreaterEqual(out["price_proximity"], 0.5)
        self.assertGreaterEqual(out["print_score"], 50.0)
        self.assertEqual(out["verdict"], "SIGNAL")
        # all three factors present
        for k in ("volume_ratio", "price_proximity", "repeat_activity"):
            self.assertIn(k, out)

    def test_nullifier_heavy_volume_away_from_price(self):
        # prints scattered far from reference price, but huge dark volume share
        prints = [{"symbol": "AAA", "price": 90.0, "volume": 30000},
                  {"symbol": "AAA", "price": 110.0, "volume": 30000}]
        out = score_dk_prints(prints, reference_price=100.0, total_volume=100000)
        self.assertLess(out["price_proximity"], 0.3)
        self.assertGreaterEqual(out["volume_ratio"], 0.5)
        self.assertEqual(out["verdict"], "NULLIFIER")


class TestTraderIntegration(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_dk_feed_trader.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    @patch("prime_intelligence.prime_dk_trader.score_dk_signal")
    @patch("prime_intelligence.prime_dk_trader.get_dk_prints")
    def test_matured_signal_upgrades_neutral_composite(self, mock_prints, mock_dk):
        # Composite says NEUTRAL, but matured prints say SIGNAL -> SIGNAL row.
        mock_dk.return_value = {"dk_score": 10.0, "dk_status": "NEUTRAL", "detail": {}}
        mock_prints.return_value = [
            {"symbol": "AAA", "price": 100.0, "volume": 25000} for _ in range(5)
        ]
        with patch("prime_intelligence.prime_dk_trader.score_dk_prints",
                   return_value={"verdict": "SIGNAL", "print_score": 80.0,
                                 "volume_ratio": 0.6, "price_proximity": 1.0,
                                 "repeat_activity": 5}):
            summary = dkt.run_dk_trader_scan(symbols=["AAA"],
                                             scan_ts="2026-06-03 10:00", db_path=self.db)
        self.assertEqual(summary["signals"], ["AAA"])
        rows = get_signals(strategy="DK", db_path=self.db)
        self.assertEqual(rows[0]["tier"], "SIGNAL")
        self.assertEqual(rows[0]["dk_status"], "CONFIRMING")
        factors = json.loads(rows[0]["factors"])
        self.assertIn("matured", factors)
        self.assertEqual(factors["matured"]["verdict"], "SIGNAL")

    @patch("prime_intelligence.prime_dk_trader.score_dk_signal")
    @patch("prime_intelligence.prime_dk_trader.get_dk_prints")
    def test_matured_nullifier_overrides_confirming(self, mock_prints, mock_dk):
        mock_dk.return_value = {"dk_score": 70.0, "dk_status": "CONFIRMING", "detail": {}}
        mock_prints.return_value = [{"symbol": "AAA", "price": 90.0, "volume": 30000}]
        with patch("prime_intelligence.prime_dk_trader.score_dk_prints",
                   return_value={"verdict": "NULLIFIER", "print_score": 30.0,
                                 "volume_ratio": 0.6, "price_proximity": 0.0,
                                 "repeat_activity": 1}):
            summary = dkt.run_dk_trader_scan(symbols=["AAA"],
                                             scan_ts="2026-06-03 10:00", db_path=self.db)
        self.assertEqual(summary["nullifiers"], ["AAA"])
        rows = get_signals(strategy="DK", db_path=self.db)
        self.assertEqual(rows[0]["tier"], "NULLIFIER")


if __name__ == "__main__":
    unittest.main()
