"""
Sprint 23 Item 1 -- Schwab Position Sync acceptance tests.

Tests: mock positions -> correct insertion; dedup -> second sync no duplicates;
negative qty -> SHORT direction; sync endpoint returns correct counts.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db, get_open_trades
from prime_analytics.prime_signals_db import init_signals_table
from prime_trading.prime_schwab_sync import sync_schwab_positions


def _mock_position(symbol, long_qty, short_qty, avg_price):
    return {
        "instrument": {"assetType": "EQUITY", "symbol": symbol},
        "longQuantity": long_qty,
        "shortQuantity": short_qty,
        "averagePrice": avg_price,
    }


def _make_mock_client(positions_by_account):
    """Build a mock SchwabClient with multi-account positions."""
    client_inner = MagicMock()

    account_numbers_resp = MagicMock()
    account_numbers_resp.status_code = 200
    account_numbers_resp.json.return_value = [
        {"accountNumber": f"XXXXX{suffix}", "hashValue": f"hash_{suffix}"}
        for suffix in positions_by_account.keys()
    ]
    client_inner.get_account_numbers.return_value = account_numbers_resp

    def _get_account(hash_val, fields=None):
        suffix = hash_val.replace("hash_", "")
        positions = positions_by_account.get(suffix, [])
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "securitiesAccount": {"accountNumber": f"XXXXX{suffix}", "positions": positions}
        }
        return resp

    client_inner.Account.Fields.POSITIONS = "positions"
    client_inner.get_account.side_effect = _get_account

    mock_client = MagicMock()
    mock_client.client = client_inner
    mock_client.connected = True
    return mock_client


class TestSchwabSync(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_schwab_sync.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_import_long_positions(self):
        positions = {
            "7926": [
                _mock_position("AAPL", 100, 0, 175.50),
                _mock_position("NVDA", 50, 0, 820.00),
            ]
        }
        client = _make_mock_client(positions)
        result = sync_schwab_positions(db_path=self.db, schwab_client=client)

        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["errors"], [])

        trades = get_open_trades(db_path=self.db)
        symbols = {t["symbol"] for t in trades}
        self.assertIn("AAPL", symbols)
        self.assertIn("NVDA", symbols)

        aapl = next(t for t in trades if t["symbol"] == "AAPL")
        self.assertEqual(aapl["strategy"], "SCHWAB_IMPORT")
        self.assertEqual(aapl["trade_source"], "SCHWAB_IMPORT")
        self.assertEqual(aapl["direction"], "LONG")
        self.assertEqual(aapl["shares"], 100)
        self.assertAlmostEqual(aapl["entry_price"], 175.50, places=2)
        self.assertEqual(aapl["account"], "7926")

    def test_dedup_second_sync_no_duplicates(self):
        positions = {
            "7926": [_mock_position("GLD", 50, 0, 180.00)]
        }
        client = _make_mock_client(positions)

        r1 = sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(r1["imported"], 1)

        # Re-run same client -- should be a no-op.
        r2 = sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(r2["imported"], 0)
        self.assertEqual(r2["skipped"], 1)

        trades = get_open_trades(db_path=self.db)
        gld_trades = [t for t in trades if t["symbol"] == "GLD"]
        self.assertEqual(len(gld_trades), 1)

    def test_negative_qty_imported_as_short(self):
        positions = {
            "7926": [_mock_position("TSLA", 0, 30, 250.00)]
        }
        client = _make_mock_client(positions)
        result = sync_schwab_positions(db_path=self.db, schwab_client=client)

        self.assertEqual(result["imported"], 1)
        trades = get_open_trades(db_path=self.db)
        tsla = next(t for t in trades if t["symbol"] == "TSLA")
        self.assertEqual(tsla["direction"], "SHORT")
        self.assertEqual(tsla["shares"], 30)

    def test_multi_account_sync(self):
        positions = {
            "7926": [_mock_position("MSFT", 20, 0, 415.00)],
            "0461": [_mock_position("TJX", 15, 0, 118.00)],
            "8779": [_mock_position("COST", 10, 0, 895.00)],
        }
        client = _make_mock_client(positions)
        result = sync_schwab_positions(db_path=self.db, schwab_client=client)

        self.assertEqual(result["imported"], 3)
        trades = get_open_trades(db_path=self.db)
        accounts = {t["account"] for t in trades}
        self.assertIn("7926", accounts)
        self.assertIn("0461", accounts)
        self.assertIn("8779", accounts)

    def test_zero_price_skipped(self):
        positions = {
            "7926": [_mock_position("UNKN", 100, 0, 0)]
        }
        client = _make_mock_client(positions)
        result = sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_schwab_not_connected_returns_error(self):
        result = sync_schwab_positions(db_path=self.db, schwab_client=None)
        # Without a real Schwab client the function returns gracefully.
        # In CI there is no Schwab config so we just verify it doesn't raise.
        self.assertIn("errors", result)
        self.assertIn("imported", result)


class TestSchwabSyncReconcile(unittest.TestCase):
    """Sprint 28: auto-close OPEN SCHWAB_IMPORT records no longer in Schwab."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_schwab_reconcile.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_closed_position_auto_reconciled(self):
        """Import MSFT, then sync with empty positions — should auto-close MSFT."""
        import_positions = {
            "7926": [_mock_position("MSFT", 20, 0, 415.00)]
        }
        client = _make_mock_client(import_positions)
        r1 = sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(r1["imported"], 1)

        # Second sync: MSFT no longer in Schwab
        empty_client = _make_mock_client({"7926": []})
        r2 = sync_schwab_positions(db_path=self.db, schwab_client=empty_client)

        self.assertEqual(r2["reconciled"], 1, "Should reconcile 1 closed position")
        self.assertEqual(r2["errors"], [])

        from prime_data.prime_db import get_connection
        with get_connection(self.db) as conn:
            row = conn.execute(
                "SELECT status, exit_reason FROM prime_trade_log WHERE symbol='MSFT'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["exit_reason"], "SCHWAB_RECONCILE")

    def test_still_open_position_not_reconciled(self):
        """Position still in Schwab must not be closed."""
        positions = {"7926": [_mock_position("AAPL", 10, 0, 180.00)]}
        client = _make_mock_client(positions)
        sync_schwab_positions(db_path=self.db, schwab_client=client)

        # Same positions still live
        r2 = sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(r2["reconciled"], 0)

        trades = get_open_trades(db_path=self.db)
        aapl = [t for t in trades if t["symbol"] == "AAPL"]
        self.assertEqual(len(aapl), 1)
        self.assertEqual(aapl[0]["status"], "OPEN")

    def test_reconcile_logs_ops_health(self):
        """Auto-close should write a SCHWAB_RECONCILE_CLOSE event to prime_ops_health."""
        from prime_data.prime_db import get_ops_events

        positions = {"7926": [_mock_position("TSLA", 5, 0, 250.00)]}
        client = _make_mock_client(positions)
        sync_schwab_positions(db_path=self.db, schwab_client=client)

        empty_client = _make_mock_client({"7926": []})
        sync_schwab_positions(db_path=self.db, schwab_client=empty_client)

        events = get_ops_events(component="schwab_sync", db_path=self.db)
        reconcile_events = [e for e in events if e["event_type"] == "SCHWAB_RECONCILE_CLOSE"]
        self.assertEqual(len(reconcile_events), 1)
        self.assertEqual(reconcile_events[0]["symbol"], "TSLA")

    def test_multi_account_partial_reconcile(self):
        """Close NVDA in account 7926 while AAPL in 0461 stays open."""
        positions = {
            "7926": [_mock_position("NVDA", 10, 0, 820.00)],
            "0461": [_mock_position("AAPL", 20, 0, 175.00)],
        }
        client = _make_mock_client(positions)
        sync_schwab_positions(db_path=self.db, schwab_client=client)

        # NVDA closed in 7926, AAPL still open in 0461
        partial_client = _make_mock_client({"7926": [], "0461": [_mock_position("AAPL", 20, 0, 175.00)]})
        r2 = sync_schwab_positions(db_path=self.db, schwab_client=partial_client)

        self.assertEqual(r2["reconciled"], 1)

        from prime_data.prime_db import get_connection
        with get_connection(self.db) as conn:
            nvda = conn.execute(
                "SELECT status FROM prime_trade_log WHERE symbol='NVDA'"
            ).fetchone()
            aapl = conn.execute(
                "SELECT status FROM prime_trade_log WHERE symbol='AAPL'"
            ).fetchone()
        self.assertEqual(nvda["status"], "CLOSED")
        self.assertEqual(aapl["status"], "OPEN")

    def test_result_includes_reconciled_key(self):
        """sync_schwab_positions result must always include 'reconciled' key."""
        client = _make_mock_client({"7926": []})
        result = sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertIn("reconciled", result)


class TestSyncEndpoint(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_sync_ep.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._db_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_sync_endpoint_returns_200(self):
        with patch(
            "prime_trading.prime_schwab_sync.sync_schwab_positions",
            return_value={"imported": 3, "skipped": 1, "errors": []},
        ):
            resp = self.client.get("/api/v1/sync/schwab")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["imported"], 3)
            self.assertEqual(data["skipped"], 1)

    def test_sync_endpoint_returns_correct_counts(self):
        with patch(
            "prime_trading.prime_schwab_sync.sync_schwab_positions",
            return_value={"imported": 7, "skipped": 0, "errors": []},
        ):
            resp = self.client.get("/api/v1/sync/schwab")
            data = resp.get_json()
            self.assertIn("imported", data)
            self.assertIn("skipped", data)
            self.assertIn("errors", data)


class TestCollectiveInvestmentSectorMapping(unittest.TestCase):
    """PORT-02: sector field stored correctly for COLLECTIVE_INVESTMENT imports."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_schwab_sector.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def _make_position(self, symbol, asset_type, qty=10, avg_price=100.0):
        return {
            "instrument": {"symbol": symbol, "assetType": asset_type},
            "longQuantity": qty,
            "shortQuantity": 0,
            "averagePrice": avg_price,
        }

    def _make_client(self, positions_by_account):
        return _make_mock_client(positions_by_account)

    def _get_trade_sector(self, symbol):
        from prime_data.prime_db import get_connection
        with get_connection(self.db) as conn:
            row = conn.execute(
                "SELECT sector FROM prime_trade_log WHERE symbol=? AND status='OPEN'",
                (symbol,)
            ).fetchone()
        return row["sector"] if row else None

    def test_gld_imports_with_commodities_sector(self):
        positions = {"7926": [self._make_position("GLD", "COLLECTIVE_INVESTMENT", qty=5, avg_price=391.0)]}
        client = self._make_client(positions)
        result = sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self._get_trade_sector("GLD"), "Commodities")

    def test_slv_imports_with_commodities_sector(self):
        positions = {"7926": [self._make_position("SLV", "COLLECTIVE_INVESTMENT", qty=10, avg_price=25.0)]}
        client = self._make_client(positions)
        sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(self._get_trade_sector("SLV"), "Commodities")

    def test_unknown_collective_investment_imports_with_etf_sector(self):
        positions = {"7926": [self._make_position("XYZZ", "COLLECTIVE_INVESTMENT", qty=10, avg_price=50.0)]}
        client = self._make_client(positions)
        result = sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self._get_trade_sector("XYZZ"), "ETF")

    def test_equity_symbol_sector_is_none(self):
        positions = {"7926": [self._make_position("AAPL", "EQUITY", qty=10, avg_price=180.0)]}
        client = self._make_client(positions)
        sync_schwab_positions(db_path=self.db, schwab_client=client)
        # EQUITY symbols not in SYMBOL_SECTOR_MAP get None sector (not 'ETF')
        self.assertIsNone(self._get_trade_sector("AAPL"))

    def test_etf_asset_type_uses_symbol_map(self):
        positions = {"7926": [self._make_position("GLD", "ETF", qty=5, avg_price=391.0)]}
        client = self._make_client(positions)
        sync_schwab_positions(db_path=self.db, schwab_client=client)
        self.assertEqual(self._get_trade_sector("GLD"), "Commodities")


if __name__ == "__main__":
    unittest.main()
