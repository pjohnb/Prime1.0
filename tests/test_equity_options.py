"""
Sprint 11 Item 2 (OPT-001) acceptance tests -- Single-Name Options Phase 1.
Covers early assignment flag, Greeks display, position sizing, DTE time stop,
instrument_type=EQUITY_OPTION.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_options.prime_equity_options import (
    MAX_LOSS_PCT,
    TIME_STOP_DTE,
    check_dte_time_stop,
    check_early_assignment_risk,
    score_equity_option_signal,
    select_directional_leg,
)


def _chain():
    return [
        {"strike": 180, "dte": 28, "iv": 0.25, "bid": 9.50, "ask": 9.80,
         "delta": 0.38, "theta": -0.15, "gamma": 0.02, "vega": 0.30,
         "option_type": "CALL", "contract_symbol": "AAPL260620C180"},
        {"strike": 185, "dte": 28, "iv": 0.24, "bid": 6.20, "ask": 6.50,
         "delta": 0.30, "theta": -0.12, "gamma": 0.02, "vega": 0.28,
         "option_type": "CALL", "contract_symbol": "AAPL260620C185"},
        {"strike": 175, "dte": 28, "iv": 0.26, "bid": 3.80, "ask": 4.10,
         "delta": -0.35, "theta": -0.13, "gamma": 0.02, "vega": 0.29,
         "option_type": "PUT", "contract_symbol": "AAPL260620P175"},
    ]


class TestEarlyAssignmentRisk(unittest.TestCase):
    """AC: ITM + DTE<14 + ex-div within range = ASSIGNMENT_RISK."""

    def test_itm_short_dte_exdiv_flags_risk(self):
        result = check_early_assignment_risk("CALL", 180, 185, 10, ex_div_dte=5)
        self.assertTrue(result["risk"])
        self.assertEqual(result["flag"], "ASSIGNMENT_RISK")

    def test_itm_short_dte_no_exdiv_flags_watch(self):
        result = check_early_assignment_risk("CALL", 180, 185, 10)
        self.assertTrue(result["risk"])
        self.assertEqual(result["flag"], "ASSIGNMENT_WATCH")

    def test_otm_no_risk(self):
        result = check_early_assignment_risk("CALL", 190, 185, 10, ex_div_dte=5)
        self.assertFalse(result["risk"])

    def test_itm_long_dte_no_risk(self):
        result = check_early_assignment_risk("CALL", 180, 185, 28, ex_div_dte=5)
        self.assertFalse(result["risk"])

    def test_put_itm_flags(self):
        result = check_early_assignment_risk("PUT", 190, 185, 10, ex_div_dte=3)
        self.assertTrue(result["risk"])
        self.assertEqual(result["flag"], "ASSIGNMENT_RISK")


class TestLegSelection(unittest.TestCase):

    def test_call_selection_near_target_delta(self):
        leg = select_directional_leg(_chain(), "LONG")
        self.assertIsNotNone(leg)
        self.assertEqual(leg["option_type"], "CALL")
        self.assertAlmostEqual(abs(leg["delta"]), 0.38, places=1)

    def test_put_selection(self):
        leg = select_directional_leg(_chain(), "SHORT")
        self.assertIsNotNone(leg)
        self.assertEqual(leg["option_type"], "PUT")

    def test_empty_chain(self):
        self.assertIsNone(select_directional_leg([], "LONG"))


class TestScoreEquityOptionSignal(unittest.TestCase):

    def test_directional_signal_generated(self):
        signal = score_equity_option_signal("AAPL", "LONG", 75.0, _chain(), 183.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["strategy_type"], "DIRECTIONAL")
        self.assertEqual(signal["instrument_type"], "EQUITY_OPTION")

    def test_greeks_present(self):
        signal = score_equity_option_signal("AAPL", "LONG", 75.0, _chain(), 183.0)
        self.assertIsNotNone(signal)
        for key in ("delta", "theta"):
            self.assertIn(key, signal["greeks"])

    def test_position_sizing_075_pct(self):
        signal = score_equity_option_signal("AAPL", "LONG", 75.0, _chain(), 183.0,
                                            portfolio_value=100_000)
        self.assertIsNotNone(signal)
        self.assertGreater(signal["max_loss"], 0)

    def test_empty_chain_returns_none(self):
        self.assertIsNone(score_equity_option_signal("AAPL", "LONG", 75.0, [], 183.0))

    def test_legs_json_parseable(self):
        signal = score_equity_option_signal("AAPL", "LONG", 75.0, _chain(), 183.0)
        self.assertIsNotNone(signal)
        legs = json.loads(signal["legs_json"])
        self.assertIsInstance(legs, list)

    def test_assignment_risk_field_present(self):
        signal = score_equity_option_signal("AAPL", "LONG", 75.0, _chain(), 183.0)
        self.assertIsNotNone(signal)
        self.assertIn("assignment_risk", signal)


class TestDteTimeStop(unittest.TestCase):
    """AC: 10 DTE time stop for single-name."""

    def test_at_time_stop(self):
        self.assertTrue(check_dte_time_stop(10))

    def test_below(self):
        self.assertTrue(check_dte_time_stop(5))

    def test_above(self):
        self.assertFalse(check_dte_time_stop(20))


if __name__ == "__main__":
    unittest.main()
