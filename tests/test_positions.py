"""
Sprint 16 Item 5 (Position Management) acceptance tests.

Covers the pure enrichment helpers (P&L, stop badge color, hold-time format,
stop price) and the POST /api/v1/trades/close endpoint (auth, validation, and
prime_trade_log update via prime_db.close_trade_manual).
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_api import prime_positions as pp
from prime_api.prime_api_server import create_app
from prime_data.prime_db import (
    init_db, insert_trade, get_trade, get_open_positions, close_trade_manual,
)
from prime_analytics.prime_signals_db import init_signals_table

TOKEN = "test-token-close"


class TestPnlHelpers(unittest.TestCase):
    def test_long_pnl_positive(self):
        out = pp.compute_pnl(100.0, 110.0, 10, "LONG")
        self.assertAlmostEqual(out["pnl_dollars"], 100.0)
        self.assertAlmostEqual(out["pnl_pct"], 10.0)
        self.assertEqual(out["color"], "green")

    def test_long_pnl_negative(self):
        out = pp.compute_pnl(100.0, 95.0, 10, "LONG")
        self.assertAlmostEqual(out["pnl_dollars"], -50.0)
        self.assertEqual(out["color"], "red")

    def test_short_pnl_inverse(self):
        out = pp.compute_pnl(100.0, 90.0, 10, "SHORT")
        self.assertAlmostEqual(out["pnl_dollars"], 100.0)  # profit when price falls
        self.assertEqual(out["color"], "green")


class TestStopBadge(unittest.TestCase):
    def test_long_green_within_bounds(self):
        # stop = 95; price 105 well above -> GREEN
        self.assertEqual(pp.stop_badge(100.0, 105.0, "LONG"), "GREEN")

    def test_long_amber_near_stop(self):
        # stop = 95; within 1% above stop (95..95.95) -> AMBER
        self.assertEqual(pp.stop_badge(100.0, 95.5, "LONG"), "AMBER")

    def test_long_red_breached(self):
        self.assertEqual(pp.stop_badge(100.0, 94.0, "LONG"), "RED")

    def test_short_red_breached(self):
        # short stop = 105; price >= 105 -> breached
        self.assertEqual(pp.stop_badge(100.0, 106.0, "SHORT"), "RED")

    def test_short_green(self):
        self.assertEqual(pp.stop_badge(100.0, 96.0, "SHORT"), "GREEN")

    def test_stop_price_long_default(self):
        self.assertAlmostEqual(pp.compute_stop_price(100.0, -5.0, "LONG"), 95.0)

    def test_stop_price_short_default(self):
        self.assertAlmostEqual(pp.compute_stop_price(100.0, -5.0, "SHORT"), 105.0)


class TestHoldTime(unittest.TestCase):
    def test_days_and_hours(self):
        now = datetime(2026, 6, 3, 14, 0, 0)
        entry = (now - timedelta(days=2, hours=4)).isoformat()
        self.assertEqual(pp.format_hold_time(entry, now), "2d 4h")

    def test_hours_and_minutes(self):
        now = datetime(2026, 6, 3, 14, 0, 0)
        entry = (now - timedelta(hours=5, minutes=12)).isoformat()
        self.assertEqual(pp.format_hold_time(entry, now), "5h 12m")

    def test_minutes_only(self):
        now = datetime(2026, 6, 3, 14, 0, 0)
        entry = (now - timedelta(minutes=7)).isoformat()
        self.assertEqual(pp.format_hold_time(entry, now), "7m")

    def test_unparseable(self):
        self.assertEqual(pp.format_hold_time("not-a-date"), "--")

    def test_enrich_flags_time_stop(self):
        now = datetime(2026, 6, 3, 14, 0, 0)
        entry = (now - timedelta(minutes=2000)).isoformat()
        pos = {"symbol": "AAA", "entry_price": 100.0, "shares": 10,
               "direction": "LONG", "entry_time": entry}
        out = pp.enrich_position(pos, current_price=105.0, now=now)
        self.assertTrue(out["time_stop_exceeded"])
        self.assertEqual(out["stop_badge"], "GREEN")
        self.assertAlmostEqual(out["unrealized_pnl"], 50.0)


def _fake_config(db_path, mode="PAPER", token=TOKEN):
    return SimpleNamespace(db_path=db_path, api_token=token, trading_mode=mode)


class TestCloseEndpoint(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_close.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()
        self.mode = "PAPER"
        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config",
            side_effect=lambda: _fake_config(self.db, self.mode),
        )
        self._cfg_patcher.start()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        # seed one open LONG position
        self.log_id = insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=10, entry_time=datetime.now().isoformat(),
            price_at_scan=100.0, entry_price=100.0, trade_source="PAPER",
            db_path=self.db,
        )

    def tearDown(self):
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def _post(self, payload, token=TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/api/v1/trades/close", json=payload, headers=headers)

    def test_close_requires_auth(self):
        resp = self._post({"log_id": self.log_id, "exit_price": 110.0}, token=None)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(get_trade(self.log_id, db_path=self.db)["status"], "OPEN")

    def test_close_updates_trade_log(self):
        resp = self._post({"log_id": self.log_id, "exit_price": 110.0,
                           "exit_reason": "MANUAL"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertAlmostEqual(body["pnl_dollars"], 100.0)  # (110-100)*10
        trade = get_trade(self.log_id, db_path=self.db)
        self.assertEqual(trade["status"], "CLOSED")
        self.assertAlmostEqual(trade["exit_price"], 110.0)
        self.assertAlmostEqual(trade["pnl_dollars"], 100.0)
        self.assertEqual(len(get_open_positions(db_path=self.db)), 0)

    def test_close_unknown_log_id_404(self):
        resp = self._post({"log_id": "nope", "exit_price": 110.0})
        self.assertEqual(resp.status_code, 404)

    def test_close_invalid_price_400(self):
        self.assertEqual(self._post({"log_id": self.log_id, "exit_price": 0}).status_code, 400)
        self.assertEqual(self._post({"log_id": self.log_id}).status_code, 400)

    def test_close_missing_log_id_400(self):
        self.assertEqual(self._post({"exit_price": 110.0}).status_code, 400)

    def test_close_rejected_in_live_mode(self):
        self.mode = "LIVE"
        resp = self._post({"log_id": self.log_id, "exit_price": 110.0})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(get_trade(self.log_id, db_path=self.db)["status"], "OPEN")

    def test_close_trade_manual_short_direction(self):
        sid = insert_trade(
            strategy="MTS", symbol="TSLA", direction="SHORT", mode="PAPER",
            order_type="MARKET", shares=5, entry_time=datetime.now().isoformat(),
            price_at_scan=200.0, entry_price=200.0, trade_source="PAPER",
            db_path=self.db,
        )
        result = close_trade_manual(sid, 190.0, "MANUAL", db_path=self.db)
        self.assertAlmostEqual(result["pnl_dollars"], 50.0)  # (200-190)*5 short profit


if __name__ == "__main__":
    unittest.main()
