"""
Sprint 9 Item 2 (CIL-099) acceptance tests -- Schwab Position Importer.
Covers full match, ghost close, new import, qty mismatch flag, API failure.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    get_open_positions,
    get_trade,
    init_db,
    insert_trade,
)
from prime_trading.prime_schwab_importer import (
    get_schwab_positions,
    reconcile_positions,
)


def _schwab_position(symbol, qty, avg_cost):
    return {
        "instrument": {"assetType": "EQUITY", "symbol": symbol},
        "longQuantity": qty if qty > 0 else 0,
        "shortQuantity": abs(qty) if qty < 0 else 0,
        "averagePrice": avg_cost,
    }


class TestGetSchwabPositions(unittest.TestCase):

    def test_normalizes_equity_positions(self):
        client = MagicMock()
        client.get_positions.return_value = [
            _schwab_position("AAPL", 100, 185.50),
            _schwab_position("MSFT", 50, 415.00),
        ]
        result = get_schwab_positions(client)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["symbol"], "AAPL")
        self.assertEqual(result[0]["qty"], 100)

    def test_skips_non_equity(self):
        client = MagicMock()
        client.get_positions.return_value = [
            {"instrument": {"assetType": "OPTION", "symbol": "AAPL_C"}, "longQuantity": 5},
            _schwab_position("MSFT", 50, 415.00),
        ]
        result = get_schwab_positions(client)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "MSFT")

    def test_empty_positions(self):
        client = MagicMock()
        client.get_positions.return_value = []
        result = get_schwab_positions(client)
        self.assertEqual(len(result), 0)


class TestReconcilePositions(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_schwab_import.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _insert(self, symbol, shares=10, trade_source="PAPER"):
        return insert_trade(
            strategy="UOA", symbol=symbol, direction="LONG",
            mode="PAPER", order_type="MARKET", shares=shares,
            entry_time="2026-05-27T10:00:00", price_at_scan=100.0,
            trade_source=trade_source, db_path=self.db,
        )

    def test_full_match_no_changes(self):
        self._insert("AAPL", shares=100)
        schwab = [{"symbol": "AAPL", "qty": 100, "avg_cost": 185.0}]
        result = reconcile_positions(schwab, db_path=self.db)
        self.assertEqual(len(result["unchanged"]), 1)
        self.assertEqual(len(result["auto_closed"]), 0)
        self.assertEqual(len(result["auto_imported"]), 0)

    def test_ghost_trade_auto_closed(self):
        lid = self._insert("AAPL")
        schwab = []  # Schwab has no AAPL
        result = reconcile_positions(schwab, db_path=self.db)
        self.assertEqual(len(result["auto_closed"]), 1)
        trade = get_trade(lid, db_path=self.db)
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["exit_reason"], "SCHWAB_RECONCILE")

    def test_new_position_auto_imported(self):
        schwab = [{"symbol": "NVDA", "qty": 25, "avg_cost": 800.0}]
        result = reconcile_positions(schwab, db_path=self.db)
        self.assertEqual(len(result["auto_imported"]), 1)
        self.assertEqual(result["auto_imported"][0]["symbol"], "NVDA")
        open_trades = get_open_positions(db_path=self.db)
        nvda = [t for t in open_trades if t["symbol"] == "NVDA"]
        self.assertEqual(len(nvda), 1)
        self.assertEqual(nvda[0]["trade_source"], "SCHWAB_IMPORT")
        self.assertEqual(nvda[0]["shares"], 25)

    def test_qty_mismatch_flagged(self):
        self._insert("AAPL", shares=50)
        schwab = [{"symbol": "AAPL", "qty": 100, "avg_cost": 185.0}]
        result = reconcile_positions(schwab, db_path=self.db)
        self.assertEqual(len(result["flagged"]), 1)
        self.assertEqual(result["flagged"][0]["reason"], "QTY_MISMATCH")
        self.assertEqual(result["flagged"][0]["schwab_qty"], 100)
        self.assertEqual(result["flagged"][0]["prime_qty"], 50)

    def test_api_failure_graceful(self):
        client = MagicMock()
        client.get_positions.side_effect = RuntimeError("API down")
        with self.assertRaises(RuntimeError):
            get_schwab_positions(client)

    def test_multiple_open_records_same_symbol(self):
        self._insert("AAPL", shares=30)
        insert_trade(
            strategy="PEAD", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=70,
            entry_time="2026-05-26T10:00:00", price_at_scan=100.0,
            db_path=self.db,
        )
        schwab = [{"symbol": "AAPL", "qty": 100, "avg_cost": 185.0}]
        result = reconcile_positions(schwab, db_path=self.db)
        self.assertEqual(len(result["unchanged"]), 1)

    def test_ghost_close_multiple_records(self):
        self._insert("AAPL", shares=30)
        insert_trade(
            strategy="PEAD", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=70,
            entry_time="2026-05-26T10:00:00", price_at_scan=100.0,
            db_path=self.db,
        )
        schwab = []
        result = reconcile_positions(schwab, db_path=self.db)
        self.assertEqual(len(result["auto_closed"]), 2)

    def test_mixed_scenario(self):
        self._insert("AAPL", shares=100)  # matches
        self._insert("TSLA", shares=10)   # ghost -- no Schwab pos
        schwab = [
            {"symbol": "AAPL", "qty": 100, "avg_cost": 185.0},
            {"symbol": "NVDA", "qty": 25, "avg_cost": 800.0},   # new import
        ]
        result = reconcile_positions(schwab, db_path=self.db)
        self.assertEqual(len(result["unchanged"]), 1)
        self.assertEqual(len(result["auto_closed"]), 1)
        self.assertEqual(len(result["auto_imported"]), 1)

    def test_schwab_import_trade_source(self):
        schwab = [{"symbol": "GOOG", "qty": 10, "avg_cost": 175.0}]
        result = reconcile_positions(schwab, db_path=self.db)
        self.assertEqual(len(result["auto_imported"]), 1)
        open_trades = get_open_positions(db_path=self.db)
        self.assertEqual(open_trades[0]["trade_source"], "SCHWAB_IMPORT")

    def test_empty_schwab_empty_prime(self):
        result = reconcile_positions([], db_path=self.db)
        self.assertEqual(len(result["unchanged"]), 0)
        self.assertEqual(len(result["auto_closed"]), 0)
        self.assertEqual(len(result["auto_imported"]), 0)


if __name__ == "__main__":
    unittest.main()
