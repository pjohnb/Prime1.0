"""
Sprint 33 Thread 1 (CIL-070) -- Polygon graceful-degradation acceptance tests.

Polygon is a soft dependency. When polygon_api_key is empty/absent:
  * the PSA/SRS/IDX scanners log a WARNING and return empty (well-formed)
    results rather than raising or calling sys.exit(1);
  * load_config() does not raise (polygon_api_key removed from REQUIRED_CONFIG_KEYS),
    so the API server can start and APScheduler can register its jobs.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_psa_scanner import run_psa_scan
from prime_scanners.prime_srs_scanner import run_srs_scan
from prime_intelligence.prime_index_scanner import run_index_scan as run_idx_scan
from prime_config.prime_config import load_config, ConfigError, REQUIRED_CONFIG_KEYS


class TestScannerGracefulDegradation(unittest.TestCase):
    """PSA/SRS/IDX return empty results (no raise) when Polygon is unavailable."""

    def test_psa_scanner_graceful_degradation_no_polygon(self):
        result = run_psa_scan(api_key="")
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["signals_found"], 0)
        self.assertTrue(result.get("polygon_unavailable"))

    def test_psa_scanner_graceful_degradation_none_key(self):
        # None key must also degrade, not raise.
        result = run_psa_scan(api_key=None)
        self.assertEqual(result["signals"], [])

    def test_srs_scanner_graceful_degradation_no_polygon(self):
        result = run_srs_scan(api_key="")
        self.assertEqual(result["sectors"], {})
        self.assertEqual(result["regime"], "MIXED")
        self.assertTrue(result.get("polygon_unavailable"))

    def test_idx_scanner_graceful_degradation_no_polygon(self):
        # Live-fetch path (no injected bars) with empty key must degrade.
        result = run_idx_scan(api_key="", db_path=None)
        self.assertEqual(result["written"], [])
        self.assertEqual(result["scanned"], 0)
        self.assertTrue(result.get("polygon_unavailable"))


class TestServerStartsWithoutPolygonKey(unittest.TestCase):
    """load_config() must not raise when polygon_api_key is absent (CIL-070)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg_path = self.tmp / "config.json"
        self.ops_path = self.tmp / "ops_config.json"
        # config.json WITHOUT polygon_api_key — the regression this test guards.
        self.cfg_path.write_text(json.dumps({
            "finnhub_api_key": "fh-test",
            "tradestation": {},
            "schwab_snapshot": {},
            "execution": {},
            "risk_management": {},
        }), encoding="utf-8")
        self.ops_path.write_text(json.dumps({
            "scan_schedule": {},
            "notification_channels": "TBD",
            "health_check_interval": 900,
        }), encoding="utf-8")

    def test_polygon_key_not_required(self):
        self.assertNotIn("polygon_api_key", REQUIRED_CONFIG_KEYS)

    def test_load_config_without_polygon_does_not_raise(self):
        try:
            cfg = load_config(config_path=self.cfg_path, ops_config_path=self.ops_path)
        except ConfigError as e:
            self.fail(f"load_config raised ConfigError without polygon_api_key: {e}")
        self.assertEqual(cfg.polygon_api_key, "")

    def test_server_app_creates_without_polygon(self):
        # The Flask app (and therefore APScheduler registration) must construct
        # even when Polygon is unavailable.
        from prime_api.prime_api_server import create_app
        app = create_app()
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
