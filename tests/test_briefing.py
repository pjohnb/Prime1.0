"""
Sprint 15 Item 4 (AI Briefing Panel) acceptance tests.

Mocks Claude; verifies headline rendering, empty-state handling when there are
no open positions, graceful degradation when the API is unavailable, and the
endpoint shape.
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
from prime_analytics.prime_signals_db import init_signals_table, insert_signal
from prime_ai import prime_briefing as briefing
from prime_ai._claude import ClaudeUnavailable


class _Base(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_briefing.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _seed(self):
        insert_trade(strategy="UOA", symbol="AAPL", direction="LONG", mode="PAPER",
                     order_type="MARKET", shares=10, entry_time="2026-06-02T09:30:00",
                     price_at_scan=312.93, entry_price=312.93, trade_source="PAPER",
                     db_path=self.db)
        insert_signal("SPY", "UOA", "2026-06-02 10:00", tier="STRONG",
                      status="APPROVED", db_path=self.db)


class TestGenerateBriefing(_Base):
    @patch("prime_ai._claude.call_claude")
    def test_headline_and_actions(self, mock_call):
        self._seed()
        mock_call.return_value = json.dumps({
            "headline": "Tech-heavy book; one strong signal in play.",
            "positions_summary": "1 open position.",
            "signals_summary": "1 STRONG today.",
            "concentration_warnings": ["UOA is 100% of open positions"],
            "recommended_actions": ["Watch AAPL for trim", "Diversify next entry"]})
        b = briefing.generate_briefing(db_path=self.db, api_key="k")
        self.assertIn("Tech-heavy", b["headline"])
        self.assertEqual(len(b["recommended_actions"]), 2)
        self.assertFalse(b["_fallback"])

    def test_empty_state_graceful(self):
        # No positions, no signals, no API key -> graceful headline, no crash.
        with patch("prime_ai._claude.get_api_key", return_value=None):
            b = briefing.generate_briefing(db_path=self.db)
        self.assertTrue(b["headline"])
        self.assertEqual(b["recommended_actions"], [])
        self.assertTrue(b["_fallback"])

    @patch("prime_ai._claude.call_claude", side_effect=ClaudeUnavailable("down"))
    def test_api_unavailable_uses_deterministic_summary(self, mock_call):
        self._seed()
        b = briefing.generate_briefing(db_path=self.db, api_key="k")
        self.assertTrue(b["_fallback"])
        self.assertIn("open position", b["positions_summary"])
        self.assertEqual(b["snapshot"]["open_position_count"], 1)


class TestEndpoint(_Base):
    def setUp(self):
        super().setUp()
        self._p = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._p.start()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def test_endpoint_returns_briefing(self):
        with patch("prime_ai._claude.get_api_key", return_value=None):
            resp = self.client.get("/api/v1/advisory/briefing")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("headline", resp.get_json())


if __name__ == "__main__":
    unittest.main()
