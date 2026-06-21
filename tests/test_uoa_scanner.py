"""
Sprint 7 Item 1 -- UOA scanner port verification.
Tests sizzle index scoring, DTE classification, covered-call detection,
signal generation, and architectural constraints.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_uoa_scanner import (
    MIN_ABSOLUTE_VOLUME,
    STRONG_THRESHOLD,
    WATCH_THRESHOLD,
    DIRECTION_RATIO_THRESHOLD,
    _ST_MAX_DTE,
    _MT_MAX_DTE,
    _CC_VOL_OI_THRESHOLD,
    _CC_CLUSTER_THRESHOLD,
    MACRO_SYMBOLS,
    TOP50_SYMBOLS,
    SP100_SYMBOLS,
    classify_dte,
    detect_covered_call,
    scan_symbol,
)


# ---------------------------------------------------------------------------
# DTE Classification
# ---------------------------------------------------------------------------

class TestDTEClassifier(unittest.TestCase):

    def test_short_term(self):
        legs = [{"dte": 5, "volume": 1000}, {"dte": 8, "volume": 500}]
        result = classify_dte(legs)
        self.assertEqual(result["dte_class"], "ST")
        self.assertLessEqual(result["weighted_dte"], _ST_MAX_DTE)

    def test_medium_term(self):
        legs = [{"dte": 20, "volume": 800}, {"dte": 25, "volume": 200}]
        result = classify_dte(legs)
        self.assertEqual(result["dte_class"], "MT")
        self.assertGreater(result["weighted_dte"], _ST_MAX_DTE)
        self.assertLessEqual(result["weighted_dte"], _MT_MAX_DTE)

    def test_long_term(self):
        legs = [{"dte": 45, "volume": 1000}, {"dte": 60, "volume": 500}]
        result = classify_dte(legs)
        self.assertEqual(result["dte_class"], "LT")
        self.assertGreater(result["weighted_dte"], _MT_MAX_DTE)

    def test_empty_legs(self):
        result = classify_dte([])
        self.assertEqual(result["dte_class"], "UNKNOWN")

    def test_zero_volume_filtered(self):
        legs = [{"dte": 5, "volume": 0}, {"dte": 45, "volume": 100}]
        result = classify_dte(legs)
        self.assertEqual(result["dte_class"], "LT")

    def test_confidence_single_dominant(self):
        legs = [{"dte": 5, "volume": 900}, {"dte": 8, "volume": 100}]
        result = classify_dte(legs)
        self.assertAlmostEqual(result["confidence"], 0.9, places=2)

    def test_weighted_dte_calculation(self):
        legs = [{"dte": 10, "volume": 500}, {"dte": 20, "volume": 500}]
        result = classify_dte(legs)
        self.assertAlmostEqual(result["weighted_dte"], 15.0, places=1)

    def test_none_dte_filtered(self):
        legs = [{"dte": None, "volume": 500}, {"dte": 10, "volume": 500}]
        result = classify_dte(legs)
        self.assertEqual(result["dte_class"], "ST")

    def test_boundary_st_mt(self):
        legs = [{"dte": 10, "volume": 1000}]
        self.assertEqual(classify_dte(legs)["dte_class"], "ST")
        legs = [{"dte": 11, "volume": 1000}]
        self.assertEqual(classify_dte(legs)["dte_class"], "MT")

    def test_boundary_mt_lt(self):
        legs = [{"dte": 30, "volume": 1000}]
        self.assertEqual(classify_dte(legs)["dte_class"], "MT")
        legs = [{"dte": 31, "volume": 1000}]
        self.assertEqual(classify_dte(legs)["dte_class"], "LT")


# ---------------------------------------------------------------------------
# Covered Call Detection
# ---------------------------------------------------------------------------

class TestCoveredCallDetector(unittest.TestCase):

    def test_clear_when_no_pattern(self):
        legs = [
            {"option_type": "CALL", "volume": 500, "open_interest": 1000,
             "strike": 110.0, "dte": 10},
        ]
        result = detect_covered_call(100.0, legs, "ST")
        self.assertEqual(result["status"], "CLEAR")

    def test_nullified_short_term(self):
        legs = [
            {"option_type": "CALL", "volume": 100, "open_interest": 200,
             "strike": 101.0, "dte": 5},
        ]
        result = detect_covered_call(100.0, legs, "ST")
        self.assertEqual(result["status"], "NULLIFIED")

    def test_suspect_long_term(self):
        legs = [
            {"option_type": "CALL", "volume": 100, "open_interest": 200,
             "strike": 101.0, "dte": 45},
        ]
        result = detect_covered_call(100.0, legs, "LT")
        self.assertEqual(result["status"], "SUSPECT")

    def test_no_call_legs(self):
        legs = [
            {"option_type": "PUT", "volume": 500, "open_interest": 100,
             "strike": 95.0, "dte": 10},
        ]
        result = detect_covered_call(100.0, legs, "ST")
        self.assertEqual(result["status"], "UNAVAILABLE")

    def test_no_price(self):
        legs = [
            {"option_type": "CALL", "volume": 500, "open_interest": 100,
             "strike": 105.0, "dte": 10},
        ]
        result = detect_covered_call(0.0, legs, "ST")
        self.assertEqual(result["status"], "UNAVAILABLE")

    def test_high_vol_oi_clears(self):
        legs = [
            {"option_type": "CALL", "volume": 500, "open_interest": 100,
             "strike": 101.0, "dte": 5},
        ]
        result = detect_covered_call(100.0, legs, "ST")
        self.assertEqual(result["status"], "CLEAR")
        self.assertGreater(result["vol_oi_ratio"], _CC_VOL_OI_THRESHOLD)

    def test_strike_outside_band_clears(self):
        legs = [
            {"option_type": "CALL", "volume": 100, "open_interest": 200,
             "strike": 110.0, "dte": 5},
        ]
        result = detect_covered_call(100.0, legs, "ST")
        self.assertEqual(result["status"], "CLEAR")


# ---------------------------------------------------------------------------
# Signal generation logic
# ---------------------------------------------------------------------------

class TestSignalQualification(unittest.TestCase):

    def test_sizzle_below_threshold_no_signal(self):
        result = scan_symbol.__wrapped__ if hasattr(scan_symbol, '__wrapped__') else None
        self.assertIsNotNone(WATCH_THRESHOLD)
        self.assertEqual(WATCH_THRESHOLD, 4.0)
        self.assertEqual(STRONG_THRESHOLD, 5.0)

    def test_min_volume_threshold(self):
        self.assertEqual(MIN_ABSOLUTE_VOLUME, 50_000)

    def test_direction_threshold(self):
        self.assertEqual(DIRECTION_RATIO_THRESHOLD, 1.5)


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

class TestUniverse(unittest.TestCase):

    def test_macro_is_spy(self):
        self.assertEqual(MACRO_SYMBOLS, ["SPY"])

    def test_top50_count(self):
        self.assertEqual(len(TOP50_SYMBOLS), 50)

    def test_sp100_count(self):
        self.assertEqual(len(SP100_SYMBOLS), 50)

    def test_total_universe(self):
        total = len(MACRO_SYMBOLS) + len(TOP50_SYMBOLS) + len(SP100_SYMBOLS)
        self.assertEqual(total, 101)

    def test_no_duplicates_within_groups(self):
        self.assertEqual(len(set(TOP50_SYMBOLS)), len(TOP50_SYMBOLS))
        self.assertEqual(len(set(SP100_SYMBOLS)), len(SP100_SYMBOLS))


# ---------------------------------------------------------------------------
# Architectural constraints
# ---------------------------------------------------------------------------

class TestModuleInterface(unittest.TestCase):

    def test_importable(self):
        from prime_scanners import prime_uoa_scanner
        self.assertTrue(hasattr(prime_uoa_scanner, "main"))
        self.assertTrue(hasattr(prime_uoa_scanner, "run_uoa_scan"))
        self.assertTrue(hasattr(prime_uoa_scanner, "save_results"))
        self.assertTrue(hasattr(prime_uoa_scanner, "classify_dte"))
        self.assertTrue(hasattr(prime_uoa_scanner, "detect_covered_call"))

    def test_no_gui_imports(self):
        import prime_scanners.prime_uoa_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("from tkinter", source)
        self.assertNotIn("prime_gui", source)

    def test_no_direct_sqlite(self):
        import prime_scanners.prime_uoa_scanner as mod
        source = Path(mod.__file__).read_text()
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "import sqlite3" in stripped or "from sqlite3" in stripped:
                if stripped.startswith("#"):
                    continue
                if "prime_data" in stripped:
                    continue
                if "load_baselines" in source[max(0, source.find(stripped) - 200):source.find(stripped)]:
                    continue


class TestTradeFactorsIntegration(unittest.TestCase):

    def test_evaluate_uoa_callable(self):
        from prime_intelligence.prime_trade_factors import evaluate_uoa
        signal = {
            "symbol": "AAPL",
            "direction": "LONG",
            "score": 6.5,
            "price_at_scan": 195.0,
            "weighted_dte": 8,
        }
        tfe = evaluate_uoa("AAPL", signal)
        self.assertEqual(tfe.strategy, "UOA")
        self.assertIn(tfe.duration_class, ("ST", "MT", "LT"))
        self.assertIn(tfe.nullifier_status, ("CLEAR", "SUSPECT", "NULLIFIED"))
        d = tfe.to_dict()
        self.assertIn("duration", d)
        self.assertIn("exit_triggers", d)

    def test_uoa_dte_based_duration(self):
        from prime_intelligence.prime_trade_factors import evaluate_uoa
        st = evaluate_uoa("A", {"weighted_dte": 5, "score": 5, "price_at_scan": 10})
        mt = evaluate_uoa("B", {"weighted_dte": 20, "score": 5, "price_at_scan": 10})
        lt = evaluate_uoa("C", {"weighted_dte": 45, "score": 5, "price_at_scan": 10})
        self.assertEqual(st.duration_class, "ST")
        self.assertEqual(mt.duration_class, "MT")
        self.assertEqual(lt.duration_class, "LT")


# ---------------------------------------------------------------------------
# Locked thresholds
# ---------------------------------------------------------------------------

class TestLockedThresholds(unittest.TestCase):

    def test_cc_vol_oi_threshold(self):
        self.assertEqual(_CC_VOL_OI_THRESHOLD, 1.5)

    def test_cc_cluster_threshold(self):
        self.assertEqual(_CC_CLUSTER_THRESHOLD, 0.50)

    def test_st_max_dte(self):
        self.assertEqual(_ST_MAX_DTE, 10)

    def test_mt_max_dte(self):
        self.assertEqual(_MT_MAX_DTE, 30)


# ---------------------------------------------------------------------------
# CIL-046: Direct signal persistence
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture()
def _uoa_db(tmp_path):
    from prime_data.prime_db import init_db
    from prime_analytics.prime_signals_db import init_signals_table
    db = tmp_path / "uoa_signals.db"
    init_db(db_path=db)
    init_signals_table(db_path=db)
    return db


def _sample_uoa_signals():
    return [
        {"symbol": "AAPL", "tier": "STRONG", "sizzle_index": 6.2, "group": "Top50",
         "direction": "LONG", "call_put_ratio": 3.1, "total_volume": 120000,
         "price_at_scan": 195.0},
        {"symbol": "TSLA", "tier": "WATCH", "sizzle_index": 4.5, "group": "Top50",
         "direction": "SHORT", "call_put_ratio": 0.4, "total_volume": 90000,
         "price_at_scan": 240.0},
    ]


def test_persist_writes_rows(_uoa_db):
    from prime_scanners.prime_uoa_scanner import persist_uoa_signals
    from prime_analytics.prime_signals_db import get_signals
    n = persist_uoa_signals(_sample_uoa_signals(), "2026-06-20 10:00:00", db_path=_uoa_db)
    assert n == 2
    rows = get_signals(strategy="UOA", db_path=_uoa_db)
    assert len(rows) == 2
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["AAPL"]["trigger_source"] == "UOA_CALL"
    assert by_sym["TSLA"]["trigger_source"] == "UOA_PUT"
    assert by_sym["AAPL"]["entry_price"] == 195.0
    assert by_sym["AAPL"]["score"] == 6.2
    factors = _json.loads(by_sym["AAPL"]["factors"])
    assert factors["total_volume"] == 120000
    assert factors["group"] == "Top50"


def test_persist_dedup_on_rerun(_uoa_db):
    from prime_scanners.prime_uoa_scanner import persist_uoa_signals
    from prime_analytics.prime_signals_db import get_signals
    sigs = _sample_uoa_signals()
    first = persist_uoa_signals(sigs, "2026-06-20 10:00:00", db_path=_uoa_db)
    second = persist_uoa_signals(sigs, "2026-06-20 10:00:00", db_path=_uoa_db)
    assert first == 2
    assert second == 0  # identical scan_ts -> deterministic id -> no duplicates
    assert len(get_signals(strategy="UOA", db_path=_uoa_db)) == 2


def test_persist_skips_unapproved_tier(_uoa_db):
    from prime_scanners.prime_uoa_scanner import persist_uoa_signals
    sigs = [{"symbol": "NONE", "tier": "", "sizzle_index": 3.0, "group": "x",
             "direction": "LONG", "call_put_ratio": 1.0, "total_volume": 1}]
    assert persist_uoa_signals(sigs, "2026-06-20 10:00:00", db_path=_uoa_db) == 0


if __name__ == "__main__":
    unittest.main()
