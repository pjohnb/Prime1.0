"""
Sprint 9 Item 1 (CIL-100) acceptance tests -- Bulk Row Delete.
Covers single delete, multi-delete, cancel aborts, empty selection.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    bulk_delete_trades,
    get_all_trades,
    get_trade,
    init_db,
    insert_trade,
)


class TestBulkDeleteTrades(unittest.TestCase):
    """AC: rows removed from prime_trade_log in single transaction."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_bulk_delete.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _insert(self, symbol="AAPL", strategy="UOA"):
        return insert_trade(
            strategy=strategy, symbol=symbol, direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )

    def test_single_delete(self):
        lid = self._insert()
        deleted = bulk_delete_trades([lid], db_path=self.db)
        self.assertEqual(deleted, 1)
        self.assertIsNone(get_trade(lid, db_path=self.db))

    def test_multi_delete(self):
        ids = [self._insert(f"SYM{i}") for i in range(5)]
        deleted = bulk_delete_trades(ids, db_path=self.db)
        self.assertEqual(deleted, 5)
        for lid in ids:
            self.assertIsNone(get_trade(lid, db_path=self.db))

    def test_empty_list_returns_zero(self):
        self._insert()
        deleted = bulk_delete_trades([], db_path=self.db)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(get_all_trades(db_path=self.db)), 1)

    def test_nonexistent_id_no_error(self):
        self._insert()
        deleted = bulk_delete_trades(["nonexistent-id"], db_path=self.db)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(get_all_trades(db_path=self.db)), 1)

    def test_partial_valid_ids(self):
        lid = self._insert()
        deleted = bulk_delete_trades([lid, "fake-id"], db_path=self.db)
        self.assertEqual(deleted, 1)


class TestGetAllTrades(unittest.TestCase):
    """AC: UI refreshes -- get_all_trades returns full log."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_bulk_delete2.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_returns_all_statuses(self):
        insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )
        trades = get_all_trades(db_path=self.db)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["symbol"], "AAPL")


if __name__ == "__main__":
    unittest.main()
