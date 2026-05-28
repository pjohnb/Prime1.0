"""
Sprint 12 Item 1 (UI-LOVABLE-001) acceptance tests -- Lovable UI Phase 1.
Covers UI server creation, API data binding, empty state handling.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_ui.prime_ui_server import create_ui_app
from prime_api.prime_api_server import create_app as create_api_app
from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table


class TestUIServer(unittest.TestCase):
    """AC: UI serves on port 5002."""

    def setUp(self):
        self.app = create_ui_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_index_returns_html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"PRIME v1.0", resp.data)

    def test_dashboard_js_served(self):
        resp = self.client.get("/dashboard.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"loadDashboard", resp.data)

    def test_positions_js_served(self):
        resp = self.client.get("/positions.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"loadPositions", resp.data)

    def test_signals_js_served(self):
        resp = self.client.get("/signals.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"loadSignals", resp.data)


class TestAPIDataBinding(unittest.TestCase):
    """AC: all data from Flask API on 5001; correct structure."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_lovable_ui.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._patcher.start()
        self.api = create_api_app()
        self.api.config["TESTING"] = True
        self.api_client = self.api.test_client()

    def tearDown(self):
        self._patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_positions_endpoint_structure(self):
        resp = self.api_client.get("/api/v1/positions")
        data = resp.get_json()
        self.assertIn("positions", data)
        self.assertIn("count", data)
        self.assertIsInstance(data["positions"], list)

    def test_signals_endpoint_structure(self):
        resp = self.api_client.get("/api/v1/signals")
        data = resp.get_json()
        self.assertIn("signals", data)
        self.assertIn("count", data)

    def test_analytics_summary_structure(self):
        resp = self.api_client.get("/api/v1/analytics/summary")
        data = resp.get_json()
        self.assertIn("strategies", data)
        self.assertIn("total_pnl", data)

    def test_health_endpoint(self):
        resp = self.api_client.get("/api/v1/health")
        data = resp.get_json()
        self.assertIn("status", data)

    def test_empty_positions_handled(self):
        resp = self.api_client.get("/api/v1/positions")
        data = resp.get_json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["positions"], [])

    def test_empty_signals_handled(self):
        resp = self.api_client.get("/api/v1/signals")
        data = resp.get_json()
        self.assertEqual(data["count"], 0)

    def test_ui_index_has_tab_navigation(self):
        ui_app = create_ui_app()
        ui_app.config["TESTING"] = True
        client = ui_app.test_client()
        resp = client.get("/")
        self.assertIn(b"Dashboard", resp.data)
        self.assertIn(b"Positions", resp.data)
        self.assertIn(b"Signals", resp.data)


if __name__ == "__main__":
    unittest.main()
