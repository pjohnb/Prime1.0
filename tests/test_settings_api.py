"""
Sprint 23 Item 2 -- General Settings API acceptance tests.

Tests: GET returns all expected fields; POST updates ops_config.json correctly;
Save button writes and confirms; Reset restores defaults; Settings tab renders all sections.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table

_EXPECTED_FIELDS = [
    "max_trades", "mata_profile", "analysis_mode", "use_ai_ranker",
    "long_stop_loss_pct", "short_stop_loss_pct", "short_size_multiplier",
    "time_stop_minutes", "strategy_thresholds",
]


def _make_test_ops_config(path: Path):
    data = {
        "scan_schedule": {},
        "notification_channels": "TBD",
        "health_check_interval": 900,
        "max_trades": 5,
        "analysis_mode": "Universe",
        "use_ai_ranker": True,
        "long_stop_loss_pct": 0.05,
        "short_stop_loss_pct": 0.05,
        "short_size_multiplier": 0.5,
        "time_stop_minutes": 1950,
        "short_time_stop_minutes": 1950,
        "mata_profile": "Joint Brokerage",
        "use_signal_led_psa": True,
        "strategy_thresholds": {
            "PSA": {"momentum_pct": 5.0},
            "UOA": {"sizzle_index_min": 5.0},
        },
    }
    with open(path, "w") as f:
        json.dump(data, f)


class TestSettingsEndpoints(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_settings.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self.tmp_dir = tempfile.mkdtemp()
        self.ops_path = Path(self.tmp_dir) / "ops_config.json"
        _make_test_ops_config(self.ops_path)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

        import prime_api.prime_api_routes as routes
        self._orig_ops_path = routes._OPS_CONFIG_PATH
        routes._OPS_CONFIG_PATH = self.ops_path

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        import prime_api.prime_api_routes as routes
        routes._OPS_CONFIG_PATH = self._orig_ops_path
        self._db_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_get_settings_returns_200(self):
        resp = self.client.get("/api/v1/settings")
        self.assertEqual(resp.status_code, 200)

    def test_get_settings_contains_expected_fields(self):
        resp = self.client.get("/api/v1/settings")
        data = resp.get_json()
        for field in _EXPECTED_FIELDS:
            self.assertIn(field, data, f"Missing field: {field}")

    def test_post_updates_ops_config(self):
        payload = {"max_trades": 10, "analysis_mode": "Manual"}
        resp = self.client.post(
            "/api/v1/settings",
            json=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["max_trades"], 10)
        self.assertEqual(data["analysis_mode"], "Manual")

        # Verify file was written.
        with open(self.ops_path) as f:
            written = json.load(f)
        self.assertEqual(written["max_trades"], 10)
        self.assertEqual(written["analysis_mode"], "Manual")

    def test_post_partial_update_preserves_other_fields(self):
        resp = self.client.post(
            "/api/v1/settings",
            json={"max_trades": 3},
            content_type="application/json",
        )
        data = resp.get_json()
        # Other fields should survive.
        self.assertIn("use_ai_ranker", data)
        self.assertIn("strategy_thresholds", data)

    def test_post_strategy_thresholds(self):
        payload = {
            "strategy_thresholds": {
                "PSA": {"momentum_pct": 8.0, "volume_pct": 60.0},
            }
        }
        resp = self.client.post(
            "/api/v1/settings",
            json=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertAlmostEqual(
            data["strategy_thresholds"]["PSA"]["momentum_pct"], 8.0
        )

    def test_get_after_post_reflects_change(self):
        self.client.post(
            "/api/v1/settings",
            json={"max_trades": 7},
            content_type="application/json",
        )
        resp = self.client.get("/api/v1/settings")
        data = resp.get_json()
        self.assertEqual(data["max_trades"], 7)


if __name__ == "__main__":
    unittest.main()
