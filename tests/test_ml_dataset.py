"""Sprint 26 Item 5: ML dataset join, row count, CSV export."""
import sqlite3
from pathlib import Path
import pytest
from datetime import datetime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def populated_db(tmp_path):
    db = tmp_path / "ml_test.db"
    from prime_data.prime_db import init_db
    init_db(db_path=db)

    conn = sqlite3.connect(db)

    # Insert a signal using actual schema columns
    conn.execute("""
        INSERT INTO prime_signals
          (symbol, strategy, tier, scan_ts, trigger_source, status, direction,
           entry_price, score)
        VALUES ('AAPL', 'PSA', 'STRONG', '2026-06-01 09:00:00', 'UOA_CALL',
                'ACTIVE', 'LONG', 150.0, 0.8)
    """)

    # Insert a matching CLOSED trade (entry within 2 days of signal)
    conn.execute("""
        INSERT INTO prime_trade_log
          (log_id, symbol, direction, shares, entry_price, exit_price, strategy,
           account, trade_source, status, entry_time, exit_time,
           pnl_dollars, pnl_pct, mode, order_type, price_at_scan)
        VALUES ('test-1', 'AAPL', 'LONG', 10, 150.0, 157.5, 'PSA',
                'PAPER', 'PAPER', 'CLOSED', '2026-06-01 09:30:00', '2026-06-02 10:00:00',
                75.0, 5.0, 'PAPER', 'MARKET', 150.0)
    """)

    # Insert an unmatched signal (no matching closed trade)
    conn.execute("""
        INSERT INTO prime_signals
          (symbol, strategy, tier, scan_ts, trigger_source, status, direction,
           entry_price, score)
        VALUES ('TSLA', 'UOA', 'WATCH', '2026-06-01 09:00:00', 'UOA_CALL',
                'ACTIVE', 'LONG', 200.0, 0.6)
    """)

    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMlDataset:

    def test_get_row_count_returns_int(self, populated_db):
        from prime_data.prime_ml_dataset import get_row_count
        count = get_row_count(db_path=populated_db)
        assert isinstance(count, int)
        assert count >= 0

    def test_get_row_count_matches_actual_rows(self, populated_db):
        from prime_data.prime_ml_dataset import get_row_count, get_training_rows
        count = get_row_count(db_path=populated_db)
        rows = get_training_rows(db_path=populated_db)
        assert count == len(rows)

    def test_get_training_rows_returns_list(self, populated_db):
        from prime_data.prime_ml_dataset import get_training_rows
        rows = get_training_rows(db_path=populated_db)
        assert isinstance(rows, list)

    def test_training_rows_contain_expected_columns(self, populated_db):
        from prime_data.prime_ml_dataset import get_training_rows
        rows = get_training_rows(db_path=populated_db)
        if rows:
            row = rows[0]
            assert "symbol" in row
            assert "strategy" in row

    def test_export_csv_creates_file(self, populated_db, tmp_path):
        from prime_data.prime_ml_dataset import get_training_rows, export_csv
        rows = get_training_rows(db_path=populated_db)
        output = tmp_path / "test_export.csv"
        export_csv(rows, output_path=output, db_path=populated_db)
        assert output.exists()

    def test_export_csv_has_header_line(self, populated_db, tmp_path):
        from prime_data.prime_ml_dataset import get_training_rows, export_csv
        rows = get_training_rows(db_path=populated_db)
        output = tmp_path / "test_export.csv"
        export_csv(rows, output_path=output, db_path=populated_db)
        content = output.read_text()
        lines = [l for l in content.splitlines() if l.strip()]
        assert len(lines) >= 1, "CSV must have at least a header line"
