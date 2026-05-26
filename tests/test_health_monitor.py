"""
Sprint 7 Item 5 -- Health Monitor verification.
Tests health check logic, alert generation, and data feed quality metrics.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db, log_ops_event, get_ops_events
from prime_ops.prime_health_monitor import (
    SCANNERS,
    STALE_THRESHOLDS,
    check_scanner_health,
    check_data_feed_quality,
    generate_alerts,
)


class _DBTestCase(unittest.TestCase):
    """Base class that creates a temp DB for each test."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = Path(self._tmp.name)
        init_db(self.db_path)

    def tearDown(self):
        try:
            self.db_path.unlink()
        except OSError:
            pass


class TestScannerHealthNeverRun(_DBTestCase):

    def test_no_events_returns_never_run(self):
        health = check_scanner_health(db_path=self.db_path)
        for h in health:
            self.assertEqual(h["status"], "NEVER_RUN")


class TestScannerHealthAfterEvent(_DBTestCase):

    def test_scan_complete_is_healthy(self):
        log_ops_event("SCAN_COMPLETE", "psa_scanner", detail="signals=5", db_path=self.db_path)
        health = check_scanner_health(db_path=self.db_path)
        psa = next(h for h in health if h["scanner"] == "psa_scanner")
        self.assertEqual(psa["status"], "HEALTHY")

    def test_scan_start_is_running(self):
        log_ops_event("SCAN_START", "uoa_scanner", db_path=self.db_path)
        health = check_scanner_health(db_path=self.db_path)
        uoa = next(h for h in health if h["scanner"] == "uoa_scanner")
        self.assertEqual(uoa["status"], "RUNNING")

    def test_scan_error_is_error(self):
        log_ops_event("SCAN_ERROR", "mts_scanner", detail="API timeout", severity="ERROR", db_path=self.db_path)
        health = check_scanner_health(db_path=self.db_path)
        mts = next(h for h in health if h["scanner"] == "mts_scanner")
        self.assertEqual(mts["status"], "ERROR")


class TestAlertGeneration(_DBTestCase):

    def test_never_run_generates_warning(self):
        health = check_scanner_health(db_path=self.db_path)
        alerts = generate_alerts(health)
        self.assertGreater(len(alerts), 0)
        levels = {a["level"] for a in alerts}
        self.assertIn("WARNING", levels)

    def test_healthy_no_alerts(self):
        for scanner in SCANNERS:
            log_ops_event("SCAN_COMPLETE", scanner, detail="signals=0", db_path=self.db_path)
        health = check_scanner_health(db_path=self.db_path)
        alerts = generate_alerts(health)
        self.assertEqual(len(alerts), 0)

    def test_error_generates_critical(self):
        log_ops_event("SCAN_ERROR", "pead_scanner", severity="ERROR", db_path=self.db_path)
        health = check_scanner_health(db_path=self.db_path)
        alerts = generate_alerts(health)
        pead_alerts = [a for a in alerts if a["scanner"] == "pead_scanner"]
        self.assertEqual(len(pead_alerts), 1)
        self.assertEqual(pead_alerts[0]["level"], "CRITICAL")


class TestDataFeedQuality(_DBTestCase):

    def test_no_data(self):
        feed = check_data_feed_quality(db_path=self.db_path)
        self.assertEqual(feed["recent_scans"], 0)
        self.assertEqual(feed["signal_rate"], 0)

    def test_with_signals(self):
        log_ops_event("SCAN_COMPLETE", "psa_scanner", detail="signals=5", db_path=self.db_path)
        log_ops_event("SCAN_COMPLETE", "uoa_scanner", detail="signals=0", db_path=self.db_path)
        feed = check_data_feed_quality(db_path=self.db_path)
        self.assertEqual(feed["recent_scans"], 2)
        self.assertEqual(feed["scans_with_signals"], 1)
        self.assertEqual(feed["total_signals"], 5)
        self.assertAlmostEqual(feed["signal_rate"], 50.0)


class TestStaleThresholds(unittest.TestCase):

    def test_all_scanners_have_thresholds(self):
        for scanner in SCANNERS:
            self.assertIn(scanner, STALE_THRESHOLDS)

    def test_psa_shorter_threshold(self):
        self.assertLess(STALE_THRESHOLDS["psa_scanner"], STALE_THRESHOLDS["uoa_scanner"])


class TestModuleInterface(unittest.TestCase):

    def test_importable(self):
        from prime_ops import prime_health_monitor
        self.assertTrue(hasattr(prime_health_monitor, "main"))
        self.assertTrue(hasattr(prime_health_monitor, "check_scanner_health"))
        self.assertTrue(hasattr(prime_health_monitor, "generate_alerts"))
        self.assertTrue(hasattr(prime_health_monitor, "check_data_feed_quality"))

    def test_no_gui_imports(self):
        import prime_ops.prime_health_monitor as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("prime_gui", source)

    def test_no_direct_sqlite(self):
        import prime_ops.prime_health_monitor as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import sqlite3", source)


if __name__ == "__main__":
    unittest.main()
