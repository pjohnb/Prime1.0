"""
Sprint 32 Thread 1 — PositionMonitor engine + /positions/health endpoint.

Covers PM-HEALTH-02 (thesis logic, RTH gating, upsert, alert-once, AUTO_SELL)
and PM-HEALTH-03 (endpoint shape, UNKNOWN fallback, counts).
"""

import sqlite3
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_trading.prime_position_monitor import (
    PositionMonitor,
    compute_thesis_status,
)


def _cfg(interval=0, action="ALERT"):
    return types.SimpleNamespace(
        position_monitor_interval_seconds=interval,
        position_monitor_action=action,
    )


def _now():
    return datetime.now()


# ---------------------------------------------------------------------------
# Fixtures / seeding
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    from prime_data.prime_db import init_db
    path = tmp_path / "pm.db"
    init_db(db_path=path)
    return path


def _insert_signal(path, signal_id, symbol, strategy, direction, dk_status,
                   scan_ts, status="APPROVED"):
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT OR REPLACE INTO prime_signals
             (signal_id, symbol, strategy, scan_ts, status, direction,
              dk_status, entry_price, score)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (signal_id, symbol, strategy, scan_ts, status, direction,
         dk_status, 100.0, 70.0),
    )
    conn.commit()
    conn.close()


def _insert_open_trade(path, log_id, symbol, direction, signal_id,
                       shares=10, account="IRA", entry_price=100.0,
                       strategy="UOA", entry_time=None):
    entry_time = entry_time or _now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO prime_trade_log
             (log_id, strategy, symbol, direction, mode, order_type, shares,
              entry_price, entry_time, status, signal_id, account)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (log_id, strategy, symbol, direction, "PAPER", "MARKET", shares,
         entry_price, entry_time, "OPEN", signal_id, account),
    )
    conn.commit()
    conn.close()


def _seed(path, symbol="COST", pos_dir="LONG", scanner="UOA",
          dk_status="NEUTRAL", sig_dir="LONG", log_id=1, signal_id="sig1",
          scan_ts=None, signal_status="APPROVED"):
    scan_ts = scan_ts or _now().strftime("%Y-%m-%d %H:%M:%S")
    _insert_signal(path, signal_id, symbol, scanner, sig_dir, dk_status,
                   scan_ts, status=signal_status)
    _insert_open_trade(path, log_id, symbol, pos_dir, signal_id, strategy=scanner)


def _health_rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM prime_position_health")]
    conn.close()
    return rows


def _ops_events(path, event_type=None):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM prime_ops_health"
    args = ()
    if event_type:
        q += " WHERE event_type=?"
        args = (event_type,)
    rows = [dict(r) for r in conn.execute(q, args)]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Pure thesis logic (PM-HEALTH-02)
# ---------------------------------------------------------------------------

class TestThesisLogic:

    def _sig(self, direction, hours_ago=1):
        ts = (_now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        return {"direction": direction, "scan_ts": ts}

    def test_green_long_neutral(self):
        status, _, alert = compute_thesis_status(
            "LONG", "sig1", "NEUTRAL", self._sig("LONG"), _now())
        assert status == "GREEN" and alert is None

    def test_green_long_confirming(self):
        status, _, _ = compute_thesis_status(
            "LONG", "sig1", "CONFIRMING", self._sig("LONG"), _now())
        assert status == "GREEN"

    def test_green_short_nullifying(self):
        status, _, _ = compute_thesis_status(
            "SHORT", "sig1", "NULLIFYING", self._sig("SHORT"), _now())
        assert status == "GREEN"

    def test_red_dk_reversal_long(self):
        status, _, alert = compute_thesis_status(
            "LONG", "sig1", "NULLIFYING", self._sig("LONG"), _now())
        assert status == "RED" and alert == "DK_REVERSAL"

    def test_red_dk_reversal_short(self):
        status, _, alert = compute_thesis_status(
            "SHORT", "sig1", "CONFIRMING", self._sig("SHORT"), _now())
        assert status == "RED" and alert == "DK_REVERSAL"

    def test_red_signal_reversal(self):
        status, _, alert = compute_thesis_status(
            "LONG", "sig1", "NEUTRAL", self._sig("SHORT"), _now())
        assert status == "RED" and alert == "SIGNAL_REVERSAL"

    def test_amber_no_signal_id(self):
        status, _, alert = compute_thesis_status(
            "LONG", None, "NEUTRAL", None, _now())
        assert status == "AMBER" and alert is None

    def test_amber_stale_signal(self):
        status, _, _ = compute_thesis_status(
            "LONG", "sig1", "NEUTRAL", self._sig("LONG", hours_ago=48), _now())
        assert status == "AMBER"

    def test_amber_no_originating_signal(self):
        status, _, _ = compute_thesis_status(
            "LONG", "sig1", "NEUTRAL", None, _now())
        assert status == "AMBER"

    def test_dk_reversal_beats_schwab_import(self):
        # Even with no signal_id, a DK reversal is RED, not AMBER.
        status, _, alert = compute_thesis_status(
            "LONG", None, "NULLIFYING", None, _now())
        assert status == "RED" and alert == "DK_REVERSAL"


# ---------------------------------------------------------------------------
# Poll / upsert / alerting (PM-HEALTH-02)
# ---------------------------------------------------------------------------

class TestPoll:

    def test_poll_upserts_health(self, db):
        _seed(db)
        n = PositionMonitor(db_path=db, config=_cfg())._poll()
        assert n == 1
        rows = _health_rows(db)
        assert len(rows) == 1
        assert rows[0]["thesis_status"] == "GREEN"
        assert str(rows[0]["log_id"]) == "1"

    def test_red_logs_alert(self, db):
        _seed(db, dk_status="NULLIFYING")           # LONG + NULLIFYING -> RED
        PositionMonitor(db_path=db, config=_cfg())._poll()
        alerts = _ops_events(db, "DK_REVERSAL_ALERT")
        assert len(alerts) == 1
        assert alerts[0]["symbol"] == "COST"
        assert alerts[0]["severity"] == "WARN"

    def test_signal_reversal_alert_event(self, db):
        _seed(db, sig_dir="SHORT")                   # latest UOA signal SHORT vs LONG
        PositionMonitor(db_path=db, config=_cfg())._poll()
        assert len(_ops_events(db, "SIGNAL_REVERSAL_ALERT")) == 1

    def test_alert_once_per_red(self, db):
        _seed(db, dk_status="NULLIFYING")
        mon = PositionMonitor(db_path=db, config=_cfg())
        mon._poll()
        mon._poll()
        mon._poll()
        # Three RED polls, but only one alert.
        assert len(_ops_events(db, "DK_REVERSAL_ALERT")) == 1

    def test_red_then_recover_then_red_realerts(self, db):
        _seed(db, dk_status="NULLIFYING")
        mon = PositionMonitor(db_path=db, config=_cfg())
        mon._poll()                                  # RED -> alert #1
        # Recover to NEUTRAL.
        _insert_signal(db, "sig1", "COST", "UOA", "LONG", "NEUTRAL",
                       _now().strftime("%Y-%m-%d %H:%M:%S"))
        mon._poll()                                  # GREEN, resets alert latch
        _insert_signal(db, "sig1", "COST", "UOA", "LONG", "NULLIFYING",
                       _now().strftime("%Y-%m-%d %H:%M:%S"))
        mon._poll()                                  # RED again -> alert #2
        assert len(_ops_events(db, "DK_REVERSAL_ALERT")) == 2

    def test_auto_sell_fires_when_configured(self, db, monkeypatch):
        _seed(db, dk_status="NULLIFYING")
        mon = PositionMonitor(db_path=db, config=_cfg(action="AUTO_SELL"))
        calls = []
        monkeypatch.setattr(mon, "_post_mata_sell", lambda sym: calls.append(sym))
        mon._poll()
        assert calls == ["COST"]
        assert len(_ops_events(db, "DK_REVERSAL_AUTO_SELL")) == 1

    def test_no_auto_sell_in_alert_mode(self, db, monkeypatch):
        _seed(db, dk_status="NULLIFYING")
        mon = PositionMonitor(db_path=db, config=_cfg(action="ALERT"))
        calls = []
        monkeypatch.setattr(mon, "_post_mata_sell", lambda sym: calls.append(sym))
        mon._poll()
        assert calls == []
        assert _ops_events(db, "DK_REVERSAL_AUTO_SELL") == []

    def test_schwab_import_position_amber(self, db):
        # Open trade with no signal_id -> AMBER, no alert.
        _insert_open_trade(db, 5, "AAPL", "LONG", None, strategy="UOA")
        PositionMonitor(db_path=db, config=_cfg())._poll()
        rows = {str(r["log_id"]): r for r in _health_rows(db)}
        assert rows["5"]["thesis_status"] == "AMBER"
        assert _ops_events(db) == []


# ---------------------------------------------------------------------------
# Lifecycle / RTH gating (PM-HEALTH-02)
# ---------------------------------------------------------------------------

class TestLifecycle:

    def test_no_poll_outside_rth(self, db, monkeypatch):
        mon = PositionMonitor(db_path=db, config=_cfg(interval=0))
        calls = []
        monkeypatch.setattr(mon, "_is_rth", lambda: False)
        monkeypatch.setattr(mon, "_poll", lambda *a, **k: calls.append(1))
        mon.start()
        time.sleep(0.05)
        mon.stop()
        assert calls == []

    def test_polls_during_rth(self, db, monkeypatch):
        mon = PositionMonitor(db_path=db, config=_cfg(interval=0))
        calls = []
        monkeypatch.setattr(mon, "_is_rth", lambda: True)
        monkeypatch.setattr(mon, "_poll", lambda *a, **k: (calls.append(1), 0)[1])
        mon.start()
        time.sleep(0.05)
        mon.stop()
        assert len(calls) >= 1

    def test_start_idempotent(self, db, monkeypatch):
        mon = PositionMonitor(db_path=db, config=_cfg(interval=0))
        monkeypatch.setattr(mon, "_is_rth", lambda: False)
        mon.start()
        first = mon._thread
        mon.start()
        assert mon._thread is first      # second start is a no-op
        mon.stop()


# Endpoint (PM-HEALTH-03) tests are added with the endpoint in a later commit.
