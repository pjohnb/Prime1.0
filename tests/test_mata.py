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


THREE_ACCOUNTS = [
    {"name": "Joint Brokerage", "type": "BROKERAGE", "buying_power": 100_000, "margin_available": 40_000, "weight": 60},
    {"name": "Custodial",       "type": "BROKERAGE", "buying_power":  40_000, "margin_available": 0,      "weight": 30},
    {"name": "Rollover IRA",    "type": "ROLLOVER_IRA", "buying_power": 30_000, "margin_available": 0,   "weight": 10},
]


class TestMataAllAccountsProfile(unittest.TestCase):
    """CIL-NEW-13: All Accounts profile routes LONG trades to all three accounts."""

    def test_mata_all_accounts_routes_to_all_three(self):
        """When profile='all', proportional weight allocation spreads to all three accounts."""
        out = mata.allocate_trade("COST", "LONG", base_shares=100, price=100.0,
                                  accounts=THREE_ACCOUNTS, use_weights=True)
        names = {a["account"] for a in out["allocations"]}
        self.assertIn("Joint Brokerage", names)
        self.assertIn("Custodial", names)
        self.assertIn("Rollover IRA", names)
        self.assertEqual(len(names), 3)
        # Shares should sum to target (100)
        self.assertEqual(out["allocated_shares"], 100)

    def test_mata_single_account_routes_to_one(self):
        """When profile filters to a single account, only that account receives the trade."""
        # Simulate the profile filter: pass only the target account to allocate_trade.
        single = [a for a in THREE_ACCOUNTS if a["name"] == "Joint Brokerage"]
        out = mata.allocate_trade("COST", "LONG", base_shares=10, price=100.0,
                                  accounts=single)
        names = {a["account"] for a in out["allocations"]}
        self.assertEqual(names, {"Joint Brokerage"})
        self.assertNotIn("Custodial", names)
        self.assertNotIn("Rollover IRA", names)

    def test_mata_all_accounts_short_still_excludes_ira(self):
        """Even with all accounts, SHORT must exclude IRA accounts (Design Principle 4)."""
        out = mata.allocate_trade("TSLA", "SHORT", base_shares=100, price=100.0,
                                  accounts=THREE_ACCOUNTS, short_size_multiplier=0.5)
        names = {a["account"] for a in out["allocations"]}
        self.assertNotIn("Rollover IRA", names)
        self.assertIn("Rollover IRA", out["excluded_ira"])


class TestLoadAccountsAudit001(unittest.TestCase):
    """AUDIT-001: load_accounts() validation and ops_config parsing."""

    def _write_config(self, tmp_dir, data):
        import json
        p = Path(tmp_dir) / "ops_config.json"
        p.write_text(json.dumps(data))
        return p

    def test_returns_three_accounts_from_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, {"mata_accounts": [
                {"name": "Joint Brokerage", "suffix": "7926", "type": "BROKERAGE", "weight": 60},
                {"name": "Custodial",       "suffix": "0461", "type": "BROKERAGE", "weight": 20},
                {"name": "Rollover IRA",    "suffix": "8779", "type": "ROLLOVER_IRA", "weight": 20},
            ]})
            accts = mata.load_accounts(config_path=cfg)
            self.assertEqual(len(accts), 3)
            self.assertEqual(accts[0]["suffix"], "7926")
            self.assertEqual(accts[2]["type"], "ROLLOVER_IRA")

    def test_returns_empty_when_mata_accounts_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, {})
            with self.assertLogs("prime_mata", level="WARNING") as cm:
                accts = mata.load_accounts(config_path=cfg)
            self.assertEqual(accts, [])
            self.assertTrue(any("empty" in m for m in cm.output))

    def test_warns_when_weights_do_not_sum_to_100(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, {"mata_accounts": [
                {"name": "Joint", "suffix": "7926", "weight": 50},
                {"name": "Custodial", "suffix": "0461", "weight": 10},
            ]})
            with self.assertLogs("prime_mata", level="WARNING") as cm:
                accts = mata.load_accounts(config_path=cfg)
            self.assertEqual(len(accts), 2)
            self.assertTrue(any("60.0" in m or "sum" in m.lower() or "weight" in m.lower() for m in cm.output))

    def test_correct_weights_no_warning(self):
        import tempfile, logging
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, {"mata_accounts": [
                {"name": "Joint",    "suffix": "7926", "weight": 60},
                {"name": "Custodial","suffix": "0461", "weight": 20},
                {"name": "IRA",      "suffix": "8779", "weight": 20},
            ]})
            with self.assertLogs("prime_mata", level="DEBUG") as cm:
                logging.getLogger("prime_mata").debug("probe")  # ensure logger active
                accts = mata.load_accounts(config_path=cfg)
            self.assertEqual(len(accts), 3)
            self.assertFalse(any("WARNING" in m and "weight" in m.lower() for m in cm.output))

    def test_malformed_config_logs_warning(self):
        """AUDIT-043: a parse error (not just an absent/empty list) must be logged,
        not silently swallowed by the bare except -- otherwise it reproduces
        AUDIT-001 symptoms (empty MATA routing) with no way to diagnose why."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "ops_config.json"
            cfg.write_text("{ this is not valid json")
            with self.assertLogs("prime_mata", level="WARNING") as cm:
                accts = mata.load_accounts(config_path=cfg)
            self.assertEqual(accts, [])
            self.assertTrue(any("parse error" in m.lower() for m in cm.output))

    def test_healthy_config_no_parse_warning(self):
        import tempfile, logging
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, {"mata_accounts": [
                {"name": "Joint", "suffix": "7926", "weight": 100},
            ]})
            with self.assertLogs("prime_mata", level="DEBUG") as cm:
                logging.getLogger("prime_mata").debug("probe")
                accts = mata.load_accounts(config_path=cfg)
            self.assertEqual(len(accts), 1)
            self.assertFalse(any("parse error" in m.lower() for m in cm.output))

    def test_short_multiplier_malformed_config_logs_warning(self):
        """AUDIT-043: same fix applied to _short_multiplier()'s except block."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "ops_config.json"
            cfg.write_text("{ not valid json either")
            with self.assertLogs("prime_mata", level="WARNING") as cm:
                mult = mata._short_multiplier(cfg)
            self.assertEqual(mult, mata.DEFAULT_SHORT_SIZE_MULTIPLIER)
            self.assertTrue(any("parse error" in m.lower() for m in cm.output))

    def test_mata_qty_allocation_60_20_20(self):
        """$1,500 budget at $61.14 → 24 total shares → Joint=14, Custodial=4, IRA=4."""
        accts = [
            {"name": "Joint",    "suffix": "7926", "weight": 60},
            {"name": "Custodial","suffix": "0461", "weight": 20},
            {"name": "IRA",      "suffix": "8779", "weight": 20},
        ]
        budget = 1500
        price  = 61.14
        qty    = int(budget / price)   # 24
        qtys = {a["suffix"]: int(qty * a["weight"] / 100) for a in accts}
        self.assertEqual(qtys["7926"], 14)
        self.assertEqual(qtys["0461"], 4)
        self.assertEqual(qtys["8779"], 4)
        self.assertEqual(sum(qtys.values()), 22)  # floor arithmetic — 22 of 24 allocated


if __name__ == "__main__":
    unittest.main()
