"""
WO-PRIME-PORTFOLIO-SYNC-MANUAL-01 acceptance tests.

Verifies that manually-placed Schwab positions are correctly imported by
sync_schwab_positions:
  AC1/AC2: all filled positions imported regardless of order origin
  AC3: imported records tagged trade_source='SCHWAB_IMPORT'
  AC4: stop attachment fires (LIVE mode) for newly imported positions
  AC5: mode uses actual system mode (not hardcoded PAPER)
  Dedup: LIVE/PAPER PRIME-originated positions prevent duplicate SCHWAB_IMPORT
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SYNC_SRC = (PROJECT_ROOT / "prime_trading" / "prime_schwab_sync.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schwab_position(symbol, asset_type="EQUITY", long_qty=10, avg_price=50.0):
    return {
        "instrument": {"symbol": symbol, "assetType": asset_type},
        "longQuantity": long_qty,
        "shortQuantity": 0,
        "averagePrice": avg_price,
    }


def _make_mock_client(positions_by_account):
    """Build a mock SchwabClient whose accounts have specified positions."""
    client = MagicMock()
    accounts_resp = MagicMock()
    accounts_resp.status_code = 200
    accounts_resp.json.return_value = [
        {"accountNumber": acct_num, "hashValue": f"hash_{acct_num}"}
        for acct_num, _ in positions_by_account
    ]
    client.client.get_account_numbers.return_value = accounts_resp

    def _get_account(hash_val, fields=None):
        resp = MagicMock()
        resp.status_code = 200
        # Map hash → positions
        acct_num = hash_val.replace("hash_", "")
        positions = next(
            (p for n, p in positions_by_account if n == acct_num), []
        )
        resp.json.return_value = {"securitiesAccount": {"positions": positions}}
        return resp

    client.client.get_account.side_effect = _get_account
    return client


# ---------------------------------------------------------------------------
# Static / source tests
# ---------------------------------------------------------------------------

class TestPortfolioSyncStaticFixes(unittest.TestCase):

    def test_open_positions_index_includes_live_and_paper(self):
        self.assertIn("'LIVE'", SYNC_SRC,
                      "_open_positions_index must include LIVE in dedup query")
        self.assertIn("'PAPER'", SYNC_SRC,
                      "_open_positions_index must include PAPER in dedup query")
        self.assertIn("trade_source IN", SYNC_SRC,
                      "_open_positions_index must use IN clause for trade_source")

    def test_import_mode_not_hardcoded_paper(self):
        import re
        # Must not use bare mode="PAPER" in insert_trade call
        hard_paper = re.search(r'insert_trade\([^)]*mode="PAPER"', SYNC_SRC, re.DOTALL)
        self.assertIsNone(hard_paper,
                          "insert_trade in sync must not hardcode mode='PAPER'")

    def test_import_mode_reads_from_config(self):
        self.assertIn("import_mode", SYNC_SRC,
                      "sync must derive import_mode from system config, not hardcode it")
        self.assertIn("get_config", SYNC_SRC,
                      "sync must call get_config() to resolve trading_mode")

    def test_hash_val_in_account_tuple(self):
        self.assertIn("acct_hash", SYNC_SRC,
                      "_get_all_account_positions must return hash_val in each tuple")

    def test_stop_price_passed_to_insert_trade(self):
        self.assertIn("stop_price=_import_stop", SYNC_SRC,
                      "insert_trade call must include stop_price for imported positions")

    def test_stop_attachment_in_live_mode(self):
        self.assertIn("attach_stop_order", SYNC_SRC,
                      "sync must call attach_stop_order for LIVE imported positions")
        self.assertIn("import_mode == \"LIVE\"", SYNC_SRC,
                      "stop attachment must be guarded by import_mode == LIVE")

    def test_stop_attachment_failure_logged_as_no_stop_violation(self):
        self.assertIn("NO_STOP_VIOLATION", SYNC_SRC,
                      "stop attachment failure must log a NO_STOP_VIOLATION ops event")


# ---------------------------------------------------------------------------
# Behavioral tests
# ---------------------------------------------------------------------------

class TestPortfolioSyncBehavior(unittest.TestCase):

    def _run_sync(self, positions_by_account, existing_set=None,
                  trading_mode="LIVE", recently_closed=None):
        """Run sync_schwab_positions with mocked dependencies."""
        from prime_trading.prime_schwab_sync import sync_schwab_positions

        mock_client = _make_mock_client(positions_by_account)

        fake_cfg = MagicMock()
        fake_cfg.trading_mode = trading_mode

        imported_calls = []

        def _fake_insert_trade(**kwargs):
            imported_calls.append(kwargs)
            return "log-" + kwargs["symbol"]

        with patch("prime_trading.prime_schwab_sync._open_positions_index",
                   return_value=set() if existing_set is None else existing_set), \
             patch("prime_trading.prime_schwab_sync._recently_closed_symbols",
                   return_value={} if recently_closed is None else recently_closed), \
             patch("prime_trading.prime_schwab_sync._reconcile_closed_positions",
                   return_value=0), \
             patch("prime_trading.prime_schwab_sync._resolve_sector",
                   return_value="Technology"), \
             patch("prime_data.prime_db.insert_trade",
                   side_effect=_fake_insert_trade), \
             patch("prime_config.prime_config.get_config", return_value=fake_cfg), \
             patch("prime_trading.prime_schwab_orders.attach_stop_order"):
            result = sync_schwab_positions(schwab_client=mock_client)

        return result, imported_calls

    def test_manual_position_imported(self):
        """AC1/AC2: a manually-placed Schwab position appears in one sync cycle."""
        result, calls = self._run_sync([
            ("12347926", [_make_schwab_position("XLB", asset_type="ETF", long_qty=75, avg_price=51.725)])
        ])
        self.assertEqual(result["imported"], 1)
        self.assertTrue(any(c["symbol"] == "XLB" for c in calls))

    def test_imported_position_tagged_schwab_import(self):
        """AC3: inserted record has trade_source='SCHWAB_IMPORT'."""
        _, calls = self._run_sync([
            ("12347926", [_make_schwab_position("AAPL", long_qty=10, avg_price=180.0)])
        ])
        aapl = next(c for c in calls if c["symbol"] == "AAPL")
        self.assertEqual(aapl["trade_source"], "SCHWAB_IMPORT")

    def test_import_mode_uses_live_when_live(self):
        """AC5: positions imported in LIVE mode are tagged mode='LIVE'."""
        _, calls = self._run_sync(
            [("12347926", [_make_schwab_position("MSFT", long_qty=5, avg_price=400.0)])],
            trading_mode="LIVE",
        )
        msft = next(c for c in calls if c["symbol"] == "MSFT")
        self.assertEqual(msft["mode"], "LIVE",
                         "Imported position must use system trading_mode, not hardcoded PAPER")

    def test_import_mode_paper_when_paper(self):
        """Positions imported in PAPER mode must be tagged mode='PAPER'."""
        _, calls = self._run_sync(
            [("12347926", [_make_schwab_position("NVDA", long_qty=3, avg_price=900.0)])],
            trading_mode="PAPER",
        )
        nvda = next(c for c in calls if c["symbol"] == "NVDA")
        self.assertEqual(nvda["mode"], "PAPER")

    def test_stop_price_set_on_import(self):
        """AC4 (DB side): stop_price is recorded in insert_trade for imported positions."""
        _, calls = self._run_sync([
            ("12347926", [_make_schwab_position("XLB", asset_type="ETF", long_qty=75, avg_price=51.725)])
        ])
        xlb = next(c for c in calls if c["symbol"] == "XLB")
        self.assertIsNotNone(xlb.get("stop_price"),
                             "import must set stop_price in insert_trade")
        self.assertLess(xlb["stop_price"], xlb["entry_price"],
                        "LONG stop must be below entry price")

    def test_live_prime_position_prevents_duplicate_import(self):
        """Dedup: a PRIME-originated LIVE open position blocks SCHWAB_IMPORT of same symbol/account."""
        result, calls = self._run_sync(
            [("12347926", [_make_schwab_position("AAPL", long_qty=10, avg_price=180.0)])],
            existing_set={("AAPL", "7926")},  # already in PRIME as LIVE trade
        )
        self.assertEqual(result["skipped"], 1,
                         "Symbol already open via LIVE trade must be skipped")
        self.assertEqual(result["imported"], 0)
        self.assertEqual(calls, [], "No insert_trade calls expected when already open")

    def test_second_sync_is_noop(self):
        """Running sync twice must not create duplicate records."""
        # First sync: XLB imported
        result1, calls1 = self._run_sync([
            ("12348779", [_make_schwab_position("XLB", asset_type="ETF", long_qty=75, avg_price=51.725)])
        ])
        self.assertEqual(result1["imported"], 1)

        # Second sync: XLB now in existing set (as SCHWAB_IMPORT)
        result2, calls2 = self._run_sync(
            [("12348779", [_make_schwab_position("XLB", asset_type="ETF", long_qty=75, avg_price=51.725)])],
            existing_set={("XLB", "8779")},
        )
        self.assertEqual(result2["imported"], 0,
                         "Second sync must not re-import existing SCHWAB_IMPORT position")
        self.assertEqual(calls2, [])


if __name__ == "__main__":
    unittest.main()
