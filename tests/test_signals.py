"""
Sprint 33 Thread 2 / CIL-075 -- PEAD signal Dismiss.

Covers the soft-delete dismiss flow: the dismiss_signal() data-layer helper,
the POST /api/v1/signals/{signal_id}/dismiss endpoint (200/404/409), and the
exclusion of DISMISSED signals from the Signals tab query and the analytics
(effectiveness) signal counts -- while the row is preserved for ML training.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import (
    init_signals_table,
    insert_signal,
    get_signals,
    get_analytics_summary,
    dismiss_signal,
)

_TOKEN = "test-token-abc123"


def _mock_config():
    cfg = MagicMock()
    cfg.trading_mode = "PAPER"
    cfg.api_token = _TOKEN
    return cfg


class _SignalsBase(unittest.TestCase):

    _counter = 0

    def setUp(self):
        self.db = Path(__file__).parent / "_test_signals.db"
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

    def _insert(self, symbol="AAPL", strategy="PEAD", status="APPROVED", entry_price=150.0):
        _SignalsBase._counter += 1
        ts = f"2026-06-20T10:{_SignalsBase._counter:02d}:00"
        return insert_signal(
            symbol=symbol, strategy=strategy, scan_ts=ts, score=80.0,
            tier="STRONG", status=status, entry_price=entry_price, db_path=self.db,
        )


# ---------------------------------------------------------------------------
# Data layer: dismiss_signal + query exclusions
# ---------------------------------------------------------------------------

class TestDismissDataLayer(_SignalsBase):

    def test_dismiss_sets_status(self):
        sid = self._insert()
        self.assertEqual(dismiss_signal(sid, db_path=self.db), "DISMISSED")
        rows = get_signals(status="DISMISSED", db_path=self.db)
        self.assertEqual([r["signal_id"] for r in rows], [sid])
        self.assertEqual(rows[0]["status"], "DISMISSED")

    def test_dismiss_unknown_returns_not_found(self):
        self.assertEqual(dismiss_signal("nope", db_path=self.db), "NOT_FOUND")

    def test_dismiss_twice_returns_already(self):
        sid = self._insert()
        dismiss_signal(sid, db_path=self.db)
        self.assertEqual(dismiss_signal(sid, db_path=self.db), "ALREADY_DISMISSED")

    def test_dismissed_signal_excluded_from_signals_tab(self):
        keep = self._insert(symbol="AAPL")
        drop = self._insert(symbol="TSLA")
        dismiss_signal(drop, db_path=self.db)
        # Default Signals-tab query (no status filter) hides the dismissed row.
        ids = [r["signal_id"] for r in get_signals(db_path=self.db)]
        self.assertIn(keep, ids)
        self.assertNotIn(drop, ids)
        # But the row is preserved and still fetchable by explicit status.
        ids_dismissed = [r["signal_id"] for r in get_signals(status="DISMISSED", db_path=self.db)]
        self.assertEqual(ids_dismissed, [drop])

    def test_dismissed_signal_excluded_from_effectiveness(self):
        self._insert(symbol="AAPL", strategy="PEAD")
        drop = self._insert(symbol="TSLA", strategy="PEAD")
        before = get_analytics_summary(db_path=self.db)["total_signals"]
        dismiss_signal(drop, db_path=self.db)
        after = get_analytics_summary(db_path=self.db)["total_signals"]
        # The dismissed signal drops out of the analytics signal count.
        self.assertEqual(after, before - 1)


# ---------------------------------------------------------------------------
# Endpoint: POST /api/v1/signals/{signal_id}/dismiss
# ---------------------------------------------------------------------------

class TestDismissEndpoint(_SignalsBase):

    def setUp(self):
        super().setUp()
        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config", return_value=_mock_config()
        )
        self._cfg_patcher.start()
        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._cfg_patcher.stop()
        super().tearDown()

    def _post(self, signal_id):
        return self.client.post(
            f"/api/v1/signals/{signal_id}/dismiss",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

    def test_dismiss_signal_endpoint(self):
        sid = self._insert()
        resp = self._post(sid)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "DISMISSED")
        rows = get_signals(status="DISMISSED", db_path=self.db)
        self.assertEqual([r["signal_id"] for r in rows], [sid])

    def test_dismiss_unknown_returns_404(self):
        self.assertEqual(self._post("does-not-exist").status_code, 404)

    def test_dismiss_already_dismissed_returns_409(self):
        sid = self._insert()
        self.assertEqual(self._post(sid).status_code, 200)
        self.assertEqual(self._post(sid).status_code, 409)

    def test_dismiss_requires_token(self):
        sid = self._insert()
        resp = self.client.post(f"/api/v1/signals/{sid}/dismiss")  # no Authorization
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# CIL-NEW-06: POST /api/v1/signals/{signal_id}/execute
# ---------------------------------------------------------------------------

class TestExecuteSignalEndpoint(_SignalsBase):
    """Tests for the Buy Signal Execution endpoint."""

    def setUp(self):
        super().setUp()
        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config", return_value=_mock_config()
        )
        self._cfg_patcher.start()
        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._cfg_patcher.stop()
        super().tearDown()

    def _post_execute(self, signal_id, payload=None):
        body = payload or {"order_type": "MARKET", "confirmed": True}
        return self.client.post(
            f"/api/v1/signals/{signal_id}/execute",
            json=body,
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

    def test_execute_signal_endpoint(self):
        """Execute returns 200 and orders_placed list for an APPROVED signal."""
        sid = self._insert(symbol="MU", strategy="PEAD", status="APPROVED", entry_price=200.0)
        # Patch RTH to True and Schwab to raise so we fall back to entry_price_scan.
        with patch("prime_api.prime_api_routes._is_rth", return_value=True), \
             patch("prime_trading.prime_schwab.SchwabClient.connect", side_effect=Exception("no token")):
            resp = self._post_execute(sid)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("orders_placed", data)
        self.assertIn("allocated_total", data)
        self.assertEqual(data["signal_id"], sid)

    def test_execute_signal_paper_mode(self):
        """PAPER mode: execute simulates order without calling Schwab."""
        sid = self._insert(symbol="NVDA", strategy="PEAD", status="APPROVED", entry_price=900.0)
        with patch("prime_api.prime_api_routes._is_rth", return_value=True), \
             patch("prime_trading.prime_schwab.SchwabClient.connect", side_effect=Exception("no token")):
            resp = self._post_execute(sid)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["mode"], "PAPER")
        statuses = [o.get("status") for o in data.get("orders_placed", [])]
        self.assertTrue(any("PAPER" in (s or "") for s in statuses))

    def test_execute_signal_after_hours_forces_limit(self):
        """Outside RTH: MARKET order returns 400 with after_hours error."""
        sid = self._insert(symbol="AAPL", strategy="PEAD", status="APPROVED")
        with patch("prime_api.prime_api_routes._is_rth", return_value=False):
            resp = self._post_execute(sid, {"order_type": "MARKET", "confirmed": True})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "after_hours")

    def test_execute_unknown_signal_returns_404(self):
        resp = self._post_execute("does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_execute_requires_confirmed(self):
        sid = self._insert(status="APPROVED")
        resp = self._post_execute(sid, {"order_type": "MARKET", "confirmed": False})
        self.assertEqual(resp.status_code, 400)

    def test_execute_requires_token(self):
        sid = self._insert(status="APPROVED")
        resp = self.client.post(
            f"/api/v1/signals/{sid}/execute",
            json={"order_type": "MARKET", "confirmed": True},
        )
        self.assertEqual(resp.status_code, 401)


class TestStagedEntry(unittest.TestCase):
    """CIL-NEW-08: Staged entry data layer and module API."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_staged_entry.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _make_entry(self, signal_id="sig-test-1", stage_count=2, stage_trigger="TIME"):
        from prime_trading.prime_staged_entry import StagedEntry
        return StagedEntry(
            signal_id=signal_id,
            symbol="AAPL",
            strategy="UOA",
            stage_count=stage_count,
            stage_trigger=stage_trigger,
            stage_interval_min=30,
            completed_stages=1,
            paper_accounts=[{"name": "PAPER", "buying_power": 100000}],
            live_accounts=[],
            execution_price=150.0,
            order_type="MARKET",
            mode="PAPER",
            max_order_pct=0.10,
            entry_price_scan=149.0,
            db_path=self.db,
        )

    def test_staged_entry_fires_stage1_immediately(self):
        """Stage 1 records land in prime_trade_log with stage_number=1."""
        from prime_trading.prime_staged_entry import StagedEntry, execute_next_stage
        entry = StagedEntry(
            signal_id="sig-stage1",
            symbol="AAPL",
            strategy="UOA",
            stage_count=2,
            stage_trigger="TIME",
            stage_interval_min=30,
            completed_stages=0,   # nothing done yet — this is Stage 1
            paper_accounts=[{"name": "PAPER", "buying_power": 100000}],
            live_accounts=[],
            execution_price=150.0,
            order_type="MARKET",
            mode="PAPER",
            max_order_pct=0.10,
            entry_price_scan=149.0,
            db_path=self.db,
        )
        result = execute_next_stage(entry)
        self.assertEqual(result["stage_number"], 1)
        self.assertGreater(result["total_allocated"], 0)
        self.assertEqual(result["orders_placed"][0]["status"], "PAPER_SIMULATED")
        # Verify DB record has stage_number=1
        from prime_data.prime_db import get_connection
        with get_connection(self.db) as conn:
            rows = conn.execute("SELECT stage_number, stage_total FROM prime_trade_log").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[0][1], 2)

    def test_staged_entry_time_trigger_schedules_stage2(self):
        """After Stage 1, register() places the entry in pending and stage_trigger=TIME is stored."""
        from prime_trading.prime_staged_entry import register, get_pending, pop_pending
        entry = self._make_entry(signal_id="sig-time-2", stage_trigger="TIME")
        register(entry)
        retrieved = get_pending("sig-time-2")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.stage_trigger, "TIME")
        self.assertEqual(retrieved.completed_stages, 1)
        pop_pending("sig-time-2")

    def test_staged_entry_dk_confirm_trigger(self):
        """DK_CONFIRM entry registers in pending and check_dk_staged_entries fires next stage."""
        from prime_trading.prime_staged_entry import register, get_pending, pop_pending, check_dk_staged_entries
        entry = self._make_entry(signal_id="sig-dk-3", stage_trigger="DK_CONFIRM")
        entry.completed_stages = 1
        register(entry)

        # Simulate DK_CONFIRM event for AAPL — check_dk_staged_entries should execute stage 2.
        check_dk_staged_entries("AAPL")

        # After firing stage 2 of 2, entry should be removed from pending.
        self.assertIsNone(get_pending("sig-dk-3"))

        # DB should have a stage_number=2 record.
        from prime_data.prime_db import get_connection
        with get_connection(self.db) as conn:
            rows = conn.execute("SELECT stage_number FROM prime_trade_log ORDER BY stage_number").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 2)


if __name__ == "__main__":
    unittest.main()
