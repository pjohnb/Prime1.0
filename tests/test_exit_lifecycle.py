"""
Sprint 31 Thread 3 -- CIL-074 exit tracking lifecycle verification.

Verifies that every exit reason produces a CLOSED prime_trade_log record with
all required exit fields (exit_price, exit_time, exit_reason, pnl_dollars,
pnl_pct, hold_minutes), that check_closed_trade_completeness() returns an empty
list on a clean database, that it flags genuinely incomplete records, and that
/api/v1/health reports incomplete_exits: 0 on a clean database.
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
    get_trade,
    close_trade,
    close_trade_manual,
    close_trade_reconcile,
    check_closed_trade_completeness,
    _CLOSED_REQUIRED_FIELDS,
)
from prime_analytics.prime_signals_db import init_signals_table

# The six exit reasons audited in CIL-074.
EXIT_REASONS = [
    "STOP_LOSS",
    "TIME_STOP",
    "TRAILING_STOP",
    "DAY_COUNT_AUTO",
    "MANUAL",
    "SCHWAB_RECONCILE",
]


class TestExitLifecycle(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_exit_lifecycle.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

    def tearDown(self):
        self._db_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def _open_trade(self, symbol, strategy="PSA"):
        # Entry 120 minutes ago so hold_minutes is a non-zero, well-defined value.
        entry = (datetime.utcnow() - timedelta(minutes=120)).isoformat()
        return insert_trade(
            strategy=strategy, symbol=symbol, direction="LONG", mode="PAPER",
            order_type="MARKET", shares=100, entry_time=entry,
            price_at_scan=50.0, entry_price=50.0, trade_source="PAPER", db_path=self.db,
        )

    def _close_via_path(self, log_id, reason):
        """Close a trade via the production path for the given exit reason."""
        if reason == "SCHWAB_RECONCILE":
            # Mirrors prime_schwab_sync._reconcile_closed_positions(): exit at
            # entry price, zero P&L, computed hold_minutes via close_trade().
            trade = get_trade(log_id, db_path=self.db)
            entry_dt = datetime.fromisoformat(trade["entry_time"])
            hold = max(0, int((datetime.utcnow() - entry_dt).total_seconds() / 60))
            close_trade(
                log_id, exit_price=trade["entry_price"],
                exit_time=datetime.utcnow().isoformat(), exit_reason=reason,
                pnl_dollars=0.0, pnl_pct=0.0, hold_minutes=hold, db_path=self.db,
            )
        else:
            # STOP_LOSS / TIME_STOP / TRAILING_STOP / DAY_COUNT_AUTO route through
            # the stop monitor's _fire_exit_sell -> close_trade_manual; MANUAL
            # routes through /sell/mata -> close_trade_manual.
            close_trade_manual(log_id, exit_price=52.5, exit_reason=reason, db_path=self.db)

    def test_all_exit_reasons_write_complete_fields(self):
        for reason in EXIT_REASONS:
            with self.subTest(exit_reason=reason):
                log_id = self._open_trade(symbol=f"SY{reason[:3]}")
                self._close_via_path(log_id, reason)
                trade = get_trade(log_id, db_path=self.db)
                self.assertEqual(trade["status"], "CLOSED")
                self.assertEqual(trade["exit_reason"], reason)
                for field in _CLOSED_REQUIRED_FIELDS:
                    self.assertIsNotNone(
                        trade[field],
                        f"{reason} close left {field} NULL",
                    )

    def test_completeness_empty_after_all_complete_closes(self):
        for reason in EXIT_REASONS:
            log_id = self._open_trade(symbol=f"CL{reason[:3]}")
            self._close_via_path(log_id, reason)
        self.assertEqual(check_closed_trade_completeness(db_path=self.db), [])

    def test_clean_database_returns_empty(self):
        self.assertEqual(check_closed_trade_completeness(db_path=self.db), [])

    def test_incomplete_record_is_flagged(self):
        # close_trade_reconcile() intentionally writes only status/reason/time,
        # leaving exit_price/pnl/hold NULL -- the checker must flag it.
        log_id = self._open_trade(symbol="BADREC")
        close_trade_reconcile(log_id, "SCHWAB_RECONCILE", db_path=self.db)
        incomplete = check_closed_trade_completeness(db_path=self.db)
        self.assertEqual(len(incomplete), 1)
        self.assertEqual(incomplete[0]["log_id"], log_id)
        self.assertIn("exit_price", incomplete[0]["missing_fields"])
        self.assertIn("hold_minutes", incomplete[0]["missing_fields"])


class TestHealthIncompleteExits(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_exit_health.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
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

    def tearDown(self):
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_health_incomplete_exits_zero_on_clean_db(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json().get("incomplete_exits"), 0)


if __name__ == "__main__":
    unittest.main()
