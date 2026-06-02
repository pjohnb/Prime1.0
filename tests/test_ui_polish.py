"""
Sprint 14 Item 3 (UI Polish) acceptance tests.

Covers the dynamic strategy filter backend: get_distinct_strategies() and the
GET /api/v1/strategies endpoint. (Score "--", DK badge colors, and 60s
auto-refresh are client-side rendering changes in signals.js / index.html.)
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_api.prime_api_server import create_app
from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import (
    init_signals_table,
    insert_signal,
    get_distinct_strategies,
)


class TestDistinctStrategies(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_ui_polish.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._patcher.start()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_empty_returns_empty_list(self):
        self.assertEqual(get_distinct_strategies(db_path=self.db), [])

    def test_distinct_sorted_no_dupes(self):
        for strat in ("UOA", "PEAD", "UOA", "MTS"):
            insert_signal(symbol="X", strategy=strat, scan_ts="2026-06-02 10:00",
                          db_path=self.db)
        self.assertEqual(get_distinct_strategies(db_path=self.db), ["MTS", "PEAD", "UOA"])

    def test_strategies_endpoint(self):
        insert_signal(symbol="SPY", strategy="UOA", scan_ts="2026-06-02 10:00",
                      db_path=self.db)
        insert_signal(symbol="GLD", strategy="MTS", scan_ts="2026-06-02 10:00",
                      db_path=self.db)
        resp = self.client.get("/api/v1/strategies")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["strategies"], ["MTS", "UOA"])
        self.assertEqual(data["count"], 2)


if __name__ == "__main__":
    unittest.main()
