"""
Sprint 17 Item 2 (Short position sizing) acceptance tests.

NON-NEGOTIABLE risk constraints tested explicitly here:
  * short size = 50% of equivalent long size (short_size_multiplier=0.5);
  * hard cap at 2% of account value per short position;
  * short stop fires at +5% above entry, and does NOT fire at -5%;
  * time stop fires the same as long.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence import prime_position_sizer as sizer


class TestShortSizing(unittest.TestCase):
    def test_short_is_half_of_equivalent_long(self):
        # account 100k, price 100, long_position_pct 0.04 -> long 40 shares.
        long_shares = sizer.calculate_long_size(100_000, 100.0, long_position_pct=0.04)
        self.assertEqual(long_shares, 40)
        out = sizer.calculate_short_size(
            "AAA", "BROKERAGE", price=100.0, account_value=100_000,
            short_size_multiplier=0.5, long_position_pct=0.04)
        self.assertEqual(out["shares"], 20)  # 50% of 40
        self.assertFalse(out["capped"])

    def test_hard_cap_at_2pct_account(self):
        # multiplier 1.0 with 4% long target -> 4% notional, must cap to 2%.
        out = sizer.calculate_short_size(
            "AAA", "BROKERAGE", price=100.0, account_value=100_000,
            short_size_multiplier=1.0, long_position_pct=0.04)
        self.assertTrue(out["capped"])
        self.assertEqual(out["shares"], 20)              # 2000 / 100
        self.assertLessEqual(out["notional"], 100_000 * 0.02 + 1e-9)

    def test_buying_power_limits_notional(self):
        out = sizer.calculate_short_size(
            "AAA", "BROKERAGE", price=100.0, account_value=100_000,
            buying_power=500.0, short_size_multiplier=0.5, long_position_pct=0.04)
        # equivalent long notional limited to 500 buying power -> short 250 -> 2 shares
        self.assertEqual(out["shares"], 2)

    def test_invalid_inputs_zero(self):
        out = sizer.calculate_short_size("AAA", None, price=0.0, account_value=100_000)
        self.assertEqual(out["shares"], 0)


class TestShortStop(unittest.TestCase):
    def test_short_stop_price_plus_5pct(self):
        self.assertAlmostEqual(sizer.short_stop_price(100.0, 0.05), 105.0)

    def test_stop_fires_at_plus_5pct(self):
        self.assertTrue(sizer.short_stop_triggered(100.0, 105.0, 0.05))
        self.assertTrue(sizer.short_stop_triggered(100.0, 106.0, 0.05))

    def test_stop_does_not_fire_at_minus_5pct(self):
        # price falling is FAVOURABLE for a short -> stop must not fire
        self.assertFalse(sizer.short_stop_triggered(100.0, 95.0, 0.05))

    def test_evaluate_exit_stop(self):
        close, trig, _ = sizer.evaluate_short_exit(100.0, 105.0, 10)
        self.assertTrue(close)
        self.assertEqual(trig, "short_stop_loss")

    def test_evaluate_exit_no_stop_on_favourable_move(self):
        close, trig, _ = sizer.evaluate_short_exit(100.0, 95.0, 10)
        self.assertFalse(close)

    def test_time_stop_fires_same_as_long(self):
        close, trig, _ = sizer.evaluate_short_exit(100.0, 100.0, 1950)
        self.assertTrue(close)
        self.assertEqual(trig, "time_stop")

    def test_take_profit_on_decline(self):
        close, trig, _ = sizer.evaluate_short_exit(100.0, 89.0, 10)
        self.assertTrue(close)
        self.assertEqual(trig, "short_take_profit")


class TestPositionReviewDirectionAware(unittest.TestCase):
    def test_review_short_stop_inverse(self):
        from sprint13_position_review import _evaluate_exit
        # SHORT: price up 6% -> close (stop). LONG with same move -> hold.
        sclose, strig, _ = _evaluate_exit("AAA", 100.0, 106.0, 10, direction="SHORT")
        self.assertTrue(sclose)
        self.assertEqual(strig, "short_stop_loss")
        lclose, _, _ = _evaluate_exit("AAA", 100.0, 106.0, 10, direction="LONG")
        self.assertFalse(lclose)


if __name__ == "__main__":
    unittest.main()
