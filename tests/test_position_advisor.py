"""
Sprint 15 Item 2 (AI Position Advisory) acceptance tests.

Mocks the Claude call; verifies JSON parsing, recommendation validation,
graceful degradation when the API is unavailable, and the endpoint shape.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_api.prime_api_server import create_app
from prime_data.prime_db import init_db, insert_trade
from prime_analytics.prime_signals_db import init_signals_table
from prime_ai import prime_position_advisor as adv
from prime_ai._claude import ClaudeUnavailable, parse_json


class TestParsing(unittest.TestCase):
    def test_parse_json_with_fences(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_parse_json_embedded(self):
        self.assertEqual(parse_json('here you go: {"a": 2} thanks'), {"a": 2})


class TestAdviseOne(unittest.TestCase):
    POS = {"symbol": "AAPL", "strategy": "UOA", "direction": "LONG",
           "entry_price": 312.93, "shares": 10, "entry_time": "2026-06-02T09:30:00"}

    @patch("prime_ai._claude.call_claude")
    def test_valid_recommendation(self, mock_call):
        mock_call.return_value = json.dumps({
            "symbol": "AAPL", "recommendation": "trim", "confidence": "high",
            "reasoning": "Up nicely; lock partial.", "suggested_action": "Sell half"})
        out = adv.advise_one(self.POS, api_key="k")
        self.assertEqual(out["recommendation"], "TRIM")
        self.assertEqual(out["confidence"], "HIGH")
        self.assertFalse(out["_fallback"])
        self.assertIn("lock partial", out["reasoning"])

    @patch("prime_ai._claude.call_claude")
    def test_unexpected_recommendation_falls_back(self, mock_call):
        mock_call.return_value = json.dumps({"symbol": "AAPL", "recommendation": "YOLO"})
        out = adv.advise_one(self.POS, api_key="k")
        self.assertEqual(out["recommendation"], "UNAVAILABLE")
        self.assertTrue(out["_fallback"])

    @patch("prime_ai._claude.call_claude", side_effect=ClaudeUnavailable("no key"))
    def test_api_unavailable_graceful(self, mock_call):
        out = adv.advise_one(self.POS, api_key=None)
        self.assertEqual(out["recommendation"], "UNAVAILABLE")
        self.assertIn("unavailable", out["reasoning"].lower())
        self.assertTrue(out["_fallback"])


class TestEndpoint(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_advisor.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._p = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._p.start()
        insert_trade(strategy="UOA", symbol="AAPL", direction="LONG", mode="PAPER",
                     order_type="MARKET", shares=10, entry_time="2026-06-02T09:30:00",
                     price_at_scan=312.93, entry_price=312.93, trade_source="PAPER",
                     db_path=self.db)
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._p.stop()
        if self.db.exists():
            self.db.unlink()

    @patch("prime_ai._claude.call_claude")
    def test_endpoint_returns_advisory(self, mock_call):
        mock_call.return_value = json.dumps({
            "symbol": "AAPL", "recommendation": "HOLD", "confidence": "MEDIUM",
            "reasoning": "Thesis intact.", "suggested_action": "Hold"})
        resp = self.client.get("/api/v1/advisory/positions")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["advisories"][0]["recommendation"], "HOLD")

    def test_endpoint_graceful_without_api_key(self):
        # No ANTHROPIC_API_KEY -> UNAVAILABLE, but HTTP 200 (never crash the UI).
        with patch("prime_ai._claude.get_api_key", return_value=None):
            resp = self.client.get("/api/v1/advisory/positions")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["advisories"][0]["recommendation"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
