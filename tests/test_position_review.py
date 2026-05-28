"""
Sprint 13 Item 1 acceptance tests -- Open Position Review.
Covers MSFT and TJX scenarios per memory: MSFT -2.1% underwater,
TJX broke below SMA on 5/26.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import (
    get_open_positions,
    get_trade,
    init_db,
    insert_trade,
)
from prime_intelligence.prime_position_review import (
    DEFAULT_STOP_PCT,
    DEFAULT_TARGET_PCT,
    evaluate_position,
    review_positions,
)


class TestEvaluatePosition(unittest.TestCase):

    def test_long_within_bounds_keep(self):
        pos = {"symbol": "AAPL", "entry_price": 185.0, "direction": "LONG", "shares": 100}
        r = evaluate_position(pos, current_price=187.0)
        self.assertEqual(r["decision"], "KEEP")

    def test_long_stop_hit_close(self):
        pos = {"symbol": "AAPL", "entry_price": 185.0, "direction": "LONG", "shares": 100}
        r = evaluate_position(pos, current_price=170.0)
        self.assertEqual(r["decision"], "CLOSE")
        self.assertEqual(r["action"], "CLOSE_STOP")

    def test_long_target_hit_close(self):
        pos = {"symbol": "AAPL", "entry_price": 185.0, "direction": "LONG", "shares": 100}
        r = evaluate_position(pos, current_price=200.0)
        self.assertEqual(r["decision"], "CLOSE")
        self.assertEqual(r["action"], "CLOSE_TARGET")

    def test_short_within_bounds(self):
        pos = {"symbol": "AAPL", "entry_price": 185.0, "direction": "SHORT", "shares": 100}
        r = evaluate_position(pos, current_price=183.0)
        self.assertEqual(r["decision"], "KEEP")

    def test_short_stop_hit(self):
        pos = {"symbol": "AAPL", "entry_price": 185.0, "direction": "SHORT", "shares": 100}
        r = evaluate_position(pos, current_price=200.0)
        self.assertEqual(r["decision"], "CLOSE")

    def test_sma_break_flags_thesis(self):
        pos = {"symbol": "TJX", "entry_price": 158.33, "direction": "LONG", "shares": 20}
        r = evaluate_position(pos, current_price=158.97, sma_20=165.0)
        self.assertEqual(r["decision"], "FLAG")
        self.assertIn("SMA20", r["reason"])

    def test_no_entry_price_flags(self):
        pos = {"symbol": "AAPL", "direction": "LONG"}
        r = evaluate_position(pos, current_price=185.0)
        self.assertEqual(r["decision"], "FLAG")

    def test_msft_underwater_scenario(self):
        # MSFT entry $424.345, current $415.23, -2.1% per memory
        pos = {"symbol": "MSFT", "entry_price": 424.345, "direction": "LONG", "shares": 36}
        r = evaluate_position(pos, current_price=415.23)
        self.assertEqual(r["decision"], "KEEP")
        self.assertLess(r["pnl_pct"], 0)
        self.assertGreater(r["pnl_pct"], -DEFAULT_STOP_PCT)

    def test_tjx_sma_break_scenario(self):
        # TJX entry $158.33, current $158.97, broke below SMA20 per memory
        pos = {"symbol": "TJX", "entry_price": 158.33, "direction": "LONG", "shares": 20}
        r = evaluate_position(pos, current_price=158.97, sma_20=164.0)
        self.assertEqual(r["decision"], "FLAG")


class TestReviewPositions(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_pos_review.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _insert(self, symbol, entry_price, shares):
        return insert_trade(
            strategy="UOA", symbol=symbol, direction="LONG",
            mode="PAPER", order_type="MARKET", shares=shares,
            entry_time=f"2026-05-01T10:00:00", price_at_scan=entry_price,
            entry_price=entry_price, db_path=self.db,
        )

    def test_review_with_no_closes(self):
        self._insert("AAPL", 185.0, 100)
        positions = get_open_positions(db_path=self.db)
        result = review_positions(positions, {"AAPL": {"current_price": 187.0}},
                                  db_path=self.db, apply_closes=False)
        self.assertEqual(result["total_reviewed"], 1)
        self.assertEqual(result["kept"], 1)

    def test_review_applies_close(self):
        lid = self._insert("AAPL", 185.0, 100)
        positions = get_open_positions(db_path=self.db)
        result = review_positions(positions, {"AAPL": {"current_price": 170.0}},
                                  db_path=self.db, apply_closes=True)
        self.assertEqual(result["closed"], 1)
        trade = get_trade(lid, db_path=self.db)
        self.assertEqual(trade["status"], "CLOSED")

    def test_review_msft_tjx_paper_scenario(self):
        # Memory: MSFT -2.1%, TJX broke below SMA
        self._insert("MSFT", 424.345, 36)
        self._insert("TJX", 158.33, 20)
        positions = get_open_positions(db_path=self.db)
        price_data = {
            "MSFT": {"current_price": 415.23, "sma_20": 420.0},
            "TJX": {"current_price": 158.97, "sma_20": 164.0},
        }
        result = review_positions(positions, price_data, db_path=self.db,
                                  apply_closes=False)
        self.assertEqual(result["total_reviewed"], 2)
        # MSFT should KEEP (within bounds), TJX should FLAG (SMA break)
        decisions = {r["symbol"]: r["decision"] for r in result["reviews"]}
        self.assertEqual(decisions["MSFT"], "KEEP")
        self.assertEqual(decisions["TJX"], "FLAG")

    def test_missing_price_flags(self):
        self._insert("AAPL", 185.0, 100)
        positions = get_open_positions(db_path=self.db)
        result = review_positions(positions, {}, db_path=self.db, apply_closes=False)
        self.assertEqual(result["flagged"], 1)

    def test_apply_closes_false_does_not_modify(self):
        lid = self._insert("AAPL", 185.0, 100)
        positions = get_open_positions(db_path=self.db)
        review_positions(positions, {"AAPL": {"current_price": 170.0}},
                         db_path=self.db, apply_closes=False)
        trade = get_trade(lid, db_path=self.db)
        self.assertEqual(trade["status"], "OPEN")


if __name__ == "__main__":
    unittest.main()
