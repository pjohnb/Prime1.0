"""
Sprint 15 Item 3 (AI Signal Ranker) acceptance tests.

Mocks Claude; verifies AI top-N selection, deterministic score-sort fallback
when the API is unavailable, pass-through when approvals <= Max Trades, and
audit logging to prime_ops_health.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db, get_ops_events
from prime_analytics.prime_signals_db import init_signals_table
from prime_ai import prime_signal_ranker as ranker
from prime_ai._claude import ClaudeUnavailable

APPROVED = [
    {"symbol": "AAA", "strategy": "PSA", "score": 5.0, "sector": "Tech"},
    {"symbol": "BBB", "strategy": "PSA", "score": 9.0, "sector": "Tech"},
    {"symbol": "CCC", "strategy": "PSA", "score": 7.0, "sector": "Energy"},
    {"symbol": "DDD", "strategy": "PSA", "score": 8.0, "sector": "Health"},
]


class TestRankSignals(unittest.TestCase):
    @patch("prime_ai._claude.call_claude")
    def test_ai_ranking_orders_and_truncates(self, mock_call):
        # AI prefers diversification: CCC (Energy) then DDD (Health), ignoring raw score.
        mock_call.return_value = json.dumps([
            {"symbol": "CCC", "rank": 1, "rationale": "diversifies energy", "portfolio_fit_score": 90},
            {"symbol": "DDD", "rank": 2, "rationale": "adds health", "portfolio_fit_score": 85},
            {"symbol": "BBB", "rank": 3, "rationale": "tech heavy", "portfolio_fit_score": 60},
            {"symbol": "AAA", "rank": 4, "rationale": "tech heavy", "portfolio_fit_score": 55},
        ])
        out = ranker.rank_signals(APPROVED, open_positions=[], max_trades=2, api_key="k")
        self.assertEqual([s["symbol"] for s in out], ["CCC", "DDD"])
        self.assertEqual(out[0]["portfolio_fit_score"], 90)
        self.assertEqual(out[0]["ai_rank"], 1)

    @patch("prime_ai._claude.call_claude", side_effect=ClaudeUnavailable("no key"))
    def test_fallback_score_sort(self, mock_call):
        out = ranker.rank_signals(APPROVED, max_trades=2, api_key=None)
        # Top 2 by raw score: BBB(9), DDD(8)
        self.assertEqual([s["symbol"] for s in out], ["BBB", "DDD"])
        self.assertIsNone(out[0]["portfolio_fit_score"])
        self.assertIn("fallback", out[0]["ai_rationale"])


class TestSelectTopN(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_ranker.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    @patch("prime_ai._claude.call_claude")
    def test_passthrough_when_within_max(self, mock_call):
        out = ranker.select_top_n(APPROVED, max_trades=5, db_path=self.db)
        self.assertEqual(len(out), 4)
        mock_call.assert_not_called()  # no AI call when within Max Trades
        self.assertNotIn("ai_rank", out[0])

    @patch("prime_ai._claude.call_claude")
    def test_logs_to_ops_health(self, mock_call):
        mock_call.return_value = json.dumps([
            {"symbol": "BBB", "rank": 1, "portfolio_fit_score": 80, "rationale": "x"},
            {"symbol": "DDD", "rank": 2, "portfolio_fit_score": 70, "rationale": "y"},
        ])
        out = ranker.select_top_n(APPROVED, open_positions=[], max_trades=2,
                                  api_key="k", db_path=self.db)
        self.assertEqual(len(out), 2)
        events = get_ops_events(component="signal_ranker", db_path=self.db)
        self.assertTrue(events)
        self.assertIn("method=AI", events[0]["detail"])

    @patch("prime_ai._claude.call_claude", side_effect=ClaudeUnavailable("down"))
    def test_logs_fallback_method(self, mock_call):
        ranker.select_top_n(APPROVED, max_trades=2, api_key="k", db_path=self.db)
        events = get_ops_events(component="signal_ranker", db_path=self.db)
        self.assertIn("method=score-sort", events[0]["detail"])


if __name__ == "__main__":
    unittest.main()
