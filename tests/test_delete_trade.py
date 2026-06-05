"""
Sprint 23 Item 4 -- Delete Paper Trade + Exit Button acceptance tests.

Tests: DELETE endpoint returns 403 in LIVE mode; DELETE removes correct record;
shutdown endpoint returns 200; delete button absent on Schwab-imported positions.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db, insert_trade, get_trade
from prime_analytics.prime_signals_db import init_signals_table


class TestDeleteEndpoint(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_delete.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

        mock_cfg = MagicMock()
        mock_cfg.trading_mode = "PAPER"
        mock_cfg.api_token = "test-token-abc123"
        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config", return_value=mock_cfg
        )
        self._cfg_patcher.start()

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self._auth = {"Authorization": "Bearer test-token-abc123"}

    def tearDown(self):
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def _insert_paper_trade(self, symbol="AAPL", source="PAPER"):
        return insert_trade(
            strategy="MANUAL",
            symbol=symbol,
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=100,
            entry_time="2026-06-05T10:00:00",
            price_at_scan=175.00,
            entry_price=175.00,
            trade_source=source,
            db_path=self.db,
        )

    def test_delete_removes_paper_trade(self):
        log_id = self._insert_paper_trade("MSFT")
        resp = self.client.delete(
            f"/api/v1/trades/{log_id}", headers=self._auth
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["deleted"], log_id)
        self.assertIsNone(get_trade(log_id, db_path=self.db))

    def test_delete_schwab_import_blocked(self):
        log_id = self._insert_paper_trade("GLD", source="SCHWAB_IMPORT")
        resp = self.client.delete(
            f"/api/v1/trades/{log_id}", headers=self._auth
        )
        self.assertEqual(resp.status_code, 403)
        # Record must still exist.
        self.assertIsNotNone(get_trade(log_id, db_path=self.db))

    def test_delete_unknown_log_id_returns_404(self):
        resp = self.client.delete(
            "/api/v1/trades/nonexistent-id-xyz", headers=self._auth
        )
        self.assertEqual(resp.status_code, 404)

    def test_delete_blocked_in_live_mode(self):
        from unittest.mock import MagicMock
        live_cfg = MagicMock()
        live_cfg.trading_mode = "LIVE"
        live_cfg.api_token = "test-token-abc123"
        with patch("prime_config.prime_config.get_config", return_value=live_cfg):
            log_id = self._insert_paper_trade("TSLA")
            resp = self.client.delete(
                f"/api/v1/trades/{log_id}", headers=self._auth
            )
        self.assertEqual(resp.status_code, 403)

    def test_delete_requires_auth_token(self):
        log_id = self._insert_paper_trade("NIO")
        resp = self.client.delete(f"/api/v1/trades/{log_id}")
        self.assertIn(resp.status_code, (401, 403))

    def test_delete_trade_db_function(self):
        from prime_data.prime_db import delete_trade
        log_id = self._insert_paper_trade("COST")
        result = delete_trade(log_id, db_path=self.db)
        self.assertTrue(result)
        self.assertIsNone(get_trade(log_id, db_path=self.db))

    def test_delete_trade_returns_false_for_unknown(self):
        from prime_data.prime_db import delete_trade
        result = delete_trade("totally-fake-id", db_path=self.db)
        self.assertFalse(result)


class TestShutdownEndpoint(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_shutdown.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._db_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_shutdown_endpoint_returns_200(self):
        with patch("prime_api.prime_api_routes._shutdown_servers"):
            with patch("prime_api.prime_api_routes.threading"):
                resp = self.client.post("/api/v1/shutdown")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "shutting_down")


if __name__ == "__main__":
    unittest.main()
