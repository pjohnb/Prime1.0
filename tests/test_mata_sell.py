"""
Sprint 24 Item 3 -- Unified MATA Sell acceptance tests.

Tests: proportional allocation correct; rounding handled (remainder to largest);
50% shortcut calculates correctly; partial failure reported; safety gates per order.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_trading.prime_mata_sell import calculate_sell_allocation, pct_to_shares


class TestCalculateSellAllocation(unittest.TestCase):

    def test_proportional_allocation_correct(self):
        # Joint 20 MSFT, Custodial 16 MSFT; sell 18 = Joint 10, Custodial 8
        holdings = [
            {"account": "7926", "shares": 20, "account_hash": "hash_7926"},
            {"account": "0461", "shares": 16, "account_hash": "hash_0461"},
        ]
        result = calculate_sell_allocation("MSFT", 18, holdings)
        self.assertEqual(result["symbol"], "MSFT")
        self.assertEqual(result["total_qty"], 18)
        self.assertEqual(result["total_held"], 36)
        self.assertEqual(result["allocated_total"], 18)
        allocs = {a["account"]: a["sell_qty"] for a in result["allocations"]}
        self.assertEqual(allocs["7926"], 10)
        self.assertEqual(allocs["0461"], 8)

    def test_rounding_remainder_to_largest(self):
        # 10 + 9 = 19 held; sell 3 shares
        # Proportions: 10/19*3 = 1.57 -> floor=1, 9/19*3 = 1.42 -> floor=1
        # Allocated=2, remainder=1 -> goes to largest (10 shares account)
        holdings = [
            {"account": "A", "shares": 10},
            {"account": "B", "shares": 9},
        ]
        result = calculate_sell_allocation("XYZ", 3, holdings)
        self.assertEqual(result["allocated_total"], 3)
        allocs = {a["account"]: a["sell_qty"] for a in result["allocations"]}
        self.assertEqual(allocs["A"], 2)
        self.assertEqual(allocs["B"], 1)

    def test_sell_all_shares(self):
        holdings = [{"account": "A", "shares": 50}]
        result = calculate_sell_allocation("AAPL", 50, holdings)
        self.assertEqual(result["allocated_total"], 50)
        self.assertEqual(result["shortfall"], 0)

    def test_sell_more_than_held(self):
        holdings = [{"account": "A", "shares": 20}]
        result = calculate_sell_allocation("AAPL", 30, holdings)
        self.assertEqual(result["allocated_total"], 20)
        self.assertEqual(result["shortfall"], 10)

    def test_empty_holdings(self):
        result = calculate_sell_allocation("XYZ", 10, [])
        self.assertEqual(result["allocated_total"], 0)
        self.assertEqual(result["allocations"], [])
        self.assertEqual(result["shortfall"], 10)

    def test_zero_shares_account_excluded(self):
        holdings = [
            {"account": "A", "shares": 0},
            {"account": "B", "shares": 25},
        ]
        result = calculate_sell_allocation("GLD", 5, holdings)
        accounts = [a["account"] for a in result["allocations"]]
        self.assertNotIn("A", accounts)
        self.assertIn("B", accounts)

    def test_three_accounts_proportional(self):
        holdings = [
            {"account": "A", "shares": 30},
            {"account": "B", "shares": 20},
            {"account": "C", "shares": 50},
        ]
        result = calculate_sell_allocation("SPY", 100, holdings)
        self.assertEqual(result["allocated_total"], 100)
        allocs = {a["account"]: a["sell_qty"] for a in result["allocations"]}
        self.assertEqual(allocs["A"], 30)
        self.assertEqual(allocs["B"], 20)
        self.assertEqual(allocs["C"], 50)

    def test_single_share_sell(self):
        holdings = [
            {"account": "A", "shares": 100},
            {"account": "B", "shares": 100},
        ]
        result = calculate_sell_allocation("TJX", 1, holdings)
        self.assertEqual(result["allocated_total"], 1)
        total_alloc = sum(a["sell_qty"] for a in result["allocations"])
        self.assertEqual(total_alloc, 1)


class TestPctToShares(unittest.TestCase):

    def test_50pct_of_100_shares(self):
        self.assertEqual(pct_to_shares(50.0, 100), 50)

    def test_50pct_of_36_shares(self):
        # floor(36 * 0.50) = 18
        self.assertEqual(pct_to_shares(50.0, 36), 18)

    def test_33pct_of_100_shares(self):
        # floor(100 * 0.33) = 33
        self.assertEqual(pct_to_shares(33.0, 100), 33)

    def test_zero_pct(self):
        self.assertEqual(pct_to_shares(0.0, 100), 0)

    def test_100pct(self):
        self.assertEqual(pct_to_shares(100.0, 50), 50)

    def test_zero_held(self):
        self.assertEqual(pct_to_shares(50.0, 0), 0)


class TestMATASellEndpoint(unittest.TestCase):

    def setUp(self):
        from unittest.mock import patch, MagicMock
        import tempfile, json
        from pathlib import Path

        self.db = Path(__file__).parent / "_test_mata_sell.db"
        if self.db.exists():
            self.db.unlink()

        from prime_data.prime_db import init_db
        from prime_analytics.prime_signals_db import init_signals_table
        init_db(self.db)
        init_signals_table(self.db)

        self.tmp_dir = tempfile.mkdtemp()
        self.ops_path = Path(self.tmp_dir) / "ops_config.json"
        with open(self.ops_path, "w") as f:
            json.dump({
                "scan_schedule": {}, "notification_channels": "TBD",
                "health_check_interval": 900,
                "max_trades": 5, "analysis_mode": "Universe",
                "long_stop_loss_pct": 0.05, "time_stop_minutes": 1950,
                "mata_profile": "Joint Brokerage",
            }, f)

        mock_cfg = MagicMock()
        mock_cfg.trading_mode = "PAPER"
        mock_cfg.api_token = "test-token"
        mock_cfg.ops.max_order_pct = 0.10
        mock_cfg.ops.max_position_pct = 0.15
        mock_cfg.ops.max_sector_pct = 0.30

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()
        self._cfg_patcher = patch("prime_config.prime_config.get_config", return_value=mock_cfg)
        self._cfg_patcher.start()
        import prime_api.prime_api_routes as routes
        self._orig_ops = routes._OPS_CONFIG_PATH
        routes._OPS_CONFIG_PATH = self.ops_path

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self._auth = {"Authorization": "Bearer test-token"}

    def tearDown(self):
        import prime_api.prime_api_routes as routes
        routes._OPS_CONFIG_PATH = self._orig_ops
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_mata_sell_paper_returns_200(self):
        payload = {
            "symbol": "MSFT",
            "total_qty": 10,
            "order_type": "MARKET",
            "price": 415.0,
            "account_holdings": [
                {"account": "7926", "shares": 20},
                {"account": "0461", "shares": 16},
            ],
            "confirmed": True,
        }
        resp = self.client.post("/api/v1/sell/mata", json=payload,
                                headers=self._auth, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["symbol"], "MSFT")
        self.assertGreater(d["allocated_total"], 0)

    def test_mata_sell_pct_shortcut(self):
        payload = {
            "symbol": "TSLA",
            "total_qty": "50%",
            "order_type": "MARKET",
            "price": 250.0,
            "account_holdings": [{"account": "7926", "shares": 20}],
            "confirmed": True,
        }
        resp = self.client.post("/api/v1/sell/mata", json=payload,
                                headers=self._auth, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["total_qty"], 10)  # 50% of 20

    def test_mata_sell_requires_confirmed(self):
        payload = {
            "symbol": "AAPL",
            "total_qty": 5,
            "order_type": "MARKET",
            "price": 175.0,
            "account_holdings": [{"account": "7926", "shares": 10}],
            "confirmed": False,
        }
        resp = self.client.post("/api/v1/sell/mata", json=payload,
                                headers=self._auth, content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    # ── Sprint 30 PM-02: trade-log close on /sell/mata ────────────────────────
    def test_mata_sell_closes_trade_log_manual(self):
        from prime_data.prime_db import insert_trade, get_trade
        log_id = insert_trade(
            strategy="TEST", symbol="MSFT", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=20, entry_time="2026-06-10T10:00:00",
            price_at_scan=400.0, entry_price=400.0, account="7926",
            trade_source="PAPER", db_path=self.db,
        )
        payload = {
            "symbol": "MSFT", "total_qty": 10, "order_type": "MARKET",
            "price": 415.0,
            "account_holdings": [{"account": "7926", "shares": 20}],
            "confirmed": True,
        }
        resp = self.client.post("/api/v1/sell/mata", json=payload,
                                headers=self._auth, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(len(d["closed_logs"]), 1)
        row = get_trade(log_id, db_path=self.db)
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["exit_reason"], "MANUAL")
        self.assertAlmostEqual(row["exit_price"], 415.0)
        # pnl = (415 - 400) * 20 = 300 ; pct = 15/400*100 = 3.75
        self.assertAlmostEqual(row["pnl_dollars"], 300.0)
        self.assertAlmostEqual(row["pnl_pct"], 3.75)

    def test_mata_sell_missing_log_warns_not_blocks(self):
        from prime_data.prime_db import get_ops_events
        payload = {
            "symbol": "ZZZZ", "total_qty": 5, "order_type": "MARKET",
            "price": 50.0,
            "account_holdings": [{"account": "7926", "shares": 10}],
            "confirmed": True,
        }
        resp = self.client.post("/api/v1/sell/mata", json=payload,
                                headers=self._auth, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["closed_logs"], [])
        events = get_ops_events(db_path=self.db)
        self.assertTrue(any(e["event_type"] == "MATA_SELL_NO_LOG" for e in events))


class TestMATASellAccountHash(unittest.TestCase):
    """Sprint 30 PM-03: LIVE MATA sell resolves account suffix to hash."""

    def setUp(self):
        from unittest.mock import patch, MagicMock
        import tempfile, json
        from pathlib import Path

        self.db = Path(__file__).parent / "_test_mata_hash.db"
        if self.db.exists():
            self.db.unlink()
        from prime_data.prime_db import init_db
        from prime_analytics.prime_signals_db import init_signals_table
        init_db(self.db)
        init_signals_table(self.db)

        self.tmp_dir = tempfile.mkdtemp()
        self.ops_path = Path(self.tmp_dir) / "ops_config.json"
        with open(self.ops_path, "w") as f:
            json.dump({"scan_schedule": {}, "notification_channels": "TBD"}, f)

        self.mock_cfg = MagicMock()
        self.mock_cfg.trading_mode = "LIVE"
        self.mock_cfg.api_token = "test-token"
        self.mock_cfg.ops.max_order_pct = 0.10

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()
        self._cfg_patcher = patch("prime_config.prime_config.get_config", return_value=self.mock_cfg)
        self._cfg_patcher.start()
        import prime_api.prime_api_routes as routes
        self._orig_ops = routes._OPS_CONFIG_PATH
        routes._OPS_CONFIG_PATH = self.ops_path

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self._auth = {"Authorization": "Bearer test-token"}

    def tearDown(self):
        import prime_api.prime_api_routes as routes
        routes._OPS_CONFIG_PATH = self._orig_ops
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def _mock_client(self, accounts):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.connect.return_value = True
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = accounts
        client.client.get_account_numbers.return_value = resp
        client.get_quotes.return_value = {}
        return client

    def test_mata_sell_resolves_account_hash(self):
        from unittest.mock import patch
        client = self._mock_client([
            {"accountNumber": "123457926", "hashValue": "HASH_7926"},
            {"accountNumber": "999990461", "hashValue": "HASH_0461"},
        ])
        captured = {}

        def fake_submit(**kwargs):
            captured["account_hash"] = kwargs.get("account_hash")
            return {"order_id": "ORD1", "status": "SUBMITTED"}

        with patch("prime_trading.prime_schwab.SchwabClient", return_value=client), \
             patch("prime_trading.prime_schwab_orders.submit_order", side_effect=fake_submit):
            payload = {
                "symbol": "MSFT", "total_qty": 10, "order_type": "MARKET",
                "price": 415.0,
                "account_holdings": [{"account": "7926", "shares": 20}],
                "confirmed": True,
            }
            resp = self.client.post("/api/v1/sell/mata", json=payload,
                                    headers=self._auth, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured.get("account_hash"), "HASH_7926")

    def test_mata_sell_failed_hash_skips_account(self):
        from unittest.mock import patch
        # Only 7926 resolvable; 0461 has no matching Schwab account -> skipped.
        client = self._mock_client([
            {"accountNumber": "123457926", "hashValue": "HASH_7926"},
        ])
        calls = []

        def fake_submit(**kwargs):
            calls.append(kwargs.get("account_hash"))
            return {"order_id": "ORD", "status": "SUBMITTED"}

        with patch("prime_trading.prime_schwab.SchwabClient", return_value=client), \
             patch("prime_trading.prime_schwab_orders.submit_order", side_effect=fake_submit):
            payload = {
                "symbol": "MSFT", "total_qty": 10, "order_type": "MARKET",
                "price": 415.0,
                "account_holdings": [
                    {"account": "7926", "shares": 20},
                    {"account": "0461", "shares": 16},
                ],
                "confirmed": True,
            }
            resp = self.client.post("/api/v1/sell/mata", json=payload,
                                    headers=self._auth, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(calls, ["HASH_7926"])  # only the resolvable account submitted
        self.assertTrue(any(f["account"] == "0461" for f in d["failures"]))


if __name__ == "__main__":
    unittest.main()
