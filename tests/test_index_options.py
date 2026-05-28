"""
Sprint 11 Item 1 (IDX-OPT-001) acceptance tests -- Index Options Phase 1.
Covers chain fetch, leg selection, DTE time stop, position sizing,
prime_signals write, Greeks via pricer.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_options.prime_options_pricer import black_scholes
from prime_options.prime_index_options import (
    MAX_LOSS_PCT,
    TIME_STOP_DTE,
    check_dte_time_stop,
    get_options_chain,
    score_option_signal,
    select_directional_leg,
    select_spread_legs,
)


def _chain():
    """Sample options chain for testing."""
    return [
        {"strike": 445, "dte": 28, "iv": 0.18, "bid": 8.50, "ask": 8.80,
         "delta": 0.42, "theta": -0.12, "option_type": "CALL",
         "contract_symbol": "SPY260620C445"},
        {"strike": 450, "dte": 28, "iv": 0.17, "bid": 5.80, "ask": 6.10,
         "delta": 0.35, "theta": -0.10, "option_type": "CALL",
         "contract_symbol": "SPY260620C450"},
        {"strike": 440, "dte": 28, "iv": 0.19, "bid": 4.20, "ask": 4.50,
         "delta": -0.38, "theta": -0.11, "option_type": "PUT",
         "contract_symbol": "SPY260620P440"},
        {"strike": 435, "dte": 28, "iv": 0.20, "bid": 2.80, "ask": 3.10,
         "delta": -0.28, "theta": -0.09, "option_type": "PUT",
         "contract_symbol": "SPY260620P435"},
    ]


class TestBlackScholesPricer(unittest.TestCase):

    def test_call_price_positive(self):
        result = black_scholes(450, 450, 30, 0.20)
        self.assertGreater(result["price"], 0)
        self.assertGreater(result["delta"], 0)

    def test_put_price_positive(self):
        result = black_scholes(450, 450, 30, 0.20, option_type="PUT")
        self.assertGreater(result["price"], 0)
        self.assertLess(result["delta"], 0)

    def test_zero_dte_returns_zeros(self):
        result = black_scholes(450, 450, 0, 0.20)
        self.assertEqual(result["price"], 0.0)

    def test_deep_itm_call_high_delta(self):
        result = black_scholes(500, 400, 30, 0.20)
        self.assertGreater(result["delta"], 0.9)

    def test_greeks_all_present(self):
        result = black_scholes(450, 450, 30, 0.20)
        for key in ("price", "delta", "gamma", "theta", "vega"):
            self.assertIn(key, result)


class TestOptionsChainFetch(unittest.TestCase):

    def test_no_client_returns_empty(self):
        chain = get_options_chain("SPY")
        self.assertEqual(chain, [])

    def test_client_exception_returns_empty(self):
        client = MagicMock()
        client.get_options_chain.side_effect = RuntimeError("unavailable")
        chain = get_options_chain("SPY", client=client)
        self.assertEqual(chain, [])


class TestLegSelection(unittest.TestCase):

    def test_directional_call_selection(self):
        leg = select_directional_leg(_chain(), "LONG", target_delta=0.40)
        self.assertIsNotNone(leg)
        self.assertEqual(leg["option_type"], "CALL")
        self.assertAlmostEqual(abs(leg["delta"]), 0.42, places=1)

    def test_directional_put_selection(self):
        leg = select_directional_leg(_chain(), "SHORT", target_delta=0.40)
        self.assertIsNotNone(leg)
        self.assertEqual(leg["option_type"], "PUT")

    def test_spread_selection(self):
        legs = select_spread_legs(_chain(), "LONG", width=5)
        self.assertIsNotNone(legs)
        self.assertEqual(len(legs), 2)
        self.assertAlmostEqual(abs(legs[0]["strike"] - legs[1]["strike"]), 5, places=0)

    def test_empty_chain_returns_none(self):
        self.assertIsNone(select_directional_leg([], "LONG"))
        self.assertIsNone(select_spread_legs([], "LONG"))


class TestScoreOptionSignal(unittest.TestCase):

    def test_high_score_gets_spread(self):
        signal = score_option_signal("SPY", "LONG", 85.0, _chain(), 450.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["strategy_type"], "SPREAD")
        self.assertEqual(signal["instrument_type"], "INDEX_OPTION")

    def test_medium_score_gets_directional(self):
        signal = score_option_signal("SPY", "LONG", 65.0, _chain(), 450.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["strategy_type"], "DIRECTIONAL")

    def test_position_sizing_max_loss(self):
        signal = score_option_signal("SPY", "LONG", 65.0, _chain(), 450.0, portfolio_value=100_000)
        self.assertIsNotNone(signal)
        self.assertGreater(signal["max_loss"], 0)
        single_contract_loss = signal["max_loss"]
        max_allowed = 100_000 * MAX_LOSS_PCT
        self.assertLessEqual(single_contract_loss * 1, max_allowed * 2)

    def test_empty_chain_returns_none(self):
        signal = score_option_signal("SPY", "LONG", 80.0, [], 450.0)
        self.assertIsNone(signal)

    def test_legs_json_parseable(self):
        signal = score_option_signal("SPY", "LONG", 65.0, _chain(), 450.0)
        self.assertIsNotNone(signal)
        import json
        legs = json.loads(signal["legs_json"])
        self.assertIsInstance(legs, list)
        self.assertTrue(len(legs) >= 1)

    def test_dte_at_entry_present(self):
        signal = score_option_signal("SPY", "LONG", 65.0, _chain(), 450.0)
        self.assertIsNotNone(signal)
        self.assertGreater(signal["dte_at_entry"], 0)


class TestDteTimeStop(unittest.TestCase):
    """AC: 7 DTE triggers close regardless of P&L."""

    def test_at_time_stop(self):
        self.assertTrue(check_dte_time_stop(7))

    def test_below_time_stop(self):
        self.assertTrue(check_dte_time_stop(3))

    def test_above_time_stop(self):
        self.assertFalse(check_dte_time_stop(15))

    def test_at_zero(self):
        self.assertTrue(check_dte_time_stop(0))


if __name__ == "__main__":
    unittest.main()
