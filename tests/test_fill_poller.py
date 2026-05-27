"""
CIL-086 Fill Confirmation Polling tests.
Covers: fill detection, timeout handling, DB update, async watcher (mocked Schwab).
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import get_trade, init_db, insert_trade
from prime_trading.prime_fill_poller import (
    poll_fill,
    start_fill_watcher,
    update_trade_on_fill,
)


class MockSchwabClient:
    """Mock client with configurable order status responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._call_count = 0

    def get_order_status(self, order_id):
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return {"status": "WORKING"}


class TestPollFill(unittest.TestCase):
    """Fill detection and timeout."""

    def test_immediate_fill(self):
        client = MockSchwabClient([
            {"status": "FILLED", "filledPrice": 190.25, "filledQuantity": 100}
        ])
        result = poll_fill("order-1", client, timeout_sec=10, poll_interval=0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["fill_price"], 190.25)
        self.assertEqual(result["shares_filled"], 100)
        self.assertIn("fill_time", result)

    def test_delayed_fill(self):
        client = MockSchwabClient([
            {"status": "WORKING"},
            {"status": "WORKING"},
            {"status": "FILLED", "filledPrice": 50.10, "filledQuantity": 200},
        ])
        result = poll_fill("order-2", client, timeout_sec=30, poll_interval=0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["fill_price"], 50.10)
        self.assertEqual(result["shares_filled"], 200)

    def test_timeout_returns_none(self):
        client = MockSchwabClient([
            {"status": "WORKING"},
            {"status": "WORKING"},
            {"status": "WORKING"},
        ])
        result = poll_fill("order-3", client, timeout_sec=0, poll_interval=0)
        self.assertIsNone(result)

    def test_canceled_order(self):
        client = MockSchwabClient([
            {"status": "WORKING"},
            {"status": "CANCELED"},
        ])
        result = poll_fill("order-4", client, timeout_sec=30, poll_interval=0)
        self.assertIsNone(result)

    def test_rejected_order(self):
        client = MockSchwabClient([
            {"status": "REJECTED"},
        ])
        result = poll_fill("order-5", client, timeout_sec=30, poll_interval=0)
        self.assertIsNone(result)

    def test_api_error_retries(self):
        client = MagicMock()
        client.get_order_status.side_effect = [
            Exception("Network error"),
            {"status": "FILLED", "filledPrice": 100.0, "filledQuantity": 50},
        ]
        result = poll_fill("order-6", client, timeout_sec=30, poll_interval=0)
        self.assertIsNotNone(result)
        self.assertEqual(client.get_order_status.call_count, 2)


class TestUpdateTradeOnFill(unittest.TestCase):
    """DB update with fill data."""

    def setUp(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test_fill.db"
        init_db(db_path=self.db_path)

    def test_updates_entry_price_and_shares(self):
        log_id = insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=100,
            entry_time="2026-05-27T12:00:00", price_at_scan=190.0,
            entry_price=190.0, db_path=self.db_path,
        )

        update_trade_on_fill(log_id, 190.25, 100, db_path=self.db_path)

        trade = get_trade(log_id, db_path=self.db_path)
        self.assertAlmostEqual(trade["entry_price"], 190.25)
        self.assertEqual(trade["shares"], 100)

    def test_partial_fill_updates_shares(self):
        log_id = insert_trade(
            strategy="PEAD", symbol="MSFT", direction="LONG",
            mode="PAPER", order_type="LIMIT", shares=50,
            entry_time="2026-05-27T12:00:00", price_at_scan=415.0,
            entry_price=415.0, db_path=self.db_path,
        )

        update_trade_on_fill(log_id, 414.80, 35, db_path=self.db_path)

        trade = get_trade(log_id, db_path=self.db_path)
        self.assertAlmostEqual(trade["entry_price"], 414.80)
        self.assertEqual(trade["shares"], 35)


class TestFillWatcher(unittest.TestCase):
    """Async watcher integration."""

    def setUp(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test_watcher.db"
        init_db(db_path=self.db_path)

    def test_watcher_updates_trade(self):
        log_id = insert_trade(
            strategy="UOA", symbol="TSLA", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T12:00:00", price_at_scan=200.0,
            entry_price=200.0, db_path=self.db_path,
        )

        client = MockSchwabClient([
            {"status": "FILLED", "filledPrice": 199.50, "filledQuantity": 10}
        ])

        with patch("prime_trading.prime_fill_poller.POLL_INTERVAL", 0):
            start_fill_watcher("order-w1", log_id, client, db_path=self.db_path)
            time.sleep(1)

        trade = get_trade(log_id, db_path=self.db_path)
        self.assertAlmostEqual(trade["entry_price"], 199.50)

    def test_watcher_timeout_logs_event(self):
        log_id = insert_trade(
            strategy="UOA", symbol="NVDA", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=5,
            entry_time="2026-05-27T12:00:00", price_at_scan=800.0,
            entry_price=800.0, db_path=self.db_path,
        )

        client = MockSchwabClient([{"status": "WORKING"}])

        with patch("prime_trading.prime_fill_poller.POLL_TIMEOUT", 0):
            with patch("prime_trading.prime_fill_poller.POLL_INTERVAL", 0):
                start_fill_watcher("order-w2", log_id, client, db_path=self.db_path)
                time.sleep(1)

        trade = get_trade(log_id, db_path=self.db_path)
        self.assertAlmostEqual(trade["entry_price"], 800.0)


if __name__ == "__main__":
    unittest.main()
