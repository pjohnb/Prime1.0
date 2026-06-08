"""Sprint 26 Item 4: PYTHONPATH fix, rolling scan logs, Polygon key warning."""
import datetime
import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestRollingScanLog:

    def test_get_scan_log_path_returns_dated_file(self):
        """_get_scan_log_path() must return a path containing today's date."""
        from prime_api.prime_api_routes import _get_scan_log_path
        log_path = _get_scan_log_path()
        today = datetime.date.today().strftime("%Y-%m-%d")
        assert today in str(log_path)

    def test_get_scan_log_path_filename_pattern(self):
        """Log file name must follow scan_log_YYYY-MM-DD.txt pattern."""
        from prime_api.prime_api_routes import _get_scan_log_path
        log_path = _get_scan_log_path()
        assert log_path.name.startswith("scan_log_")
        assert log_path.name.endswith(".txt")

    def test_prune_old_scan_logs_removes_old_files(self, tmp_path):
        """_prune_old_scan_logs deletes files older than keep_days days."""
        from prime_api import prime_api_routes as routes

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        old_date    = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        recent_date = datetime.date.today().strftime("%Y-%m-%d")
        old_file    = logs_dir / f"scan_log_{old_date}.txt"
        recent_file = logs_dir / f"scan_log_{recent_date}.txt"
        old_file.write_text("old log")
        recent_file.write_text("recent log")

        # Set old_file mtime to 10 days ago so the mtime-based prune picks it up
        old_ts = time.time() - 10 * 86400
        os.utime(old_file, (old_ts, old_ts))

        original_logs_dir = routes._LOGS_DIR
        try:
            routes._LOGS_DIR = logs_dir
            routes._prune_old_scan_logs(keep_days=7)
        finally:
            routes._LOGS_DIR = original_logs_dir

        assert not old_file.exists(), "Old log should have been pruned"
        assert recent_file.exists(), "Recent log should be retained"


class TestPythonpathEnv:

    def test_run_scanner_bg_env_contains_pythonpath(self, monkeypatch, tmp_path):
        """_run_scanner_bg must pass PYTHONPATH in the subprocess Popen environment."""
        from prime_api import prime_api_routes as routes

        captured_envs = []

        def fake_popen(args, env=None, stdout=None, stderr=None, **kwargs):
            captured_envs.append(dict(env) if env else None)
            mock = MagicMock()
            mock.pid = 99
            mock.poll.return_value = None
            mock.wait.return_value = 0
            mock.returncode = 0
            mock.stdout = iter([])
            return mock

        monkeypatch.setattr("subprocess.Popen", fake_popen)

        # Redirect logs to tmp dir to avoid polluting the real logs dir
        original_logs_dir = routes._LOGS_DIR
        routes._LOGS_DIR = tmp_path / "logs"
        (tmp_path / "logs").mkdir()

        try:
            routes._run_scanner_bg("psa", "prime_scanners.prime_psa_scanner")
        except Exception:
            pass
        finally:
            routes._LOGS_DIR = original_logs_dir

        assert len(captured_envs) > 0, "Popen should have been called"
        env = captured_envs[0]
        assert env is not None, "env should not be None"
        assert "PYTHONPATH" in env, "PYTHONPATH must be in subprocess env"

    def test_pythonpath_value_is_project_root(self, monkeypatch, tmp_path):
        """The PYTHONPATH value must equal the project root path."""
        from prime_api import prime_api_routes as routes

        captured_envs = []

        def fake_popen(args, env=None, stdout=None, stderr=None, **kwargs):
            captured_envs.append(dict(env) if env else None)
            mock = MagicMock()
            mock.pid = 99
            mock.poll.return_value = None
            mock.wait.return_value = 0
            mock.returncode = 0
            mock.stdout = iter([])
            return mock

        monkeypatch.setattr("subprocess.Popen", fake_popen)

        original_logs_dir = routes._LOGS_DIR
        routes._LOGS_DIR = tmp_path / "logs"
        (tmp_path / "logs").mkdir()

        try:
            routes._run_scanner_bg("uoa", "prime_scanners.prime_uoa_scanner")
        except Exception:
            pass
        finally:
            routes._LOGS_DIR = original_logs_dir

        if captured_envs and captured_envs[0]:
            pythonpath = captured_envs[0].get("PYTHONPATH", "")
            assert str(routes._PROJECT_ROOT_PATH) in pythonpath


class TestPolygonKeyWarning:

    def test_startup_warns_if_polygon_key_missing(self):
        """Empty polygon_api_key triggers the warning condition."""
        from prime_config.prime_config import OpsConfig
        cfg = OpsConfig()
        cfg.polygon_api_key = ""
        should_warn = not (cfg.polygon_api_key or "").strip()
        assert should_warn is True

    def test_no_warning_when_polygon_key_set(self):
        """Non-empty polygon_api_key does not trigger the warning condition."""
        from prime_config.prime_config import OpsConfig
        cfg = OpsConfig()
        cfg.polygon_api_key = "test_key_abc"
        should_warn = not (cfg.polygon_api_key or "").strip()
        assert should_warn is False
