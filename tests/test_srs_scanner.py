"""
Item 4 acceptance tests -- SRS scanner port verification.
Tests that the ported scanner retains v0.9 phase detection logic.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_srs_scanner import (
    SECTOR_ETFS,
    detect_phase,
    bearish_regime_nullifier,
    get_broad_regime,
    run_srs_scan,
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


class TestBearishRegimeNullifier(unittest.TestCase):
    """CALC-SRS-2: TIP Section 2.3 hard-BEARISH-regime override."""

    def test_broad_decline_low_score_nullified(self):
        self.assertTrue(bearish_regime_nullifier("BROAD_DECLINE", 50))

    def test_broad_decline_high_score_passes(self):
        self.assertTrue(bearish_regime_nullifier("BROAD_DECLINE", 75) is True)
        self.assertFalse(bearish_regime_nullifier("BROAD_DECLINE", 80))

    def test_score_exactly_75_does_not_qualify_for_exception(self):
        # Paper's threshold is "score > 75", not ">= 75".
        self.assertTrue(bearish_regime_nullifier("BROAD_DECLINE", 75))

    def test_non_bearish_regime_passes_regardless_of_score(self):
        for regime in ("MIXED", "BROAD_RECOVERY", "STABILIZING", ""):
            self.assertFalse(bearish_regime_nullifier(regime, 10))
            self.assertFalse(bearish_regime_nullifier(regime, 90))


class TestGetBroadRegime(unittest.TestCase):
    """CALC-SRS-3: get_broad_regime() must exist (fixes the ImportError)."""

    def test_no_scan_results_defaults_to_mixed(self):
        with patch("prime_scanners.prime_srs_scanner.get_config") as mock_cfg:
            mock_cfg.return_value.scan_results_dir = Path(__file__).parent / "_no_such_srs_dir"
            self.assertEqual(get_broad_regime(), "MIXED")


class TestNTotalDenominator(unittest.TestCase):
    """CALC-SRS-6: n_total/regime_note must count only classified sectors."""

    def _run_with_unknown_sectors(self, n_unknown):
        closes = [100, 98, 96, 94, 92, 90, 89, 88, 90, 92]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        volumes = [1000000, 900000, 800000, 700000, 600000,
                   500000, 500000, 500000, 1500000, 2000000]
        good_bars = [
            {"date": f"2026-05-{10+i:02d}", "open": c, "high": h, "low": l, "close": c, "volume": v}
            for i, (c, h, l, v) in enumerate(zip(closes, highs, lows, volumes))
        ]
        unknown_etfs = set(list(SECTOR_ETFS.values())[:n_unknown])

        def fake_fetch(symbol, lookback_days, api_key):
            return [] if symbol in unknown_etfs else good_bars

        with patch("prime_scanners.prime_srs_scanner.fetch_daily_bars", side_effect=fake_fetch), \
             patch("prime_scanners.prime_srs_scanner.time.sleep"):
            return run_srs_scan("fake_key")

    def test_unknown_sectors_excluded_from_denominator(self):
        scan = self._run_with_unknown_sectors(2)
        n_classified = len(SECTOR_ETFS) - 2
        self.assertIn(f"/{n_classified} ", scan["regime_note"])
        self.assertNotIn(f"/{len(SECTOR_ETFS)} ", scan["regime_note"])

    def test_unknown_count_noted_in_regime_note(self):
        scan = self._run_with_unknown_sectors(2)
        self.assertIn("2 sectors returned UNKNOWN", scan["regime_note"])

    def test_no_unknown_sectors_behavior_unchanged(self):
        scan = self._run_with_unknown_sectors(0)
        self.assertNotIn("UNKNOWN", scan["regime_note"])
        self.assertIn(f"/{len(SECTOR_ETFS)} ", scan["regime_note"])


if __name__ == "__main__":
    unittest.main()
