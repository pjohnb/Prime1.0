"""
WO-PRIME-PSA-STAGE0-CALIBRATION-01 acceptance tests.

Verifies that the Stage 0 volume gate:
  1. Sums volume across all bars in the window (not single-bar)
  2. Normalizes the sum to a full-day equivalent before comparing against the
     500 000 daily threshold. PSA pulls 39 × 5-min bars (~3.25 h); raw-summing
     that window against a daily threshold would still under-count by ~2x.
     Normalization: daily_equiv = raw_sum * 78 / len(bars)
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PSA_SRC = (PROJECT_ROOT / "prime_scanners" / "prime_psa_scanner.py").read_text(encoding="utf-8")


def _make_bars(n, close=50.0, volume_per_bar=15000):
    return [
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": volume_per_bar,
         "timestamp": 1716800000000 + i * 300000}
        for i in range(n)
    ]


class TestPSAStage0VolumeAggregation(unittest.TestCase):

    # AC1: source uses sum() over bars, not bars[-1]
    def test_source_uses_sum_for_volume(self):
        self.assertIn(
            "sum(b.get(\"volume\", 0) for b in bars)",
            PSA_SRC,
            "PSA scanner must aggregate volume across all bars with sum()",
        )

    def test_source_does_not_use_single_bar_volume(self):
        import re
        # The old pattern: bars[-1].get("volume", ...) assigned to last_vol
        old_pattern = r'last_vol\s*=\s*bars\[-1\]\.get\("volume"'
        self.assertNotRegex(
            PSA_SRC, old_pattern,
            "PSA scanner must NOT use bars[-1] for volume (single-bar anti-pattern)",
        )

    # AC1b: normalization uses an interval-aware bars-per-day factor
    # (CALC-PSA-4: the old hardcoded _FULL_DAY_BARS_5MIN=78 constant was wrong
    # whenever the actual scan interval wasn't 5min).
    def test_source_uses_full_day_bars_normalization(self):
        from prime_scanners.prime_psa_scanner import _full_day_bars
        self.assertEqual(_full_day_bars("5min"), 78,
                          "5-min interval must still normalize to 78 bars/day")
        self.assertIn(
            "_full_day_bars(interval) / len(bars)",
            PSA_SRC,
            "PSA scanner must normalize raw_vol by (interval-aware bars/day / len(bars))",
        )

    # AC2: per-symbol Stage0 rejection logging present
    def test_per_symbol_stage0_debug_logging(self):
        self.assertIn(
            "Stage0 rejected %s",
            PSA_SRC,
            "PSA scanner must log per-symbol Stage0 rejection at debug level",
        )

    # AC3: functional — summed volume passes threshold that single bar would fail
    def test_summed_volume_passes_threshold(self):
        from prime_scanners.prime_psa_scanner import stage0_filter, DEFAULT_MIN_DAILY_VOLUME
        # 50 bars * 11 000 per bar = 550 000 > 500 000 threshold
        bars = _make_bars(50, close=50.0, volume_per_bar=11000)
        total_vol = sum(b["volume"] for b in bars)  # 550 000
        self.assertGreater(total_vol, DEFAULT_MIN_DAILY_VOLUME,
                           "Test bars must exceed daily volume threshold when summed")
        single_bar_vol = bars[-1]["volume"]  # 11 000
        self.assertLess(single_bar_vol, DEFAULT_MIN_DAILY_VOLUME,
                        "Single bar must be below threshold (confirming the old bug)")
        result = stage0_filter("TEST", {"price": 50.0, "volume": total_vol},
                               5.0, 500.0, DEFAULT_MIN_DAILY_VOLUME)
        self.assertIsNone(result, "Symbol with adequate summed volume must pass Stage0")

    def test_single_bar_volume_fails_threshold(self):
        from prime_scanners.prime_psa_scanner import stage0_filter, DEFAULT_MIN_DAILY_VOLUME
        single_bar_vol = 300.0  # realistic single pre-market bar (old code would use this)
        result = stage0_filter("TEST", {"price": 50.0, "volume": single_bar_vol},
                               5.0, 500.0, DEFAULT_MIN_DAILY_VOLUME)
        self.assertIsNotNone(result, "Single bar volume must fail Stage0 (confirms the old bug)")
        self.assertEqual(result["criterion"], "min_daily_volume")

    # AC3b: normalization math — half-window that would fail without it
    def test_half_window_passes_after_normalization(self):
        """39-bar window with 7000 vol/bar: raw=273K (fails 500K), normalized=546K (passes)."""
        from prime_scanners.prime_psa_scanner import stage0_filter, DEFAULT_MIN_DAILY_VOLUME
        n_bars = 39
        vol_per_bar = 7000
        raw_vol = n_bars * vol_per_bar  # 273 000 — would FAIL without normalization
        full_day_bars = 78
        normalized = raw_vol * full_day_bars / n_bars  # 546 000 — should PASS

        self.assertLess(raw_vol, DEFAULT_MIN_DAILY_VOLUME,
                        "Raw partial-window volume must be below threshold (confirms need for normalization)")
        self.assertGreater(normalized, DEFAULT_MIN_DAILY_VOLUME,
                           "Normalized daily-equivalent volume must exceed threshold")

        result = stage0_filter("TEST", {"price": 50.0, "volume": normalized},
                               5.0, 500.0, DEFAULT_MIN_DAILY_VOLUME)
        self.assertIsNone(result,
                          "Half-window with adequate annualized pace must pass Stage0 after normalization")

    # AC4: run_psa_scan integration — mocked fetch_bars returning multi-bar data
    def test_run_psa_scan_passes_symbol_with_adequate_summed_volume(self):
        from prime_scanners.prime_psa_scanner import run_psa_scan

        # 78 bars * 8000 = 624 000 total volume (passes 500K gate)
        bars = _make_bars(78, close=50.0, volume_per_bar=8000)
        # Make bars trend upward to pass momentum gates
        for i, b in enumerate(bars):
            b["close"] = 45.0 + i * 0.1
            b["open"] = b["close"] - 0.05
            b["high"] = b["close"] + 0.1
            b["low"] = b["close"] - 0.1

        mock_signal_led = MagicMock(return_value={
            "scan_time": "2026-01-01T09:00:00",
            "scanner": "prime_psa_scanner",
            "version": "1.0",
            "interval": 5,
            "universe_size": 1,
            "total_bars": 78,
            "thresholds": {},
            "analyzed": 1,
            "signals_found": 0,
            "stage0_rejected": 0,
            "stage1_rejected": 1,
            "fetch_failures": 0,
            "signals": [],
            "stage0_rejections": [],
        })

        with patch("prime_scanners.prime_psa_scanner.fetch_bars", return_value=bars), \
             patch("prime_scanners.prime_psa_scanner._cache_get_intraday", return_value=None), \
             patch("prime_scanners.prime_psa_scanner.apply_signal_led_psa",
                   side_effect=mock_signal_led) as mock_sla:
            run_psa_scan(universe=["AAPL"], api_key="test_key")

        # apply_signal_led_psa was called, meaning the symbol reached Stage1
        # (not rejected at Stage0). The stage0_rejected count in the result is 0.
        call_args = mock_signal_led.call_args[0][0]
        self.assertEqual(call_args["stage0_rejected"], 0,
                         "Symbol with adequate summed volume must NOT be Stage0 rejected")
        self.assertGreaterEqual(call_args["analyzed"], 1,
                                "Symbol must reach Stage1 analysis when volume passes")

    def test_run_psa_scan_rejects_symbol_with_zero_bars(self):
        from prime_scanners.prime_psa_scanner import run_psa_scan

        mock_signal_led = MagicMock(return_value={
            "scan_time": "2026-01-01T09:00:00",
            "scanner": "prime_psa_scanner",
            "version": "1.0",
            "interval": 5,
            "universe_size": 1,
            "total_bars": 78,
            "thresholds": {},
            "analyzed": 0,
            "signals_found": 0,
            "stage0_rejected": 0,
            "stage1_rejected": 0,
            "fetch_failures": 1,
            "signals": [],
            "stage0_rejections": [],
        })

        with patch("prime_scanners.prime_psa_scanner.fetch_bars", return_value=[]), \
             patch("prime_scanners.prime_psa_scanner._cache_get_intraday", return_value=None), \
             patch("prime_scanners.prime_psa_scanner.apply_signal_led_psa",
                   side_effect=mock_signal_led):
            run_psa_scan(universe=["AAPL"], api_key="test_key")

        call_args = mock_signal_led.call_args[0][0]
        self.assertEqual(call_args["fetch_failures"], 1,
                         "Symbol with no bars must count as fetch_failure, not Stage0 rejection")
        self.assertEqual(call_args["stage0_rejected"], 0,
                         "Empty bars must NOT produce a Stage0 rejection entry")


if __name__ == "__main__":
    unittest.main()
