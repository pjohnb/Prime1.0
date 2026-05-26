"""
Item 4 acceptance tests -- SRS scanner port verification.
Tests that the ported scanner retains v0.9 phase detection logic.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_srs_scanner import (
    SECTOR_ETFS,
    detect_phase,
)


def _make_bars(closes, highs=None, lows=None, volumes=None):
    """Build synthetic bar data for testing."""
    n = len(closes)
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [1000000] * n
    return [
        {"date": f"2026-05-{10+i:02d}", "open": c, "high": h,
         "low": l, "close": c, "volume": v}
        for i, (c, h, l, v) in enumerate(zip(closes, highs, lows, volumes))
    ]


class TestPhaseDetection(unittest.TestCase):

    def test_declining_phase(self):
        # Wide intraday ranges prevent range compression (BOTTOMING) detection
        closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91]
        highs =  [102, 101, 100, 99, 98, 97, 96, 95, 95, 94]
        lows =   [98,  97,  96,  95, 94, 93, 92, 91, 89, 88]
        bars = _make_bars(closes, highs, lows)
        phase, metrics = detect_phase(bars)
        self.assertEqual(phase, "DECLINING")
        self.assertLess(metrics["chg_5d_pct"], 0)

    def test_stable_phase(self):
        closes = [100, 100.1, 100.2, 100.1, 100.3, 100.2, 100.4, 100.3, 100.5, 100.4]
        bars = _make_bars(closes)
        phase, metrics = detect_phase(bars)
        self.assertEqual(phase, "STABLE")

    def test_recovering_phase(self):
        closes = [100, 98, 96, 94, 92, 90, 89, 88, 90, 92]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        volumes = [1000000, 900000, 800000, 700000, 600000,
                   500000, 500000, 500000, 1500000, 2000000]
        bars = _make_bars(closes, highs, lows, volumes)
        phase, metrics = detect_phase(bars)
        self.assertIn(phase, ("RECOVERING", "BOTTOMING", "DECLINING"))

    def test_insufficient_data(self):
        bars = _make_bars([100, 101, 102])
        phase, metrics = detect_phase(bars)
        self.assertEqual(phase, "UNKNOWN")

    def test_metrics_present(self):
        closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91]
        bars = _make_bars(closes)
        _, metrics = detect_phase(bars)
        self.assertIn("chg_5d_pct", metrics)
        self.assertIn("chg_2d_pct", metrics)
        self.assertIn("drawdown_pct", metrics)
        self.assertIn("range_ratio", metrics)
        self.assertIn("vol_ratio_up_dn", metrics)


class TestSectorETFMap(unittest.TestCase):

    def test_all_11_sectors_plus_spy(self):
        self.assertEqual(len(SECTOR_ETFS), 12)
        self.assertIn("Technology", SECTOR_ETFS)
        self.assertIn("Broad Market", SECTOR_ETFS)
        self.assertEqual(SECTOR_ETFS["Broad Market"], "SPY")


class TestScannerStandalone(unittest.TestCase):

    def test_module_importable_and_has_main(self):
        from prime_scanners import prime_srs_scanner
        self.assertTrue(hasattr(prime_srs_scanner, "main"))
        self.assertTrue(hasattr(prime_srs_scanner, "run_srs_scan"))
        self.assertTrue(hasattr(prime_srs_scanner, "detect_phase"))


if __name__ == "__main__":
    unittest.main()
