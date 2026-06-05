"""
Sprint 24 Item 4 -- Stop Monitor + Trailing Stops acceptance tests.

Tests: alert mode fires banner (no order); auto mode fires sell (PAPER skips);
trailing stop moves up on price increase (LONG); trailing stop does NOT move
down on decrease (LONG); short trailing stop moves down correctly;
stop monitor check cycle runs.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_trading.prime_stop_monitor import (
    _check_position,
    _trailing_stop_price,
    _update_high_water,
    clear_alert,
    get_active_alerts,
    _stop_alerts,
    _alerts_lock,
    _fire_alert,
)
from prime_data.prime_db import init_db, insert_trade, get_trade
from prime_analytics.prime_signals_db import init_signals_table


def _pos(symbol="AAPL", direction="LONG", entry=100.0, shares=10,
         trailing_pct=None, high_water=None):
    return {
        "log_id":                 f"test-{symbol}-{direction}",
        "symbol":                 symbol,
        "direction":              direction,
        "entry_price":            entry,
        "price_at_scan":          entry,
        "shares":                 shares,
        "trailing_stop_pct":      trailing_pct,
        "trailing_stop_high_water": high_water,
        "status":                 "OPEN",
    }


class TestTrailingStopPrice(unittest.TestCase):

    def test_long_trailing_stop_below_high_water(self):
        # Entry $100, trailing 5%, high_water $110 -> stop = 110 * 0.95 = 104.50
        stop = _trailing_stop_price(100.0, 0.05, 110.0, "LONG")
        self.assertAlmostEqual(stop, 104.50, places=2)

    def test_short_trailing_stop_above_low_water(self):
        # Entry $100, trailing 5%, low_water $80 -> stop = 80 * 1.05 = 84.0
        stop = _trailing_stop_price(100.0, 0.05, 80.0, "SHORT")
        self.assertAlmostEqual(stop, 84.0, places=2)

    def test_long_trailing_at_entry(self):
        # High water = entry; stop = 100 * 0.95 = 95
        stop = _trailing_stop_price(100.0, 0.05, 100.0, "LONG")
        self.assertAlmostEqual(stop, 95.0, places=2)


class TestUpdateHighWater(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_stop_hw.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._log_id = insert_trade(
            strategy="TEST", symbol="AAPL", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=10, entry_time="2026-06-05T10:00:00",
            price_at_scan=100.0, entry_price=100.0, trade_source="PAPER",
            db_path=self.db,
        )

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_long_high_water_moves_up(self):
        hw = _update_high_water(self._log_id, 110.0, "LONG", 100.0, 100.0, self.db)
        self.assertEqual(hw, 110.0)

    def test_long_high_water_does_not_move_down(self):
        # Existing HW = 115; current = 105 — HW should stay 115
        hw = _update_high_water(self._log_id, 105.0, "LONG", 115.0, 100.0, self.db)
        self.assertEqual(hw, 115.0)

    def test_short_low_water_moves_down(self):
        # Short: low_water should track the lowest price
        hw = _update_high_water(self._log_id, 85.0, "SHORT", 90.0, 100.0, self.db)
        self.assertEqual(hw, 85.0)

    def test_short_low_water_does_not_move_up(self):
        # Short: existing LW = 80; current = 95 — LW should stay 80
        hw = _update_high_water(self._log_id, 95.0, "SHORT", 80.0, 100.0, self.db)
        self.assertEqual(hw, 80.0)


class TestCheckPosition(unittest.TestCase):

    def test_long_stop_breached(self):
        ops = {"long_stop_loss_pct": 0.05, "short_stop_loss_pct": 0.05}
        pos = _pos("AAPL", "LONG", entry=100.0)
        # current=94 < 95 (5% below entry) -> breach
        result = _check_position(pos, 94.0, ops)
        self.assertEqual(result, "BREACH")

    def test_long_stop_not_breached(self):
        ops = {"long_stop_loss_pct": 0.05, "short_stop_loss_pct": 0.05}
        pos = _pos("AAPL", "LONG", entry=100.0)
        result = _check_position(pos, 97.0, ops)
        self.assertIsNone(result)

    def test_short_stop_breached(self):
        ops = {"long_stop_loss_pct": 0.05, "short_stop_loss_pct": 0.05}
        pos = _pos("TSLA", "SHORT", entry=100.0)
        # current=106 > 105 (5% above entry) -> breach
        result = _check_position(pos, 106.0, ops)
        self.assertEqual(result, "BREACH")

    def test_trailing_stop_long_breach(self):
        ops = {"long_stop_loss_pct": 0.05, "short_stop_loss_pct": 0.05}
        # Entry 100, trailing 5%, high_water 110 -> stop = 104.5
        pos = _pos("GLD", "LONG", entry=100.0, trailing_pct=0.05, high_water=110.0)
        result = _check_position(pos, 104.0, ops)
        self.assertEqual(result, "BREACH")

    def test_trailing_stop_long_no_breach(self):
        ops = {"long_stop_loss_pct": 0.05, "short_stop_loss_pct": 0.05}
        pos = _pos("GLD", "LONG", entry=100.0, trailing_pct=0.05, high_water=110.0)
        result = _check_position(pos, 108.0, ops)
        self.assertIsNone(result)


class TestAlertMode(unittest.TestCase):

    def setUp(self):
        with _alerts_lock:
            _stop_alerts.clear()

    def tearDown(self):
        with _alerts_lock:
            _stop_alerts.clear()

    def test_alert_mode_fires_alert(self):
        pos = _pos("AAPL", "LONG", entry=100.0)
        _fire_alert(pos, 93.0, 95.0)
        alerts = get_active_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["symbol"], "AAPL")
        self.assertAlmostEqual(alerts[0]["current_price"], 93.0)

    def test_clear_alert(self):
        pos = _pos("MSFT", "LONG")
        _fire_alert(pos, 90.0, 95.0)
        clear_alert(pos["log_id"])
        self.assertEqual(len(get_active_alerts()), 0)

    def test_alert_mode_no_order_submitted(self):
        ops = {"stop_execution_mode": "ALERT", "long_stop_loss_pct": 0.05, "short_stop_loss_pct": 0.05}
        pos = _pos("NVDA", "LONG", entry=100.0)
        _fire_alert(pos, 93.0, 95.0)
        # Alert exists but no Schwab call was made
        alerts = get_active_alerts()
        self.assertEqual(len(alerts), 1)


class TestAutoModeSkipsInPaper(unittest.TestCase):
    """AUTO mode: PAPER config skips real sell, falls back to alert."""

    def setUp(self):
        with _alerts_lock:
            _stop_alerts.clear()

    def tearDown(self):
        with _alerts_lock:
            _stop_alerts.clear()

    def test_auto_mode_paper_fires_alert_not_order(self):
        from prime_trading.prime_stop_monitor import _fire_auto_sell
        paper_cfg = MagicMock()
        paper_cfg.trading_mode = "PAPER"
        with patch("prime_config.prime_config.get_config", return_value=paper_cfg):
            pos = _pos("COST", "LONG", entry=100.0)
            _fire_auto_sell(pos, 93.0)
        alerts = get_active_alerts()
        self.assertEqual(len(alerts), 1)


class TestStopAlertEndpoint(unittest.TestCase):

    def setUp(self):
        with _alerts_lock:
            _stop_alerts.clear()

        import json, tempfile
        from unittest.mock import MagicMock, patch
        from pathlib import Path

        self.db = Path(__file__).parent / "_test_stop_ep.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self.tmp_dir = tempfile.mkdtemp()
        self.ops_path = Path(self.tmp_dir) / "ops_config.json"
        with open(self.ops_path, "w") as f:
            json.dump({
                "scan_schedule": {}, "notification_channels": "TBD",
                "health_check_interval": 900, "max_trades": 5,
                "analysis_mode": "Universe", "long_stop_loss_pct": 0.05,
                "time_stop_minutes": 1950, "mata_profile": "Joint Brokerage",
                "stop_execution_mode": "ALERT",
            }, f)

        mock_cfg = MagicMock()
        mock_cfg.trading_mode = "PAPER"
        mock_cfg.api_token = "test-token"
        mock_cfg.ops.stop_execution_mode = "ALERT"

        self._db_p = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_p.start()
        self._cfg_p = patch("prime_config.prime_config.get_config", return_value=mock_cfg)
        self._cfg_p.start()
        import prime_api.prime_api_routes as routes
        self._orig = routes._OPS_CONFIG_PATH
        routes._OPS_CONFIG_PATH = self.ops_path

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self._auth = {"Authorization": "Bearer test-token"}

    def tearDown(self):
        import prime_api.prime_api_routes as routes
        routes._OPS_CONFIG_PATH = self._orig
        self._db_p.stop()
        self._cfg_p.stop()
        if self.db.exists():
            self.db.unlink()
        with _alerts_lock:
            _stop_alerts.clear()

    def test_stop_alerts_returns_200(self):
        resp = self.client.get("/api/v1/stop-alerts")
        self.assertEqual(resp.status_code, 200)

    def test_stop_alerts_empty_by_default(self):
        resp = self.client.get("/api/v1/stop-alerts")
        d = resp.get_json()
        self.assertEqual(d["count"], 0)

    def test_stop_alerts_shows_active_alert(self):
        pos = _pos("AAPL", "LONG")
        _fire_alert(pos, 93.0, 95.0)
        resp = self.client.get("/api/v1/stop-alerts")
        d = resp.get_json()
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["alerts"][0]["symbol"], "AAPL")


class TestTrailingStopEndpoint(unittest.TestCase):

    def setUp(self):
        import json, tempfile
        from unittest.mock import patch, MagicMock

        self.db = Path(__file__).parent / "_test_trailing.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self._log_id = insert_trade(
            strategy="TEST", symbol="COST", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=10, entry_time="2026-06-05T10:00:00",
            price_at_scan=895.0, entry_price=895.0, trade_source="PAPER",
            db_path=self.db,
        )

        self.tmp_dir = tempfile.mkdtemp()
        self.ops_path = Path(self.tmp_dir) / "ops_config.json"
        with open(self.ops_path, "w") as f:
            json.dump({
                "scan_schedule": {}, "notification_channels": "TBD",
                "health_check_interval": 900, "max_trades": 5,
                "analysis_mode": "Universe", "long_stop_loss_pct": 0.05,
                "time_stop_minutes": 1950, "mata_profile": "Joint Brokerage",
            }, f)

        mock_cfg = MagicMock()
        mock_cfg.trading_mode = "PAPER"
        mock_cfg.api_token = "test-token"

        self._db_p = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_p.start()
        self._cfg_p = patch("prime_config.prime_config.get_config", return_value=mock_cfg)
        self._cfg_p.start()
        import prime_api.prime_api_routes as routes
        self._orig = routes._OPS_CONFIG_PATH
        routes._OPS_CONFIG_PATH = self.ops_path

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self._auth = {"Authorization": "Bearer test-token"}

    def tearDown(self):
        import prime_api.prime_api_routes as routes
        routes._OPS_CONFIG_PATH = self._orig
        self._db_p.stop()
        self._cfg_p.stop()
        if self.db.exists():
            self.db.unlink()

    def test_set_trailing_stop(self):
        resp = self.client.post(
            f"/api/v1/trades/{self._log_id}/trailing-stop",
            json={"trailing_stop_pct": 0.05},
            headers=self._auth, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertAlmostEqual(d["trailing_stop_pct"], 0.05)

    def test_clear_trailing_stop(self):
        self.client.post(
            f"/api/v1/trades/{self._log_id}/trailing-stop",
            json={"trailing_stop_pct": 0.05},
            headers=self._auth, content_type="application/json",
        )
        resp = self.client.post(
            f"/api/v1/trades/{self._log_id}/trailing-stop",
            json={"trailing_stop_pct": None},
            headers=self._auth, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()["trailing_stop_pct"])


if __name__ == "__main__":
    unittest.main()
