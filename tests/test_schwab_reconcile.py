"""
Item 2 (Schwab Reconciler) acceptance tests.
Uses a mock broker client to test reconciliation logic without live Schwab API.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    get_ops_events,
    get_trade,
    init_db,
    insert_trade,
)
from prime_trading.prime_schwab import reconcile_open_trades


class MockBrokerClient:
    """Mock Schwab client for testing reconciliation logic."""

    def __init__(
        self,
        positions: List[Dict[str, Any]] = None,
        pending_orders: List[Dict[str, Any]] = None,
        fail_positions: bool = False,
    ):
        self._positions = positions or []
        self._pending_orders = pending_orders or []
        self._fail_positions = fail_positions

    def get_positions(self) -> List[Dict[str, Any]]:
        if self._fail_positions:
            raise ConnectionError("Schwab API unavailable")
        return self._positions

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        return self._pending_orders


def _schwab_position(symbol: str) -> dict:
    return {
        "instrument": {"assetType": "EQUITY", "symbol": symbol},
        "longQuantity": 100,
        "marketValue": 10000.0,
    }


def _schwab_pending_order(symbol: str) -> dict:
    return {
        "status": "WORKING",
        "orderLegCollection": [
            {"instrument": {"symbol": symbol}}
        ],
    }


class TestReconcileAutoClose(unittest.TestCase):
    """AC 2.1 -- OPEN records with no Schwab position auto-closed."""

    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "test_reconcile.db"
        init_db(db_path=self.db)

    def _add_trade(self, symbol):
        return insert_trade(
            strategy="UOA", symbol=symbol, direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-26T10:00:00", price_at_scan=100.0,
            score=5.0, db_path=self.db,
        )

    def test_close_trade_with_no_schwab_position(self):
        log_id = self._add_trade("AAPL")
        client = MockBrokerClient(positions=[])
        result = reconcile_open_trades(client, db_path=self.db)

        self.assertEqual(len(result["closed"]), 1)
        self.assertEqual(result["closed"][0]["symbol"], "AAPL")

        trade = get_trade(log_id, db_path=self.db)
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["exit_reason"], "SCHWAB_RECONCILE")

    def test_keep_trade_with_schwab_position(self):
        log_id = self._add_trade("MSFT")
        client = MockBrokerClient(positions=[_schwab_position("MSFT")])
        result = reconcile_open_trades(client, db_path=self.db)

        self.assertEqual(len(result["unchanged"]), 1)
        trade = get_trade(log_id, db_path=self.db)
        self.assertEqual(trade["status"], "OPEN")

    def test_mixed_close_and_keep(self):
        id_aapl = self._add_trade("AAPL")
        id_msft = self._add_trade("MSFT")
        id_goog = self._add_trade("GOOG")

        client = MockBrokerClient(positions=[_schwab_position("MSFT")])
        result = reconcile_open_trades(client, db_path=self.db)

        self.assertEqual(len(result["closed"]), 2)
        self.assertEqual(len(result["unchanged"]), 1)

        closed_symbols = {c["symbol"] for c in result["closed"]}
        self.assertEqual(closed_symbols, {"AAPL", "GOOG"})

        self.assertEqual(get_trade(id_aapl, db_path=self.db)["status"], "CLOSED")
        self.assertEqual(get_trade(id_msft, db_path=self.db)["status"], "OPEN")
        self.assertEqual(get_trade(id_goog, db_path=self.db)["status"], "CLOSED")


class TestReconcileStandalone(unittest.TestCase):
    """AC 2.2 -- Reconciler runs standalone (module is importable, has main)."""

    def test_module_importable(self):
        from prime_trading import prime_schwab
        self.assertTrue(hasattr(prime_schwab, "main"))
        self.assertTrue(hasattr(prime_schwab, "reconcile_open_trades"))
        self.assertTrue(hasattr(prime_schwab, "SchwabClient"))


class TestReconcileOpsLogging(unittest.TestCase):
    """AC 2.3 -- Closures logged to prime_ops_health."""

    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "test_reconcile_ops.db"
        init_db(db_path=self.db)

    def test_closure_logged_to_ops_health(self):
        insert_trade(
            strategy="PEAD", symbol="NVDA", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=50,
            entry_time="2026-05-26T10:00:00", price_at_scan=950.0,
            score=7.0, db_path=self.db,
        )
        client = MockBrokerClient(positions=[])
        reconcile_open_trades(client, db_path=self.db)

        events = get_ops_events(component="prime_schwab", db_path=self.db)
        reconcile_events = [e for e in events if e["event_type"] == "SCHWAB_RECONCILE"]
        self.assertGreaterEqual(len(reconcile_events), 1)
        self.assertIn("NVDA", reconcile_events[0]["symbol"])
        self.assertIn("NVDA", reconcile_events[0]["detail"])


class TestReconcileAmbiguous(unittest.TestCase):
    """AC 2.4 -- Ambiguous records (pending orders) flagged, not auto-closed."""

    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "test_reconcile_ambig.db"
        init_db(db_path=self.db)

    def test_pending_order_flagged_not_closed(self):
        log_id = insert_trade(
            strategy="UOA", symbol="TSLA", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-26T10:00:00", price_at_scan=250.0,
            score=6.0, db_path=self.db,
        )
        client = MockBrokerClient(
            positions=[],
            pending_orders=[_schwab_pending_order("TSLA")],
        )
        result = reconcile_open_trades(client, db_path=self.db)

        self.assertEqual(len(result["flagged"]), 1)
        self.assertEqual(result["flagged"][0]["symbol"], "TSLA")
        self.assertEqual(result["flagged"][0]["reason"], "pending_order")

        trade = get_trade(log_id, db_path=self.db)
        self.assertEqual(trade["status"], "OPEN")

        events = get_ops_events(component="prime_schwab", db_path=self.db)
        flagged_events = [e for e in events if e["event_type"] == "RECONCILE_FLAGGED"]
        self.assertGreaterEqual(len(flagged_events), 1)


class TestReconcileApiFailure(unittest.TestCase):
    """AC 2.5 -- Schwab API failure exits gracefully with logged error."""

    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "test_reconcile_fail.db"
        init_db(db_path=self.db)

    def test_api_failure_does_not_crash(self):
        insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-26T10:00:00", price_at_scan=185.0,
            score=5.0, db_path=self.db,
        )
        client = MockBrokerClient(fail_positions=True)
        result = reconcile_open_trades(client, db_path=self.db)

        self.assertIsNotNone(result["schwab_error"])
        self.assertIn("unavailable", result["schwab_error"])
        self.assertEqual(len(result["closed"]), 0)

        trade_still_open = get_trade(
            insert_trade(
                strategy="UOA", symbol="TEST", direction="LONG",
                mode="PAPER", order_type="MARKET", shares=1,
                entry_time="2026-05-26T11:00:00", price_at_scan=10.0,
                score=1.0, db_path=self.db,
            ),
            db_path=self.db,
        )
        self.assertEqual(trade_still_open["status"], "OPEN")

        events = get_ops_events(component="prime_schwab", db_path=self.db)
        error_events = [e for e in events if e["event_type"] == "SCHWAB_API_ERROR"]
        self.assertGreaterEqual(len(error_events), 1)


if __name__ == "__main__":
    unittest.main()
