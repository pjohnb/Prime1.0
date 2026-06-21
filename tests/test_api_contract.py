"""
Sprint 11 Item 3 (UI-CONTRACT-001) acceptance tests -- API Contract Layer.
Covers all endpoints return 200 + valid JSON, health reflects DB state,
no direct SQL in routes, OpenAPI spec valid.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_api.prime_api_server import create_app
from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table


def _mock_config(db_path):
    """Create a mock config that points to the test DB."""
    cfg = MagicMock()
    cfg.db_path = db_path
    return cfg


class TestApiEndpoints(unittest.TestCase):
    """AC: each endpoint returns 200 and valid JSON structure."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_api.db"
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

    def test_root_returns_200(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["name"], "PRIME API")

    def test_positions_returns_200(self):
        resp = self.client.get("/api/v1/positions")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("positions", data)
        self.assertIn("count", data)

    def test_signals_returns_200(self):
        resp = self.client.get("/api/v1/signals")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("signals", data)
        self.assertIn("count", data)

    def test_signals_filter_by_strategy(self):
        resp = self.client.get("/api/v1/signals?strategy=UOA")
        self.assertEqual(resp.status_code, 200)

    def test_analytics_effectiveness_returns_200(self):
        # CIL-063: effectiveness endpoint returns grouping structure.
        resp = self.client.get("/api/v1/analytics/effectiveness")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("by_strategy", data)
        self.assertIn("overall", data)
        self.assertIn("as_of", data)

    def test_analytics_summary_returns_200(self):
        resp = self.client.get("/api/v1/analytics/summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("strategies", data)

    def test_analytics_by_strategy_returns_200(self):
        resp = self.client.get("/api/v1/analytics/by-strategy?strategy=UOA")
        self.assertEqual(resp.status_code, 200)

    def test_health_returns_200(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("status", data)
        self.assertIn("db_connected", data)


class TestNoDirectSQL(unittest.TestCase):
    """AC: no direct SQL calls in prime_api_routes.py."""

    def test_no_sqlite_import(self):
        source = Path(PROJECT_ROOT / "prime_api" / "prime_api_routes.py").read_text()
        self.assertNotIn("import sqlite3", source)
        self.assertNotIn("conn.execute", source)
        self.assertNotIn("cursor.execute", source)


class TestOpenApiSpec(unittest.TestCase):
    """AC: OpenAPI spec parseable and valid."""

    def test_spec_parseable(self):
        spec_path = PROJECT_ROOT / "prime_api" / "openapi_spec.yaml"
        self.assertTrue(spec_path.exists())
        with open(spec_path) as f:
            spec = yaml.safe_load(f)
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertIn("paths", spec)

    def test_spec_has_all_endpoints(self):
        spec_path = PROJECT_ROOT / "prime_api" / "openapi_spec.yaml"
        with open(spec_path) as f:
            spec = yaml.safe_load(f)
        paths = spec["paths"]
        self.assertIn("/api/v1/positions", paths)
        self.assertIn("/api/v1/signals", paths)
        self.assertIn("/api/v1/analytics/summary", paths)
        self.assertIn("/api/v1/analytics/by-strategy", paths)
        self.assertIn("/api/v1/health", paths)


if __name__ == "__main__":
    unittest.main()
