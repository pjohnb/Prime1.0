"""Sprint 26 Item 2: stop_price / target_price / time_stop_minutes DB columns and API."""
import sqlite3
from pathlib import Path
import pytest
from datetime import datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert(symbol, db, stop_price=None, target_price=None, time_stop_minutes=None):
    from prime_data.prime_db import insert_trade
    return insert_trade(
        strategy="UOA", symbol=symbol, direction="LONG",
        mode="PAPER", order_type="MARKET", shares=10,
        entry_time=datetime.now().isoformat(),
        price_at_scan=150.0, entry_price=150.0,
        account="PAPER", trade_source="PAPER",
        stop_price=stop_price,
        target_price=target_price,
        time_stop_minutes=time_stop_minutes,
        db_path=db,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    db = tmp_path / "test_prime.db"
    from prime_data.prime_db import init_db
    init_db(db_path=db)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStopTargetDbColumns:

    def test_columns_exist_after_init(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cur = conn.execute("PRAGMA table_info(prime_trade_log)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "stop_price" in cols
        assert "target_price" in cols
        assert "time_stop_minutes" in cols

    def test_insert_trade_accepts_stop_target(self, tmp_db):
        log_id = _insert("TSLA", tmp_db, stop_price=142.5, target_price=165.0,
                          time_stop_minutes=480)
        assert log_id is not None

        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT stop_price, target_price, time_stop_minutes FROM prime_trade_log WHERE log_id=?",
            (log_id,)
        ).fetchone()
        conn.close()
        assert row[0] == pytest.approx(142.5)
        assert row[1] == pytest.approx(165.0)
        assert row[2] == 480

    def test_insert_trade_without_stop_target(self, tmp_db):
        """stop/target columns should default to NULL when not provided."""
        log_id = _insert("AAPL", tmp_db)

        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT stop_price, target_price, time_stop_minutes FROM prime_trade_log WHERE log_id=?",
            (log_id,)
        ).fetchone()
        conn.close()
        assert row[0] is None
        assert row[1] is None
        assert row[2] is None

    def test_set_trade_stop_target(self, tmp_db):
        from prime_data.prime_db import set_trade_stop_target
        log_id = _insert("MSFT", tmp_db)
        set_trade_stop_target(log_id, stop_price=285.0, target_price=330.0,
                              time_stop_minutes=600, db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT stop_price, target_price, time_stop_minutes FROM prime_trade_log WHERE log_id=?",
            (log_id,)
        ).fetchone()
        conn.close()
        assert row[0] == pytest.approx(285.0)
        assert row[1] == pytest.approx(330.0)
        assert row[2] == 600

    def test_enrich_position_uses_stored_stop(self):
        """enrich_position must use the stored stop_price value."""
        from prime_api.prime_positions import enrich_position
        pos = {
            "log_id": "test-1", "symbol": "NVDA", "direction": "LONG",
            "shares": 10, "entry_price": 500.0, "stop_price": 475.0,
            "status": "OPEN",
        }
        enriched = enrich_position(pos)
        assert enriched.get("stop_price") == pytest.approx(475.0)
