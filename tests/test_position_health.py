"""Sprint 32 Thread 3 -- PM-HEALTH-01 helpers + CIL-099 ML outcome capture.

Covers the two signal-to-position linkage helpers
(get_latest_signal_for_symbol, get_open_positions_with_signal_context) and the
CIL-099 fix that extends ML outcome capture to close_trade_with_fill() and
close_trade_reconcile(), which previously bypassed close_trade() and missed
update_ml_outcome().
"""
import sqlite3
from datetime import datetime

import pytest

from prime_data.prime_db import (
    init_db,
    get_connection,
    insert_trade,
    close_trade_with_fill,
    close_trade_reconcile,
    get_latest_signal_for_symbol,
    get_open_positions_with_signal_context,
)
from prime_analytics.prime_signals_db import insert_signal
import prime_ml.prime_ml_capture_v2 as cap
from prime_ml.prime_ml_capture_v2 import capture_ml_event

_TODAY = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture()
def db(tmp_path):
    """Fresh DB (trade log + signals + ml_dataset all created by init_db) with
    the regime cache pre-seeded so capture never reaches for the network."""
    path = tmp_path / "position_health.db"
    init_db(db_path=path)
    cap._REGIME_CACHE.clear()
    cap._REGIME_CACHE[_TODAY] = "NEUTRAL"
    yield path
    cap._REGIME_CACHE.clear()


def _open(db, symbol, *, strategy="UOA", signal_id=None, source="PAPER",
          entry_time="2026-06-20T09:30:00"):
    return insert_trade(
        strategy=strategy, symbol=symbol, direction="LONG", mode="PAPER",
        order_type="MARKET", shares=100, entry_time=entry_time,
        price_at_scan=150.0, entry_price=150.0, trade_source=source,
        signal_id=signal_id, db_path=db,
    )


def _ml_row(db, signal_id):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT * FROM prime_ml_dataset WHERE signal_id=?", (signal_id,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _set_dk_status(db, signal_id, dk_status):
    with get_connection(db) as conn:
        conn.execute(
            "UPDATE prime_signals SET dk_status=? WHERE signal_id=?",
            (dk_status, signal_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Helper 1: get_latest_signal_for_symbol
# ---------------------------------------------------------------------------

class TestGetLatestSignalForSymbol:

    def test_returns_most_recent_approved(self, db):
        insert_signal("AAPL", "UOA", "2026-06-18T09:30:00", score=70.0,
                      tier="STRONG", status="APPROVED", db_path=db)
        insert_signal("AAPL", "UOA", "2026-06-20T09:30:00", score=88.0,
                      tier="STRONG", status="APPROVED", db_path=db)
        sig = get_latest_signal_for_symbol("AAPL", "UOA", db_path=db)
        assert sig is not None
        assert sig["scan_ts"] == "2026-06-20T09:30:00"
        assert sig["score"] == 88.0

    def test_returns_none_when_no_signal(self, db):
        assert get_latest_signal_for_symbol("ZZZ", "UOA", db_path=db) is None

    def test_ignores_non_approved(self, db):
        insert_signal("TSLA", "UOA", "2026-06-20T09:30:00", status="NEW", db_path=db)
        assert get_latest_signal_for_symbol("TSLA", "UOA", db_path=db) is None

    def test_filters_by_scanner(self, db):
        insert_signal("NVDA", "PEAD", "2026-06-20T09:30:00", status="APPROVED", db_path=db)
        # Same symbol, different scanner -> no match for UOA.
        assert get_latest_signal_for_symbol("NVDA", "UOA", db_path=db) is None
        assert get_latest_signal_for_symbol("NVDA", "PEAD", db_path=db) is not None

    def test_symbol_match_is_case_insensitive(self, db):
        insert_signal("AMD", "UOA", "2026-06-20T09:30:00", status="APPROVED", db_path=db)
        assert get_latest_signal_for_symbol("amd", "UOA", db_path=db) is not None


# ---------------------------------------------------------------------------
# Helper 2: get_open_positions_with_signal_context
# ---------------------------------------------------------------------------

class TestOpenPositionsWithSignalContext:

    def test_returns_all_open_positions(self, db):
        _open(db, "AAA")
        _open(db, "BBB")
        rows = get_open_positions_with_signal_context(db_path=db)
        assert {r["symbol"] for r in rows} == {"AAA", "BBB"}

    def test_excludes_closed_positions(self, db):
        log_id = _open(db, "CCC")
        close_trade_reconcile(log_id, db_path=db)
        symbols = {r["symbol"] for r in get_open_positions_with_signal_context(db_path=db)}
        assert "CCC" not in symbols

    def test_signal_context_joined(self, db):
        sid = insert_signal("MSFT", "UOA", "2026-06-20T09:30:00", score=82.0,
                            tier="STRONG", status="APPROVED", db_path=db)
        _set_dk_status(db, sid, "CONFIRMING")
        _open(db, "MSFT", strategy="UOA", signal_id=sid)
        rows = get_open_positions_with_signal_context(db_path=db)
        row = next(r for r in rows if r["symbol"] == "MSFT")
        assert row["scanner"] == "UOA"
        assert row["dk_status"] == "CONFIRMING"
        assert row["score"] == 82.0
        assert row["tier"] == "STRONG"
        assert row["signal_id"] == sid

    def test_schwab_import_has_null_context(self, db):
        # No signal_id -> LEFT JOIN yields NULL scanner/dk_status/score/tier.
        _open(db, "GLD", source="SCHWAB_IMPORT", signal_id=None)
        rows = get_open_positions_with_signal_context(db_path=db)
        row = next(r for r in rows if r["symbol"] == "GLD")
        assert row["signal_id"] is None
        assert row["scanner"] is None
        assert row["dk_status"] is None
        assert row["score"] is None
        assert row["tier"] is None


# ---------------------------------------------------------------------------
# CIL-099: ML outcome capture in close_trade_with_fill / close_trade_reconcile
# ---------------------------------------------------------------------------

class TestCloseTradeWithFillMLOutcome:

    def test_updates_ml_outcome_when_signal_present(self, db):
        capture_ml_event(
            {"signal_id": "sig_fill", "scanner": "uoa", "symbol": "AAPL",
             "direction": "LONG", "score": 7.5, "price_at_scan": 150.0},
            db_path=db,
        )
        log_id = _open(db, "AAPL", signal_id="sig_fill",
                       entry_time="2026-06-20T09:30:00")
        close_trade_with_fill(log_id, fill_price=160.0, fill_qty=100,
                              close_ts="2026-06-20T11:30:00", exit_reason="FILL",
                              db_path=db)
        row = _ml_row(db, "sig_fill")
        assert row["exit_price"] == 160.0
        assert row["pnl_dollars"] == 1000.0   # (160-150)*100
        assert row["hold_minutes"] == 120     # 09:30 -> 11:30
        assert row["exit_reason"] == "FILL"
        assert row["outcome_captured_at"] is not None

    def test_null_signal_id_no_error_no_capture(self, db):
        log_id = _open(db, "GLD", source="SCHWAB_IMPORT", signal_id=None)
        result = close_trade_with_fill(log_id, fill_price=160.0, fill_qty=100,
                                       close_ts="2026-06-20T11:30:00", db_path=db)
        assert result is not None
        conn = sqlite3.connect(str(db))
        try:
            status = conn.execute(
                "SELECT status FROM prime_trade_log WHERE log_id=?", (log_id,)
            ).fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM prime_ml_dataset").fetchone()[0]
        finally:
            conn.close()
        assert status == "CLOSED"
        assert count == 0


class TestCloseTradeReconcileMLOutcome:

    def test_updates_ml_outcome_when_signal_present(self, db):
        capture_ml_event(
            {"signal_id": "sig_rec", "scanner": "uoa", "symbol": "AAPL",
             "direction": "LONG", "score": 7.5, "price_at_scan": 150.0},
            db_path=db,
        )
        log_id = _open(db, "AAPL", signal_id="sig_rec")
        close_trade_reconcile(log_id, db_path=db)
        row = _ml_row(db, "sig_rec")
        # Reconcile computes no P&L -> metrics stay NULL, but the outcome is
        # stamped with the SCHWAB_RECONCILE reason so ML training can filter it.
        assert row["exit_reason"] == "SCHWAB_RECONCILE"
        assert row["outcome_captured_at"] is not None
        assert row["exit_price"] is None
        assert row["pnl_dollars"] is None

    def test_null_signal_id_no_error(self, db):
        log_id = _open(db, "GLD", source="SCHWAB_IMPORT", signal_id=None)
        close_trade_reconcile(log_id, db_path=db)
        conn = sqlite3.connect(str(db))
        try:
            status = conn.execute(
                "SELECT status FROM prime_trade_log WHERE log_id=?", (log_id,)
            ).fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM prime_ml_dataset").fetchone()[0]
        finally:
            conn.close()
        assert status == "CLOSED"
        assert count == 0
