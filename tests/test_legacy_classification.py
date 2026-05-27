"""
CIL-102 Legacy Position Classification tests.
Covers: schema accepts LEGACY, seeder is idempotent, reconciler treats LEGACY as LIVE,
get_open_positions() includes LEGACY.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    TradeRecordError,
    get_connection,
    get_open_positions,
    get_open_trades,
    get_table_columns,
    init_db,
    insert_trade,
    update_trade_source,
)
from prime_data.prime_legacy_seeder import LEGACY_POSITIONS, seed_legacy_positions


class TestTradeSourceSchema(unittest.TestCase):
    """AC (a): Schema accepts LEGACY value."""

    def setUp(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test_legacy.db"
        init_db(db_path=self.db_path)

    def test_trade_source_column_exists(self):
        cols = get_table_columns("prime_trade_log", db_path=self.db_path)
        self.assertIn("trade_source", cols)

    def test_insert_legacy_trade(self):
        log_id = insert_trade(
            strategy="Pre-PRIME",
            symbol="GLD",
            direction="LONG",
            mode="LIVE",
            order_type="MARKET",
            shares=10,
            entry_time="2026-05-27T12:00:00",
            price_at_scan=309.67,
            trade_source="LEGACY",
            db_path=self.db_path,
        )
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT trade_source FROM prime_trade_log WHERE log_id=?", (log_id,)
            ).fetchone()
        self.assertEqual(row["trade_source"], "LEGACY")

    def test_insert_paper_trade_default(self):
        log_id = insert_trade(
            strategy="UOA",
            symbol="AAPL",
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=100,
            entry_time="2026-05-27T12:00:00",
            price_at_scan=150.0,
            db_path=self.db_path,
        )
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT trade_source FROM prime_trade_log WHERE log_id=?", (log_id,)
            ).fetchone()
        self.assertEqual(row["trade_source"], "PAPER")

    def test_insert_live_trade(self):
        log_id = insert_trade(
            strategy="UOA",
            symbol="MSFT",
            direction="LONG",
            mode="LIVE",
            order_type="MARKET",
            shares=10,
            entry_time="2026-05-27T12:00:00",
            price_at_scan=420.0,
            trade_source="LIVE",
            db_path=self.db_path,
        )
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT trade_source FROM prime_trade_log WHERE log_id=?", (log_id,)
            ).fetchone()
        self.assertEqual(row["trade_source"], "LIVE")

    def test_invalid_trade_source_rejected(self):
        with self.assertRaises(TradeRecordError):
            insert_trade(
                strategy="UOA",
                symbol="BAD",
                direction="LONG",
                mode="PAPER",
                order_type="MARKET",
                shares=1,
                entry_time="2026-05-27T12:00:00",
                price_at_scan=10.0,
                trade_source="INVALID",
                db_path=self.db_path,
            )


class TestLegacySeeder(unittest.TestCase):
    """AC (b): Seeder is idempotent."""

    def setUp(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test_seeder.db"
        init_db(db_path=self.db_path)

    def test_seed_inserts_all_positions(self):
        result = seed_legacy_positions(db_path=self.db_path)
        self.assertEqual(len(result["inserted"]), 5)
        self.assertEqual(len(result["updated"]), 0)
        self.assertEqual(len(result["skipped"]), 0)

        positions = get_open_positions(db_path=self.db_path)
        symbols = {p["symbol"] for p in positions}
        for pos in LEGACY_POSITIONS:
            self.assertIn(pos["symbol"], symbols)

        for p in positions:
            self.assertEqual(p["trade_source"], "LEGACY")

    def test_seed_is_idempotent(self):
        seed_legacy_positions(db_path=self.db_path)
        result = seed_legacy_positions(db_path=self.db_path)
        self.assertEqual(len(result["inserted"]), 0)
        self.assertEqual(len(result["updated"]), 0)
        self.assertEqual(len(result["skipped"]), 5)

        positions = get_open_positions(db_path=self.db_path)
        self.assertEqual(len(positions), 5)

    def test_seed_updates_paper_to_legacy(self):
        insert_trade(
            strategy="PEAD",
            symbol="TJX",
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=50,
            entry_time="2026-05-20T12:00:00",
            price_at_scan=155.0,
            trade_source="PAPER",
            db_path=self.db_path,
        )
        insert_trade(
            strategy="UOA",
            symbol="MSFT",
            direction="LONG",
            mode="PAPER",
            order_type="MARKET",
            shares=10,
            entry_time="2026-05-20T12:00:00",
            price_at_scan=420.0,
            trade_source="PAPER",
            db_path=self.db_path,
        )

        result = seed_legacy_positions(db_path=self.db_path)
        self.assertIn("TJX", result["updated"])
        self.assertIn("MSFT", result["updated"])
        self.assertIn("GLD", result["inserted"])
        self.assertIn("NIO", result["inserted"])
        self.assertIn("DDOG", result["inserted"])

        positions = get_open_positions(db_path=self.db_path)
        for p in positions:
            self.assertEqual(p["trade_source"], "LEGACY")


class TestReconcilerLegacy(unittest.TestCase):
    """AC (c): Reconciler treats LEGACY as LIVE."""

    def setUp(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test_reconcile.db"
        init_db(db_path=self.db_path)

    def test_legacy_unchanged_when_in_schwab(self):
        from prime_trading.prime_schwab import reconcile_open_trades

        insert_trade(
            strategy="Pre-PRIME",
            symbol="GLD",
            direction="LONG",
            mode="LIVE",
            order_type="MARKET",
            shares=10,
            entry_time="2026-05-27T12:00:00",
            price_at_scan=309.67,
            trade_source="LEGACY",
            db_path=self.db_path,
        )

        class MockClient:
            def get_positions(self):
                return [{"instrument": {"assetType": "EQUITY", "symbol": "GLD"}}]
            def get_pending_orders(self):
                return []

        result = reconcile_open_trades(MockClient(), db_path=self.db_path)
        self.assertEqual(len(result["unchanged"]), 1)
        self.assertEqual(len(result["closed"]), 0)

    def test_legacy_closed_when_not_in_schwab(self):
        from prime_trading.prime_schwab import reconcile_open_trades

        insert_trade(
            strategy="Pre-PRIME",
            symbol="GONE",
            direction="LONG",
            mode="LIVE",
            order_type="MARKET",
            shares=5,
            entry_time="2026-05-27T12:00:00",
            price_at_scan=100.0,
            trade_source="LEGACY",
            db_path=self.db_path,
        )

        class MockClient:
            def get_positions(self):
                return []
            def get_pending_orders(self):
                return []

        result = reconcile_open_trades(MockClient(), db_path=self.db_path)
        self.assertEqual(len(result["closed"]), 1)


class TestGetOpenPositions(unittest.TestCase):
    """AC (d): get_open_positions() includes LEGACY."""

    def setUp(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test_positions.db"
        init_db(db_path=self.db_path)

    def test_returns_all_sources(self):
        insert_trade(
            strategy="Pre-PRIME", symbol="GLD", direction="LONG",
            mode="LIVE", order_type="MARKET", shares=10,
            entry_time="2026-05-27T12:00:00", price_at_scan=309.67,
            trade_source="LEGACY", db_path=self.db_path,
        )
        insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-27T12:00:00", price_at_scan=150.0,
            trade_source="PAPER", db_path=self.db_path,
        )
        insert_trade(
            strategy="PEAD", symbol="TSLA", direction="LONG",
            mode="LIVE", order_type="MARKET", shares=50,
            entry_time="2026-05-27T12:00:00", price_at_scan=200.0,
            trade_source="LIVE", db_path=self.db_path,
        )

        positions = get_open_positions(db_path=self.db_path)
        sources = {p["trade_source"] for p in positions}
        self.assertEqual(sources, {"LEGACY", "PAPER", "LIVE"})
        self.assertEqual(len(positions), 3)

    def test_get_open_trades_also_includes_legacy(self):
        insert_trade(
            strategy="Pre-PRIME", symbol="GLD", direction="LONG",
            mode="LIVE", order_type="MARKET", shares=10,
            entry_time="2026-05-27T12:00:00", price_at_scan=309.67,
            trade_source="LEGACY", db_path=self.db_path,
        )
        trades = get_open_trades(db_path=self.db_path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["trade_source"], "LEGACY")


if __name__ == "__main__":
    unittest.main()
