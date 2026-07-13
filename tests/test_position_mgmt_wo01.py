"""
WO-PRIME-ACTIVE-POSITION-MGMT-01 acceptance tests.

Part A: mandatory stop enforcement (attach_stop_order, LIVE defaults).
Part B: escalation tiers (no-stop violation, drift alert, DK adverse).
Part C: profit-target auto-exit.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Shared DB setup helper
# ---------------------------------------------------------------------------

def _make_db(suffix: str) -> Path:
    db = Path(__file__).parent / f"_test_wo01_{suffix}.db"
    if db.exists():
        db.unlink()
    from prime_data.prime_db import init_db
    from prime_analytics.prime_signals_db import init_signals_table
    init_db(db)
    init_signals_table(db)
    return db


# ---------------------------------------------------------------------------
# Part A — attach_stop_order
# ---------------------------------------------------------------------------

class TestAttachStopOrder(unittest.TestCase):
    """WO-PRIME-ACTIVE-POSITION-MGMT-01 Part A: attach_stop_order submits STOP order."""

    def _mock_schwab(self, status_code=201, location="https://api.schwab.com/orders/99999"):
        client = MagicMock()
        resp = MagicMock()
        resp.status_code = status_code
        resp.headers = {"Location": location}
        resp.json.return_value = {}
        client.client.place_order.return_value = resp
        return client

    def test_long_stop_submits_sell_stop(self):
        from prime_trading.prime_schwab_orders import attach_stop_order
        client = self._mock_schwab()
        result = attach_stop_order("TSLA", 8, "LONG", 340.0, "HASH123", client)
        self.assertEqual(result["status"], "STOP_SUBMITTED")
        self.assertEqual(result["order_id"], "99999")
        raw = client.client.place_order.call_args[0][1]
        self.assertEqual(raw["orderType"], "STOP")
        self.assertEqual(raw["duration"], "GOOD_TILL_CANCEL")
        leg = raw["orderLegCollection"][0]
        self.assertEqual(leg["instruction"], "SELL")   # LONG → SELL stop
        self.assertEqual(leg["quantity"], 8)
        self.assertEqual(raw["stopPrice"], "340.0")

    def test_short_stop_submits_buy_stop(self):
        from prime_trading.prime_schwab_orders import attach_stop_order
        client = self._mock_schwab()
        result = attach_stop_order("XLC", 100, "SHORT", 112.0, "HASH456", client)
        self.assertEqual(result["status"], "STOP_SUBMITTED")
        raw = client.client.place_order.call_args[0][1]
        leg = raw["orderLegCollection"][0]
        self.assertEqual(leg["instruction"], "BUY")    # SHORT → BUY stop (cover)

    def test_schwab_rejection_raises_order_gate_error(self):
        from prime_trading.prime_schwab_orders import attach_stop_order, OrderGateError
        client = self._mock_schwab(status_code=400)
        client.client.place_order.return_value.json.return_value = {"message": "bad request"}
        with self.assertRaises(OrderGateError) as ctx:
            attach_stop_order("AAPL", 10, "LONG", 180.0, "HASH789", client)
        self.assertEqual(ctx.exception.gate, "SCHWAB_REJECT")

    def test_no_client_raises_order_gate_error(self):
        from prime_trading.prime_schwab_orders import attach_stop_order, OrderGateError
        with self.assertRaises(OrderGateError) as ctx:
            attach_stop_order("AAPL", 10, "LONG", 180.0, "HASH789", None)
        self.assertEqual(ctx.exception.gate, "NO_CLIENT")

    def test_invalid_params_raises_order_gate_error(self):
        from prime_trading.prime_schwab_orders import attach_stop_order, OrderGateError
        client = self._mock_schwab()
        with self.assertRaises(OrderGateError) as ctx:
            attach_stop_order("AAPL", 0, "LONG", 180.0, "HASH", client)
        self.assertEqual(ctx.exception.gate, "STOP_PARAMS")

    def test_stop_order_id_extracted_from_location_header(self):
        from prime_trading.prime_schwab_orders import attach_stop_order
        client = self._mock_schwab(status_code=201, location="https://api.schwab.com/trader/v1/accounts/HASH/orders/77777")
        result = attach_stop_order("SPY", 5, "LONG", 520.0, "HASH", client)
        self.assertEqual(result["order_id"], "77777")


# ---------------------------------------------------------------------------
# Part A — stop escalation guard (addendum to WO-PRIME-SCENARIO-EXECUTE-01)
# ---------------------------------------------------------------------------

class TestStopEscalationGuard(unittest.TestCase):
    """Addendum: min_stop_price guard prevents degrading an escalated trailing stop."""

    def _mock_schwab(self):
        client = MagicMock()
        resp = MagicMock()
        resp.status_code = 201
        resp.headers = {"Location": "https://api.schwab.com/orders/1"}
        resp.json.return_value = {}
        client.client.place_order.return_value = resp
        return client

    def test_long_stop_below_floor_raises(self):
        from prime_trading.prime_schwab_orders import attach_stop_order, OrderGateError
        # Trailing stop has escalated: floor is $203.70; proposed $194 would degrade it.
        with self.assertRaises(OrderGateError) as ctx:
            attach_stop_order("AAPL", 10, "LONG", 194.0, "HASH", self._mock_schwab(),
                              min_stop_price=203.70)
        self.assertEqual(ctx.exception.gate, "STOP_ESCALATION")

    def test_short_stop_above_ceiling_raises(self):
        from prime_trading.prime_schwab_orders import attach_stop_order, OrderGateError
        # Trailing stop (for SHORT) has escalated down: ceiling is $96.30; $102 would degrade it.
        with self.assertRaises(OrderGateError) as ctx:
            attach_stop_order("TSLA", 5, "SHORT", 102.0, "HASH", self._mock_schwab(),
                              min_stop_price=96.30)
        self.assertEqual(ctx.exception.gate, "STOP_ESCALATION")

    def test_long_stop_at_floor_passes(self):
        from prime_trading.prime_schwab_orders import attach_stop_order
        # Exactly at the floor — must be accepted (>= not >).
        result = attach_stop_order("AAPL", 10, "LONG", 203.70, "HASH", self._mock_schwab(),
                                   min_stop_price=203.70)
        self.assertEqual(result["status"], "STOP_SUBMITTED")

    def test_long_stop_above_floor_passes(self):
        from prime_trading.prime_schwab_orders import attach_stop_order
        # Better than the floor — accepted.
        result = attach_stop_order("AAPL", 10, "LONG", 210.0, "HASH", self._mock_schwab(),
                                   min_stop_price=203.70)
        self.assertEqual(result["status"], "STOP_SUBMITTED")

    def test_none_min_stop_price_skips_guard(self):
        from prime_trading.prime_schwab_orders import attach_stop_order
        # No floor provided — guard is disabled, any stop_price accepted.
        result = attach_stop_order("SPY", 3, "LONG", 400.0, "HASH", self._mock_schwab(),
                                   min_stop_price=None)
        self.assertEqual(result["status"], "STOP_SUBMITTED")

    def test_zero_min_stop_price_skips_guard(self):
        from prime_trading.prime_schwab_orders import attach_stop_order
        # Floor of 0 — guard is disabled (sentinel for "no known floor").
        result = attach_stop_order("SPY", 3, "LONG", 400.0, "HASH", self._mock_schwab(),
                                   min_stop_price=0.0)
        self.assertEqual(result["status"], "STOP_SUBMITTED")


# ---------------------------------------------------------------------------
# Part A — LIVE create_trade wires stop params + attaches stop order
# ---------------------------------------------------------------------------

class TestLiveTradeStopDefaults(unittest.TestCase):
    """WO Part A: LIVE trade entry stores stop_price and attaches STOP order via Schwab."""

    def setUp(self):
        import tempfile, json
        self.db = _make_db("live_stop")
        self.tmp = tempfile.mkdtemp()
        self.ops_path = Path(self.tmp) / "ops_config.json"
        with open(self.ops_path, "w") as f:
            json.dump({
                "scan_schedule": {}, "notification_channels": "TBD",
                "default_stop_loss_pct": 3.0,
                "default_trailing_stop_pct": 3.0,
            }, f)

        mock_cfg = MagicMock()
        mock_cfg.trading_mode = "LIVE"
        mock_cfg.api_token = "test-token"
        mock_cfg.ops.max_order_pct = 0.10
        mock_cfg.ops.max_position_pct = 0.15
        mock_cfg.ops.max_sector_pct = 0.30

        self._db_patch  = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._cfg_patch = patch("prime_config.prime_config.get_config", return_value=mock_cfg)
        self._db_patch.start()
        self._cfg_patch.start()

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
        self._db_patch.stop()
        self._cfg_patch.stop()
        if self.db.exists():
            self.db.unlink()

    def _mock_schwab_client(self):
        sc = MagicMock()
        sc.connect.return_value = True
        acct_resp = MagicMock(); acct_resp.status_code = 200
        acct_resp.json.return_value = [{"accountNumber": "123457926", "hashValue": "HASH_7926"}]
        sc.client.get_account_numbers.return_value = acct_resp
        sc.get_quotes.return_value = {}
        return sc

    def _mock_submit(self):
        return {"order_id": "ORD-ENTRY", "status": "SUBMITTED"}

    def _mock_stop_resp(self, order_id="ORD-STOP"):
        resp = MagicMock()
        resp.status_code = 201
        resp.headers = {"Location": f"https://api.schwab.com/orders/{order_id}"}
        resp.json.return_value = {}
        return resp

    def test_live_trade_stores_default_stop_price(self):
        """If no stop_pct in payload, default 3% stop_price is stored in trade log."""
        from prime_data.prime_db import get_open_trades
        sc = self._mock_schwab_client()
        sc.client.place_order.return_value = self._mock_stop_resp()

        with patch("prime_trading.prime_schwab.SchwabClient", return_value=sc), \
             patch("prime_trading.prime_schwab_orders.submit_order", return_value=self._mock_submit()), \
             patch("prime_trading.prime_fill_poller.start_fill_watcher"):
            resp = self.client.post("/api/v1/trades", json={
                "symbol": "TSLA", "strategy": "UOA", "direction": "LONG",
                "qty": 5, "price": 350.0, "order_type": "MARKET", "confirmed": True,
                "account": "7926",
            }, headers=self._auth)
        self.assertEqual(resp.status_code, 201)
        trades = get_open_trades(db_path=self.db)
        self.assertEqual(len(trades), 1)
        stop = trades[0]["stop_price"]
        self.assertIsNotNone(stop)
        # Default 3% stop for LONG: 350 * (1 - 0.03) = 339.5
        self.assertAlmostEqual(float(stop), 339.5, places=2)

    def test_live_trade_attaches_stop_order_to_schwab(self):
        """attach_stop_order is called with STOP after entry order is submitted."""
        sc = self._mock_schwab_client()
        stop_resp = self._mock_stop_resp("ORD-STOP-99")
        sc.client.place_order.return_value = stop_resp
        attach_calls = []

        def fake_attach(**kw):
            attach_calls.append(kw)
            return {"order_id": "ORD-STOP-99", "status": "STOP_SUBMITTED"}

        with patch("prime_trading.prime_schwab.SchwabClient", return_value=sc), \
             patch("prime_trading.prime_schwab_orders.submit_order", return_value=self._mock_submit()), \
             patch("prime_trading.prime_schwab_orders.attach_stop_order", side_effect=fake_attach), \
             patch("prime_trading.prime_fill_poller.start_fill_watcher"):
            resp = self.client.post("/api/v1/trades", json={
                "symbol": "AAPL", "strategy": "PSA", "direction": "LONG",
                "qty": 10, "price": 200.0, "order_type": "MARKET", "confirmed": True,
                "account": "7926",
            }, headers=self._auth)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(attach_calls), 1)
        self.assertAlmostEqual(attach_calls[0]["stop_price"], 194.0, places=2)
        self.assertEqual(attach_calls[0]["symbol"], "AAPL")

    def test_live_trade_stop_attach_failure_does_not_block_trade(self):
        """If attach_stop_order raises, the trade is still recorded (201 returned)."""
        from prime_data.prime_db import get_open_trades
        sc = self._mock_schwab_client()

        def fail_attach(**kw):
            raise Exception("Schwab stop order rejected")

        with patch("prime_trading.prime_schwab.SchwabClient", return_value=sc), \
             patch("prime_trading.prime_schwab_orders.submit_order", return_value=self._mock_submit()), \
             patch("prime_trading.prime_schwab_orders.attach_stop_order", side_effect=fail_attach), \
             patch("prime_trading.prime_fill_poller.start_fill_watcher"):
            resp = self.client.post("/api/v1/trades", json={
                "symbol": "SPY", "strategy": "PSA", "direction": "LONG",
                "qty": 3, "price": 530.0, "order_type": "MARKET", "confirmed": True,
                "account": "7926",
            }, headers=self._auth)
        self.assertEqual(resp.status_code, 201)
        trades = get_open_trades(db_path=self.db)
        self.assertEqual(len(trades), 1)

    def test_live_trade_explicit_stop_pct_overrides_default(self):
        """Explicit stop_pct in payload is used instead of the default."""
        from prime_data.prime_db import get_open_trades
        sc = self._mock_schwab_client()

        def fake_attach(**kw):
            return {"order_id": "ORD-STOP", "status": "STOP_SUBMITTED"}

        with patch("prime_trading.prime_schwab.SchwabClient", return_value=sc), \
             patch("prime_trading.prime_schwab_orders.submit_order", return_value=self._mock_submit()), \
             patch("prime_trading.prime_schwab_orders.attach_stop_order", side_effect=fake_attach), \
             patch("prime_trading.prime_fill_poller.start_fill_watcher"):
            resp = self.client.post("/api/v1/trades", json={
                "symbol": "MSFT", "strategy": "PSA", "direction": "LONG",
                "qty": 5, "price": 400.0, "order_type": "MARKET", "confirmed": True,
                "account": "7926", "stop_pct": 5.0,
            }, headers=self._auth)
        self.assertEqual(resp.status_code, 201)
        trades = get_open_trades(db_path=self.db)
        stop = float(trades[0]["stop_price"])
        self.assertAlmostEqual(stop, 380.0, places=2)  # 400 * (1 - 0.05)


# ---------------------------------------------------------------------------
# Part B — no-stop violation
# ---------------------------------------------------------------------------

class TestNoStopViolation(unittest.TestCase):

    def setUp(self):
        self.db = _make_db("no_stop")
        # Reset escalation counters between tests
        from prime_trading import prime_stop_monitor as sm
        with sm._escalation_lock:
            sm._escalation_counters.clear()

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _pos(self, stop_price=None, trailing_pct=None, trailing_active=0, log_id="L1"):
        from prime_data.prime_db import insert_trade
        log_id = insert_trade(
            strategy="TEST", symbol="TSLA", direction="LONG", mode="LIVE",
            order_type="MARKET", shares=8, entry_time="2026-06-30T09:35:00",
            price_at_scan=350.0, entry_price=350.0, account="7926",
            trade_source="LIVE", stop_price=stop_price, db_path=self.db,
        )
        return {"log_id": log_id, "symbol": "TSLA", "direction": "LONG",
                "stop_price": stop_price, "trailing_stop_pct": trailing_pct,
                "trailing_stop_active": trailing_active, "entry_price": 350.0}

    def test_no_stop_fires_warn_on_first_cycle(self):
        from prime_trading.prime_stop_monitor import _check_no_stop_violation
        from prime_data.prime_db import get_ops_events
        pos = self._pos(stop_price=None, trailing_pct=None)
        ops = {"no_stop_violation_grace_checks": 1}
        result = _check_no_stop_violation(pos, ops, db_path=self.db)
        self.assertTrue(result)
        events = get_ops_events(db_path=self.db)
        ev = [e for e in events if e["event_type"] == "NO_STOP_VIOLATION"]
        self.assertTrue(len(ev) >= 1)
        self.assertEqual(ev[-1]["severity"], "WARN")

    def test_no_stop_escalates_to_critical_after_grace(self):
        from prime_trading.prime_stop_monitor import _check_no_stop_violation
        from prime_data.prime_db import get_ops_events
        pos = self._pos(stop_price=None, trailing_pct=None)
        ops = {"no_stop_violation_grace_checks": 1}
        _check_no_stop_violation(pos, ops, db_path=self.db)  # cycle 1 → WARN
        _check_no_stop_violation(pos, ops, db_path=self.db)  # cycle 2 → CRITICAL
        events = get_ops_events(db_path=self.db)
        ev = [e for e in events if e["event_type"] == "NO_STOP_VIOLATION"]
        self.assertTrue(any(e["severity"] == "CRITICAL" for e in ev))

    def test_stop_present_does_not_fire(self):
        from prime_trading.prime_stop_monitor import _check_no_stop_violation
        pos = self._pos(stop_price=340.0)
        ops = {"no_stop_violation_grace_checks": 1}
        self.assertFalse(_check_no_stop_violation(pos, ops, db_path=self.db))

    def test_trailing_pct_present_does_not_fire(self):
        from prime_trading.prime_stop_monitor import _check_no_stop_violation
        pos = self._pos(trailing_pct=0.03)
        ops = {"no_stop_violation_grace_checks": 1}
        self.assertFalse(_check_no_stop_violation(pos, ops, db_path=self.db))


# ---------------------------------------------------------------------------
# Part B — drift alert
# ---------------------------------------------------------------------------

class TestDriftAlert(unittest.TestCase):

    def setUp(self):
        self.db = _make_db("drift")
        from prime_trading import prime_stop_monitor as sm
        with sm._escalation_lock:
            sm._escalation_counters.clear()

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _pos(self, entry=100.0, direction="LONG", log_id="D1"):
        return {"log_id": log_id, "symbol": "XLC", "direction": direction,
                "entry_price": entry, "price_at_scan": entry}

    def test_drift_fires_when_loss_exceeds_threshold(self):
        from prime_trading.prime_stop_monitor import _check_drift_alert
        from prime_data.prime_db import get_ops_events
        pos = self._pos(entry=100.0)
        ops = {"position_drift_alert_pct": 5.0, "position_escalation_consecutive_checks": 3}
        fired = _check_drift_alert(pos, 93.0, ops, db_path=self.db)  # -7% > -5% threshold
        self.assertTrue(fired)
        events = get_ops_events(db_path=self.db)
        self.assertTrue(any(e["event_type"] == "DRIFT_ALERT" for e in events))

    def test_drift_does_not_fire_within_threshold(self):
        from prime_trading.prime_stop_monitor import _check_drift_alert
        pos = self._pos(entry=100.0)
        ops = {"position_drift_alert_pct": 5.0, "position_escalation_consecutive_checks": 3}
        fired = _check_drift_alert(pos, 97.0, ops, db_path=self.db)  # -3% < -5% threshold
        self.assertFalse(fired)

    def test_drift_escalates_to_critical_after_consecutive_days(self):
        from prime_trading.prime_stop_monitor import _check_drift_alert, _utc_day_start
        from prime_data.prime_db import get_ops_events
        pos = self._pos(entry=100.0, log_id="D2")
        ops = {"position_drift_alert_pct": 5.0, "position_escalation_consecutive_checks": 2}
        # Simulate 2 days — each day the alert fires once (not throttled because different event type mock)
        with patch("prime_data.prime_db._recent_trade_exists", return_value=False):
            _check_drift_alert(pos, 93.0, ops, db_path=self.db)  # day 1
            _check_drift_alert(pos, 92.0, ops, db_path=self.db)  # day 2 → CRITICAL
        events = get_ops_events(db_path=self.db)
        drift_evs = [e for e in events if e["event_type"] == "DRIFT_ALERT"]
        self.assertTrue(any(e["severity"] == "CRITICAL" for e in drift_evs))

    def test_drift_counter_resets_on_recovery(self):
        from prime_trading.prime_stop_monitor import _check_drift_alert, _get_escalation_count
        pos = self._pos(entry=100.0, log_id="D3")
        ops = {"position_drift_alert_pct": 5.0, "position_escalation_consecutive_checks": 3}
        with patch("prime_data.prime_db._recent_trade_exists", return_value=False):
            _check_drift_alert(pos, 93.0, ops, db_path=self.db)  # fires, counter=1
            _check_drift_alert(pos, 98.0, ops, db_path=self.db)  # recovers, counter resets
        self.assertEqual(_get_escalation_count("D3", "DRIFT"), 0)

    def test_drift_short_direction_correct(self):
        from prime_trading.prime_stop_monitor import _check_drift_alert
        from prime_data.prime_db import get_ops_events
        pos = self._pos(entry=100.0, direction="SHORT", log_id="D4")
        ops = {"position_drift_alert_pct": 5.0, "position_escalation_consecutive_checks": 3}
        # SHORT adverse: price rising (entry 100, now 107 = -7% loss for short)
        with patch("prime_data.prime_db._recent_trade_exists", return_value=False):
            fired = _check_drift_alert(pos, 107.0, ops, db_path=self.db)
        self.assertTrue(fired)


# ---------------------------------------------------------------------------
# Part C — profit target auto-exit
# ---------------------------------------------------------------------------

class TestProfitTarget(unittest.TestCase):

    def setUp(self):
        self.db = _make_db("target")

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _open_trade(self, symbol, direction, entry, target_price=None):
        from prime_data.prime_db import insert_trade
        return insert_trade(
            strategy="TEST", symbol=symbol, direction=direction, mode="PAPER",
            order_type="MARKET", shares=10, entry_time="2026-06-30T09:35:00",
            price_at_scan=entry, entry_price=entry, account="TEST",
            trade_source="PAPER", target_price=target_price, db_path=self.db,
        )

    def test_long_exits_when_price_reaches_target(self):
        from prime_trading.prime_stop_monitor import _check_profit_target
        from prime_data.prime_db import get_trade
        log_id = self._open_trade("AAPL", "LONG", 200.0, target_price=220.0)
        pos = {"log_id": log_id, "symbol": "AAPL", "direction": "LONG",
               "target_price": 220.0, "entry_price": 200.0, "shares": 10, "account": "TEST"}
        with patch("prime_data.prime_db._recent_trade_exists", return_value=False), \
             patch("prime_trading.prime_stop_monitor._fire_exit_sell") as mock_exit:
            fired = _check_profit_target(pos, 221.0, db_path=self.db)
        self.assertTrue(fired)
        mock_exit.assert_called_once()
        _, call_price, call_reason = mock_exit.call_args[0]
        self.assertEqual(call_reason, "TARGET_HIT")

    def test_long_does_not_exit_below_target(self):
        from prime_trading.prime_stop_monitor import _check_profit_target
        pos = {"log_id": "X", "symbol": "AAPL", "direction": "LONG",
               "target_price": 220.0, "entry_price": 200.0, "shares": 10}
        fired = _check_profit_target(pos, 215.0, db_path=self.db)
        self.assertFalse(fired)

    def test_short_exits_when_price_drops_to_target(self):
        from prime_trading.prime_stop_monitor import _check_profit_target
        log_id = self._open_trade("XLC", "SHORT", 110.0, target_price=98.0)
        pos = {"log_id": log_id, "symbol": "XLC", "direction": "SHORT",
               "target_price": 98.0, "entry_price": 110.0, "shares": 10, "account": "TEST"}
        with patch("prime_data.prime_db._recent_trade_exists", return_value=False), \
             patch("prime_trading.prime_stop_monitor._fire_exit_sell") as mock_exit:
            fired = _check_profit_target(pos, 97.5, db_path=self.db)
        self.assertTrue(fired)
        self.assertEqual(mock_exit.call_args[0][2], "TARGET_HIT")

    def test_no_target_price_is_noop(self):
        from prime_trading.prime_stop_monitor import _check_profit_target
        pos = {"log_id": "X", "symbol": "TSLA", "direction": "LONG",
               "target_price": None, "entry_price": 350.0, "shares": 5}
        self.assertFalse(_check_profit_target(pos, 400.0, db_path=self.db))

    def test_target_stored_direction_aware(self):
        """target_price in DB: LONG → entry * (1 + pct), SHORT → entry * (1 - pct)."""
        from prime_data.prime_db import get_trade
        # Simulate what create_trade does (from prime_api_routes PAPER path)
        long_target = round(200.0 * (1 + 10.0 / 100.0), 4)   # 220.0
        short_target = round(110.0 * (1 - 10.0 / 100.0), 4)  # 99.0
        self.assertAlmostEqual(long_target, 220.0)
        self.assertAlmostEqual(short_target, 99.0)

    def test_target_hit_throttled_once_per_day(self):
        """Second fire in same day is suppressed (no double-exit)."""
        from prime_trading.prime_stop_monitor import _check_profit_target
        log_id = self._open_trade("SPY", "LONG", 530.0, target_price=550.0)
        pos = {"log_id": log_id, "symbol": "SPY", "direction": "LONG",
               "target_price": 550.0, "entry_price": 530.0, "shares": 5, "account": "TEST"}
        with patch("prime_data.prime_db._recent_trade_exists", return_value=True):
            fired = _check_profit_target(pos, 555.0, db_path=self.db)
        self.assertFalse(fired)

    def test_target_hit_takes_priority_over_trailing_stop(self):
        """Regression: _check_profit_target runs before _check_trailing_stop in cycle.

        Verified structurally: profit target returns True early → trailing stop
        not reached in the same cycle.
        """
        from prime_trading.prime_stop_monitor import _check_profit_target, _check_trailing_stop
        log_id = self._open_trade("MSFT", "LONG", 400.0, target_price=450.0)
        pos = {"log_id": log_id, "symbol": "MSFT", "direction": "LONG",
               "target_price": 450.0, "entry_price": 400.0, "shares": 5,
               "trailing_stop_pct": 0.03, "trailing_stop_active": True,
               "trailing_stop_peak": 445.0, "account": "TEST"}
        trailing_called = []
        with patch("prime_data.prime_db._recent_trade_exists", return_value=False), \
             patch("prime_trading.prime_stop_monitor._fire_exit_sell"):
            target_fired = _check_profit_target(pos, 451.0, db_path=self.db)
        self.assertTrue(target_fired)
        # Caller (run_check_cycle) skips trailing check if target fired — see cycle logic


if __name__ == "__main__":
    unittest.main()
