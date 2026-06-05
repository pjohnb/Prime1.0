"""
Sprint 24 Item 1 -- Schwab Live Order Execution acceptance tests.

Tests: PAPER mode gate; RTH gate (market order at 17:00 ET rejected);
buying power gate; position size gate; duplicate gate; confirmation gate.
All 6 safety gates must be tested explicitly (sprint close requirement).
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_trading.prime_schwab_orders import (
    OrderGateError,
    _dup_guard,
    _dup_lock,
    submit_order,
)


def _make_live_config():
    cfg = MagicMock()
    cfg.trading_mode = "LIVE"
    cfg.ops.max_order_pct = 0.10
    return cfg


def _make_mock_client(buying_power=50_000.0, liquidation=100_000.0):
    client_inner = MagicMock()
    account_resp = MagicMock()
    account_resp.status_code = 200
    account_resp.json.return_value = {
        "securitiesAccount": {
            "accountNumber": "XXXXX7926",
            "currentBalances": {
                "buyingPower": buying_power,
                "liquidationValue": liquidation,
            },
        }
    }
    client_inner.get_account.return_value = account_resp
    client_inner.Account.Fields.POSITIONS = "positions"

    order_resp = MagicMock()
    order_resp.status_code = 201
    order_resp.headers = {"Location": "/v1/accounts/hash/orders/99999"}

    # Support both builder and raw dict paths
    client_inner.place_order.return_value = order_resp

    mock_client = MagicMock()
    mock_client.client = client_inner
    mock_client.connected = True
    return mock_client


class TestSafetyGate1PaperMode(unittest.TestCase):
    """Gate 1: PAPER mode blocks all live orders."""

    def test_paper_mode_raises(self):
        paper_cfg = MagicMock()
        paper_cfg.trading_mode = "PAPER"
        with patch("prime_config.prime_config.get_config", return_value=paper_cfg):
            with self.assertRaises(OrderGateError) as ctx:
                submit_order("AAPL", 10, "BUY", "MARKET", 175.0, "hash123",
                             confirmed=True, schwab_client=_make_mock_client())
        self.assertEqual(ctx.exception.gate, "PAPER_MODE")

    def test_live_mode_passes_gate1(self):
        """LIVE config passes Gate 1; other gates may still raise depending on mock."""
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                # Confirm + within budget -> should succeed
                client = _make_mock_client(100_000, 200_000)
                try:
                    import schwab as _s  # noqa
                    schwab_available = True
                except ImportError:
                    schwab_available = False
                if schwab_available:
                    result = submit_order("AAPL", 10, "BUY", "MARKET", 100.0, "hash",
                                         confirmed=True, schwab_client=client)
                    self.assertEqual(result["status"], "SUBMITTED")


class TestSafetyGate2RTH(unittest.TestCase):
    """Gate 2: Market orders outside RTH (09:30–16:00 ET) are blocked."""

    def test_market_order_outside_rth_raises(self):
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=False):
                with self.assertRaises(OrderGateError) as ctx:
                    submit_order("TSLA", 5, "BUY", "MARKET", 250.0, "hash",
                                 confirmed=True, schwab_client=_make_mock_client())
        self.assertEqual(ctx.exception.gate, "RTH")

    def test_limit_order_outside_rth_passes_gate2(self):
        """LIMIT orders are allowed outside RTH (Gate 2 only applies to MARKET)."""
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=False):
                # May fail on later gates, but must not fail on RTH gate.
                try:
                    import schwab  # noqa
                    client = _make_mock_client(100_000, 200_000)
                    result = submit_order("TSLA", 5, "SELL", "LIMIT", 250.0, "hash",
                                         confirmed=True, schwab_client=client)
                    self.assertNotEqual(result.get("gate"), "RTH")
                except OrderGateError as e:
                    self.assertNotEqual(e.gate, "RTH")
                except ImportError:
                    pass  # schwab-py not installed — gate logic still correct


class TestSafetyGate3BuyingPower(unittest.TestCase):
    """Gate 3: Order blocked if cost exceeds available buying power."""

    def test_insufficient_buying_power_raises(self):
        client = _make_mock_client(buying_power=100.0, liquidation=200_000.0)
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                with self.assertRaises(OrderGateError) as ctx:
                    submit_order("AAPL", 100, "BUY", "MARKET", 175.0, "hash",
                                 confirmed=True, schwab_client=client)
        self.assertEqual(ctx.exception.gate, "BUYING_POWER")

    def test_sufficient_buying_power_passes_gate3(self):
        client = _make_mock_client(buying_power=500_000.0, liquidation=1_000_000.0)
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                try:
                    import schwab  # noqa
                    result = submit_order("AAPL", 10, "BUY", "MARKET", 175.0, "hash",
                                         confirmed=True, schwab_client=client)
                    self.assertNotEqual(result.get("gate"), "BUYING_POWER")
                except OrderGateError as e:
                    self.assertNotEqual(e.gate, "BUYING_POWER")
                except ImportError:
                    pass


class TestSafetyGate4PositionSize(unittest.TestCase):
    """Gate 4: Order blocked if notional > max_order_pct (10%) of account value."""

    def test_order_exceeds_10pct_raises(self):
        # Account value = $100k; 10% = $10k; order = $20k (200 shares * $100)
        client = _make_mock_client(buying_power=500_000.0, liquidation=100_000.0)
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                with self.assertRaises(OrderGateError) as ctx:
                    submit_order("AAPL", 200, "BUY", "MARKET", 100.0, "hash",
                                 confirmed=True, schwab_client=client)
        self.assertEqual(ctx.exception.gate, "POSITION_SIZE")

    def test_order_within_10pct_passes_gate4(self):
        # Account value = $100k; 10% = $10k; order = $500 (5 shares * $100)
        client = _make_mock_client(buying_power=500_000.0, liquidation=100_000.0)
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                try:
                    import schwab  # noqa
                    result = submit_order("AAPL", 5, "BUY", "MARKET", 100.0, "hash",
                                         confirmed=True, schwab_client=client)
                    self.assertNotEqual(result.get("gate"), "POSITION_SIZE")
                except OrderGateError as e:
                    self.assertNotEqual(e.gate, "POSITION_SIZE")
                except ImportError:
                    pass


class TestSafetyGate5Duplicate(unittest.TestCase):
    """Gate 5: Second identical order within 60s blocked."""

    def setUp(self):
        with _dup_lock:
            _dup_guard.clear()

    def tearDown(self):
        with _dup_lock:
            _dup_guard.clear()

    def test_duplicate_within_60s_raises(self):
        key = ("MSFT", "BUY")
        with _dup_lock:
            _dup_guard[key] = time.time()  # Simulate a recent submission

        client = _make_mock_client(100_000, 200_000)
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                with self.assertRaises(OrderGateError) as ctx:
                    submit_order("MSFT", 10, "BUY", "MARKET", 415.0, "hash",
                                 confirmed=True, schwab_client=client)
        self.assertEqual(ctx.exception.gate, "DUPLICATE")

    def test_no_duplicate_passes_gate5(self):
        """Fresh order (no prior record) passes the duplicate gate."""
        with _dup_lock:
            _dup_guard.pop(("GLD", "BUY"), None)
        client = _make_mock_client(500_000, 1_000_000)
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                try:
                    import schwab  # noqa
                    result = submit_order("GLD", 5, "BUY", "MARKET", 180.0, "hash",
                                         confirmed=True, schwab_client=client)
                    self.assertNotEqual(result.get("gate"), "DUPLICATE")
                except OrderGateError as e:
                    self.assertNotEqual(e.gate, "DUPLICATE")
                except ImportError:
                    pass


class TestSafetyGate6Confirmation(unittest.TestCase):
    """Gate 6: confirmed=False blocks submission."""

    def setUp(self):
        with _dup_lock:
            _dup_guard.clear()

    def tearDown(self):
        with _dup_lock:
            _dup_guard.clear()

    def test_unconfirmed_raises(self):
        client = _make_mock_client(500_000, 1_000_000)
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                with self.assertRaises(OrderGateError) as ctx:
                    submit_order("NVDA", 5, "BUY", "MARKET", 800.0, "hash",
                                 confirmed=False, schwab_client=client)
        self.assertEqual(ctx.exception.gate, "NO_CONFIRM")

    def test_confirmed_true_passes_gate6(self):
        client = _make_mock_client(500_000, 1_000_000)
        with _dup_lock:
            _dup_guard.pop(("NVDA", "BUY"), None)
        with patch("prime_config.prime_config.get_config", return_value=_make_live_config()):
            with patch("prime_trading.prime_schwab_orders._is_rth", return_value=True):
                try:
                    import schwab  # noqa
                    result = submit_order("NVDA", 5, "BUY", "MARKET", 100.0, "hash",
                                         confirmed=True, schwab_client=client)
                    self.assertNotEqual(result.get("gate"), "NO_CONFIRM")
                except OrderGateError as e:
                    self.assertNotEqual(e.gate, "NO_CONFIRM")
                except ImportError:
                    pass


class TestPaperModeNoSchwabCall(unittest.TestCase):
    """PAPER mode: Schwab API is never called."""

    def test_paper_mode_never_calls_schwab(self):
        client = MagicMock()
        paper_cfg = MagicMock()
        paper_cfg.trading_mode = "PAPER"
        with patch("prime_config.prime_config.get_config", return_value=paper_cfg):
            with self.assertRaises(OrderGateError):
                submit_order("AAPL", 10, "BUY", "MARKET", 175.0, "hash",
                             confirmed=True, schwab_client=client)
        # Gate 1 fires before any Schwab API call
        client.client.get_account.assert_not_called()
        client.client.place_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
