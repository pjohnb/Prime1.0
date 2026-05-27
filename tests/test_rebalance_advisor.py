"""
Sprint 9 Item 4 (ML-17) acceptance tests -- AI Portfolio Rebalancing.
Covers suggestion render, API failure fallback, empty portfolio,
sector over-concentration trigger, snapshot building.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_rebalance_advisor import (
    build_portfolio_snapshot,
    get_ai_rebalance_suggestions,
    _fallback_suggestions,
)


class TestBuildPortfolioSnapshot(unittest.TestCase):

    def test_builds_from_positions(self):
        positions = [
            {"symbol": "AAPL", "shares": 100, "current_price": 185.0, "trade_source": "PAPER"},
            {"symbol": "JPM", "shares": 50, "current_price": 200.0, "trade_source": "LIVE"},
        ]
        snapshot = build_portfolio_snapshot(positions)
        self.assertEqual(snapshot["position_count"], 2)
        self.assertGreater(snapshot["total_market_value"], 0)
        self.assertEqual(len(snapshot["positions"]), 2)
        self.assertEqual(snapshot["positions"][0]["sector"], "Technology")
        self.assertEqual(snapshot["positions"][1]["sector"], "Financials")

    def test_empty_portfolio(self):
        snapshot = build_portfolio_snapshot([])
        self.assertEqual(snapshot["position_count"], 0)
        self.assertEqual(snapshot["total_market_value"], 0)

    def test_includes_risk_data(self):
        positions = [
            {"symbol": "AAPL", "shares": 100, "current_price": 185.0},
        ]
        snapshot = build_portfolio_snapshot(positions)
        self.assertIn("risk", snapshot)
        self.assertIn("sector_concentration", snapshot["risk"])


class TestFallbackSuggestions(unittest.TestCase):

    def test_concentration_breach_generates_reduce(self):
        snapshot = {
            "risk": {
                "concentration_breach": True,
                "max_sector": "Technology",
                "max_sector_weight": 0.65,
                "correlation_flags": [],
            }
        }
        result = _fallback_suggestions(snapshot, "test")
        self.assertTrue(result["_fallback"])
        self.assertTrue(any(s["action"] == "REDUCE" for s in result["suggestions"]))

    def test_no_breach_generates_hold(self):
        snapshot = {
            "risk": {
                "concentration_breach": False,
                "max_sector": "Technology",
                "max_sector_weight": 0.25,
                "correlation_flags": [],
            }
        }
        result = _fallback_suggestions(snapshot, "test")
        self.assertTrue(any(s["action"] == "HOLD" for s in result["suggestions"]))

    def test_correlation_flags_generate_suggestions(self):
        snapshot = {
            "risk": {
                "concentration_breach": False,
                "correlation_flags": ["Tech+Comm correlation risk: combined 55% of portfolio"],
            }
        }
        result = _fallback_suggestions(snapshot, "test")
        reduce = [s for s in result["suggestions"] if s["action"] == "REDUCE"]
        self.assertTrue(len(reduce) >= 1)


class TestGetAiRebalanceSuggestions(unittest.TestCase):

    def test_no_api_key_returns_fallback(self):
        snapshot = build_portfolio_snapshot([
            {"symbol": "AAPL", "shares": 100, "current_price": 185.0},
        ])
        result = get_ai_rebalance_suggestions(snapshot, api_key=None)
        self.assertTrue(result["_fallback"])
        self.assertIn("no API key", result["_fallback_reason"])

    def test_result_has_required_fields(self):
        snapshot = build_portfolio_snapshot([
            {"symbol": "AAPL", "shares": 100, "current_price": 185.0},
        ])
        result = get_ai_rebalance_suggestions(snapshot, api_key=None)
        self.assertIn("suggestions", result)
        self.assertIn("timestamp", result)
        self.assertIsInstance(result["suggestions"], list)

    def test_api_import_error_fallback(self):
        snapshot = build_portfolio_snapshot([
            {"symbol": "AAPL", "shares": 100, "current_price": 185.0},
        ])
        with patch.dict("sys.modules", {"anthropic": None}):
            result = get_ai_rebalance_suggestions(snapshot, api_key="fake-key")
        self.assertTrue(result["_fallback"])

    def test_concentration_breach_triggers_high_urgency(self):
        positions = [
            {"symbol": "AAPL", "shares": 500, "current_price": 185.0},
            {"symbol": "MSFT", "shares": 500, "current_price": 415.0},
            {"symbol": "JPM", "shares": 10, "current_price": 200.0},
        ]
        snapshot = build_portfolio_snapshot(positions)
        result = get_ai_rebalance_suggestions(snapshot, api_key=None)
        high_urgency = [s for s in result["suggestions"] if s["urgency"] == "HIGH"]
        self.assertTrue(len(high_urgency) >= 1)

    def test_empty_portfolio_returns_hold(self):
        snapshot = build_portfolio_snapshot([])
        result = get_ai_rebalance_suggestions(snapshot, api_key=None)
        self.assertTrue(any(s["action"] == "HOLD" for s in result["suggestions"]))

    def test_api_exception_fallback(self):
        snapshot = build_portfolio_snapshot([
            {"symbol": "AAPL", "shares": 100, "current_price": 185.0},
        ])
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value.messages.create.side_effect = RuntimeError("API down")
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = get_ai_rebalance_suggestions(snapshot, api_key="fake-key")
        self.assertTrue(result["_fallback"])
        self.assertIn("API down", result["_fallback_reason"])

    def test_suggestions_advisory_only(self):
        snapshot = build_portfolio_snapshot([
            {"symbol": "AAPL", "shares": 100, "current_price": 185.0},
        ])
        result = get_ai_rebalance_suggestions(snapshot, api_key=None)
        for s in result["suggestions"]:
            self.assertIn(s["action"], ("REDUCE", "INCREASE", "HOLD", "EXIT"))
            self.assertNotIn("order", s)
            self.assertNotIn("execute", s)


if __name__ == "__main__":
    unittest.main()
