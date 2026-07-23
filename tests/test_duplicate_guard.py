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


# ---------------------------------------------------------------------------
# CIL-56 — MATA multi-account trade_log unique index collision
# ---------------------------------------------------------------------------

class TestMataTradeLogUniqueIndex(unittest.TestCase):
    """CIL-56: idx_signal_dedup didn't include account, so a MATA order across
    2+ accounts on the same symbol (sharing one entry_time) hit a UNIQUE
    constraint violation on the second account's insert -- first account
    recorded, the rest silently failed.
    """

    def setUp(self):
        self.db = Path(__file__).parent / "_test_cil56.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_three_mata_accounts_write_separate_rows(self):
        ts = datetime.now().isoformat()
        log_ids = [
            insert_trade(
                strategy="MTFA", symbol="AAPL", direction="LONG", mode="LIVE",
                order_type="MARKET", shares=10, entry_time=ts,
                price_at_scan=100.0, entry_price=100.0, account=suffix,
                trade_source="LIVE", db_path=self.db,
            )
            for suffix in ("7926", "0461", "8779")
        ]
        self.assertTrue(all(log_ids), "every MATA account insert must succeed")
        self.assertEqual(len(set(log_ids)), 3, "each account must get its own log_id")

        import sqlite3
        conn = sqlite3.connect(str(self.db))
        rows = conn.execute(
            "SELECT account, shares FROM prime_trade_log"
            " WHERE symbol='AAPL' AND strategy='MTFA'"
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 3)
        self.assertEqual({r[0] for r in rows}, {"7926", "0461", "8779"})

    def test_single_account_duplicate_still_blocked(self):
        # Same symbol/strategy/entry_time/account twice, both OPEN -> the
        # partial unique index must still reject the second insert (dedup
        # protection unchanged, just now keyed on account too).
        ts = datetime.now().isoformat()
        first = insert_trade(
            strategy="MTFA", symbol="AAPL", direction="LONG", mode="LIVE",
            order_type="MARKET", shares=10, entry_time=ts,
            price_at_scan=100.0, entry_price=100.0, account="7926",
            trade_source="LIVE", db_path=self.db,
        )
        self.assertTrue(first)
        with self.assertRaises(Exception):
            insert_trade(
                strategy="MTFA", symbol="AAPL", direction="LONG", mode="LIVE",
                order_type="MARKET", shares=10, entry_time=ts,
                price_at_scan=100.0, entry_price=100.0, account="7926",
                trade_source="LIVE", db_path=self.db,
            )

    def test_non_mata_single_order_still_works(self):
        log_id = insert_trade(
            strategy="PSA", symbol="WDC", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=100, entry_time=datetime.now().isoformat(),
            price_at_scan=50.0, entry_price=50.0, trade_source="PAPER", db_path=self.db,
        )
        self.assertTrue(log_id)


if __name__ == "__main__":
    unittest.main()
