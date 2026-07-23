"""
Sprint 16 Item 3 (Index Trader strategy) acceptance tests.

Covers the pure scoring core (SMA, golden/death crossover, relative strength,
classification for every tier) and scan orchestration (IDX signal insertion to
prime_signals, DK nullifier suppression, MATA account routing) -- all offline
via an injected bars_by_symbol map.

Note: this is the Sprint 16 technical index strategy in
prime_intelligence/prime_index_scanner.py. It coexists with the earlier
options-flow scanner in prime_scanners/prime_index_scanner.py (test_index_scanner.py).
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table, get_signals
from prime_intelligence import prime_index_scanner as idx
from prime_intelligence.prime_dk_trader import _write_dk_row


def _bars(closes, volumes=None):
    n = len(closes)
    vols = volumes if volumes is not None else [1_000_000] * n
    return [{"close": c, "volume": v, "high": c, "low": c, "open": c, "timestamp": i}
            for i, (c, v) in enumerate(zip(closes, vols))]


class TestMathHelpers(unittest.TestCase):
    def test_compute_sma(self):
        self.assertEqual(idx.compute_sma([1, 2, 3, 4], 4), 2.5)
        self.assertIsNone(idx.compute_sma([1, 2], 5))

    def test_golden_cross(self):
        # fast(2) crosses above slow(3) on the last bar
        closes = [10, 10, 10, 1, 1, 12]
        self.assertEqual(idx.detect_sma_crossover(closes, fast=2, slow=3), "GOLDEN")

    def test_death_cross(self):
        # fast(2) crosses below slow(3) on the last bar
        closes = [1, 1, 1, 12, 12, 2]
        self.assertEqual(idx.detect_sma_crossover(closes, fast=2, slow=3), "DEATH")

    def test_no_crossover(self):
        closes = [100.0 + i for i in range(260)]  # steadily rising, fast stays above
        self.assertIsNone(idx.detect_sma_crossover(closes))

    def test_relative_strength(self):
        sym = [100.0] * 21
        sym[-1] = 110.0  # +10% over 20 bars
        spy = [100.0] * 21
        spy[-1] = 105.0  # +5%
        rs = idx.relative_strength(sym, spy, lookback=20)
        self.assertAlmostEqual(rs, 5.0, places=3)


class TestCrossoverMinBarsGate(unittest.TestCase):
    """CALC-IDX-6: off-by-one in the SMA-200 crossover minimum-bars gate
    silently zeroed trend_score's crossover component at exactly 200 bars
    (the standard fetch size) because the "prev" reading needs period+1
    (201) closes for a strict full-period window."""

    @staticmethod
    def _spike_series(n):
        # Flat at 90 for n-1 bars, then a spike on the very last bar -- fast
        # SMA(50) crosses above slow SMA(200) only on the latest bar.
        return [90.0] * (n - 1) + [200.0]

    def test_200_bars_crossover_included(self):
        closes = self._spike_series(200)
        self.assertEqual(idx.detect_sma_crossover(closes, fast=50, slow=200), "GOLDEN")

    def test_199_bars_crossover_excluded(self):
        closes = self._spike_series(199)
        volumes = [1_000_000] * 199
        spy_closes = [100.0] * 199
        self.assertIsNone(idx.compute_metrics(closes, volumes, spy_closes))

    def test_201_bars_crossover_included(self):
        closes = self._spike_series(201)
        self.assertEqual(idx.detect_sma_crossover(closes, fast=50, slow=200), "GOLDEN")

    def test_200_bars_compute_metrics_includes_crossover(self):
        closes = self._spike_series(200)
        volumes = [1_000_000] * 200
        spy_closes = [100.0] * 200
        metrics = idx.compute_metrics(closes, volumes, spy_closes)
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["crossover"], "GOLDEN")

    def test_sma_at_full_period_unaffected_above_threshold(self):
        # >= period+1 closes: _sma_at must still use a full, unclamped window.
        closes = list(range(1, 202))  # 201 points
        self.assertEqual(idx._sma_at(closes, 200, 1), sum(range(1, 201)) / 200)


class TestClassification(unittest.TestCase):
    def test_strong_long(self):
        m = {"price": 120, "sma50": 110, "sma100": 105, "sma200": 100,
             "crossover": "GOLDEN", "rs_vs_spy": 3.0, "volume_ratio": 1.5}
        out = idx.classify_index(m)
        self.assertEqual(out["classification"], idx.STRONG_LONG)
        self.assertEqual(out["direction"], "LONG")

    def test_weak_long_without_volume(self):
        m = {"price": 120, "sma50": 110, "sma100": 105, "sma200": 100,
             "crossover": "GOLDEN", "rs_vs_spy": 3.0, "volume_ratio": 0.8}
        out = idx.classify_index(m)
        self.assertEqual(out["classification"], idx.WEAK_LONG)
        self.assertEqual(out["direction"], "LONG")

    def test_strong_short(self):
        m = {"price": 80, "sma50": 90, "sma100": 95, "sma200": 100,
             "crossover": "DEATH", "rs_vs_spy": -3.0, "volume_ratio": 1.5}
        out = idx.classify_index(m)
        self.assertEqual(out["classification"], idx.STRONG_SHORT)
        self.assertEqual(out["direction"], "SHORT")

    def test_weak_short_without_volume(self):
        m = {"price": 80, "sma50": 90, "sma100": 95, "sma200": 100,
             "crossover": "DEATH", "rs_vs_spy": -3.0, "volume_ratio": 0.9}
        out = idx.classify_index(m)
        self.assertEqual(out["classification"], idx.WEAK_SHORT)
        self.assertEqual(out["direction"], "SHORT")

    def test_neutral(self):
        # trend_score nets to 0 -> NEUTRAL / FLAT
        m = {"price": 105, "sma50": 100, "sma100": 110, "sma200": 100,
             "crossover": None, "rs_vs_spy": 0.0, "volume_ratio": 1.0}
        out = idx.classify_index(m)
        self.assertEqual(out["classification"], idx.NEUTRAL)
        self.assertEqual(out["direction"], "FLAT")


class TestScanOrchestration(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_idx_trader.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self.no_cfg = self.db.parent / "_no_idx_cfg.json"

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _strong_long_bars(self):
        # steadily rising -> price above all SMAs, fast > slow; volume confirms
        return _bars([100.0 + i * 0.5 for i in range(260)],
                     volumes=[1_000_000] * 259 + [3_000_000])

    def _spy_flat_bars(self):
        return _bars([100.0] * 260)

    def test_idx_signal_written_with_strategy_and_direction(self):
        bars_by_symbol = {"SPY": self._spy_flat_bars(), "XLK": self._strong_long_bars()}
        idx.run_index_scan(symbols=["SPY", "XLK"], db_path=self.db,
                           config_path=self.no_cfg, bars_by_symbol=bars_by_symbol)
        rows = get_signals(strategy="IDX", db_path=self.db)
        self.assertIn("XLK", {r["symbol"] for r in rows})
        xlk = [r for r in rows if r["symbol"] == "XLK"][0]
        self.assertEqual(xlk["strategy"], "IDX")
        self.assertEqual(xlk["instrument_type"], "ETF")
        self.assertEqual(xlk["direction"], "LONG")
        self.assertIn(xlk["tier"], (idx.STRONG_LONG, idx.WEAK_LONG))
        factors = json.loads(xlk["factors"])
        self.assertEqual(factors["routed_account"], "Joint Brokerage")

    def test_dk_nullifier_suppresses_index_signal(self):
        _write_dk_row("XLK", {"dk_score": 0.0, "dk_status": "NULLIFYING", "detail": {}},
                      "NULLIFIER", "2026-06-03T00:00:00", self.db)
        bars_by_symbol = {"SPY": self._spy_flat_bars(), "XLK": self._strong_long_bars()}
        summary = idx.run_index_scan(symbols=["SPY", "XLK"], db_path=self.db,
                                     config_path=self.no_cfg, bars_by_symbol=bars_by_symbol)
        self.assertIn("XLK", summary["suppressed"])
        idx_rows = get_signals(strategy="IDX", db_path=self.db)
        xlk = [r for r in idx_rows if r["symbol"] == "XLK"][0]
        self.assertEqual(xlk["status"], "SUPPRESSED")

    def test_route_index_account_default(self):
        self.assertEqual(idx.route_index_account(self.no_cfg), "Joint Brokerage")

    def test_universe_has_14_instruments(self):
        self.assertEqual(len(idx.INDEX_UNIVERSE), 14)
        for sym in ("SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLV", "XLI",
                    "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU"):
            self.assertIn(sym, idx.INDEX_UNIVERSE)


class TestIdxSessionDedup(unittest.TestCase):
    """WO-PRIME-IDX-DEDUP-01: IDX signals are upserted per session, not duplicated."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_idx_dedup.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self.no_cfg = self.db.parent / "_no_idx_dedup_cfg.json"

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _strong_long_bars(self, scale=1.0):
        closes = [100.0 * scale + i * 0.5 for i in range(260)]
        return _bars(closes, volumes=[1_000_000] * 259 + [3_000_000])

    def _spy_flat_bars(self):
        return _bars([100.0] * 260)

    def test_second_run_same_day_does_not_add_rows(self):
        """Running IDX twice on the same calendar day must yield 1 row per symbol."""
        symbols = ["SPY", "XLK", "XLF"]
        bars = {s: (self._spy_flat_bars() if s == "SPY" else self._strong_long_bars())
                for s in symbols}
        idx.run_index_scan(symbols=symbols, db_path=self.db,
                           config_path=self.no_cfg, bars_by_symbol=bars,
                           scan_ts="2026-07-17T11:54:00")
        count_first = len(get_signals(strategy="IDX", db_path=self.db))

        idx.run_index_scan(symbols=symbols, db_path=self.db,
                           config_path=self.no_cfg, bars_by_symbol=bars,
                           scan_ts="2026-07-17T12:12:00")
        count_second = len(get_signals(strategy="IDX", db_path=self.db))

        self.assertEqual(count_first, count_second,
                         "Second IDX run same session must not add rows")

    def test_second_run_updates_scan_ts_not_duplicates(self):
        """After two same-day runs, XLK appears exactly once with the later scan_ts."""
        bars = {"SPY": self._spy_flat_bars(), "XLK": self._strong_long_bars()}
        idx.run_index_scan(symbols=["SPY", "XLK"], db_path=self.db,
                           config_path=self.no_cfg, bars_by_symbol=bars,
                           scan_ts="2026-07-17T11:54:00")
        idx.run_index_scan(symbols=["SPY", "XLK"], db_path=self.db,
                           config_path=self.no_cfg, bars_by_symbol=bars,
                           scan_ts="2026-07-17T12:12:00")

        xlk_rows = [r for r in get_signals(strategy="IDX", db_path=self.db)
                    if r["symbol"] == "XLK"]
        self.assertEqual(len(xlk_rows), 1)
        self.assertEqual(xlk_rows[0]["scan_ts"], "2026-07-17T12:12:00")

    def test_different_day_creates_new_row(self):
        """Signals on separate calendar days must each get their own row."""
        bars = {"SPY": self._spy_flat_bars(), "XLK": self._strong_long_bars()}
        idx.run_index_scan(symbols=["SPY", "XLK"], db_path=self.db,
                           config_path=self.no_cfg, bars_by_symbol=bars,
                           scan_ts="2026-07-16T11:54:00")
        idx.run_index_scan(symbols=["SPY", "XLK"], db_path=self.db,
                           config_path=self.no_cfg, bars_by_symbol=bars,
                           scan_ts="2026-07-17T11:54:00")

        xlk_rows = [r for r in get_signals(strategy="IDX", db_path=self.db)
                    if r["symbol"] == "XLK"]
        self.assertEqual(len(xlk_rows), 2,
                         "Signals on different days must be separate rows")


if __name__ == "__main__":
    unittest.main()
