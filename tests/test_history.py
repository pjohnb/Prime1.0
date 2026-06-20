"""
Sprint 29 History tab acceptance tests: H-01, H-02, H-03.

H-01: date range filter (from_date, to_date, quick-select, AND logic).
H-02: signal_id linkage (migration, insert_trade param, endpoint response).
H-03: unified Open+Closed view (status param, STATUS badge, OPEN hold/P&L).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_api.prime_api_server import create_app
from prime_data.prime_db import init_db, insert_trade, get_open_trades


def _make_trade(db, strategy="PSA", symbol="SPY", direction="LONG",
                entry_time="2026-06-10T10:00:00", exit_time=None,
                pnl=100.0, exit_price=110.0, signal_id=None):
    """Insert a CLOSED or OPEN trade for testing."""
    log_id = insert_trade(
        strategy=strategy,
        symbol=symbol,
        direction=direction,
        mode="PAPER",
        order_type="MARKET",
        shares=10,
        entry_time=entry_time,
        price_at_scan=100.0,
        entry_price=100.0,
        signal_id=signal_id,
        db_path=db,
    )
    if exit_time:
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.execute(
            """UPDATE prime_trade_log
               SET status='CLOSED', exit_time=?, exit_price=?, pnl_dollars=?, pnl_pct=10.0, hold_minutes=60
               WHERE log_id=?""",
            (exit_time, exit_price, pnl, log_id),
        )
        conn.commit()
        conn.close()
    return log_id


class _HistBase(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_history.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        self._patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._patcher.start()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._patcher.stop()
        if self.db.exists():
            self.db.unlink()


# ---------------------------------------------------------------------------
# H-01: Date Range Filter
# ---------------------------------------------------------------------------

class TestH01DateRangeFilter(_HistBase):
    def setUp(self):
        super().setUp()
        _make_trade(self.db, symbol="AAA", entry_time="2026-06-01T09:00:00",
                    exit_time="2026-06-01T14:00:00")
        _make_trade(self.db, symbol="BBB", entry_time="2026-06-10T09:00:00",
                    exit_time="2026-06-10T14:00:00")
        _make_trade(self.db, symbol="CCC", entry_time="2026-06-18T09:00:00",
                    exit_time="2026-06-18T14:00:00")

    def test_from_date_excludes_earlier_trades(self):
        r = self.client.get("/api/v1/trades/history?from_date=2026-06-05&status=closed")
        data = r.get_json()
        syms = [t["symbol"] for t in data["trades"]]
        self.assertNotIn("AAA", syms)
        self.assertIn("BBB", syms)
        self.assertIn("CCC", syms)

    def test_to_date_excludes_later_trades(self):
        r = self.client.get("/api/v1/trades/history?to_date=2026-06-15&status=closed")
        data = r.get_json()
        syms = [t["symbol"] for t in data["trades"]]
        self.assertIn("AAA", syms)
        self.assertIn("BBB", syms)
        self.assertNotIn("CCC", syms)

    def test_from_and_to_date_combined(self):
        r = self.client.get(
            "/api/v1/trades/history?from_date=2026-06-05&to_date=2026-06-15&status=closed"
        )
        data = r.get_json()
        syms = [t["symbol"] for t in data["trades"]]
        self.assertEqual(syms, ["BBB"])

    def test_strategy_and_date_combined_and_logic(self):
        _make_trade(self.db, symbol="DDD", strategy="UOA",
                    entry_time="2026-06-10T09:00:00",
                    exit_time="2026-06-10T14:00:00")
        r = self.client.get(
            "/api/v1/trades/history?strategy=PSA&from_date=2026-06-05&to_date=2026-06-15&status=closed"
        )
        data = r.get_json()
        syms = [t["symbol"] for t in data["trades"]]
        self.assertIn("BBB", syms)
        self.assertNotIn("DDD", syms)

    def test_no_date_params_returns_all_closed(self):
        r = self.client.get("/api/v1/trades/history?status=closed")
        data = r.get_json()
        self.assertEqual(len(data["trades"]), 3)


# ---------------------------------------------------------------------------
# H-02: Signal Origin Linkage
# ---------------------------------------------------------------------------

class TestH02SignalIdLinkage(_HistBase):
    def test_signal_id_column_exists_after_init(self):
        from prime_data.prime_db import get_table_columns
        cols = get_table_columns("prime_trade_log", db_path=self.db)
        self.assertIn("signal_id", cols)

    def test_insert_trade_with_signal_id(self):
        sig_id = "sig-abc-123"
        log_id = _make_trade(self.db, symbol="XYZ", signal_id=sig_id,
                             exit_time="2026-06-10T15:00:00")
        import sqlite3
        conn = sqlite3.connect(str(self.db))
        row = conn.execute(
            "SELECT signal_id FROM prime_trade_log WHERE log_id=?", (log_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], sig_id)

    def test_history_endpoint_returns_signal_id(self):
        sig_id = "sig-xyz-456"
        _make_trade(self.db, symbol="XYZ", signal_id=sig_id,
                    exit_time="2026-06-10T15:00:00")
        r = self.client.get("/api/v1/trades/history?status=closed")
        data = r.get_json()
        self.assertTrue(any(t.get("signal_id") == sig_id for t in data["trades"]))

    def test_trade_without_signal_id_has_null(self):
        _make_trade(self.db, symbol="XYZ", signal_id=None,
                    exit_time="2026-06-10T15:00:00")
        r = self.client.get("/api/v1/trades/history?status=closed")
        data = r.get_json()
        self.assertTrue(any(t.get("signal_id") is None for t in data["trades"]))


# ---------------------------------------------------------------------------
# H-03: Unified Open + Closed View
# ---------------------------------------------------------------------------

class TestH03UnifiedView(_HistBase):
    def setUp(self):
        super().setUp()
        # One CLOSED trade.
        _make_trade(self.db, symbol="CLOSED_ONE",
                    entry_time="2026-06-10T09:00:00",
                    exit_time="2026-06-10T14:00:00")
        # One OPEN trade (no exit_time).
        _make_trade(self.db, symbol="OPEN_ONE",
                    entry_time="2026-06-18T09:00:00")

    def test_status_all_returns_both_open_and_closed(self):
        r = self.client.get("/api/v1/trades/history?status=all")
        data = r.get_json()
        syms = [t["symbol"] for t in data["trades"]]
        self.assertIn("CLOSED_ONE", syms)
        self.assertIn("OPEN_ONE", syms)

    def test_status_open_returns_only_open(self):
        r = self.client.get("/api/v1/trades/history?status=open")
        data = r.get_json()
        syms = [t["symbol"] for t in data["trades"]]
        self.assertIn("OPEN_ONE", syms)
        self.assertNotIn("CLOSED_ONE", syms)

    def test_status_closed_returns_only_closed(self):
        r = self.client.get("/api/v1/trades/history?status=closed")
        data = r.get_json()
        syms = [t["symbol"] for t in data["trades"]]
        self.assertIn("CLOSED_ONE", syms)
        self.assertNotIn("OPEN_ONE", syms)

    def test_open_rows_appear_before_closed_rows(self):
        r = self.client.get("/api/v1/trades/history?status=all")
        data = r.get_json()
        trades = data["trades"]
        open_idx  = next(i for i, t in enumerate(trades) if t["symbol"] == "OPEN_ONE")
        close_idx = next(i for i, t in enumerate(trades) if t["symbol"] == "CLOSED_ONE")
        self.assertLess(open_idx, close_idx)

    def test_open_rows_have_null_pnl(self):
        r = self.client.get("/api/v1/trades/history?status=open")
        data = r.get_json()
        for t in data["trades"]:
            self.assertIsNone(t.get("pnl_dollars"))
            self.assertIsNone(t.get("pnl_pct"))

    def test_open_rows_have_hold_minutes_computed(self):
        r = self.client.get("/api/v1/trades/history?status=open")
        data = r.get_json()
        for t in data["trades"]:
            self.assertIsNotNone(t.get("hold_minutes"))
            self.assertGreaterEqual(t["hold_minutes"], 0)

    def test_summary_counts_only_closed_trades(self):
        r = self.client.get("/api/v1/trades/history?status=all")
        data = r.get_json()
        # Only 1 closed trade was inserted.
        self.assertEqual(data["summary"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
