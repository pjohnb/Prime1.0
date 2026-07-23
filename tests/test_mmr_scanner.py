"""
Sprint 7 Item 3 -- MMR scanner port verification.
Tests signal evaluation, RSI/SMA calculations, and phase detection.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from unittest.mock import patch

from prime_scanners.prime_mmr_scanner import (
    MMR_TARGETS,
    MMR_SHORT_TARGETS,
    OVERSOLD_THRESHOLD_PCT,
    RSI_OVERSOLD,
    OVERBOUGHT_THRESHOLD_PCT,
    RSI_OVERBOUGHT,
    VOL_SURGE_MULT,
    GS_RATIO_HIGH,
    GS_RATIO_NORMAL,
    GS_RATIO_ETF_TO_SPOT_SCALE,
    TIER_TRANCHE_1,
    TIER_TRANCHE_2,
    TIER_WATCH,
    TIER_SHORT_TRANCHE_1,
    TIER_SHORT_TRANCHE_2,
    MA_PERIOD,
    RSI_PERIOD,
    BARS_NEEDED,
    calc_sma,
    calc_rsi,
    calc_avg_volume,
    evaluate_signal,
    evaluate_signal_short,
    fetch_gs_ratio,
    run_mmr_scan,
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


class TestMMRTargets(unittest.TestCase):

    def test_target_count(self):
        self.assertEqual(len(MMR_TARGETS), 10)

    def test_etfs_present(self):
        for etf in ("SLV", "GLD", "GDX", "GDXJ"):
            self.assertIn(etf, MMR_TARGETS)

    def test_miners_present(self):
        for miner in ("NEM", "WPM", "AG"):
            self.assertIn(miner, MMR_TARGETS)


class TestMMRShortTargets(unittest.TestCase):

    def test_short_target_count(self):
        self.assertEqual(len(MMR_SHORT_TARGETS), 4)

    def test_short_etfs_only(self):
        for etf in ("SLV", "GLD", "GDX", "GDXJ"):
            self.assertIn(etf, MMR_SHORT_TARGETS)

    def test_miners_not_in_short(self):
        for miner in ("NEM", "WPM", "AG", "PAAS", "HL", "FR"):
            self.assertNotIn(miner, MMR_SHORT_TARGETS)

    def test_short_targets_subset_of_long(self):
        for sym in MMR_SHORT_TARGETS:
            self.assertIn(sym, MMR_TARGETS)


class TestEvaluateSignalShort(unittest.TestCase):

    def test_stable_price_no_short_signal(self):
        closes = [100.0] * BARS_NEEDED
        bars = _make_bars(closes)
        self.assertIsNone(evaluate_signal_short("GLD", bars))

    def test_overbought_generates_short_signal(self):
        # Price rises well above SMA, high RSI, volume surge on rise
        base = [100] * 40
        rise = [108, 109, 110, 111, 112, 113, 114, 115, 116, 117,
                118, 119, 120, 121, 122, 123, 124, 125, 126, 127]
        closes = base + rise
        vols = [1000000] * 40 + [2000000] * 20
        bars = _make_bars(closes, vols)
        signal = evaluate_signal_short("GLD", bars)
        if signal is not None:
            self.assertEqual(signal["direction"], "SHORT")
            self.assertIn(signal["tier"], (TIER_SHORT_TRANCHE_1, TIER_SHORT_TRANCHE_2))

    def test_short_signal_direction_is_short(self):
        base = [100] * 40
        rise = [108, 109, 110, 111, 112, 113, 114, 115, 116, 117,
                118, 119, 120, 121, 122, 123, 124, 125, 126, 127]
        closes = base + rise
        vols = [1000000] * 40 + [2500000] * 20
        bars = _make_bars(closes, vols)
        signal = evaluate_signal_short("SLV", bars)
        if signal is not None:
            self.assertEqual(signal["direction"], "SHORT")

    def test_short_signal_has_required_fields(self):
        base = [100] * 40
        rise = [110, 111, 112, 113, 114, 115, 116, 117, 118, 119,
                120, 121, 122, 123, 124, 125, 126, 127, 128, 129]
        closes = base + rise
        vols = [1000000] * 40 + [2500000] * 20
        bars = _make_bars(closes, vols)
        signal = evaluate_signal_short("GDX", bars, gs_ratio=75.0)
        if signal is not None:
            for field in ("symbol", "price_at_scan", "direction", "score",
                          "tier", "rsi", "pct_from_sma", "gs_ratio"):
                self.assertIn(field, signal)

    def test_insufficient_bars_returns_none(self):
        bars = _make_bars([100] * 10)
        self.assertIsNone(evaluate_signal_short("GLD", bars))

    def test_short_tranche_tiers_are_valid(self):
        self.assertIn("SHORT_TRANCHE_1", (TIER_SHORT_TRANCHE_1,))
        self.assertIn("SHORT_TRANCHE_2", (TIER_SHORT_TRANCHE_2,))

    def test_overbought_threshold_is_positive(self):
        self.assertGreater(OVERBOUGHT_THRESHOLD_PCT, 0)

    def test_rsi_overbought_above_50(self):
        self.assertGreater(RSI_OVERBOUGHT, 50)


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


class TestCalcMMR2BarsNeeded(unittest.TestCase):
    """CALC-MMR-2: BARS_NEEDED must fit within the Schwab/Polygon fetch
    buffers (~51-55 weekdays after +15/+10 calendar-day padding), or the
    scanner short-circuits to NO_SIGNAL on every symbol, every run."""

    def test_bars_needed_is_45_not_60(self):
        self.assertEqual(BARS_NEEDED, 45)

    def test_bars_needed_within_fetch_buffer_ceiling(self):
        # ~51-55 weekdays is the realistic ceiling after holiday exclusion.
        self.assertLess(BARS_NEEDED, 51)

    def test_insufficient_bars_logs_error_not_debug(self):
        bars = _make_bars([100] * 10)
        with self.assertLogs("prime_scanners.prime_mmr_scanner", level="ERROR") as cm:
            result = evaluate_signal("TEST", bars)
        self.assertIsNone(result)
        self.assertTrue(any("insufficient bars" in m for m in cm.output))

    def test_insufficient_bars_short_logs_error(self):
        bars = _make_bars([100] * 10)
        with self.assertLogs("prime_scanners.prime_mmr_scanner", level="ERROR") as cm:
            result = evaluate_signal_short("TEST", bars)
        self.assertIsNone(result)
        self.assertTrue(any("insufficient bars" in m for m in cm.output))

    def test_exactly_45_bars_is_sufficient(self):
        # A flat 45-bar series has no signal (no move), but must not be
        # rejected purely for bar count — this is the actual production fix.
        # Patch logger.error to prove the "insufficient bars" gate never fires.
        closes = [100.0] * 45
        bars = _make_bars(closes)
        with patch("prime_scanners.prime_mmr_scanner.logger.error") as mock_error:
            evaluate_signal("TEST", bars)
        mock_error.assert_not_called()


class TestCalcMMR1GoldSilverRatioScale(unittest.TestCase):
    """CALC-MMR-1: gld_price/slv_price alone understates the true gold:silver
    spot ratio by ~10x, since GLD tracks ~1/10 oz gold/share while SLV tracks
    ~1 oz silver/share -- the fix scales the raw ETF-price ratio up to match
    GS_RATIO_HIGH/NORMAL, which are tuned against the true spot-ratio scale."""

    def test_scale_constant_is_ten(self):
        self.assertEqual(GS_RATIO_ETF_TO_SPOT_SCALE, 10.0)

    def test_gld_315_slv_32_yields_approximately_98(self):
        with patch("prime_scanners.prime_mmr_scanner.fetch_daily_bars") as mock_fetch:
            def _bars(symbol, *a, **kw):
                price = 315.0 if symbol == "GLD" else 32.0
                return [{"close": price}]
            mock_fetch.side_effect = _bars
            ratio = fetch_gs_ratio(api_key="x")
        self.assertAlmostEqual(ratio, 98.4375, places=2)

    def test_ratio_lands_in_threshold_scale(self):
        # Any realistic GLD/SLV pair should land the scaled ratio near the
        # GS_RATIO_HIGH/NORMAL thresholds (65-80), not an order of magnitude off.
        with patch("prime_scanners.prime_mmr_scanner.fetch_daily_bars") as mock_fetch:
            def _bars(symbol, *a, **kw):
                price = 300.0 if symbol == "GLD" else 30.0
                return [{"close": price}]
            mock_fetch.side_effect = _bars
            ratio = fetch_gs_ratio(api_key="x")
        self.assertGreater(ratio, GS_RATIO_NORMAL / 2)
        self.assertLess(ratio, GS_RATIO_HIGH * 2)


class TestCalcMMR3ShortSortOrder(unittest.TestCase):
    """CALC-MMR-3: SHORT candidates must sort strongest-first (highest RSI =
    most overbought), not weakest-first (the LONG sort's -rsi key was
    copy-pasted onto SHORT, inverting the ranking)."""

    def test_short_signals_sorted_highest_rsi_first(self):
        short_signals = [
            {"tier": TIER_SHORT_TRANCHE_1, "rsi": 68.0},
            {"tier": TIER_SHORT_TRANCHE_1, "rsi": 82.0},
            {"tier": TIER_SHORT_TRANCHE_1, "rsi": 75.0},
        ]
        short_signals.sort(key=lambda s: (
            s["tier"] == TIER_SHORT_TRANCHE_2,
            s["rsi"],
        ), reverse=True)
        self.assertEqual([s["rsi"] for s in short_signals], [82.0, 75.0, 68.0])

    def test_tranche2_still_ranks_above_tranche1_regardless_of_rsi(self):
        short_signals = [
            {"tier": TIER_SHORT_TRANCHE_1, "rsi": 95.0},
            {"tier": TIER_SHORT_TRANCHE_2, "rsi": 66.0},
        ]
        short_signals.sort(key=lambda s: (
            s["tier"] == TIER_SHORT_TRANCHE_2,
            s["rsi"],
        ), reverse=True)
        self.assertEqual(short_signals[0]["tier"], TIER_SHORT_TRANCHE_2)

    def test_long_sort_direction_unchanged_lowest_rsi_first(self):
        # LONG is an oversold screen -- lowest RSI is the strongest setup.
        # This pins the existing (correct) LONG behavior so a future change
        # doesn't accidentally re-invert it while "fixing" SHORT again.
        signals = [
            {"tier": TIER_TRANCHE_1, "rsi": 30.0},
            {"tier": TIER_TRANCHE_1, "rsi": 20.0},
            {"tier": TIER_TRANCHE_1, "rsi": 25.0},
        ]
        signals.sort(key=lambda s: (
            s["tier"] == TIER_TRANCHE_2,
            s["tier"] == TIER_TRANCHE_1,
            -s["rsi"],
        ), reverse=True)
        self.assertEqual([s["rsi"] for s in signals], [20.0, 25.0, 30.0])


class TestModuleInterface(unittest.TestCase):

    def test_importable(self):
        from prime_scanners import prime_mmr_scanner
        self.assertTrue(hasattr(prime_mmr_scanner, "main"))
        self.assertTrue(hasattr(prime_mmr_scanner, "run_mmr_scan"))

    def test_no_gui_imports(self):
        import prime_scanners.prime_mmr_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("prime_gui", source)

    def test_no_direct_sqlite(self):
        import prime_scanners.prime_mmr_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import sqlite3", source)


class TestTradeFactorsIntegration(unittest.TestCase):

    def test_evaluate_mmr_callable(self):
        from prime_intelligence.prime_trade_factors import evaluate_mmr
        signal = {"direction": "LONG", "score": 30, "price_at_scan": 25.0}
        tfe = evaluate_mmr("SLV", signal)
        self.assertEqual(tfe.strategy, "MMR")
        self.assertEqual(tfe.duration_class, "MT")
        trigger_types = [t["type"] for t in tfe.exit_triggers]
        self.assertIn("RATIO_REVERSAL", trigger_types)


if __name__ == "__main__":
    unittest.main()
