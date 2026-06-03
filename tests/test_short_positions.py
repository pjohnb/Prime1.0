"""
Sprint 17 Item 5 (Short Position Monitoring -- Lovable UI) acceptance tests.

Covers inverse P&L for SHORT, the SHORT stop badge colors (GREEN below entry,
AMBER within 1% of the +5% stop, RED at/above entry*1.05), direction carried on
the enriched position (for the UI direction badge), and the AI advisor receiving
direction in its context with SHORT-appropriate prompt guidance.
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_api import prime_positions as pp
from prime_ai import prime_position_advisor as advisor


class TestShortPnl(unittest.TestCase):
    def test_short_profit_when_price_falls(self):
        out = pp.compute_pnl(100.0, 90.0, 10, "SHORT")
        self.assertAlmostEqual(out["pnl_dollars"], 100.0)
        self.assertEqual(out["color"], "green")

    def test_short_loss_when_price_rises(self):
        out = pp.compute_pnl(100.0, 110.0, 10, "SHORT")
        self.assertAlmostEqual(out["pnl_dollars"], -100.0)
        self.assertEqual(out["color"], "red")


class TestShortStopBadge(unittest.TestCase):
    def test_green_below_entry(self):
        self.assertEqual(pp.short_stop_badge(100.0, 96.0, 0.05), "GREEN")

    def test_amber_within_1pct_of_stop(self):
        # stop = 105; within 1% below stop (>=103.95) -> AMBER
        self.assertEqual(pp.short_stop_badge(100.0, 104.5, 0.05), "AMBER")

    def test_red_at_or_above_stop(self):
        self.assertEqual(pp.short_stop_badge(100.0, 105.0, 0.05), "RED")
        self.assertEqual(pp.short_stop_badge(100.0, 107.0, 0.05), "RED")

    def test_short_stop_price(self):
        self.assertAlmostEqual(pp.short_stop_price(100.0, 0.05), 105.0)


class TestEnrichShortPosition(unittest.TestCase):
    def test_enrich_short_uses_inverse_pnl_and_short_stop(self):
        now = datetime(2026, 6, 3, 14, 0, 0)
        entry = (now - timedelta(minutes=30)).isoformat()
        pos = {"symbol": "AAA", "entry_price": 100.0, "shares": 10,
               "direction": "SHORT", "entry_time": entry}
        out = pp.enrich_position(pos, current_price=90.0, now=now,
                                 config_path=Path(__file__).parent / "_no_cfg.json")
        self.assertAlmostEqual(out["unrealized_pnl"], 100.0)   # short profit on a drop
        self.assertEqual(out["pnl_color"], "green")
        self.assertAlmostEqual(out["stop_price"], 105.0)        # +5% short stop
        self.assertEqual(out["stop_badge"], "GREEN")
        self.assertEqual(out["direction"], "SHORT")

    def test_enrich_short_stop_breached_red(self):
        pos = {"symbol": "AAA", "entry_price": 100.0, "shares": 10, "direction": "SHORT"}
        out = pp.enrich_position(pos, current_price=106.0,
                                 config_path=Path(__file__).parent / "_no_cfg.json")
        self.assertEqual(out["stop_badge"], "RED")
        self.assertTrue(out["unrealized_pnl"] < 0)

    def test_enrich_long_unchanged(self):
        pos = {"symbol": "BBB", "entry_price": 100.0, "shares": 10, "direction": "LONG"}
        out = pp.enrich_position(pos, current_price=94.0,
                                 config_path=Path(__file__).parent / "_no_cfg.json")
        self.assertAlmostEqual(out["stop_price"], 95.0)   # long -5% stop
        self.assertEqual(out["stop_badge"], "RED")        # 94 below 95 stop


class TestAdvisorDirectionContext(unittest.TestCase):
    def test_context_includes_direction(self):
        ctx = advisor.build_context({"symbol": "AAA", "direction": "SHORT",
                                     "entry_price": 100.0, "shares": 10})
        self.assertEqual(ctx["direction"], "SHORT")

    def test_system_prompt_has_short_guidance(self):
        self.assertIn("cover", advisor.SYSTEM_PROMPT.lower())
        self.assertIn("SHORT", advisor.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
