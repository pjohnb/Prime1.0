"""
Sprint 7 Item 4 -- Scheduler + ops_config verification.
Tests task definition building, ops_config parsing, and module integrity.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_ops.prime_scheduler import (
    TASK_PREFIX,
    build_task_definitions,
)


class TestBuildTaskDefinitions(unittest.TestCase):

    def test_returns_list(self):
        tasks = build_task_definitions()
        self.assertIsInstance(tasks, list)

    def test_all_tasks_have_required_fields(self):
        tasks = build_task_definitions()
        for task in tasks:
            self.assertIn("task_name", task)
            self.assertIn("scanner", task)
            self.assertIn("script", task)
            self.assertIn("time_et", task)
            self.assertIn("days", task)
            self.assertIn("module", task)

    def test_task_names_prefixed(self):
        tasks = build_task_definitions()
        for task in tasks:
            self.assertTrue(
                task["task_name"].startswith(TASK_PREFIX),
                f"{task['task_name']} missing prefix {TASK_PREFIX}",
            )

    def test_scripts_exist(self):
        tasks = build_task_definitions()
        for task in tasks:
            self.assertTrue(
                Path(task["script"]).exists(),
                f"Script not found: {task['script']} for {task['scanner']}",
            )

    def test_psa_has_four_runs(self):
        tasks = build_task_definitions()
        psa_tasks = [t for t in tasks if t["scanner"] == "psa"]
        self.assertEqual(len(psa_tasks), 4, "PSA should have 4 daily runs")

    def test_midday_scanners(self):
        tasks = build_task_definitions()
        midday = [t for t in tasks if t["time_et"] == "12:40"]
        midday_scanners = {t["scanner"] for t in midday}
        for expected in ("uoa", "pead", "srs", "idx"):
            self.assertIn(expected, midday_scanners, f"{expected} should run at 12:40")

    def test_mts_runs_after_close(self):
        tasks = build_task_definitions()
        mts_tasks = [t for t in tasks if t["scanner"] == "mts"]
        self.assertEqual(len(mts_tasks), 1)
        self.assertEqual(mts_tasks[0]["time_et"], "16:32")

    def test_all_weekdays(self):
        tasks = build_task_definitions()
        for task in tasks:
            self.assertEqual(task["days"], "weekdays")


class TestOpsConfig(unittest.TestCase):

    def test_ops_config_exists(self):
        path = PROJECT_ROOT / "ops_config.json"
        self.assertTrue(path.exists())

    def test_ops_config_parseable(self):
        path = PROJECT_ROOT / "ops_config.json"
        with open(path) as f:
            data = json.load(f)
        self.assertIn("scan_schedule", data)
        self.assertIn("health_check_interval", data)
        self.assertIn("retry", data)

    def test_scan_schedule_has_all_scanners(self):
        path = PROJECT_ROOT / "ops_config.json"
        with open(path) as f:
            data = json.load(f)
        schedule = data["scan_schedule"]
        for scanner in ("psa", "uoa", "pead", "srs", "mts", "idx"):
            self.assertIn(scanner, schedule, f"{scanner} missing from scan_schedule")

    def test_health_check_interval_numeric(self):
        path = PROJECT_ROOT / "ops_config.json"
        with open(path) as f:
            data = json.load(f)
        self.assertIsInstance(data["health_check_interval"], int)
        self.assertEqual(data["health_check_interval"], 900)

    def test_retry_config(self):
        path = PROJECT_ROOT / "ops_config.json"
        with open(path) as f:
            data = json.load(f)
        retry = data["retry"]
        self.assertEqual(retry["max_attempts"], 3)
        self.assertEqual(retry["interval_seconds"], 300)


class TestModuleInterface(unittest.TestCase):

    def test_importable(self):
        from prime_ops import prime_scheduler
        self.assertTrue(hasattr(prime_scheduler, "main"))
        self.assertTrue(hasattr(prime_scheduler, "build_task_definitions"))
        self.assertTrue(hasattr(prime_scheduler, "cmd_register"))
        self.assertTrue(hasattr(prime_scheduler, "cmd_status"))

    def test_no_gui_imports(self):
        import prime_ops.prime_scheduler as mod
        source = Path(mod.__file__).read_text()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("prime_gui", source)


if __name__ == "__main__":
    unittest.main()
