"""
WO-PRIME-MTFA-01 acceptance tests.

Covers: trend detection, scoring, high/low flags, bridge adapter,
scenario Types 9 and 10, scanner registration in SCANNER_MAP.
"""

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_mtfa_scanner import (
    _trend,
    _scan_one,
    analyze_symbol,
    fetch_daily_bars,
    fetch_intraday_bars,
    run_mtfa_scan,
    SCORE_ALL_ALIGNED,
    SCORE_TWO_ALIGNED,
    SCORE_ONE_ALIGNED,
    SCORE_NONE,
)
from prime_bridge import prime_signal_bridge as bridge
from prime_analytics.prime_signals_db import init_signals_table, get_signals
from prime_data.prime_db import init_db
from prime_scenarios.prime_scenario_engine import (
    detect_scenarios,
    SCENARIO_TYPES,
)


def _bars(closes, highs=None, lows=None):
    """Build minimal bar list from close prices."""
    bars = []
    for i, c in enumerate(closes):
        bars.append({
            "open": c, "high": highs[i] if highs else c,
            "low": lows[i] if lows else c, "close": c, "volume": 1_000_000,
        })
    return bars


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------

class TestTrend(unittest.TestCase):
    def test_up_trend(self):
        self.assertEqual(_trend([100, 101, 102, 103, 104]), "UP")

    def test_down_trend(self):
        self.assertEqual(_trend([104, 103, 102, 101, 100]), "DOWN")

    def test_flat(self):
        self.assertEqual(_trend([100, 100, 100, 100]), "FLAT")

    def test_single_bar_flat(self):
        self.assertEqual(_trend([100]), "FLAT")

    def test_empty_flat(self):
        self.assertEqual(_trend([]), "FLAT")


# ---------------------------------------------------------------------------
# Score + tier
# ---------------------------------------------------------------------------

class TestAnalyzeSymbol(unittest.TestCase):
    def _analyze(self, intraday, weekly_daily, annual_daily):
        """Helper: annual_daily is the full daily bar list; weekly is taken from its tail."""
        daily = _bars(annual_daily)
        intra = _bars(intraday)
        return analyze_symbol(daily, intra)

    def test_all_up_score_100_strong(self):
        up = list(range(100, 352))   # 252 rising bars (annual)
        r = self._analyze(
            intraday=list(range(100, 120)),   # rising intraday
            weekly_daily=None,
            annual_daily=up,
        )
        self.assertEqual(r["score"], SCORE_ALL_ALIGNED)
        self.assertEqual(r["tier"], "STRONG")
        self.assertEqual(r["direction"], "LONG")
        self.assertEqual(r["aligned_count"], 3)

    def test_two_up_one_down_score_667_weak(self):
        # annual: UP, weekly: DOWN (last 5 descend), intraday: UP
        annual = list(range(100, 352))  # 252 bars UP overall
        annual[-5:] = [210, 205, 200, 195, 190]  # last 5 go DOWN (weekly)
        r = self._analyze(
            intraday=list(range(100, 120)),   # UP
            weekly_daily=None,
            annual_daily=annual,
        )
        self.assertEqual(r["score"], SCORE_TWO_ALIGNED)
        self.assertEqual(r["tier"], "WEAK")

    def test_all_flat_score_0_watch(self):
        flat = [100] * 252
        r = self._analyze(
            intraday=[100] * 20,
            weekly_daily=None,
            annual_daily=flat,
        )
        self.assertIn(r["tier"], ("WATCH",))
        self.assertLessEqual(r["score"], SCORE_ONE_ALIGNED)

    def test_none_when_no_data(self):
        self.assertIsNone(analyze_symbol([], []))

    def test_near_52w_high_flag(self):
        # Last price within 2% of 52-week high
        highs = [100.0] * 252
        highs[100] = 200.0   # 52-week high = 200
        closes = [100.0] * 252
        closes[-1] = 198.0   # within 2% of 200
        daily = []
        for i, c in enumerate(closes):
            daily.append({"open": c, "high": highs[i], "low": c * 0.99,
                          "close": c, "volume": 1_000_000})
        intra = _bars([198.0, 198.5, 199.0])
        r = analyze_symbol(daily, intra)
        self.assertTrue(r["near_52w_high"])
        self.assertFalse(r["near_52w_low"])

    def test_near_session_low_flag(self):
        # Last price within 1% of session low
        closes = [100.0, 99.0, 98.5, 98.0, 98.1]
        lows = [99.5, 98.5, 98.0, 97.8, 98.0]
        intra = []
        for i, c in enumerate(closes):
            intra.append({"open": c, "high": c + 0.5, "low": lows[i],
                          "close": c, "volume": 500_000})
        daily = _bars(list(range(100, 352)))
        r = analyze_symbol(daily, intra)
        self.assertTrue(r["near_session_low"])


# ---------------------------------------------------------------------------
# Bridge adapter
# ---------------------------------------------------------------------------

class TestMTFABridge(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_mtfa_bridge.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _make_scan(self, signals):
        return {
            "scan_time": datetime.utcnow().isoformat(),
            "scanner": "prime_mtfa_scanner",
            "signals": signals,
        }

    def test_strong_signal_inserted(self):
        data = self._make_scan([{
            "symbol": "AAPL",
            "scan_ts": "2026-07-14 09:30",
            "tier": "STRONG",
            "direction": "LONG",
            "score": 100.0,
            "entry_price": 225.50,
            "intraday_trend": "UP",
            "weekly_trend": "UP",
            "annual_trend": "UP",
            "aligned_count": 3,
            "near_52w_high": False,
            "near_52w_low": False,
            "near_session_high": False,
            "near_session_low": False,
        }])
        count = bridge.bridge_mtfa_result(data, self.db)
        self.assertEqual(count, 1)
        sigs = get_signals(db_path=self.db, strategy="MTFA")
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["tier"], "STRONG")
        self.assertEqual(sigs[0]["trigger_source"], "MTFA_STRONG")

    def test_weak_signal_inserted(self):
        data = self._make_scan([{
            "symbol": "MSFT",
            "scan_ts": "2026-07-14 09:30",
            "tier": "WEAK",
            "direction": "LONG",
            "score": 66.7,
            "entry_price": 450.0,
            "intraday_trend": "UP",
            "weekly_trend": "UP",
            "annual_trend": "DOWN",
            "aligned_count": 2,
            "near_52w_high": False,
            "near_52w_low": False,
            "near_session_high": False,
            "near_session_low": False,
        }])
        count = bridge.bridge_mtfa_result(data, self.db)
        self.assertEqual(count, 1)

    def test_watch_signal_excluded(self):
        data = self._make_scan([{
            "symbol": "GOOG",
            "scan_ts": "2026-07-14 09:30",
            "tier": "WATCH",
            "direction": "LONG",
            "score": 33.3,
            "entry_price": 180.0,
            "intraday_trend": "UP",
            "weekly_trend": "DOWN",
            "annual_trend": "DOWN",
            "aligned_count": 1,
            "near_52w_high": False,
            "near_52w_low": False,
            "near_session_high": False,
            "near_session_low": False,
        }])
        count = bridge.bridge_mtfa_result(data, self.db)
        self.assertEqual(count, 0)

    def test_dedup_idempotent(self):
        sig = {
            "symbol": "NVDA",
            "scan_ts": "2026-07-14 09:30",
            "tier": "STRONG",
            "direction": "LONG",
            "score": 100.0,
            "entry_price": 130.0,
            "intraday_trend": "UP",
            "weekly_trend": "UP",
            "annual_trend": "UP",
            "aligned_count": 3,
            "near_52w_high": True,
            "near_52w_low": False,
            "near_session_high": False,
            "near_session_low": False,
        }
        data = self._make_scan([sig])
        self.assertEqual(bridge.bridge_mtfa_result(data, self.db), 1)
        self.assertEqual(bridge.bridge_mtfa_result(data, self.db), 0)  # idempotent


# ---------------------------------------------------------------------------
# Scenario engine: Types 9 and 10
# ---------------------------------------------------------------------------

def _sig(strategy, tier="", direction="LONG", symbol="AAPL",
         status="APPROVED", signal_id=None):
    scan_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    return {
        "signal_id": signal_id or f"{strategy}-{symbol}-{tier}",
        "strategy": strategy,
        "tier": tier,
        "direction": direction,
        "symbol": symbol,
        "status": status,
        "scan_ts": scan_ts,
    }


class TestScenarioTypes(unittest.TestCase):
    def test_type_9_mtfa_plus_idx(self):
        """Type 9: MTFA STRONG + IDX."""
        signals = [
            _sig("MTFA", tier="STRONG", symbol="AAPL"),
            _sig("IDX", tier="STRONG-LONG", direction="LONG", symbol="SPY"),
        ]
        scenarios = detect_scenarios(signals)
        types = [s["type_num"] for s in scenarios]
        self.assertIn("9", types)

    def test_type_9_mtfa_plus_psa(self):
        """Type 9: MTFA STRONG + PSA APPROVED on same symbol."""
        signals = [
            _sig("MTFA", tier="STRONG", symbol="AAPL"),
            _sig("PSA", tier="", symbol="AAPL", status="APPROVED"),
        ]
        scenarios = detect_scenarios(signals)
        types = [s["type_num"] for s in scenarios]
        self.assertIn("9", types)

    def test_type_9_no_confirming_signal_no_fire(self):
        """Type 9 must NOT fire when MTFA STRONG has no confirming signal."""
        signals = [_sig("MTFA", tier="STRONG", symbol="AAPL")]
        scenarios = detect_scenarios(signals)
        types = [s["type_num"] for s in scenarios]
        self.assertNotIn("9", types)

    def test_type_10_ultimate(self):
        """Type 10: IDX STRONG + UOA + PSA APPROVED + MTFA STRONG."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        signals = [
            _sig("IDX", tier="STRONG-LONG", direction="LONG", symbol="SPY"),
            _sig("UOA", tier="STRONG", symbol="AAPL"),
            _sig("PSA", tier="", symbol="AAPL", status="APPROVED"),
            _sig("MTFA", tier="STRONG", symbol="AAPL"),
        ]
        scenarios = detect_scenarios(signals)
        types = [s["type_num"] for s in scenarios]
        self.assertIn("10", types)
        # Type 10 should supersede Type 4 for the same symbol set
        self.assertNotIn("4", types)

    def test_type_10_falls_back_to_type4_without_mtfa(self):
        """Without MTFA, the same signal set fires Type 4, not Type 10."""
        signals = [
            _sig("IDX", tier="STRONG-LONG", direction="LONG", symbol="SPY"),
            _sig("UOA", tier="STRONG", symbol="AAPL"),
            _sig("PSA", tier="", symbol="AAPL", status="APPROVED"),
        ]
        scenarios = detect_scenarios(signals)
        types = [s["type_num"] for s in scenarios]
        self.assertIn("4", types)
        self.assertNotIn("10", types)

    def test_type9_watch_not_eligible(self):
        """MTFA WATCH must not trigger Type 9."""
        signals = [
            _sig("MTFA", tier="WATCH", symbol="AAPL"),
            _sig("IDX", tier="STRONG-LONG", direction="LONG", symbol="SPY"),
        ]
        scenarios = detect_scenarios(signals)
        types = [s["type_num"] for s in scenarios]
        self.assertNotIn("9", types)


# ---------------------------------------------------------------------------
# Registry checks
# ---------------------------------------------------------------------------

class TestMTFARegistry(unittest.TestCase):
    def test_scenario_types_9_10_registered(self):
        self.assertIn("9", SCENARIO_TYPES)
        self.assertIn("10", SCENARIO_TYPES)
        self.assertEqual(SCENARIO_TYPES["9"]["conviction"], "HIGH")
        self.assertEqual(SCENARIO_TYPES["10"]["conviction"], "HIGHEST")

    def test_scanner_map_contains_mtfa(self):
        ROUTES_SRC = (PROJECT_ROOT / "prime_api" / "prime_api_routes.py").read_text(encoding="utf-8")
        self.assertIn('"mtfa"', ROUTES_SRC)
        self.assertIn("prime_mtfa_scanner", ROUTES_SRC)

    def test_mtfa_in_stage1(self):
        ROUTES_SRC = (PROJECT_ROOT / "prime_api" / "prime_api_routes.py").read_text(encoding="utf-8")
        import re
        m = re.search(r"stage1\s*=\s*\[([^\]]+)\]", ROUTES_SRC)
        self.assertIsNotNone(m, "stage1 list not found")
        self.assertIn("mtfa", m.group(1))

    def test_mtfa_polygon_api_class(self):
        ROUTES_SRC = (PROJECT_ROOT / "prime_api" / "prime_api_routes.py").read_text(encoding="utf-8")
        self.assertIn('"mtfa":  "polygon"', ROUTES_SRC)

    def test_ingest_latest_includes_mtfa(self):
        BRIDGE_SRC = (PROJECT_ROOT / "prime_bridge" / "prime_signal_bridge.py").read_text(encoding="utf-8")
        self.assertIn("MTFA", BRIDGE_SRC)
        self.assertIn("bridge_mtfa_result", BRIDGE_SRC)
        self.assertIn("mtfa_scan_*.json", BRIDGE_SRC)


# ---------------------------------------------------------------------------
# WO-PRIME-MTFA-BACKBONE-01-B: bar cache integration + 2-session window
# ---------------------------------------------------------------------------

class TestMTFABackboneB(unittest.TestCase):

    def _daily_bar(self, close=100.0, bar_date="2026-07-17"):
        return {"bar_date": bar_date, "open": close, "high": close + 1,
                "low": close - 1, "close": close, "volume": 2_000_000}

    def _intraday_bar(self, close=100.0, ts=1_720_000_000_000):
        return {"timestamp": ts, "open": close, "high": close + 0.5,
                "low": close - 0.5, "close": close, "volume": 500_000}

    # 3a — write_daily_bars and write_intraday_bars called after a successful fetch
    @patch("prime_scanners.prime_mtfa_scanner.write_intraday_bars")
    @patch("prime_scanners.prime_mtfa_scanner.write_daily_bars")
    @patch("prime_scanners.prime_mtfa_scanner.fetch_intraday_bars")
    @patch("prime_scanners.prime_mtfa_scanner.fetch_daily_bars")
    def test_cache_write_called_after_successful_fetch(
        self, mock_fd, mock_fi, mock_wd, mock_wi
    ):
        daily = [self._daily_bar()] * 10
        intra = [self._intraday_bar()] * 10
        mock_fd.return_value = daily
        mock_fi.return_value = intra
        _scan_one("AAPL", "key", 1.0, 100, "2026-07-17 09:30")
        mock_wd.assert_called_once_with("AAPL", daily)
        mock_wi.assert_called_once_with("AAPL", intra)

    # 3a — cache writes skipped when stage0 filter rejects the symbol
    @patch("prime_scanners.prime_mtfa_scanner.write_intraday_bars")
    @patch("prime_scanners.prime_mtfa_scanner.write_daily_bars")
    @patch("prime_scanners.prime_mtfa_scanner.fetch_intraday_bars")
    @patch("prime_scanners.prime_mtfa_scanner.fetch_daily_bars")
    def test_cache_not_written_on_stage0_reject(
        self, mock_fd, mock_fi, mock_wd, mock_wi
    ):
        # price=1.0 < min_price=10.0 triggers stage0 return before cache writes
        mock_fd.return_value = [self._daily_bar(close=1.0)]
        _, _, outcome = _scan_one("AAPL", "key", 10.0, 100, "2026-07-17 09:30")
        self.assertEqual(outcome, "stage0")
        mock_wd.assert_not_called()
        mock_wi.assert_not_called()

    # 3a — cache write exception must not abort the scan
    @patch("prime_scanners.prime_mtfa_scanner.write_intraday_bars",
           side_effect=RuntimeError("db locked"))
    @patch("prime_scanners.prime_mtfa_scanner.write_daily_bars",
           side_effect=RuntimeError("db locked"))
    @patch("prime_scanners.prime_mtfa_scanner.fetch_intraday_bars")
    @patch("prime_scanners.prime_mtfa_scanner.fetch_daily_bars")
    def test_cache_write_failure_nonfatal(
        self, mock_fd, mock_fi, mock_wd, mock_wi
    ):
        mock_fd.return_value = [self._daily_bar()] * 10
        mock_fi.return_value = [self._intraday_bar()] * 10
        sym, sig, outcome = _scan_one("AAPL", "key", 1.0, 100, "2026-07-17 09:30")
        self.assertEqual(sym, "AAPL")
        self.assertNotEqual(outcome, "fetch")

    # 3b — intraday endpoint must include both yesterday and today
    @patch("prime_scanners.prime_mtfa_scanner._polygon_get")
    def test_intraday_endpoint_spans_two_sessions(self, mock_get):
        mock_get.return_value = {"results": []}
        fetch_intraday_bars("AAPL", "key")
        endpoint = mock_get.call_args[0][0]
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertIn(yesterday, endpoint)
        self.assertIn(today, endpoint)

    # 3b — each intraday bar carries a 'timestamp' key (ms epoch)
    @patch("prime_scanners.prime_mtfa_scanner._polygon_get")
    def test_intraday_bars_contain_timestamp_key(self, mock_get):
        mock_get.return_value = {"results": [
            {"t": 1_720_000_000_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 500_000}
        ]}
        bars = fetch_intraday_bars("AAPL", "key")
        self.assertEqual(len(bars), 1)
        self.assertIn("timestamp", bars[0])
        self.assertEqual(bars[0]["timestamp"], 1_720_000_000_000)

    # 3b — each daily bar carries a 'bar_date' key (YYYY-MM-DD)
    @patch("prime_scanners.prime_mtfa_scanner._polygon_get")
    def test_daily_bars_contain_bar_date_key(self, mock_get):
        mock_get.return_value = {"results": [
            {"t": 1_720_000_000_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 1_000_000}
        ]}
        bars = fetch_daily_bars("AAPL", 1, "key")
        self.assertEqual(len(bars), 1)
        self.assertIn("bar_date", bars[0])
        self.assertRegex(bars[0]["bar_date"], r"^\d{4}-\d{2}-\d{2}$")

    # 3c — universe resolved from ops_config.json via resolve_psa_universe
    @patch("prime_scanners.prime_psa_scanner.resolve_psa_universe")
    @patch("prime_scanners.prime_mtfa_scanner.get_config")
    def test_universe_coupled_to_ops_config(self, mock_cfg, mock_resolve):
        mock_resolve.return_value = []
        cfg = mock_cfg.return_value
        cfg.ops.psa_universe = "default"
        cfg.ops.psa_universe_custom = []
        cfg.ops.psa_universe_sector = None
        cfg.ops.mtfa_workers = 1
        run_mtfa_scan(api_key="test_key", universe=None)
        mock_resolve.assert_called_once_with(
            mode="default", custom=[], sector=None
        )

    # 3c — switching config to mag7 causes only Mag7 symbols to be scanned
    @patch("prime_scanners.prime_mtfa_scanner._polygon_get")
    @patch("prime_scanners.prime_psa_scanner.resolve_psa_universe")
    @patch("prime_scanners.prime_mtfa_scanner.get_config")
    def test_universe_switch_to_mag7(self, mock_cfg, mock_resolve, mock_get):
        MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]
        mock_resolve.return_value = MAG7
        cfg = mock_cfg.return_value
        cfg.ops.psa_universe = "mag7"
        cfg.ops.psa_universe_custom = []
        cfg.ops.psa_universe_sector = None
        cfg.ops.mtfa_workers = 2
        mock_get.return_value = None  # all fetches fail → fetch_failures only
        result = run_mtfa_scan(api_key="test_key", universe=None)
        self.assertEqual(result["universe_size"], len(MAG7))
        self.assertEqual(result["fetch_failures"], len(MAG7))


if __name__ == "__main__":
    unittest.main()
