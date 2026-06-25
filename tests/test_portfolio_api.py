"""
Sprint 24 Item 2 -- Consolidated Portfolio View acceptance tests.

Tests: portfolio endpoint returns 200; aggregation correct across accounts;
weighted avg entry price correct; P&L calculation correct; summary totals.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db, insert_trade
from prime_analytics.prime_signals_db import init_signals_table


def _mock_config():
    cfg = MagicMock()
    cfg.trading_mode = "PAPER"
    cfg.api_token = "test-token-abc123"
    cfg.ops.max_order_pct = 0.10
    cfg.ops.max_position_pct = 0.15
    cfg.ops.max_sector_pct = 0.30
    return cfg


class TestPortfolioEndpoint(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_portfolio.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config", return_value=_mock_config()
        )
        self._cfg_patcher.start()

        # Prevent real Schwab connections so current_price always falls back to entry_price
        self._schwab_patcher = patch(
            "prime_trading.prime_schwab.SchwabClient",
            side_effect=Exception("test isolation — no live Schwab in tests"),
        )
        self._schwab_patcher.start()

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._schwab_patcher.stop()
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    _insert_counter = 0

    def _insert(self, symbol, shares, price, account="7926"):
        TestPortfolioEndpoint._insert_counter += 1
        ts = f"2026-06-05T10:{TestPortfolioEndpoint._insert_counter:02d}:00"
        return insert_trade(
            strategy="MANUAL",
            symbol=symbol,
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=shares,
            entry_time=ts,
            price_at_scan=price,
            entry_price=price,
            account=account,
            trade_source="PAPER",
            db_path=self.db,
        )

    def test_portfolio_returns_200(self):
        resp = self.client.get("/api/v1/portfolio")
        self.assertEqual(resp.status_code, 200)

    def test_portfolio_empty(self):
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        self.assertEqual(d["count"], 0)
        self.assertEqual(d["rows"], [])

    def test_single_position_aggregated(self):
        self._insert("AAPL", 100, 175.0, "7926")
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        self.assertEqual(d["count"], 1)
        row = d["rows"][0]
        self.assertEqual(row["symbol"], "AAPL")
        self.assertEqual(row["total_shares"], 100)
        self.assertAlmostEqual(row["avg_entry_price"], 175.0, places=2)
        self.assertAlmostEqual(row["total_cost"], 17500.0, places=2)

    def test_aggregation_across_two_accounts(self):
        # Joint: 20 MSFT @ $415 | Custodial: 16 MSFT @ $410
        self._insert("MSFT", 20, 415.0, "7926")
        self._insert("MSFT", 16, 410.0, "0461")
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        msft_rows = [r for r in d["rows"] if r["symbol"] == "MSFT"]
        self.assertEqual(len(msft_rows), 1)
        row = msft_rows[0]
        self.assertEqual(row["total_shares"], 36)
        # Weighted avg: (20*415 + 16*410) / 36 = (8300 + 6560) / 36 = 412.78
        expected_avg = (20 * 415.0 + 16 * 410.0) / 36
        self.assertAlmostEqual(row["avg_entry_price"], expected_avg, places=2)
        self.assertIn("7926", row["accounts"])
        self.assertIn("0461", row["accounts"])

    def test_weighted_avg_entry_price_correct(self):
        # 10 shares @ $100 + 40 shares @ $200 = avg $180
        self._insert("GLD", 10, 100.0, "A")
        self._insert("GLD", 40, 200.0, "B")
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        row = next(r for r in d["rows"] if r["symbol"] == "GLD")
        expected = (10 * 100.0 + 40 * 200.0) / 50
        self.assertAlmostEqual(row["avg_entry_price"], expected, places=2)

    def test_pnl_uses_entry_price_when_no_live_quote(self):
        # No live Schwab connection in test — current_price falls back to avg_entry
        self._insert("TJX", 50, 118.0, "7926")
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        row = next(r for r in d["rows"] if r["symbol"] == "TJX")
        # P&L should be 0 when current == entry
        self.assertAlmostEqual(row["unrealized_pnl"], 0.0, places=2)

    def test_summary_totals_correct(self):
        self._insert("AAPL", 10, 200.0, "7926")  # cost = 2000
        self._insert("TSLA", 5, 300.0, "7926")   # cost = 1500
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        summary = d["summary"]
        self.assertAlmostEqual(summary["total_cost_basis"], 3500.0, places=2)
        self.assertEqual(summary["position_count"], 2)

    def test_sorted_by_market_value_descending(self):
        self._insert("CHEAP", 1, 10.0, "7926")    # market_value = 10
        self._insert("EXPENSIVE", 100, 500.0, "7926")  # market_value = 50000
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        rows = d["rows"]
        self.assertEqual(rows[0]["symbol"], "EXPENSIVE")

    def test_response_has_warnings_key(self):
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        self.assertIn("warnings", d)
        self.assertIsInstance(d["warnings"], list)


class TestPortfolioRefreshSync(unittest.TestCase):
    """PORT-01: /sync/schwab returns importable summary; portfolio responds 200 after sync."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_refresh.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config", return_value=_mock_config()
        )
        self._cfg_patcher.start()

        self._schwab_patcher = patch(
            "prime_trading.prime_schwab.SchwabClient",
            side_effect=Exception("test isolation — no live Schwab"),
        )
        self._schwab_patcher.start()

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._schwab_patcher.stop()
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_sync_endpoint_returns_imported_count(self):
        mock_sync = MagicMock(return_value={"imported": 3, "skipped": 0, "errors": []})
        with patch("prime_trading.prime_schwab_sync.sync_schwab_positions", mock_sync):
            resp = self.client.get("/api/v1/sync/schwab")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["imported"], 3)

    def test_sync_endpoint_zero_on_no_positions(self):
        mock_sync = MagicMock(return_value={"imported": 0, "skipped": 0, "errors": []})
        with patch("prime_trading.prime_schwab_sync.sync_schwab_positions", mock_sync):
            resp = self.client.get("/api/v1/sync/schwab")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["imported"], 0)

    def test_sync_endpoint_degrades_gracefully_on_error(self):
        with patch(
            "prime_trading.prime_schwab_sync.sync_schwab_positions",
            side_effect=Exception("connection refused"),
        ):
            resp = self.client.get("/api/v1/sync/schwab")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertIn("imported", d)
        self.assertEqual(d["imported"], 0)

    def test_portfolio_returns_200_after_sync(self):
        mock_sync = MagicMock(return_value={"imported": 0, "skipped": 0, "errors": []})
        with patch("prime_trading.prime_schwab_sync.sync_schwab_positions", mock_sync):
            self.client.get("/api/v1/sync/schwab")
        resp = self.client.get("/api/v1/portfolio")
        self.assertEqual(resp.status_code, 200)


class TestStopPriceDisplay(unittest.TestCase):
    """CIL-NEW-04: /api/v1/portfolio returns stop_price per position."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_stop_display.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()
        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config", return_value=_mock_config()
        )
        self._cfg_patcher.start()
        self._schwab_patcher = patch(
            "prime_trading.prime_schwab.SchwabClient",
            side_effect=Exception("test isolation"),
        )
        self._schwab_patcher.start()

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._schwab_patcher.stop()
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    _counter = 0

    def _insert(self, symbol, shares, price, account="7926"):
        TestStopPriceDisplay._counter += 1
        ts = f"2026-06-25T10:{TestStopPriceDisplay._counter:02d}:00"
        return insert_trade(
            strategy="MANUAL",
            symbol=symbol,
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=shares,
            entry_time=ts,
            price_at_scan=price,
            entry_price=price,
            account=account,
            trade_source="PAPER",
            db_path=self.db,
        )

    def test_portfolio_endpoint_includes_stop_price(self):
        from prime_data.prime_db import set_trade_stop_target
        log_id = self._insert("NVDA", 10, 150.0)
        set_trade_stop_target(log_id=log_id, stop_price=140.0, db_path=self.db)
        resp = self.client.get("/api/v1/portfolio")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        row = next(r for r in d["rows"] if r["symbol"] == "NVDA")
        self.assertIn("stop_price", row)
        self.assertAlmostEqual(row["stop_price"], 140.0, places=2)

    def test_portfolio_stop_price_null_when_not_set(self):
        self._insert("AMZN", 5, 200.0)
        resp = self.client.get("/api/v1/portfolio")
        d = resp.get_json()
        row = next(r for r in d["rows"] if r["symbol"] == "AMZN")
        self.assertIn("stop_price", row)
        self.assertIsNone(row["stop_price"])


if __name__ == "__main__":
    unittest.main()
