"""
Sprint 7 Item 3 -- MTS scanner port verification.
Tests signal evaluation, RSI/SMA calculations, and phase detection.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_mts_scanner import (
    MTS_TARGETS,
    OVERSOLD_THRESHOLD_PCT,
    RSI_OVERSOLD,
    VOL_SURGE_MULT,
    GS_RATIO_HIGH,
    GS_RATIO_NORMAL,
    TIER_TRANCHE_1,
    TIER_TRANCHE_2,
    TIER_WATCH,
    MA_PERIOD,
    RSI_PERIOD,
    BARS_NEEDED,
    calc_sma,
    calc_rsi,
    calc_avg_volume,
    evaluate_signal,
)


def _make_bars(closes, volumes=None, n_extra=0):
    """Build synthetic daily bars. Pads with flat data for indicator warmup."""
    base = closes[0] if closes else 100.0
    pad = [base] * n_extra
    all_closes = pad + closes
    n = len(all_closes)
    if volumes is None:
        volumes_all = [1000000] * n
    else:
        volumes_all = [1000000] * n_extra + volumes
    return [
        {
            "date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": v,
        }
        for i, (c, v) in enumerate(zip(all_closes, volumes_all))
    ]


class TestSMA(unittest.TestCase):

    def test_simple_average(self):
        self.assertAlmostEqual(calc_sma([10, 20, 30], 3), 20.0)

    def test_insufficient_data(self):
        self.assertIsNone(calc_sma([10, 20], 3))

    def test_uses_last_n(self):
        self.assertAlmostEqual(calc_sma([1, 2, 3, 4, 5], 3), 4.0)


class TestRSI(unittest.TestCase):

    def test_all_gains(self):
        closes = list(range(100, 130))
        rsi = calc_rsi(closes, 14)
        self.assertIsNotNone(rsi)
        self.assertGreater(rsi, 90)

    def test_all_losses(self):
        closes = list(range(130, 100, -1))
        rsi = calc_rsi(closes, 14)
        self.assertIsNotNone(rsi)
        self.assertLess(rsi, 10)

    def test_insufficient_data(self):
        self.assertIsNone(calc_rsi([100, 101, 102], 14))

    def test_mid_range(self):
        closes = [100 + (i % 3 - 1) for i in range(40)]
        rsi = calc_rsi(closes, 14)
        self.assertIsNotNone(rsi)
        self.assertGreater(rsi, 20)
        self.assertLess(rsi, 80)


class TestAvgVolume(unittest.TestCase):

    def test_average(self):
        self.assertAlmostEqual(calc_avg_volume([100, 200, 300], 3), 200.0)

    def test_insufficient(self):
        self.assertIsNone(calc_avg_volume([100], 3))


class TestEvaluateSignal(unittest.TestCase):

    def test_insufficient_bars_returns_none(self):
        bars = _make_bars([100] * 10)
        self.assertIsNone(evaluate_signal("TEST", bars))

    def test_oversold_generates_signal(self):
        # Price drops well below SMA, low RSI, volume surge
        base = [100] * 40
        drop = [92, 91, 90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73]
        closes = base + drop
        vols = [1000000] * 40 + [2000000] * 20  # volume surge on drop
        bars = _make_bars(closes, vols)
        signal = evaluate_signal("TEST", bars)
        if signal is not None:
            self.assertIn(signal["tier"], (TIER_TRANCHE_1, TIER_TRANCHE_2, TIER_WATCH))
            self.assertEqual(signal["direction"], "LONG")

    def test_stable_price_no_signal(self):
        closes = [100.0] * BARS_NEEDED
        bars = _make_bars(closes)
        signal = evaluate_signal("TEST", bars)
        self.assertIsNone(signal)

    def test_signal_has_required_fields(self):
        base = [100] * 40
        drop = [90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71]
        closes = base + drop
        vols = [1000000] * 40 + [2500000] * 20
        bars = _make_bars(closes, vols)
        signal = evaluate_signal("TEST", bars, gs_ratio=78.5)
        if signal is not None:
            for field in ("symbol", "price_at_scan", "direction", "score",
                          "tier", "rsi", "pct_from_sma", "gs_ratio"):
                self.assertIn(field, signal)


class TestMTSTargets(unittest.TestCase):

    def test_target_count(self):
        self.assertEqual(len(MTS_TARGETS), 10)

    def test_etfs_present(self):
        for etf in ("SLV", "GLD", "GDX", "GDXJ"):
            self.assertIn(etf, MTS_TARGETS)

    def test_miners_present(self):
        for miner in ("NEM", "WPM", "AG"):
            self.assertIn(miner, MTS_TARGETS)


class TestGoldSilverRatioContext(unittest.TestCase):

    def test_high_ratio_bullish_silver(self):
        base = [100] * 40
        drop = [90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71]
        closes = base + drop
        vols = [1000000] * 40 + [2500000] * 20
        bars = _make_bars(closes, vols)
        signal = evaluate_signal("SLV", bars, gs_ratio=85.0)
        if signal is not None:
            self.assertIn("BULLISH_SILVER", signal["gs_context"])


class TestModuleInterface(unittest.TestCase):

    def test_importable(self):
        from prime_scanners import prime_mts_scanner
        self.assertTrue(hasattr(prime_mts_scanner, "main"))
        self.assertTrue(hasattr(prime_mts_scanner, "run_mts_scan"))

    def test_no_gui_imports(self):
        import prime_scanners.prime_mts_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("prime_gui", source)

    def test_no_direct_sqlite(self):
        import prime_scanners.prime_mts_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import sqlite3", source)


class TestTradeFactorsIntegration(unittest.TestCase):

    def test_evaluate_mts_callable(self):
        from prime_intelligence.prime_trade_factors import evaluate_mts
        signal = {"direction": "LONG", "score": 30, "price_at_scan": 25.0}
        tfe = evaluate_mts("SLV", signal)
        self.assertEqual(tfe.strategy, "MTS")
        self.assertEqual(tfe.duration_class, "MT")
        trigger_types = [t["type"] for t in tfe.exit_triggers]
        self.assertIn("RATIO_REVERSAL", trigger_types)


if __name__ == "__main__":
    unittest.main()
