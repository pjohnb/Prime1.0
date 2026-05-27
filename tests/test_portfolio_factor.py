"""
Sprint 8 Item 7 (UOA-ENH-002) acceptance tests -- Portfolio Factor Module.
Covers concentration calc, rebalance suggestions, and sector map fallback.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_portfolio_factor import (
    MAX_SECTOR_CONCENTRATION,
    evaluate_portfolio_risk,
    get_rebalance_suggestions,
    sector_map,
)


class TestSectorMap(unittest.TestCase):
    """AC: sector_map() reuses SRS scanner sector data; fallback to 'Unknown'."""

    def test_known_symbol(self):
        self.assertEqual(sector_map("AAPL"), "Technology")
        self.assertEqual(sector_map("UNH"), "Health Care")
        self.assertEqual(sector_map("JPM"), "Financials")

    def test_case_insensitive(self):
        self.assertEqual(sector_map("aapl"), "Technology")
        self.assertEqual(sector_map("Aapl"), "Technology")

    def test_unknown_symbol_returns_unknown(self):
        self.assertEqual(sector_map("ZZZZZ"), "Unknown")

    def test_etf_symbols(self):
        self.assertEqual(sector_map("SPY"), "Broad Market")
        self.assertEqual(sector_map("QQQ"), "Technology")

    def test_legacy_positions(self):
        self.assertEqual(sector_map("GLD"), "Materials")
        self.assertEqual(sector_map("NIO"), "Consumer Discretionary")
        self.assertEqual(sector_map("TJX"), "Consumer Discretionary")
        self.assertEqual(sector_map("MSFT"), "Technology")
        self.assertEqual(sector_map("DDOG"), "Technology")


class TestEvaluatePortfolioRisk(unittest.TestCase):
    """AC: evaluate_portfolio_risk() with concentration limit at 40%."""

    def test_empty_positions(self):
        result = evaluate_portfolio_risk([])
        self.assertEqual(result["position_count"], 0)
        self.assertFalse(result["concentration_breach"])
        self.assertEqual(result["max_sector"], "N/A")

    def test_single_position(self):
        positions = [
            {"symbol": "AAPL", "shares": 100, "current_price": 200.0},
        ]
        result = evaluate_portfolio_risk(positions)
        self.assertEqual(result["position_count"], 1)
        self.assertAlmostEqual(result["max_sector_weight"], 1.0)
        self.assertTrue(result["concentration_breach"])
        self.assertEqual(result["max_sector"], "Technology")
        self.assertEqual(result["max_position_symbol"], "AAPL")

    def test_diversified_no_breach(self):
        positions = [
            {"symbol": "AAPL", "shares": 10, "current_price": 100.0},
            {"symbol": "JPM", "shares": 10, "current_price": 100.0},
            {"symbol": "UNH", "shares": 10, "current_price": 100.0},
            {"symbol": "XOM", "shares": 10, "current_price": 100.0},
        ]
        result = evaluate_portfolio_risk(positions)
        self.assertFalse(result["concentration_breach"])
        self.assertAlmostEqual(result["max_sector_weight"], 0.25)
        self.assertEqual(len(result["correlation_flags"]), 0)

    def test_tech_concentration_breach(self):
        positions = [
            {"symbol": "AAPL", "shares": 30, "current_price": 100.0},
            {"symbol": "MSFT", "shares": 30, "current_price": 100.0},
            {"symbol": "JPM", "shares": 10, "current_price": 100.0},
            {"symbol": "UNH", "shares": 10, "current_price": 100.0},
        ]
        result = evaluate_portfolio_risk(positions)
        self.assertTrue(result["concentration_breach"])
        self.assertEqual(result["max_sector"], "Technology")
        self.assertGreater(result["max_sector_weight"], MAX_SECTOR_CONCENTRATION)

    def test_tech_comm_correlation_flag(self):
        positions = [
            {"symbol": "AAPL", "shares": 40, "current_price": 100.0},
            {"symbol": "VZ", "shares": 20, "current_price": 100.0},
            {"symbol": "JPM", "shares": 10, "current_price": 100.0},
        ]
        result = evaluate_portfolio_risk(positions)
        corr_flags = [f for f in result["correlation_flags"] if "Tech+Comm" in f]
        self.assertTrue(len(corr_flags) > 0)

    def test_includes_all_trade_sources(self):
        positions = [
            {"symbol": "GLD", "shares": 10, "current_price": 310.0, "trade_source": "LEGACY"},
            {"symbol": "AAPL", "shares": 5, "current_price": 200.0, "trade_source": "PAPER"},
            {"symbol": "MSFT", "shares": 5, "current_price": 415.0, "trade_source": "LIVE"},
        ]
        result = evaluate_portfolio_risk(positions)
        self.assertEqual(result["position_count"], 3)
        self.assertGreater(result["total_market_value"], 0)

    def test_fallback_price_fields(self):
        pos_entry = [{"symbol": "AAPL", "shares": 10, "entry_price": 150.0}]
        pos_scan = [{"symbol": "AAPL", "shares": 10, "price_at_scan": 155.0}]
        r1 = evaluate_portfolio_risk(pos_entry)
        r2 = evaluate_portfolio_risk(pos_scan)
        self.assertAlmostEqual(r1["total_market_value"], 1500.0)
        self.assertAlmostEqual(r2["total_market_value"], 1550.0)

    def test_max_position_weight(self):
        positions = [
            {"symbol": "AAPL", "shares": 100, "current_price": 200.0},
            {"symbol": "JPM", "shares": 10, "current_price": 200.0},
        ]
        result = evaluate_portfolio_risk(positions)
        self.assertEqual(result["max_position_symbol"], "AAPL")
        expected = (100 * 200) / (100 * 200 + 10 * 200)
        self.assertAlmostEqual(result["max_position_weight"], round(expected, 4), places=3)


class TestGetRebalanceSuggestions(unittest.TestCase):
    """AC: get_rebalance_suggestions() is advisory only, respects limits."""

    def test_signal_within_headroom(self):
        positions = [
            {"symbol": "AAPL", "shares": 10, "current_price": 100.0},
            {"symbol": "JPM", "shares": 10, "current_price": 100.0},
            {"symbol": "UNH", "shares": 10, "current_price": 100.0},
        ]
        signals = [{"symbol": "XOM", "price_at_scan": 110.0}]
        suggestions = get_rebalance_suggestions(positions, signals)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["action"], "SIZE")
        self.assertGreater(suggestions[0]["suggested_shares"], 0)

    def test_signal_blocked_by_concentration(self):
        positions = [
            {"symbol": "AAPL", "shares": 50, "current_price": 100.0},
            {"symbol": "MSFT", "shares": 50, "current_price": 100.0},
            {"symbol": "JPM", "shares": 10, "current_price": 100.0},
        ]
        signals = [{"symbol": "NVDA", "price_at_scan": 800.0}]
        suggestions = get_rebalance_suggestions(positions, signals)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["action"], "SKIP")

    def test_max_alloc_capped_at_five_pct(self):
        positions = [
            {"symbol": "JPM", "shares": 100, "current_price": 100.0},
        ]
        signals = [{"symbol": "XOM", "price_at_scan": 50.0}]
        suggestions = get_rebalance_suggestions(positions, signals)
        self.assertEqual(suggestions[0]["action"], "SIZE")
        self.assertLessEqual(suggestions[0]["suggested_pct"], 0.05)

    def test_zero_price_signal(self):
        positions = [
            {"symbol": "JPM", "shares": 100, "current_price": 100.0},
        ]
        signals = [{"symbol": "XOM", "price_at_scan": 0}]
        suggestions = get_rebalance_suggestions(positions, signals)
        self.assertEqual(suggestions[0]["suggested_shares"], 0)

    def test_multiple_signals(self):
        positions = [
            {"symbol": "AAPL", "shares": 10, "current_price": 200.0},
        ]
        signals = [
            {"symbol": "JPM", "price_at_scan": 180.0},
            {"symbol": "UNH", "price_at_scan": 500.0},
        ]
        suggestions = get_rebalance_suggestions(positions, signals)
        self.assertEqual(len(suggestions), 2)
        symbols = {s["symbol"] for s in suggestions}
        self.assertEqual(symbols, {"JPM", "UNH"})

    def test_advisory_only_no_side_effects(self):
        positions = [
            {"symbol": "AAPL", "shares": 10, "current_price": 200.0},
        ]
        original_pos = [dict(p) for p in positions]
        get_rebalance_suggestions(positions, [{"symbol": "JPM", "price_at_scan": 180.0}])
        self.assertEqual(positions[0]["shares"], original_pos[0]["shares"])


if __name__ == "__main__":
    unittest.main()
