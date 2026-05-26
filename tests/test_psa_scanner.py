"""
Sprint 7 Item 3 -- PSA scanner port verification.
Tests A-B-C-D ratio analysis, pattern detection, and threshold gates.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_psa_scanner import (
    DEFAULT_BASELINE_PERIODS,
    DEFAULT_LONG_PERIODS,
    DEFAULT_SHORT_PERIODS,
    DEFAULT_REQUIRED_POSITIVE,
    DEFAULT_MOMENTUM_THRESHOLD,
    DEFAULT_VOLUME_THRESHOLD,
    DEFAULT_VOLATILITY_THRESHOLD,
    DEFAULT_BC_MAX_DRAWDOWN,
    DEFAULT_CD_MAX_DRAWDOWN,
    MAX_REASONABLE_MOMENTUM,
    analyze_symbol,
    _pct_changes,
    _safe_mean,
    _safe_std,
    _max_drawdown,
    _detect_patterns,
)


def _make_bars(closes, volumes=None):
    n = len(closes)
    if volumes is None:
        volumes = [1000000] * n
    return [
        {
            "open": c,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": v,
            "timestamp": 1716800000000 + i * 300000,
        }
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


class TestPctChanges(unittest.TestCase):

    def test_basic(self):
        result = _pct_changes([100, 110, 121])
        self.assertAlmostEqual(result[0], 0.1)
        self.assertAlmostEqual(result[1], 0.1)

    def test_empty(self):
        self.assertEqual(_pct_changes([100]), [])
        self.assertEqual(_pct_changes([]), [])

    def test_zero_safe(self):
        result = _pct_changes([0, 100])
        self.assertEqual(result, [])


class TestSafeMean(unittest.TestCase):

    def test_basic(self):
        self.assertAlmostEqual(_safe_mean([1, 2, 3]), 2.0)

    def test_empty(self):
        self.assertEqual(_safe_mean([]), 0.0)


class TestSafeStd(unittest.TestCase):

    def test_constant(self):
        self.assertAlmostEqual(_safe_std([5, 5, 5]), 0.0)

    def test_variation(self):
        self.assertGreater(_safe_std([1, 2, 3, 4, 5]), 0)

    def test_short(self):
        self.assertEqual(_safe_std([1]), 0.0)


class TestMaxDrawdown(unittest.TestCase):

    def test_no_drawdown(self):
        self.assertAlmostEqual(_max_drawdown([100, 101, 102, 103]), 0.0)

    def test_drawdown(self):
        dd = _max_drawdown([100, 110, 100])
        self.assertAlmostEqual(dd, (10 / 110) * 100, places=1)

    def test_single_bar(self):
        self.assertAlmostEqual(_max_drawdown([100]), 0.0)


class TestAnalyzeSymbol(unittest.TestCase):

    def _default_thresholds(self):
        return {
            "momentum": DEFAULT_MOMENTUM_THRESHOLD,
            "volume": DEFAULT_VOLUME_THRESHOLD,
            "volatility": DEFAULT_VOLATILITY_THRESHOLD,
        }

    def test_insufficient_bars(self):
        bars = _make_bars([100] * 10)
        result = analyze_symbol(
            bars, DEFAULT_BASELINE_PERIODS, DEFAULT_LONG_PERIODS,
            DEFAULT_SHORT_PERIODS, DEFAULT_REQUIRED_POSITIVE,
            self._default_thresholds(), DEFAULT_BC_MAX_DRAWDOWN, DEFAULT_CD_MAX_DRAWDOWN,
        )
        self.assertFalse(result["approved"])

    def test_flat_market_rejected(self):
        n = DEFAULT_BASELINE_PERIODS + DEFAULT_LONG_PERIODS + DEFAULT_SHORT_PERIODS
        bars = _make_bars([100.0] * n)
        result = analyze_symbol(
            bars, DEFAULT_BASELINE_PERIODS, DEFAULT_LONG_PERIODS,
            DEFAULT_SHORT_PERIODS, DEFAULT_REQUIRED_POSITIVE,
            self._default_thresholds(), DEFAULT_BC_MAX_DRAWDOWN, DEFAULT_CD_MAX_DRAWDOWN,
        )
        self.assertFalse(result["approved"])

    def test_uptrend_with_momentum(self):
        n = DEFAULT_BASELINE_PERIODS + DEFAULT_LONG_PERIODS + DEFAULT_SHORT_PERIODS
        # Baseline flat, then accelerating uptrend
        base = [100.0] * DEFAULT_BASELINE_PERIODS
        mid = [100.0 + i * 0.3 for i in range(DEFAULT_LONG_PERIODS)]
        end = [100.0 + DEFAULT_LONG_PERIODS * 0.3 + i * 1.0
               for i in range(DEFAULT_SHORT_PERIODS)]
        closes = base + mid + end
        # Higher volume in recent segment
        vols = [500000] * DEFAULT_BASELINE_PERIODS + \
               [700000] * DEFAULT_LONG_PERIODS + \
               [1200000] * DEFAULT_SHORT_PERIODS
        bars = _make_bars(closes, vols)
        result = analyze_symbol(
            bars, DEFAULT_BASELINE_PERIODS, DEFAULT_LONG_PERIODS,
            DEFAULT_SHORT_PERIODS, DEFAULT_REQUIRED_POSITIVE,
            {"momentum": 10, "volume": 10, "volatility": 0},
            DEFAULT_BC_MAX_DRAWDOWN, DEFAULT_CD_MAX_DRAWDOWN,
        )
        self.assertIn("momentum_pct", result)
        self.assertIn("volume_pct", result)
        self.assertIn("volatility_pct", result)

    def test_negative_bd_direction_rejected(self):
        n = DEFAULT_BASELINE_PERIODS + DEFAULT_LONG_PERIODS + DEFAULT_SHORT_PERIODS
        base = [100.0] * DEFAULT_BASELINE_PERIODS
        down = [100.0 - i * 0.5 for i in range(DEFAULT_LONG_PERIODS + DEFAULT_SHORT_PERIODS)]
        bars = _make_bars(base + down)
        result = analyze_symbol(
            bars, DEFAULT_BASELINE_PERIODS, DEFAULT_LONG_PERIODS,
            DEFAULT_SHORT_PERIODS, DEFAULT_REQUIRED_POSITIVE,
            self._default_thresholds(), DEFAULT_BC_MAX_DRAWDOWN, DEFAULT_CD_MAX_DRAWDOWN,
        )
        self.assertFalse(result["approved"])

    def test_anomalous_momentum_rejected(self):
        n = DEFAULT_BASELINE_PERIODS + DEFAULT_LONG_PERIODS + DEFAULT_SHORT_PERIODS
        # Extreme spike in last segment
        base = [100.0] * DEFAULT_BASELINE_PERIODS
        mid = [100.0] * DEFAULT_LONG_PERIODS
        end = [100.0, 200.0, 500.0][:DEFAULT_SHORT_PERIODS]
        while len(end) < DEFAULT_SHORT_PERIODS:
            end.append(end[-1] * 2)
        bars = _make_bars(base + mid + end)
        result = analyze_symbol(
            bars, DEFAULT_BASELINE_PERIODS, DEFAULT_LONG_PERIODS,
            DEFAULT_SHORT_PERIODS, DEFAULT_REQUIRED_POSITIVE,
            self._default_thresholds(), DEFAULT_BC_MAX_DRAWDOWN, DEFAULT_CD_MAX_DRAWDOWN,
        )
        self.assertFalse(result["approved"])

    def test_result_structure(self):
        n = DEFAULT_BASELINE_PERIODS + DEFAULT_LONG_PERIODS + DEFAULT_SHORT_PERIODS
        bars = _make_bars([100.0] * n)
        result = analyze_symbol(
            bars, DEFAULT_BASELINE_PERIODS, DEFAULT_LONG_PERIODS,
            DEFAULT_SHORT_PERIODS, DEFAULT_REQUIRED_POSITIVE,
            self._default_thresholds(), DEFAULT_BC_MAX_DRAWDOWN, DEFAULT_CD_MAX_DRAWDOWN,
        )
        for key in ("approved", "momentum_pct", "volume_pct", "volatility_pct"):
            self.assertIn(key, result)


class TestPatternDetection(unittest.TestCase):

    def test_breakout(self):
        closes = [100] * 15 + [105]
        volumes = [1000000] * 15 + [2000000]
        bars = _make_bars(closes, volumes)
        patterns = _detect_patterns(bars, 10)
        self.assertIn("breakout", patterns)

    def test_higher_highs(self):
        closes = list(range(100, 110))
        bars = _make_bars(closes)
        patterns = _detect_patterns(bars, 5)
        self.assertIn("higher_highs", patterns)

    def test_volume_expansion(self):
        closes = [100] * 15
        volumes = [500000] * 14 + [1000000]
        bars = _make_bars(closes, volumes)
        patterns = _detect_patterns(bars, 10)
        self.assertIn("volume_expansion", patterns)

    def test_no_patterns_flat(self):
        bars = _make_bars([100] * 10, [1000000] * 10)
        patterns = _detect_patterns(bars, 5)
        self.assertEqual(patterns, [])


class TestModuleInterface(unittest.TestCase):

    def test_importable(self):
        from prime_scanners import prime_psa_scanner
        self.assertTrue(hasattr(prime_psa_scanner, "main"))
        self.assertTrue(hasattr(prime_psa_scanner, "run_psa_scan"))
        self.assertTrue(hasattr(prime_psa_scanner, "analyze_symbol"))

    def test_no_gui_imports(self):
        import prime_scanners.prime_psa_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("prime_gui", source)

    def test_no_direct_sqlite(self):
        import prime_scanners.prime_psa_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import sqlite3", source)


if __name__ == "__main__":
    unittest.main()
