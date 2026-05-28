"""
Sprint 12 Item 4 (ML-19) acceptance tests -- Smart Entry Selection.
Covers top-N by score, tie-break alphabetically, alpha selection absent.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_smart_selector import select_entries, _get_score


class TestSmartSelector(unittest.TestCase):

    def test_top_3_by_score(self):
        approved = [
            {"symbol": "AAPL", "score": 7.0},
            {"symbol": "MSFT", "score": 9.0},
            {"symbol": "NVDA", "score": 8.0},
            {"symbol": "JPM", "score": 6.0},
            {"symbol": "UNH", "score": 5.0},
        ]
        selected = select_entries(approved, max_trades=3)
        self.assertEqual(len(selected), 3)
        symbols = [s["symbol"] for s in selected]
        self.assertEqual(symbols, ["MSFT", "NVDA", "AAPL"])

    def test_tie_break_alphabetical(self):
        approved = [
            {"symbol": "MSFT", "score": 8.0},
            {"symbol": "AAPL", "score": 8.0},
            {"symbol": "NVDA", "score": 8.0},
        ]
        selected = select_entries(approved, max_trades=2)
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["symbol"], "AAPL")
        self.assertEqual(selected[1]["symbol"], "MSFT")

    def test_fewer_than_max_trades_returns_all(self):
        approved = [
            {"symbol": "AAPL", "score": 7.0},
            {"symbol": "MSFT", "score": 9.0},
        ]
        selected = select_entries(approved, max_trades=5)
        self.assertEqual(len(selected), 2)

    def test_empty_approved_returns_empty(self):
        self.assertEqual(select_entries([], max_trades=5), [])

    def test_no_alpha_selection(self):
        approved = [
            {"symbol": "ZZZ", "score": 10.0},
            {"symbol": "AAA", "score": 5.0},
            {"symbol": "MMM", "score": 8.0},
        ]
        selected = select_entries(approved, max_trades=2)
        self.assertEqual(selected[0]["symbol"], "ZZZ")
        self.assertEqual(selected[1]["symbol"], "MMM")

    def test_composite_score_field_fallback(self):
        approved = [
            {"symbol": "AAPL", "composite_score": 9.0},
            {"symbol": "MSFT", "signal_score": 7.0},
            {"symbol": "NVDA", "score": 8.0},
        ]
        selected = select_entries(approved, max_trades=2)
        self.assertEqual(selected[0]["symbol"], "AAPL")

    def test_get_score_fallback(self):
        self.assertEqual(_get_score({"composite_score": 9.0}), 9.0)
        self.assertEqual(_get_score({"score": 7.0}), 7.0)
        self.assertEqual(_get_score({"signal_score": 5.0}), 5.0)
        self.assertEqual(_get_score({}), 0.0)

    def test_max_trades_exact_boundary(self):
        approved = [{"symbol": f"S{i}", "score": float(i)} for i in range(5)]
        selected = select_entries(approved, max_trades=5)
        self.assertEqual(len(selected), 5)


if __name__ == "__main__":
    unittest.main()
