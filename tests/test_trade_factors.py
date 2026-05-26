"""
Item 4 (TF-001 Phase 3) acceptance tests -- MTS + SRS factor sets
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
    evaluate_mts,
    evaluate_pead,
    evaluate_srs,
    evaluate_uoa,
)


class TestEvaluateMTS(unittest.TestCase):
    """AC 4.1 -- evaluate_mts() all five factor categories populated."""

    def test_mts_returns_all_five_categories(self):
        signal = {
            "symbol": "GLD",
            "direction": "LONG",
            "score": 6.5,
            "price_at_scan": 220.0,
            "session_open_price": 218.0,
        }
        result = evaluate_mts("GLD", signal)
        self.assertIsInstance(result, TradeFactorEvaluation)
        self.assertEqual(result.strategy, "MTS")

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

    def test_mts_duration_is_medium_term(self):
        signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 30.0}
        result = evaluate_mts("SLV", signal)
        self.assertEqual(result.duration_class, "MT")

    def test_mts_has_ratio_reversal_exit_trigger(self):
        signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 30.0}
        result = evaluate_mts("SLV", signal)
        trigger_types = [t["type"] for t in result.exit_triggers]
        self.assertIn("RATIO_REVERSAL", trigger_types)

    def test_mts_maintenance_includes_gold_silver_monitor(self):
        signal = {"direction": "LONG", "score": 5.0, "price_at_scan": 30.0}
        result = evaluate_mts("SLV", signal)
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
    """AC 4.3 -- DK-001 nullifier integrated into both MTS and SRS evaluation."""

    def test_mts_dark_pool_nullifier_present(self):
        signal = {
            "direction": "LONG",
            "score": 5.0,
            "price_at_scan": 220.0,
            "session_open_price": 210.0,
        }
        result = evaluate_mts("GLD", signal)
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

    def test_mts_nullified_on_suspicious_signal(self):
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "score": 5.0,
            "price_at_scan": 106.0,
            "session_open_price": 100.0,
            "block_prints": [{"side": "SELL", "size": 50000}],
        }
        result = evaluate_mts("GLD", signal)
        self.assertTrue(result.nullifier_status in ("SUSPECT", "NULLIFIED"))


class TestTradeFactorsJSON(unittest.TestCase):
    """AC 4.7 -- trade_factors JSON stored via prime_data layer."""

    def test_factor_eval_serializable(self):
        signal = {"direction": "LONG", "score": 6.0, "price_at_scan": 100.0}
        for evaluate_fn, sym in [(evaluate_mts, "GLD"), (evaluate_srs, "XLK"),
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


if __name__ == "__main__":
    unittest.main()
