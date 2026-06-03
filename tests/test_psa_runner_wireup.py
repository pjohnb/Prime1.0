"""
Sprint 16 Item 2 (PSA runner wire-up) acceptance tests.

Verifies the use_ai_ranker toggle and overflow routing in
prime_signal_ranker.select_for_execution:
  * use_ai_ranker=True  + overflow   -> select_top_n() called, path=ai_ranker
  * use_ai_ranker=False              -> select_entries() (no AI), path=score_sort
  * use_ai_ranker=True  + no overflow-> select_entries() (no API), path=score_sort
  * ops_health log entry present after each path
  * toggle is read fresh at runtime (no restart needed)
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

APPROVED = [
    {"symbol": "AAA", "strategy": "PSA", "score": 5.0, "sector": "Tech"},
    {"symbol": "BBB", "strategy": "PSA", "score": 9.0, "sector": "Tech"},
    {"symbol": "CCC", "strategy": "PSA", "score": 7.0, "sector": "Energy"},
    {"symbol": "DDD", "strategy": "PSA", "score": 8.0, "sector": "Health"},
]


class TestSelectForExecution(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_psa_wireup.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self.cfg = Path(__file__).parent / "_test_psa_ops_config.json"

    def tearDown(self):
        for p in (self.db, self.cfg):
            if p.exists():
                p.unlink()

    def _write_cfg(self, use_ai: bool):
        self.cfg.write_text(json.dumps({"use_ai_ranker": use_ai, "max_trades": 2}),
                            encoding="utf-8")

    @patch("prime_ai.prime_signal_ranker.select_top_n")
    def test_ai_ranker_on_overflow_calls_select_top_n(self, mock_topn):
        mock_topn.return_value = APPROVED[:2]
        self._write_cfg(True)
        ranker.select_for_execution(APPROVED, max_trades=2,
                                    db_path=self.db, config_path=self.cfg)
        mock_topn.assert_called_once()
        events = get_ops_events(component="psa_runner", db_path=self.db)
        self.assertTrue(events)
        self.assertIn("path=ai_ranker", events[0]["detail"])

    @patch("prime_ai.prime_signal_ranker.select_top_n")
    def test_toggle_off_calls_select_entries(self, mock_topn):
        self._write_cfg(False)
        out = ranker.select_for_execution(APPROVED, max_trades=2,
                                          db_path=self.db, config_path=self.cfg)
        mock_topn.assert_not_called()
        self.assertEqual(len(out), 2)  # deterministic top-2 by score
        events = get_ops_events(component="psa_runner", db_path=self.db)
        self.assertIn("path=score_sort", events[0]["detail"])

    @patch("prime_ai.prime_signal_ranker.select_top_n")
    def test_no_overflow_uses_select_entries_no_api(self, mock_topn):
        self._write_cfg(True)  # AI on, but no overflow -> no API cost
        out = ranker.select_for_execution(APPROVED, max_trades=10,
                                          db_path=self.db, config_path=self.cfg)
        mock_topn.assert_not_called()
        self.assertEqual(len(out), 4)
        events = get_ops_events(component="psa_runner", db_path=self.db)
        self.assertIn("path=score_sort", events[0]["detail"])

    def test_toggle_read_fresh_at_runtime(self):
        # Flipping the config flips the path with no module reload / restart.
        self._write_cfg(False)
        self.assertFalse(ranker._read_use_ai_ranker(self.cfg))
        self._write_cfg(True)
        self.assertTrue(ranker._read_use_ai_ranker(self.cfg))

    def test_default_true_when_key_absent(self):
        self.cfg.write_text(json.dumps({"max_trades": 2}), encoding="utf-8")
        self.assertTrue(ranker._read_use_ai_ranker(self.cfg))


if __name__ == "__main__":
    unittest.main()
