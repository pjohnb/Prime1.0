"""
Sprint 30 Thread 3 -- CIL-095 double-execute guard acceptance tests.

Covers the /trades POST duplicate guard and the _recent_open_trade_exists()
helper: an identical trade within 60s returns 409; the same symbol after the
60s window is accepted; the same symbol with a different strategy within 60s
is allowed (not a duplicate).
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    init_db,
    insert_trade,
    _recent_open_trade_exists,
)
from prime_analytics.prime_signals_db import init_signals_table


class TestDuplicateGuard(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_dup_guard.db"
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

    def _post_trade(self, symbol="WDC", strategy="PSA", qty=100, price=50.0):
        return self.client.post(
            "/api/v1/trades",
            json={
                "symbol": symbol,
                "strategy": strategy,
                "direction": "LONG",
                "qty": qty,
                "price": price,
                "confirmed": True,
            },
            headers=self._auth,
        )

    # ── Route-level acceptance tests (per work order) ────────────────────────

    def test_duplicate_trade_rejected_within_60s(self):
        first = self._post_trade()
        self.assertEqual(first.status_code, 201)
        second = self._post_trade()
        self.assertEqual(second.status_code, 409)
        self.assertIn("Duplicate", second.get_json().get("error", ""))

    def test_same_symbol_after_60s_allowed(self):
        # Seed an OPEN trade whose entry_time is outside the 60s window.
        old_entry = (datetime.now() - timedelta(seconds=120)).isoformat()
        insert_trade(
            strategy="PSA",
            symbol="WDC",
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=100,
            entry_time=old_entry,
            price_at_scan=50.0,
            entry_price=50.0,
            trade_source="PAPER",
            db_path=self.db,
        )
        resp = self._post_trade()
        self.assertEqual(resp.status_code, 201)

    def test_different_strategy_same_symbol_allowed(self):
        first = self._post_trade(symbol="WDC", strategy="PSA")
        self.assertEqual(first.status_code, 201)
        # Same symbol, different strategy, within 60s -> not a duplicate.
        second = self._post_trade(symbol="WDC", strategy="UOA")
        self.assertEqual(second.status_code, 201)

    # ── Helper-level unit tests ──────────────────────────────────────────────

    def test_helper_detects_recent_open_trade(self):
        insert_trade(
            strategy="PSA", symbol="WDC", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=100, entry_time=datetime.now().isoformat(),
            price_at_scan=50.0, entry_price=50.0, trade_source="PAPER", db_path=self.db,
        )
        self.assertTrue(_recent_open_trade_exists("WDC", "PSA", 60, db_path=self.db))

    def test_helper_ignores_old_trade(self):
        old_entry = (datetime.now() - timedelta(seconds=120)).isoformat()
        insert_trade(
            strategy="PSA", symbol="WDC", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=100, entry_time=old_entry,
            price_at_scan=50.0, entry_price=50.0, trade_source="PAPER", db_path=self.db,
        )
        self.assertFalse(_recent_open_trade_exists("WDC", "PSA", 60, db_path=self.db))

    def test_helper_ignores_different_strategy(self):
        insert_trade(
            strategy="PSA", symbol="WDC", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=100, entry_time=datetime.now().isoformat(),
            price_at_scan=50.0, entry_price=50.0, trade_source="PAPER", db_path=self.db,
        )
        self.assertFalse(_recent_open_trade_exists("WDC", "UOA", 60, db_path=self.db))


if __name__ == "__main__":
    unittest.main()
