"""
Sprint 29 SIG-01 acceptance tests.

Covers the dynamic tier filter backend: get_distinct_tiers() and the
GET /api/v1/tiers endpoint. Mirrors the strategy-filter approach (Sprint 22
Item 3c) so any tier present in prime_signals — e.g. WEAK-LONG — is filterable.
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
    get_distinct_tiers,
)


class TestDistinctTiers(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_tier_filter.db"
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
        self.assertEqual(get_distinct_tiers(db_path=self.db), [])

    def test_distinct_sorted_no_dupes(self):
        for tier in ("STRONG-LONG", "WEAK-LONG", "STRONG-LONG", "WATCH"):
            insert_signal(symbol="X", strategy="PSA", scan_ts="2026-06-02 10:00",
                          tier=tier, db_path=self.db)
        self.assertEqual(
            get_distinct_tiers(db_path=self.db),
            ["STRONG-LONG", "WATCH", "WEAK-LONG"],
        )

    def test_weak_long_is_present(self):
        """The reported missing tier (WEAK-LONG) must surface in the list."""
        insert_signal(symbol="GLD", strategy="MTS", scan_ts="2026-06-02 10:00",
                      tier="WEAK-LONG", db_path=self.db)
        self.assertIn("WEAK-LONG", get_distinct_tiers(db_path=self.db))

    def test_blank_tier_excluded(self):
        insert_signal(symbol="SPY", strategy="UOA", scan_ts="2026-06-02 10:00",
                      tier="", db_path=self.db)
        insert_signal(symbol="QQQ", strategy="UOA", scan_ts="2026-06-02 10:00",
                      tier="TRANCHE_1", db_path=self.db)
        self.assertEqual(get_distinct_tiers(db_path=self.db), ["TRANCHE_1"])

    def test_tiers_endpoint(self):
        insert_signal(symbol="SPY", strategy="UOA", scan_ts="2026-06-02 10:00",
                      tier="STRONG-LONG", db_path=self.db)
        insert_signal(symbol="GLD", strategy="MTS", scan_ts="2026-06-02 10:00",
                      tier="WEAK-LONG", db_path=self.db)
        resp = self.client.get("/api/v1/tiers")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["tiers"], ["STRONG-LONG", "WEAK-LONG"])
        self.assertEqual(data["count"], 2)


if __name__ == "__main__":
    unittest.main()
