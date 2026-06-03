"""
Sprint 17 Item 4 (Direction-Aware MATA Routing) acceptance tests.

NON-NEGOTIABLE (Design Principle 4): no shorts in IRAs. Covers IRA exclusion for
SHORT, margin (not buying_power) capacity for SHORT, short_size_multiplier
applied automatically, SHORT direction stored in prime_trade_log, and LONG
routing left unchanged.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_trading import prime_mata as mata
from prime_data.prime_db import init_db, insert_trade, get_trade
from prime_analytics.prime_signals_db import init_signals_table

ACCOUNTS = [
    {"name": "Joint Brokerage", "type": "BROKERAGE", "buying_power": 100_000, "margin_available": 40_000},
    {"name": "Individual", "type": "INDIVIDUAL", "buying_power": 60_000, "margin_available": 30_000},
    {"name": "Rollover IRA", "type": "ROLLOVER_IRA", "buying_power": 200_000, "margin_available": 0},
]


class TestAllocateTrade(unittest.TestCase):
    def test_short_excludes_ira(self):
        out = mata.allocate_trade("AAPL", "SHORT", base_shares=100, price=100.0,
                                  accounts=ACCOUNTS, short_size_multiplier=0.5)
        names = {a["account"] for a in out["allocations"]}
        self.assertNotIn("Rollover IRA", names)
        self.assertIn("Rollover IRA", out["excluded_ira"])

    def test_short_uses_margin_not_buying_power(self):
        out = mata.allocate_trade("AAPL", "SHORT", base_shares=100, price=100.0,
                                  accounts=ACCOUNTS, short_size_multiplier=0.5)
        self.assertEqual(out["capacity_field"], "margin_available")
        # target = 100 * 0.5 = 50 shares; capacity from margin (40k+30k = 700 sh) covers it
        self.assertEqual(out["target_shares"], 50)
        self.assertEqual(out["allocated_shares"], 50)

    def test_short_multiplier_applied(self):
        out = mata.allocate_trade("AAPL", "SHORT", base_shares=80, price=100.0,
                                  accounts=ACCOUNTS, short_size_multiplier=0.5)
        self.assertEqual(out["target_shares"], 40)

    def test_short_capacity_limited_by_margin(self):
        # margin only 100 (1 share at $100); target 50 -> only 1 allocatable
        tight = [{"name": "B", "type": "BROKERAGE", "buying_power": 100_000, "margin_available": 100}]
        out = mata.allocate_trade("AAPL", "SHORT", base_shares=100, price=100.0,
                                  accounts=tight, short_size_multiplier=0.5)
        self.assertEqual(out["allocated_shares"], 1)

    def test_long_routing_unchanged(self):
        out = mata.allocate_trade("AAPL", "LONG", base_shares=100, price=100.0,
                                  accounts=ACCOUNTS, short_size_multiplier=0.5)
        self.assertEqual(out["capacity_field"], "buying_power")
        self.assertEqual(out["target_shares"], 100)         # no multiplier for long
        names = {a["account"] for a in out["allocations"]}
        self.assertIn("Rollover IRA", names)                # IRA eligible for long

    def test_is_ira_detection(self):
        self.assertTrue(mata.is_ira({"type": "ROLLOVER_IRA"}))
        self.assertTrue(mata.is_ira({"type": "Traditional IRA"}))
        self.assertFalse(mata.is_ira({"type": "BROKERAGE"}))


class TestShortStoredInTradeLog(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_mata.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_short_direction_stored(self):
        log_id = insert_trade(
            strategy="SHORT", symbol="AAPL", direction="SHORT", mode="PAPER",
            order_type="MARKET", shares=10, entry_time=datetime.now().isoformat(),
            price_at_scan=100.0, entry_price=100.0, account="Joint Brokerage",
            trade_source="PAPER", db_path=self.db)
        trade = get_trade(log_id, db_path=self.db)
        self.assertEqual(trade["direction"], "SHORT")


if __name__ == "__main__":
    unittest.main()
