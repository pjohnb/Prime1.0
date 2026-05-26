"""
Item 5 (CIL-PRIME-IDX-001) acceptance tests -- Index Scanner.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_index_scanner import (
    INDEX_TARGETS,
    evaluate_index_signal,
)


def _mock_snapshot(close=530.0, open_price=528.0, volume=80000000, prev_close=525.0, prev_vol=60000000):
    return {
        "day": {"o": open_price, "h": close + 2, "l": open_price - 1, "c": close, "v": volume},
        "prevDay": {"c": prev_close, "v": prev_vol},
    }


def _mock_options(call_vol=400000, put_vol=600000, call_oi=2000000, put_oi=3000000):
    return {
        "call_volume": call_vol,
        "put_volume": put_vol,
        "total_volume": call_vol + put_vol,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "put_call_ratio": round(put_vol / call_vol, 3) if call_vol > 0 else 0,
    }


class TestIndexScannerStandalone(unittest.TestCase):
    """AC 5.1 -- prime_index_scanner.py runs standalone, produces signals."""

    def test_module_importable_and_has_main(self):
        from prime_scanners import prime_index_scanner
        self.assertTrue(hasattr(prime_index_scanner, "main"))
        self.assertTrue(hasattr(prime_index_scanner, "run_index_scan"))

    def test_targets_are_spy_qqq_iwm(self):
        self.assertIn("SPY", INDEX_TARGETS)
        self.assertIn("QQQ", INDEX_TARGETS)
        self.assertIn("IWM", INDEX_TARGETS)

    def test_evaluate_produces_signal_dict(self):
        snapshot = _mock_snapshot()
        options = _mock_options()
        result = evaluate_index_signal("SPY", snapshot, options)

        self.assertEqual(result["symbol"], "SPY")
        self.assertIn("score", result)
        self.assertIn("direction", result)
        self.assertIn("signal", result)
        self.assertIn("price_at_scan", result)
        self.assertGreater(result["price_at_scan"], 0)

    def test_high_put_call_ratio_generates_short_signal(self):
        snapshot = _mock_snapshot(close=520.0, prev_close=525.0)
        options = _mock_options(call_vol=200000, put_vol=600000)
        result = evaluate_index_signal("SPY", snapshot, options)
        self.assertEqual(result["direction"], "SHORT")

    def test_low_put_call_ratio_generates_long_signal(self):
        snapshot = _mock_snapshot()
        options = _mock_options(call_vol=800000, put_vol=200000)
        result = evaluate_index_signal("SPY", snapshot, options)
        self.assertEqual(result["direction"], "LONG")

    def test_no_options_data_still_works(self):
        snapshot = _mock_snapshot()
        result = evaluate_index_signal("QQQ", snapshot, None)
        self.assertIn("score", result)
        self.assertEqual(result["strategy"], "IDX")


class TestIndexFactorEvaluation(unittest.TestCase):
    """AC 5.2 -- evaluate_index() in prime_trade_factors.py."""

    def test_all_five_categories_from_factor_eval(self):
        from prime_intelligence.prime_trade_factors import evaluate_index as eval_idx
        signal = {
            "symbol": "SPY",
            "direction": "LONG",
            "score": 6.5,
            "price_at_scan": 530.0,
            "session_open_price": 528.0,
        }
        result = eval_idx("SPY", signal)
        d = result.to_dict()
        self.assertIn("duration", d)
        self.assertIn("entry", d)
        self.assertIn("exit_triggers", d)
        self.assertIn("nullifier", d)
        self.assertIn("maintenance_flags", d)


class TestIndexDK001Integration(unittest.TestCase):
    """AC 5.4 -- DK-001 nullifier integrated."""

    def test_dark_pool_eval_present(self):
        from prime_intelligence.prime_trade_factors import evaluate_index as eval_idx
        signal = {
            "direction": "LONG",
            "score": 6.0,
            "price_at_scan": 530.0,
            "session_open_price": 520.0,
        }
        result = eval_idx("SPY", signal)
        self.assertIsNotNone(result.dark_pool_eval)
        self.assertIn(result.nullifier_status, ("CLEAR", "SUSPECT", "NULLIFIED"))


class TestIndexTradeFactorsStorage(unittest.TestCase):
    """AC 5.6 -- trade_factors JSON stored via prime_data layer."""

    def test_serializable(self):
        from prime_intelligence.prime_trade_factors import evaluate_index as eval_idx
        signal = {"direction": "LONG", "score": 6.0, "price_at_scan": 530.0}
        result = eval_idx("SPY", signal)
        serialized = json.dumps(result.to_dict())
        parsed = json.loads(serialized)
        self.assertEqual(parsed["strategy"], "IDX")
        self.assertEqual(parsed["symbol"], "SPY")


if __name__ == "__main__":
    unittest.main()
