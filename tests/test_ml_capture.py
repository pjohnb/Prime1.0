"""Sprint 31 / Thread 1: ML data pipeline.

Covers CIL-041-031 (prime_ml_dataset table + capture_ml_event + PrimeMLEvent
schema), CIL-042 (scan-pipeline wiring + market-regime detection), and
CIL-043 (unified outcome capture from close_trade).
"""
import sqlite3
from dataclasses import fields
from datetime import datetime
from unittest import mock

import pytest

from prime_data.prime_db import (
    init_db,
    insert_trade,
    close_trade,
    update_ml_outcome,
)
import prime_ml.prime_ml_capture_v2 as cap
from prime_ml.prime_ml_capture_v2 import (
    PrimeMLEvent,
    ML_COLUMNS,
    capture_ml_event,
    build_ml_event,
    _get_market_regime,
)


_TODAY = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture()
def db(tmp_path):
    """A fresh initialised prime_trades.db with the regime cache pre-seeded
    so capture never reaches for the network during a test."""
    path = tmp_path / "ml_capture.db"
    init_db(db_path=path)
    cap._REGIME_CACHE.clear()
    cap._REGIME_CACHE[_TODAY] = "NEUTRAL"
    yield path
    cap._REGIME_CACHE.clear()


def _uoa_signal(**over):
    sig = {
        "signal_id": "sig_uoa_1",
        "scanner": "uoa",
        "symbol": "AAPL",
        "direction": "LONG",
        "score": 7.5,
        "tier": "STRONG",
        "sizzle_index": 3.2,
        "price_at_scan": 150.0,
    }
    sig.update(over)
    return sig


def _table_columns(path):
    conn = sqlite3.connect(str(path))
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(prime_ml_dataset)")]
    finally:
        conn.close()


def _row(path, signal_id):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT * FROM prime_ml_dataset WHERE signal_id=?", (signal_id,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _count(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT COUNT(*) FROM prime_ml_dataset").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CIL-041-031: schema + table + capture
# ---------------------------------------------------------------------------

class TestSchemaAndTable:

    def test_table_created_by_init_db(self, db):
        conn = sqlite3.connect(str(db))
        try:
            got = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='prime_ml_dataset'"
            ).fetchone()
        finally:
            conn.close()
        assert got is not None

    def test_init_db_idempotent(self, db):
        # Re-running init_db on an existing db must not error or drop data.
        capture_ml_event(_uoa_signal(), db_path=db)
        init_db(db_path=db)
        assert _count(db) == 1

    def test_table_columns_match_dataclass(self, db):
        assert _table_columns(db) == ML_COLUMNS

    def test_dataclass_field_order_matches_columns(self):
        assert [f.name for f in fields(PrimeMLEvent)] == ML_COLUMNS

    def test_primary_key_is_signal_id(self, db):
        conn = sqlite3.connect(str(db))
        try:
            pk = [r[1] for r in conn.execute("PRAGMA table_info(prime_ml_dataset)") if r[5]]
        finally:
            conn.close()
        assert pk == ["signal_id"]


class TestCapture:

    def test_capture_writes_row(self, db):
        sid = capture_ml_event(_uoa_signal(), db_path=db)
        assert sid == "sig_uoa_1"
        row = _row(db, "sig_uoa_1")
        assert row["symbol"] == "AAPL"
        assert row["direction"] == "LONG"
        assert row["score"] == 7.5
        assert row["tier"] == "STRONG"
        assert row["scanner"] == "uoa"

    def test_capture_scanner_specific_fields(self, db):
        # UOA populates sizzle_index; MTS-only fields stay NULL.
        capture_ml_event(_uoa_signal(), db_path=db)
        row = _row(db, "sig_uoa_1")
        assert row["sizzle_index"] == 3.2
        assert row["rsi"] is None
        assert row["eps_surprise"] is None
        assert row["borrow_rate"] is None

    def test_capture_pead_fields(self, db):
        sig = {
            "signal_id": "sig_pead_1", "scanner": "pead", "symbol": "NVDA",
            "direction": "LONG", "score": 9.0, "eps_surprise": 12.5,
            "guidance_flag": "RAISED", "price_at_scan": 800.0,
        }
        capture_ml_event(sig, db_path=db)
        row = _row(db, "sig_pead_1")
        assert row["eps_surprise"] == 12.5
        assert row["guidance_flag"] == "RAISED"
        assert row["sizzle_index"] is None

    def test_outcome_fields_default_null(self, db):
        capture_ml_event(_uoa_signal(), db_path=db)
        row = _row(db, "sig_uoa_1")
        assert row["exit_price"] is None
        assert row["pnl_dollars"] is None
        assert row["outcome_captured_at"] is None

    def test_duplicate_signal_id_replaces(self, db):
        capture_ml_event(_uoa_signal(score=7.5), db_path=db)
        capture_ml_event(_uoa_signal(score=9.9), db_path=db)
        assert _count(db) == 1                     # no duplicate row
        assert _row(db, "sig_uoa_1")["score"] == 9.9   # replaced in place

    def test_missing_signal_id_gets_deterministic_id(self, db):
        sig = _uoa_signal()
        del sig["signal_id"]
        sig["scan_ts"] = "2026-06-20T09:30:00"
        sid = capture_ml_event(sig, db_path=db)
        assert sid and sid.startswith("sig_")
        # Same scan re-captured -> same derived id -> still one row.
        capture_ml_event(dict(sig), db_path=db)
        assert _count(db) == 1

    def test_two_missing_id_signals_distinct_rows(self, db):
        a = _uoa_signal(); del a["signal_id"]; a["symbol"] = "AAA"
        b = _uoa_signal(); del b["signal_id"]; b["symbol"] = "BBB"
        a["scan_ts"] = b["scan_ts"] = "2026-06-20T09:30:00"
        capture_ml_event(a, db_path=db)
        capture_ml_event(b, db_path=db)
        assert _count(db) == 2

    def test_capture_never_raises(self, db):
        # A non-dict signal would blow up inside; capture must swallow it.
        assert capture_ml_event(None, db_path=db) is None
        assert _count(db) == 0

    def test_capture_db_error_returns_none(self, db):
        with mock.patch.object(cap, "get_connection", side_effect=RuntimeError("boom")):
            assert capture_ml_event(_uoa_signal(), db_path=db) is None


class TestOutcomeUpdate:

    def test_update_outcome_sets_fields(self, db):
        capture_ml_event(_uoa_signal(), db_path=db)
        ok = update_ml_outcome("sig_uoa_1", 160.0, 100.0, 6.67, 120, "TARGET", db_path=db)
        assert ok is True
        row = _row(db, "sig_uoa_1")
        assert row["exit_price"] == 160.0
        assert row["pnl_dollars"] == 100.0
        assert row["pnl_pct"] == 6.67
        assert row["hold_minutes"] == 120
        assert row["exit_reason"] == "TARGET"
        assert row["outcome_captured_at"] is not None

    def test_update_outcome_noop_when_missing(self, db):
        assert update_ml_outcome("nope", 1, 1, 1, 1, "X", db_path=db) is False


# ---------------------------------------------------------------------------
# CIL-042: market regime + scan-pipeline wiring
# ---------------------------------------------------------------------------

class _FakeResp:
    status_code = 200

    def __init__(self, closes):
        self._closes = closes

    def json(self):
        return {"candles": [{"close": c} for c in self._closes]}


class _FakeClient:
    def __init__(self, closes):
        self._closes = closes
        self.calls = 0

    def get_price_history_every_day(self, symbol, start_datetime=None, end_datetime=None):
        self.calls += 1
        return _FakeResp(self._closes)


class TestMarketRegime:

    def setup_method(self):
        cap._REGIME_CACHE.clear()

    def teardown_method(self):
        cap._REGIME_CACHE.clear()

    def test_bull(self):
        closes = [100.0] * 50 + [130.0]      # last well above SMA50
        assert _get_market_regime(_FakeClient(closes)) == "BULL"

    def test_bear(self):
        closes = [100.0] * 50 + [90.0]       # last below SMA50 * 0.97
        assert _get_market_regime(_FakeClient(closes)) == "BEAR"

    def test_neutral(self):
        closes = [100.0] * 49 + [100.0, 99.0]  # just under SMA, within 3%
        assert _get_market_regime(_FakeClient(closes)) == "NEUTRAL"

    def test_unknown_when_no_data(self):
        assert _get_market_regime(_FakeClient([])) == "UNKNOWN"

    def test_unknown_when_client_raises(self):
        client = mock.Mock()
        client.get_price_history_every_day.side_effect = RuntimeError("auth")
        assert _get_market_regime(client) == "UNKNOWN"

    def test_cached_one_call_per_day(self):
        client = _FakeClient([100.0] * 50 + [130.0])
        _get_market_regime(client)
        _get_market_regime(client)
        _get_market_regime(client)
        assert client.calls == 1            # cached after first call


class TestScanPipelineWiring:

    def test_post_scan_notify_captures_each_approved(self, db, monkeypatch):
        cap._REGIME_CACHE.clear()
        cap._REGIME_CACHE[_TODAY] = "BULL"
        from prime_ops import prime_scheduler

        signals = [
            _uoa_signal(signal_id="sig_a", symbol="AAA", score=8.0),
            _uoa_signal(signal_id="sig_b", symbol="BBB", score=6.0),
        ]
        # Neutralise the unrelated notification side-effects; let capture run real.
        monkeypatch.setattr("prime_data.prime_db.get_open_positions", lambda *a, **k: [])
        monkeypatch.setattr("prime_notifications.prime_digest.assemble_digest",
                            lambda *a, **k: ({}, ""))
        monkeypatch.setattr("prime_notifications.prime_notifier.send_digest", lambda *a, **k: None)
        monkeypatch.setattr("prime_notifications.prime_push_signal.push_signal_alerts",
                            lambda *a, **k: None)
        monkeypatch.setattr("prime_ai.prime_signal_ranker.select_for_execution",
                            lambda approved, **k: approved)
        # Route capture writes to the test db.
        real_capture = cap.capture_ml_event
        monkeypatch.setattr("prime_ml.prime_ml_capture_v2.capture_ml_event",
                            lambda sig, db_path=None: real_capture(sig, db_path=db))

        prime_scheduler.post_scan_notify("uoa", {"signals": signals})

        assert _count(db) == 2
        assert _row(db, "sig_a")["market_regime"] == "BULL"
        assert _row(db, "sig_a")["scanner"] == "uoa"

    def test_capture_failure_does_not_break_scan(self, db, monkeypatch):
        from prime_ops import prime_scheduler
        monkeypatch.setattr("prime_data.prime_db.get_open_positions", lambda *a, **k: [])
        monkeypatch.setattr("prime_notifications.prime_digest.assemble_digest",
                            lambda *a, **k: ({}, ""))
        monkeypatch.setattr("prime_notifications.prime_notifier.send_digest", lambda *a, **k: None)
        monkeypatch.setattr("prime_notifications.prime_push_signal.push_signal_alerts",
                            lambda *a, **k: None)
        monkeypatch.setattr("prime_ai.prime_signal_ranker.select_for_execution",
                            lambda approved, **k: approved)
        monkeypatch.setattr("prime_ml.prime_ml_capture_v2.capture_ml_event",
                            mock.Mock(side_effect=RuntimeError("capture boom")))
        # Must not raise despite capture blowing up.
        prime_scheduler.post_scan_notify("uoa", {"signals": [_uoa_signal()]})


# ---------------------------------------------------------------------------
# CIL-043: unified outcome capture from close_trade
# ---------------------------------------------------------------------------

class TestCloseTradeOutcome:

    def _open_trade(self, db, signal_id):
        return insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG", mode="PAPER",
            order_type="MARKET", shares=100, entry_time="2026-06-20T09:30:00",
            price_at_scan=150.0, score=7.5, signal_id=signal_id, db_path=db,
        )

    def test_close_trade_updates_ml_outcome(self, db):
        capture_ml_event(_uoa_signal(signal_id="sig_link"), db_path=db)
        log_id = self._open_trade(db, "sig_link")
        close_trade(log_id, 160.0, "2026-06-20T11:30:00", "TARGET",
                    1000.0, 6.67, 120, db_path=db)
        row = _row(db, "sig_link")
        assert row["exit_price"] == 160.0
        assert row["pnl_dollars"] == 1000.0
        assert row["exit_reason"] == "TARGET"
        assert row["outcome_captured_at"] is not None

    def test_close_trade_null_signal_id_no_error(self, db):
        # SCHWAB_IMPORT-style trade with no signal_id: must not error.
        log_id = self._open_trade(db, None)
        close_trade(log_id, 160.0, "2026-06-20T11:30:00", "SCHWAB_RECONCILE",
                    0.0, 0.0, 60, db_path=db)
        conn = sqlite3.connect(str(db))
        try:
            status = conn.execute(
                "SELECT status FROM prime_trade_log WHERE log_id=?", (log_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert status == "CLOSED"
        assert _count(db) == 0           # nothing captured, nothing updated

    def test_close_trade_outcome_failure_never_blocks(self, db, monkeypatch):
        capture_ml_event(_uoa_signal(signal_id="sig_link"), db_path=db)
        log_id = self._open_trade(db, "sig_link")
        monkeypatch.setattr("prime_data.prime_db.update_ml_outcome",
                            mock.Mock(side_effect=RuntimeError("outcome boom")))
        # Trade close must still succeed.
        close_trade(log_id, 160.0, "2026-06-20T11:30:00", "TARGET",
                    1000.0, 6.67, 120, db_path=db)
        conn = sqlite3.connect(str(db))
        try:
            status = conn.execute(
                "SELECT status FROM prime_trade_log WHERE log_id=?", (log_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert status == "CLOSED"

    def test_close_trade_no_signal_row_is_noop(self, db):
        # Trade has a signal_id but no capture row exists -> no error.
        log_id = self._open_trade(db, "sig_orphan")
        close_trade(log_id, 160.0, "2026-06-20T11:30:00", "STOP",
                    -500.0, -3.0, 45, db_path=db)
        assert _row(db, "sig_orphan") is None
