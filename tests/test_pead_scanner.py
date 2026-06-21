"""
Sprint 7 Item 2 -- PEAD scanner port verification.
Tests the four-factor scoring model, earnings data builder, and signal generation.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_pead_scanner import (
    MIN_SIGNAL_SCORE,
    WEIGHT_EPS_SURPRISE,
    WEIGHT_PRICE_MOMENTUM,
    WEIGHT_ANALYST_COVERAGE,
    WEIGHT_REVENUE_SURPRISE,
    EPS_SURPRISE_CAP_PCT,
    score_eps_surprise,
    score_price_momentum,
    score_analyst_coverage,
    score_revenue_surprise,
    calculate_pead_signal,
    build_earnings_data,
)


class TestEpsSurpriseScoring(unittest.TestCase):

    def test_below_threshold(self):
        self.assertEqual(score_eps_surprise(0.5), 0.0)
        self.assertEqual(score_eps_surprise(-0.9), 0.0)

    def test_small_beat(self):
        s = score_eps_surprise(2.0)
        self.assertGreaterEqual(s, 20.0)
        self.assertLessEqual(s, 40.0)

    def test_moderate_beat(self):
        s = score_eps_surprise(5.0)
        self.assertGreaterEqual(s, 40.0)
        self.assertLessEqual(s, 70.0)

    def test_large_beat(self):
        s = score_eps_surprise(10.0)
        self.assertGreaterEqual(s, 70.0)
        self.assertLessEqual(s, 90.0)

    def test_massive_beat(self):
        s = score_eps_surprise(20.0)
        self.assertGreaterEqual(s, 90.0)
        self.assertLessEqual(s, 100.0)

    def test_negative_surprise_same_scale(self):
        self.assertEqual(score_eps_surprise(5.0), score_eps_surprise(-5.0))

    def test_capped_at_100(self):
        self.assertLessEqual(score_eps_surprise(500.0), 100.0)

    def test_boundary_values(self):
        self.assertEqual(score_eps_surprise(1.0), 20.0)
        self.assertEqual(score_eps_surprise(3.0), 40.0)
        self.assertEqual(score_eps_surprise(7.0), 70.0)
        self.assertEqual(score_eps_surprise(15.0), 90.0)


class TestPriceMomentumScoring(unittest.TestCase):

    def test_zero_inputs(self):
        self.assertEqual(score_price_momentum(0, 5.0), 0.0)
        self.assertEqual(score_price_momentum(5.0, 0), 0.0)

    def test_confirming_drift_small(self):
        s = score_price_momentum(5.0, 0.5)
        self.assertEqual(s, 30.0)

    def test_confirming_drift_large(self):
        s = score_price_momentum(5.0, 6.0)
        self.assertGreaterEqual(s, 80.0)

    def test_reversal_small(self):
        s = score_price_momentum(5.0, -0.5)
        self.assertEqual(s, 20.0)

    def test_reversal_large(self):
        s = score_price_momentum(5.0, -4.0)
        self.assertEqual(s, 0.0)

    def test_negative_surprise_negative_price_confirms(self):
        s = score_price_momentum(-5.0, -3.5)
        self.assertGreaterEqual(s, 60.0)


class TestAnalystCoverageScoring(unittest.TestCase):

    def test_unknown_coverage(self):
        self.assertEqual(score_analyst_coverage(0), 80.0)

    def test_low_coverage(self):
        self.assertEqual(score_analyst_coverage(3), 100.0)

    def test_moderate_coverage(self):
        self.assertEqual(score_analyst_coverage(8), 70.0)

    def test_high_coverage(self):
        self.assertEqual(score_analyst_coverage(15), 40.0)

    def test_very_high_coverage(self):
        self.assertEqual(score_analyst_coverage(25), 20.0)


class TestRevenueSurpriseScoring(unittest.TestCase):

    def test_no_data(self):
        self.assertEqual(score_revenue_surprise(5.0, None, None), 50.0)

    def test_both_beat(self):
        self.assertEqual(score_revenue_surprise(5.0, 110.0, 100.0), 100.0)

    def test_both_miss(self):
        self.assertEqual(score_revenue_surprise(-5.0, 90.0, 100.0), 100.0)

    def test_eps_beat_rev_miss(self):
        self.assertEqual(score_revenue_surprise(5.0, 90.0, 100.0), 50.0)

    def test_rev_beat_eps_miss(self):
        self.assertEqual(score_revenue_surprise(-5.0, 110.0, 100.0), 30.0)

    def test_zero_estimate(self):
        self.assertEqual(score_revenue_surprise(5.0, 100.0, 0), 50.0)


class TestWeightsSum(unittest.TestCase):

    def test_weights_sum_to_100(self):
        total = (
            WEIGHT_EPS_SURPRISE
            + WEIGHT_PRICE_MOMENTUM
            + WEIGHT_ANALYST_COVERAGE
            + WEIGHT_REVENUE_SURPRISE
        )
        self.assertEqual(total, 100)


class TestCalculatePeadSignal(unittest.TestCase):

    def _make_earnings(self, surprise_pct=5.0, symbol="TEST"):
        return {
            "symbol": symbol,
            "date": "2026-05-20",
            "hour": "amc",
            "epsEstimate": 1.00,
            "epsActual": 1.05,
            "revenueEstimate": 1000.0,
            "revenueActual": 1050.0,
            "surprisePercent": surprise_pct,
        }

    def test_strong_long_signal(self):
        earnings = self._make_earnings(surprise_pct=8.0)
        price = {"pct_change": 4.0, "days": 3, "open_after": 50.0, "close_latest": 52.0}
        signal = calculate_pead_signal(earnings, price, analyst_count=4)
        self.assertEqual(signal["direction"], "LONG")
        self.assertGreaterEqual(signal["score"], MIN_SIGNAL_SCORE)
        self.assertEqual(signal["days_since_earnings"], 3)

    def test_short_signal(self):
        earnings = self._make_earnings(surprise_pct=-8.0)
        price = {"pct_change": -4.0, "days": 2, "open_after": 50.0, "close_latest": 48.0}
        signal = calculate_pead_signal(earnings, price, analyst_count=4)
        self.assertEqual(signal["direction"], "SHORT")

    def test_no_price_data(self):
        earnings = self._make_earnings(surprise_pct=10.0)
        signal = calculate_pead_signal(earnings, None, analyst_count=0)
        self.assertGreater(signal["score"], 0)
        self.assertTrue(signal["momentum_pending"])
        self.assertEqual(signal["days_since_earnings"], 0)

    def test_score_range(self):
        earnings = self._make_earnings(surprise_pct=5.0)
        price = {"pct_change": 2.0, "days": 3, "open_after": 50.0, "close_latest": 51.0}
        signal = calculate_pead_signal(earnings, price, analyst_count=10)
        self.assertGreaterEqual(signal["score"], 0)
        self.assertLessEqual(signal["score"], 100)

    def test_neutral_direction(self):
        earnings = self._make_earnings(surprise_pct=0.0)
        signal = calculate_pead_signal(earnings, None, analyst_count=5)
        self.assertEqual(signal["direction"], "NEUTRAL")

    def test_factors_structure(self):
        earnings = self._make_earnings()
        signal = calculate_pead_signal(earnings, None, analyst_count=5)
        self.assertIn("factors", signal)
        for key in ("eps_surprise", "price_momentum", "analyst_coverage", "revenue_surprise"):
            self.assertIn(key, signal["factors"])
            f = signal["factors"][key]
            self.assertIn("score", f)
            self.assertIn("weight", f)
            self.assertIn("value", f)


class TestBuildEarningsData(unittest.TestCase):

    def test_calendar_with_actuals(self):
        entry = {
            "symbol": "AAPL",
            "date": "2026-05-20",
            "hour": "amc",
            "epsEstimate": 2.00,
            "epsActual": 2.10,
            "revenueEstimate": 90000.0,
            "revenueActual": 92000.0,
        }
        data = build_earnings_data(entry, [])
        self.assertAlmostEqual(data["surprisePercent"], 5.0, places=1)

    def test_calendar_without_actuals_uses_history(self):
        entry = {
            "symbol": "AAPL",
            "date": "2026-05-20",
            "epsEstimate": None,
            "epsActual": None,
        }
        history = [{"actual": 2.10, "estimate": 2.00, "period": "2026-05-15", "surprisePercent": 5.0}]
        data = build_earnings_data(entry, history)
        self.assertAlmostEqual(data["surprisePercent"], 5.0)

    def test_zero_estimate_no_divide_by_zero(self):
        entry = {
            "symbol": "XYZ",
            "date": "2026-05-20",
            "epsEstimate": 0,
            "epsActual": 0.05,
        }
        data = build_earnings_data(entry, [])
        self.assertIsNone(data["surprisePercent"])

    def test_no_data_returns_none_surprise(self):
        entry = {"symbol": "UNKNOWN", "date": "2026-05-20"}
        data = build_earnings_data(entry, [])
        self.assertIsNone(data["surprisePercent"])


class TestModuleInterface(unittest.TestCase):

    def test_importable(self):
        from prime_scanners import prime_pead_scanner
        self.assertTrue(hasattr(prime_pead_scanner, "main"))
        self.assertTrue(hasattr(prime_pead_scanner, "run_pead_scan"))
        self.assertTrue(hasattr(prime_pead_scanner, "save_results"))

    def test_no_gui_imports(self):
        import prime_scanners.prime_pead_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("from tkinter", source)
        self.assertNotIn("prime_gui", source)

    def test_no_direct_sqlite(self):
        import prime_scanners.prime_pead_scanner as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import sqlite3", source)

    def test_constants_sane(self):
        from prime_scanners.prime_pead_scanner import (
            MIN_SIGNAL_SCORE,
            EPS_SURPRISE_CAP_PCT,
            MAX_LOOKBACK_DAYS,
        )
        self.assertGreater(MIN_SIGNAL_SCORE, 0)
        self.assertGreater(EPS_SURPRISE_CAP_PCT, 100)
        self.assertGreater(MAX_LOOKBACK_DAYS, 0)


class TestTradeFactorsIntegration(unittest.TestCase):

    def test_evaluate_pead_callable(self):
        from prime_intelligence.prime_trade_factors import evaluate_pead
        signal = {
            "symbol": "TEST",
            "direction": "LONG",
            "score": 75.0,
            "price_at_scan": 100.0,
            "days_since_earnings": 2,
        }
        tfe = evaluate_pead("TEST", signal)
        self.assertEqual(tfe.strategy, "PEAD")
        self.assertEqual(tfe.symbol, "TEST")
        self.assertIn(tfe.duration_class, ("ST", "MT", "LT"))
        self.assertIn(tfe.nullifier_status, ("CLEAR", "SUSPECT", "NULLIFIED"))
        d = tfe.to_dict()
        self.assertIn("duration", d)
        self.assertIn("exit_triggers", d)

    def test_pead_duration_classification(self):
        from prime_intelligence.prime_trade_factors import evaluate_pead
        st = evaluate_pead("A", {"days_since_earnings": 2, "score": 50, "price_at_scan": 10})
        mt = evaluate_pead("B", {"days_since_earnings": 7, "score": 50, "price_at_scan": 10})
        lt = evaluate_pead("C", {"days_since_earnings": 15, "score": 50, "price_at_scan": 10})
        self.assertEqual(st.duration_class, "ST")
        self.assertEqual(mt.duration_class, "MT")
        self.assertEqual(lt.duration_class, "LT")

    def test_pead_exit_includes_drift_decay(self):
        from prime_intelligence.prime_trade_factors import evaluate_pead
        tfe = evaluate_pead("TEST", {"days_since_earnings": 2, "score": 50, "price_at_scan": 100})
        trigger_types = [t["type"] for t in tfe.exit_triggers]
        self.assertIn("DRIFT_DECAY", trigger_types)
        self.assertIn("STOP_LOSS", trigger_types)
        self.assertIn("PRICE_TARGET", trigger_types)
        self.assertIn("TIME_STOP", trigger_types)


# ---------------------------------------------------------------------------
# CIL-057: Estimate cross-validation
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402


def _recent_date(days_ago=10):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class TestValidateEstimate(unittest.TestCase):

    def test_null_estimate_invalid(self):
        from prime_scanners.prime_pead_scanner import _validate_estimate
        self.assertEqual(
            _validate_estimate("X", 1.05, None, _recent_date()), "INVALID")

    def test_zero_estimate_invalid(self):
        from prime_scanners.prime_pead_scanner import _validate_estimate
        self.assertEqual(
            _validate_estimate("X", 1.05, 0, _recent_date()), "INVALID")

    def test_surprise_over_200pct_invalid(self):
        from prime_scanners.prime_pead_scanner import _validate_estimate
        # actual 4.0 vs estimate 1.0 -> +300% surprise -> data error.
        self.assertEqual(
            _validate_estimate("X", 4.0, 1.0, _recent_date()), "INVALID")

    def test_stale_estimate_invalid(self):
        from prime_scanners.prime_pead_scanner import _validate_estimate
        self.assertEqual(
            _validate_estimate("X", 1.05, 1.0, _recent_date(days_ago=200)),
            "INVALID")

    def test_cross_source_low_confidence(self):
        from prime_scanners.prime_pead_scanner import _validate_estimate
        # Finnhub 1.00 vs Polygon 0.40 -> 60% disagreement -> LOW_CONFIDENCE.
        self.assertEqual(
            _validate_estimate("X", 1.05, 1.00, _recent_date(), polygon_estimate=0.40),
            "LOW_CONFIDENCE")

    def test_high_confidence_passes(self):
        from prime_scanners.prime_pead_scanner import _validate_estimate
        self.assertEqual(
            _validate_estimate("X", 1.05, 1.00, _recent_date(), polygon_estimate=0.98),
            "HIGH")

    def test_low_confidence_multiplier_is_0_7(self):
        from prime_scanners.prime_pead_scanner import LOW_CONFIDENCE_SCORE_MULTIPLIER
        self.assertEqual(LOW_CONFIDENCE_SCORE_MULTIPLIER, 0.7)


# ---------------------------------------------------------------------------
# CIL-046/047: Direct PEAD signal persistence
# ---------------------------------------------------------------------------

@pytest.fixture()
def _pead_db(tmp_path):
    from prime_data.prime_db import init_db
    from prime_analytics.prime_signals_db import init_signals_table
    db = tmp_path / "pead_signals.db"
    init_db(db_path=db)
    init_signals_table(db_path=db)
    return db


def _sample_pead_signals():
    return [
        {"symbol": "AAA", "direction": "LONG", "score": 72.0, "approved": True,
         "guidance_flag": "BEAT_RAISE", "surprise_pct": 8.0,
         "finnhub_guidance_available": True, "confidence_level": "HIGH",
         "price_at_scan": 150.0},
        {"symbol": "BBB", "direction": "SHORT", "score": 64.0, "approved": True,
         "guidance_flag": "MISS_CUT", "surprise_pct": -9.0,
         "finnhub_guidance_available": False, "confidence_level": "LOW_CONFIDENCE",
         "price_at_scan": 88.0},
    ]


def test_persist_pead_writes_rows(_pead_db):
    from prime_scanners.prime_pead_scanner import persist_pead_signals
    from prime_analytics.prime_signals_db import get_signals
    n = persist_pead_signals(_sample_pead_signals(), "2026-06-20 10:00:00", db_path=_pead_db)
    assert n == 2
    rows = {r["symbol"]: r for r in get_signals(strategy="PEAD", db_path=_pead_db)}
    assert rows["AAA"]["trigger_source"] == "PEAD_BEAT"
    assert rows["BBB"]["trigger_source"] == "PEAD_MISS"
    assert rows["AAA"]["guidance_flag"] == "BEAT_RAISE"
    factors = _json.loads(rows["AAA"]["factors"])
    assert factors["eps_surprise"] == 8.0
    assert factors["confidence_level"] == "HIGH"


def test_persist_pead_tier_from_guidance(_pead_db):
    from prime_scanners.prime_pead_scanner import persist_pead_signals
    from prime_analytics.prime_signals_db import get_signals
    persist_pead_signals(_sample_pead_signals(), "2026-06-20 10:00:00", db_path=_pead_db)
    rows = {r["symbol"]: r for r in get_signals(strategy="PEAD", db_path=_pead_db)}
    # BEAT_RAISE + LONG -> STRONG; MISS_CUT + SHORT -> STRONG short candidate.
    assert rows["AAA"]["tier"] == "STRONG"
    assert rows["BBB"]["tier"] == "STRONG"


def test_persist_pead_suppressed_status(_pead_db):
    from prime_scanners.prime_pead_scanner import persist_pead_signals
    from prime_analytics.prime_signals_db import get_signals
    # MISS_CUT + LONG -> SUPPRESSED tier + status.
    sigs = [{"symbol": "CCC", "direction": "LONG", "score": 70.0, "approved": True,
             "guidance_flag": "MISS_CUT", "surprise_pct": -3.0,
             "confidence_level": "HIGH", "price_at_scan": 10.0}]
    persist_pead_signals(sigs, "2026-06-20 10:00:00", db_path=_pead_db)
    row = get_signals(strategy="PEAD", db_path=_pead_db)[0]
    assert row["tier"] == "SUPPRESSED"
    assert row["status"] == "SUPPRESSED"


def test_persist_pead_dedup_and_skips_unapproved(_pead_db):
    from prime_scanners.prime_pead_scanner import persist_pead_signals
    from prime_analytics.prime_signals_db import get_signals
    sigs = _sample_pead_signals()
    assert persist_pead_signals(sigs, "2026-06-20 10:00:00", db_path=_pead_db) == 2
    assert persist_pead_signals(sigs, "2026-06-20 10:00:00", db_path=_pead_db) == 0
    # Unapproved (below threshold) is not written.
    unapproved = [{"symbol": "ZZZ", "direction": "LONG", "score": 30.0,
                   "approved": False, "guidance_flag": "BEAT_HOLD",
                   "surprise_pct": 2.0, "price_at_scan": 5.0}]
    assert persist_pead_signals(unapproved, "2026-06-20 11:00:00", db_path=_pead_db) == 0
    assert len(get_signals(strategy="PEAD", db_path=_pead_db)) == 2


if __name__ == "__main__":
    unittest.main()
