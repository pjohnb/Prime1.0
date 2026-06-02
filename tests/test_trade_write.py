"""
Sprint 14 Item 2 (Lovable UI Write Paths) acceptance tests.

Covers POST /api/v1/trades: valid PAPER write, LIVE-mode rejection, missing /
invalid token rejection, and the duplicate-trade guard.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_api.prime_api_server import create_app
from prime_data.prime_db import init_db, get_open_positions
from prime_analytics.prime_signals_db import init_signals_table

TOKEN = "test-token-abc123"


def _fake_config(db_path, mode="PAPER", token=TOKEN):
    return SimpleNamespace(db_path=db_path, api_token=token, trading_mode=mode)


class TestTradeWrite(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_trade_write.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

        self.mode = "PAPER"
        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config",
            side_effect=lambda: _fake_config(self.db, self.mode),
        )
        self._cfg_patcher.start()

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def _post(self, payload, token=TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/api/v1/trades", json=payload, headers=headers)

    _VALID = {"symbol": "AAPL", "qty": 10, "strategy": "UOA",
              "direction": "BUY", "account": "PAPER1", "price": 312.93}

    def test_valid_paper_trade_writes_db(self):
        resp = self._post(self._VALID)
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertIn("log_id", body)
        self.assertEqual(body["trade_source"], "PAPER")

        positions = get_open_positions(db_path=self.db)
        self.assertEqual(len(positions), 1)
        p = positions[0]
        self.assertEqual(p["symbol"], "AAPL")
        self.assertEqual(p["direction"], "LONG")  # BUY -> LONG
        self.assertEqual(p["shares"], 10)
        self.assertAlmostEqual(p["entry_price"], 312.93, places=2)
        self.assertEqual(p["trade_source"], "PAPER")

    def test_live_mode_rejected(self):
        self.mode = "LIVE"
        resp = self._post(self._VALID)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(len(get_open_positions(db_path=self.db)), 0)

    def test_missing_token_rejected(self):
        resp = self._post(self._VALID, token=None)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(get_open_positions(db_path=self.db)), 0)

    def test_wrong_token_rejected(self):
        resp = self._post(self._VALID, token="wrong-token")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(get_open_positions(db_path=self.db)), 0)

    def test_duplicate_trade_guarded(self):
        first = self._post(self._VALID)
        self.assertEqual(first.status_code, 201)
        second = self._post(self._VALID)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(len(get_open_positions(db_path=self.db)), 1)

    def test_invalid_payload_rejected(self):
        bad = dict(self._VALID)
        bad["price"] = 0
        self.assertEqual(self._post(bad).status_code, 400)
        bad2 = dict(self._VALID)
        bad2.pop("symbol")
        self.assertEqual(self._post(bad2).status_code, 400)

    def test_non_paper_direction_normalised(self):
        resp = self._post({**self._VALID, "symbol": "TSLA", "direction": "SELL"})
        self.assertEqual(resp.status_code, 201)
        pos = [p for p in get_open_positions(db_path=self.db) if p["symbol"] == "TSLA"][0]
        self.assertEqual(pos["direction"], "SHORT")


if __name__ == "__main__":
    unittest.main()
