"""
Sprint 12 Item 7 (CIL-PNL-INT) acceptance tests -- P&L Data Integrity.
Covers close_trade_with_fill for LONG/SHORT, audit script flags, fill_price accuracy.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    close_trade,
    close_trade_with_fill,
    get_trade,
    init_db,
    insert_trade,
)
from scripts.prime_pnl_audit import run_audit


class TestCloseTradeWithFill(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_pnl.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _insert(self, symbol="AAPL", direction="LONG", entry_price=185.0):
        return insert_trade(
            strategy="UOA", symbol=symbol, direction=direction,
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-27T10:00:00", price_at_scan=entry_price,
            entry_price=entry_price, db_path=self.db,
        )

    def test_long_positive_pnl(self):
        lid = self._insert(entry_price=185.0)
        result = close_trade_with_fill(lid, fill_price=190.0, fill_qty=100,
                                        close_ts="2026-05-27T14:00:00", db_path=self.db)
        self.assertEqual(result["realized_pnl"], 500.0)
        trade = get_trade(lid, db_path=self.db)
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["exit_price"], 190.0)

    def test_long_negative_pnl(self):
        lid = self._insert(entry_price=185.0)
        result = close_trade_with_fill(lid, fill_price=180.0, fill_qty=100,
                                        close_ts="2026-05-27T14:00:00", db_path=self.db)
        self.assertEqual(result["realized_pnl"], -500.0)

    def test_short_positive_pnl(self):
        lid = self._insert(direction="SHORT", entry_price=185.0)
        result = close_trade_with_fill(lid, fill_price=180.0, fill_qty=100,
                                        close_ts="2026-05-27T14:00:00", db_path=self.db)
        self.assertEqual(result["realized_pnl"], 500.0)

    def test_short_negative_pnl(self):
        lid = self._insert(direction="SHORT", entry_price=185.0)
        result = close_trade_with_fill(lid, fill_price=190.0, fill_qty=100,
                                        close_ts="2026-05-27T14:00:00", db_path=self.db)
        self.assertEqual(result["realized_pnl"], -500.0)

    def test_fill_qty_written(self):
        lid = self._insert()
        close_trade_with_fill(lid, fill_price=190.0, fill_qty=50,
                              close_ts="2026-05-27T14:00:00", db_path=self.db)
        trade = get_trade(lid, db_path=self.db)
        self.assertEqual(trade["shares"], 50)

    def test_nonexistent_trade_returns_none(self):
        result = close_trade_with_fill("fake-id", 190.0, 100, "2026-05-27T14:00:00",
                                        db_path=self.db)
        self.assertIsNone(result)


class TestPnlAudit(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_pnl_audit.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()
        report = Path(PROJECT_ROOT / "logs")
        for f in report.glob("pnl_audit_*.txt"):
            f.unlink(missing_ok=True)

    def test_audit_clean_trades(self):
        lid = insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            entry_price=185.0, db_path=self.db,
        )
        close_trade_with_fill(lid, 190.0, 100, "2026-05-27T14:00:00", db_path=self.db)
        result = run_audit(db_path=self.db)
        self.assertEqual(len(result["flagged"]), 0)

    def test_audit_flags_null_exit_price(self):
        lid = insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )
        close_trade(lid, 0, "2026-05-27T14:00:00", "TEST", 0, 0, 120, db_path=self.db)
        result = run_audit(db_path=self.db)
        self.assertEqual(len(result["flagged"]), 1)
        self.assertIn("exit_price", result["flagged"][0]["issues"][0])

    def test_audit_report_written(self):
        result = run_audit(db_path=self.db)
        self.assertTrue(Path(result["report_path"]).exists())

    def test_audit_no_closed_trades(self):
        result = run_audit(db_path=self.db)
        self.assertEqual(result["total_closed"], 0)
        self.assertEqual(len(result["flagged"]), 0)


if __name__ == "__main__":
    unittest.main()
