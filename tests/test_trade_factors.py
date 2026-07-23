"""
Item 4 (TF-001 Phase 3) acceptance tests -- MMR + SRS factor sets
and Item 5 (IDX-001) factor set.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_trade_factors import (
    TradeFactorEvaluation,
    evaluate_index,
    evaluate_mmr,
    evaluate_pead,
    evaluate_srs,
    evaluate_uoa,
    _evaluate,
    _normalize_score,
)


class TestEvaluateMMR(unittest.TestCase):
    """AC 4.1 -- evaluate_mmr() all five factor categories populated."""

    def test_mmr_returns_all_five_categories(self):
        signal = {
            "symbol": "GLD",
            "direction": "LONG",
            "score": 6.5,
            "price_at_scan": 220.0,
            "session_open_price": 218.0,
        }
        result = evaluate_mmr("GLD", signal)
        self.assertIsInstance(result, TradeFactorEvaluation)
        self.assertEqual(result.strategy, "MMR")

        d = result.to_dict()
        self.assertIn("duration", d)
        self.assertIn("class", d["duration"])
        self.assertIn("entry", d)
        self.assertIn("method", d["entry"])
        self.assertIn("exit_triggers", d)
        self.assertGreater(len(d["exit_triggers"]), 0)
        self.assertIn("nullifier", d)
        self.assertIn("status", d["nullifier"])
        self.assertIn("maintenance_flags", d)

    def test_mmr_duration_is_medium_term(self):
        signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 30.0}
        result = evaluate_mmr("SLV", signal)
        self.assertEqual(result.duration_class, "MT")

    def test_mmr_has_ratio_reversal_exit_trigger(self):
        signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 30.0}
        result = evaluate_mmr("SLV", signal)
        trigger_types = [t["type"] for t in result.exit_triggers]
        self.assertIn("RATIO_REVERSAL", trigger_types)

    def test_mmr_maintenance_includes_gold_silver_monitor(self):
        signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 30.0}
        result = evaluate_mmr("SLV", signal)
        combined = " ".join(result.maintenance_flags)
        self.assertIn("gold/silver", combined.lower())


class TestEvaluateSRS(unittest.TestCase):
    """AC 4.2 -- evaluate_srs() all five factor categories populated."""

    def test_srs_returns_all_five_categories(self):
        signal = {
            "symbol": "XLK",
            "direction": "LONG",
            "score": 7.0,
            "price_at_scan": 200.0,
            "session_open_price": 198.0,
            "sector_phase": "RECOVERING",
        }
        result = evaluate_srs("XLK", signal)
        self.assertIsInstance(result, TradeFactorEvaluation)
        self.assertEqual(result.strategy, "SRS")

        d = result.to_dict()
        self.assertIn("duration", d)
        self.assertIn("entry", d)
        self.assertIn("exit_triggers", d)
        self.assertIn("nullifier", d)
        self.assertIn("maintenance_flags", d)

    def test_srs_recovering_is_medium_term(self):
        signal = {"direction": "LONG", "score": 7.0, "price_at_scan": 50.0,
                  "sector_phase": "RECOVERING"}
        result = evaluate_srs("XLE", signal)
        self.assertEqual(result.duration_class, "MT")

    def test_srs_has_regime_flip_exit_trigger(self):
        signal = {"direction": "LONG", "score": 7.0, "price_at_scan": 50.0,
                  "sector_phase": "RECOVERING"}
        result = evaluate_srs("XLE", signal)
        trigger_types = [t["type"] for t in result.exit_triggers]
        self.assertIn("REGIME_FLIP", trigger_types)


class TestDK001Integration(unittest.TestCase):
    """AC 4.3 -- DK-001 nullifier integrated into both MMR and SRS evaluation."""

    def test_mmr_dark_pool_nullifier_present(self):
        signal = {
            "direction": "LONG",
            "score": 5.0,
            "price_at_scan": 220.0,
            "session_open_price": 210.0,
        }
        result = evaluate_mmr("GLD", signal)
        self.assertIsNotNone(result.dark_pool_eval)
        self.assertIn(result.nullifier_status, ("CLEAR", "SUSPECT", "NULLIFIED"))

    def test_srs_dark_pool_nullifier_present(self):
        signal = {
            "direction": "LONG",
            "score": 7.0,
            "price_at_scan": 200.0,
            "session_open_price": 190.0,
            "sector_phase": "RECOVERING",
        }
        result = evaluate_srs("XLK", signal)
        self.assertIsNotNone(result.dark_pool_eval)
        self.assertIn(result.nullifier_status, ("CLEAR", "SUSPECT", "NULLIFIED"))

    def test_mmr_nullified_on_suspicious_signal(self):
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "score": 5.0,
            "price_at_scan": 106.0,
            "session_open_price": 100.0,
            "block_prints": [{"side": "SELL", "size": 50000}],
        }
        result = evaluate_mmr("GLD", signal)
        self.assertTrue(result.nullifier_status in ("SUSPECT", "NULLIFIED"))


class TestTradeFactorsJSON(unittest.TestCase):
    """AC 4.7 -- trade_factors JSON stored via prime_data layer."""

    def test_factor_eval_serializable(self):
        signal = {"direction": "LONG", "score": 6.0, "price_at_scan": 100.0}
        for evaluate_fn, sym in [(evaluate_mmr, "GLD"), (evaluate_srs, "XLK"),
                                  (evaluate_index, "SPY")]:
            result = evaluate_fn(sym, signal)
            serialized = json.dumps(result.to_dict())
            parsed = json.loads(serialized)
            self.assertEqual(parsed["strategy"], result.strategy)
            self.assertEqual(parsed["symbol"], sym)


class TestNoGUIImports(unittest.TestCase):
    """AC 4.8 -- No factor evaluation logic inside any prime_gui/ file."""

    def test_trade_factors_module_has_no_gui_imports(self):
        import prime_intelligence.prime_trade_factors as tf
        source = Path(tf.__file__).read_text()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("from tkinter", source)
        self.assertNotIn("prime_gui", source)


class TestEvaluateIndex(unittest.TestCase):
    """AC 5.2 -- evaluate_index() all five factor categories."""

    def test_index_returns_all_five_categories(self):
        signal = {
            "symbol": "SPY",
            "direction": "LONG",
            "score": 6.0,
            "price_at_scan": 530.0,
            "session_open_price": 528.0,
        }
        result = evaluate_index("SPY", signal)
        self.assertIsInstance(result, TradeFactorEvaluation)
        self.assertEqual(result.strategy, "IDX")

        d = result.to_dict()
        self.assertIn("duration", d)
        self.assertIn("entry", d)
        self.assertIn("exit_triggers", d)
        self.assertIn("nullifier", d)
        self.assertIn("maintenance_flags", d)

    def test_index_has_sma_break_exit_trigger(self):
        signal = {"direction": "LONG", "score": 6.0, "price_at_scan": 530.0}
        result = evaluate_index("SPY", signal)
        trigger_types = [t["type"] for t in result.exit_triggers]
        self.assertIn("SMA_BREAK", trigger_types)

    def test_index_dk001_integrated(self):
        signal = {
            "direction": "LONG",
            "score": 6.0,
            "price_at_scan": 530.0,
            "session_open_price": 520.0,
        }
        result = evaluate_index("SPY", signal)
        self.assertIsNotNone(result.dark_pool_eval)


class TestCalcTradeFactorsML3ScoreNormalization(unittest.TestCase):
    """CALC-TRADE_FACTORS_ML-3: entry-modifier thresholds assumed a 0-10
    score scale, making IMMEDIATE_HALF/SCALED unreachable for PEAD/MMR and
    IMMEDIATE_FULL rare for IDX. Scores are now normalized to 0-100 before
    the (now 80/60) thresholds are applied."""

    def test_psa_score_normalizes_and_hits_immediate_half(self):
        self.assertEqual(_normalize_score("PSA", 500.0), 50.0)
        signal = {"direction": "LONG", "score": 500.0, "price_at_scan": 100.0,
                  "session_open_price": 100.0}
        result = _evaluate("PSA", "AAPL", signal)
        self.assertEqual(result.normalized_score, 50.0)
        self.assertEqual(result.entry_method, "IMMEDIATE_HALF")

    def test_mtfa_score_passes_through_unchanged(self):
        self.assertEqual(_normalize_score("MTFA", 66.7), 66.7)
        signal = {"direction": "LONG", "score": 66.7, "price_at_scan": 100.0,
                  "session_open_price": 100.0}
        result = _evaluate("MTFA", "AAPL", signal)
        self.assertEqual(result.normalized_score, 66.7)

    def test_idx_high_score_hits_immediate_full_after_normalization(self):
        signal = {"direction": "LONG", "score": 83.3, "price_at_scan": 100.0,
                  "session_open_price": 100.0}
        result = evaluate_index("SPY", signal)
        self.assertEqual(result.entry_method, "IMMEDIATE_FULL")

    def test_uoa_watch_tier_sizzle_reaches_mid_band(self):
        # WATCH_THRESHOLD sizzle=4.0 -> normalized 64 (SCALED/FULL band, not HALF).
        self.assertEqual(_normalize_score("UOA", 4.0), 64.0)

    def test_uoa_extreme_sizzle_clamps_to_100(self):
        self.assertEqual(_normalize_score("UOA", 300.85), 100.0)

    def test_immediate_half_reachable_for_pead(self):
        # PEAD's own MIN_SIGNAL_SCORE floor is 50; a near-floor score must
        # now be able to land below the 60 threshold (was structurally
        # unreachable under the old 0-10-scale 6.0/8.0 thresholds).
        signal = {"direction": "LONG", "score": 52.0, "price_at_scan": 100.0,
                  "session_open_price": 100.0, "days_since_earnings": 1}
        result = evaluate_pead("MSFT", signal)
        self.assertEqual(result.entry_method, "IMMEDIATE_HALF")

    def test_scaled_reachable_for_uoa_lt_mid_band(self):
        # weighted_dte > 30 -> LT duration; sizzle 4.5 -> normalized 72 (60-80 band).
        signal = {"direction": "LONG", "score": 4.5, "price_at_scan": 100.0,
                  "session_open_price": 100.0, "weighted_dte": 45}
        result = evaluate_uoa("AAPL", signal)
        self.assertEqual(result.duration_class, "LT")
        self.assertEqual(result.entry_method, "SCALED")


class TestCalcTradeFactorsML2NullifierCoverage(unittest.TestCase):
    """CALC-TRADE_FACTORS_ML-2: covered-call, contradictory-signal, and
    sector-regime nullifiers must actually affect nullifier_status, not just
    append a cosmetic maintenance flag."""

    def test_covered_call_nullifier_status_propagates(self):
        signal = {
            "direction": "LONG", "score": 5.0, "price_at_scan": 100.0,
            "session_open_price": 100.0,
            "covered_call_eval": {"status": "NULLIFIED", "rationale": "CC pattern"},
        }
        result = evaluate_uoa("AAPL", signal)
        self.assertEqual(result.nullifier_status, "NULLIFIED")
        self.assertIn("COVERED_CALL", result.nullifier_flags)

    def test_covered_call_suspect_does_not_escalate_to_nullified(self):
        signal = {
            "direction": "LONG", "score": 5.0, "price_at_scan": 100.0,
            "session_open_price": 100.0,
            "covered_call_eval": {"status": "SUSPECT", "rationale": "CC pattern LT"},
        }
        result = evaluate_uoa("AAPL", signal)
        self.assertEqual(result.nullifier_status, "SUSPECT")

    def test_covered_call_clear_does_not_affect_status(self):
        signal = {
            "direction": "LONG", "score": 5.0, "price_at_scan": 100.0,
            "session_open_price": 100.0,
            "covered_call_eval": {"status": "CLEAR"},
        }
        result = evaluate_uoa("AAPL", signal)
        self.assertEqual(result.nullifier_status, "CLEAR")


def test_contradictory_signal_nullifies_opposing_direction(tmp_path):
    from prime_data.prime_db import init_db
    from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup
    from prime_intelligence.prime_trade_factors import evaluate_uoa

    db = tmp_path / "contradictory.db"
    init_db(db)
    init_signals_table(db)
    insert_signal_dedup(
        symbol="AAPL", strategy="IDX", scan_ts="2026-07-23 09:00:00",
        direction="SHORT", status="APPROVED", db_path=db,
    )

    signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 100.0,
              "session_open_price": 100.0}
    result = evaluate_uoa("AAPL", signal, db_path=db)
    assert result.nullifier_status == "NULLIFIED"
    assert "CONTRADICTORY_SIGNAL" in result.nullifier_flags


def test_no_contradictory_signal_when_same_direction(tmp_path):
    from prime_data.prime_db import init_db
    from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup
    from prime_intelligence.prime_trade_factors import evaluate_uoa

    db = tmp_path / "concordant.db"
    init_db(db)
    init_signals_table(db)
    insert_signal_dedup(
        symbol="AAPL", strategy="IDX", scan_ts="2026-07-23 09:00:00",
        direction="LONG", status="APPROVED", db_path=db,
    )

    signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 100.0,
              "session_open_price": 100.0}
    result = evaluate_uoa("AAPL", signal, db_path=db)
    assert "CONTRADICTORY_SIGNAL" not in result.nullifier_flags


def test_contradictory_signal_ignores_same_strategy(tmp_path):
    """An opposing-direction row from the SAME strategy (e.g. a stale prior
    UOA run) must not self-contradict."""
    from prime_data.prime_db import init_db
    from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup
    from prime_intelligence.prime_trade_factors import evaluate_uoa

    db = tmp_path / "self.db"
    init_db(db)
    init_signals_table(db)
    insert_signal_dedup(
        symbol="AAPL", strategy="UOA", scan_ts="2026-07-22 09:00:00",
        direction="SHORT", status="APPROVED", db_path=db,
    )

    signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 100.0,
              "session_open_price": 100.0}
    result = evaluate_uoa("AAPL", signal, db_path=db)
    assert "CONTRADICTORY_SIGNAL" not in result.nullifier_flags


def test_sector_regime_nullifies_long_signal_in_bearish_regime(tmp_path, monkeypatch):
    from prime_intelligence.prime_trade_factors import evaluate_uoa

    monkeypatch.setattr(
        "prime_scanners.prime_srs_scanner.get_broad_regime",
        lambda db_path=None: "BROAD_DECLINE",
    )
    signal = {"direction": "LONG", "score": 50.0, "price_at_scan": 100.0,
              "session_open_price": 100.0}
    result = evaluate_uoa("AAPL", signal, db_path=tmp_path / "regime.db")
    assert result.nullifier_status == "NULLIFIED"
    assert "SECTOR_REGIME_BEARISH" in result.nullifier_flags


def test_sector_regime_high_conviction_exception_clears(tmp_path, monkeypatch):
    from prime_intelligence.prime_trade_factors import evaluate_uoa

    monkeypatch.setattr(
        "prime_scanners.prime_srs_scanner.get_broad_regime",
        lambda db_path=None: "BROAD_DECLINE",
    )
    signal = {"direction": "LONG", "score": 80.0, "price_at_scan": 100.0,
              "session_open_price": 100.0}
    result = evaluate_uoa("AAPL", signal, db_path=tmp_path / "regime2.db")
    assert "SECTOR_REGIME_BEARISH" not in result.nullifier_flags


def test_sector_regime_does_not_affect_short_signals(tmp_path, monkeypatch):
    from prime_intelligence.prime_trade_factors import evaluate_uoa

    monkeypatch.setattr(
        "prime_scanners.prime_srs_scanner.get_broad_regime",
        lambda db_path=None: "BROAD_DECLINE",
    )
    signal = {"direction": "SHORT", "score": 50.0, "price_at_scan": 100.0,
              "session_open_price": 100.0}
    result = evaluate_uoa("AAPL", signal, db_path=tmp_path / "regime3.db")
    assert "SECTOR_REGIME_BEARISH" not in result.nullifier_flags


if __name__ == "__main__":
    unittest.main()
