"""
Item 0 acceptance tests — scaffold verification.
All ACs must pass before any feature work begins.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestDirectoryStructure(unittest.TestCase):
    """AC 0.1 — All required directories exist."""

    REQUIRED_DIRS = [
        "prime_scanners",
        "prime_intelligence",
        "prime_data",
        "prime_notifications",
        "prime_ops",
        "prime_trading",
        "prime_gui",
        "prime_config",
        "tests",
        "data",
        "scan_results",
        "logs",
    ]

    def test_all_directories_exist(self):
        for d in self.REQUIRED_DIRS:
            path = PROJECT_ROOT / d
            self.assertTrue(path.is_dir(), f"Missing directory: {d}")


class TestInitFiles(unittest.TestCase):
    """AC 0.2 — All code module directories contain __init__.py."""

    CODE_MODULES = [
        "prime_scanners",
        "prime_intelligence",
        "prime_data",
        "prime_notifications",
        "prime_ops",
        "prime_trading",
        "prime_gui",
        "prime_config",
    ]

    def test_all_init_files_present(self):
        for mod in self.CODE_MODULES:
            init = PROJECT_ROOT / mod / "__init__.py"
            self.assertTrue(init.exists(), f"Missing __init__.py in {mod}/")


class TestConfig(unittest.TestCase):
    """AC 0.3 — prime_config.py loads config.json without error."""

    def test_load_config_from_project_root(self):
        from prime_config.prime_config import load_config

        cfg = load_config()
        self.assertIsNotNone(cfg)
        self.assertIsNotNone(cfg.execution)
        self.assertIsNotNone(cfg.risk_management)
        self.assertIsNotNone(cfg.tradestation)
        self.assertIsNotNone(cfg.schwab_snapshot)
        self.assertIsNotNone(cfg.ops)

    def test_derived_paths(self):
        from prime_config.prime_config import load_config

        cfg = load_config()
        self.assertEqual(cfg.project_root, PROJECT_ROOT)
        self.assertEqual(cfg.db_path, PROJECT_ROOT / "data" / "prime_trades.db")

    def test_missing_config_raises(self):
        from prime_config.prime_config import ConfigError, load_config

        bogus = Path(tempfile.mkdtemp()) / "nonexistent.json"
        with self.assertRaises(ConfigError):
            load_config(config_path=bogus)

    def test_missing_required_key_raises(self):
        from prime_config.prime_config import ConfigError, load_config

        tmp = Path(tempfile.mkdtemp())
        bad_cfg = tmp / "config.json"
        ops_cfg = tmp / "ops_config.json"
        bad_cfg.write_text(json.dumps({"polygon_api_key": "x"}))
        ops_cfg.write_text(json.dumps({
            "scan_schedule": "TBD",
            "notification_channels": "TBD",
            "health_check_interval": "TBD",
        }))
        with self.assertRaises(ConfigError) as ctx:
            load_config(config_path=bad_cfg, ops_config_path=ops_cfg)
        self.assertIn("missing required keys", str(ctx.exception))


class TestDatabase(unittest.TestCase):
    """AC 0.4 — prime_db.py initializes DB with both required tables."""

    def test_init_creates_tables(self):
        from prime_data.prime_db import init_db, table_exists, get_table_columns

        tmp = Path(tempfile.mkdtemp()) / "test_prime.db"
        init_db(db_path=tmp)

        self.assertTrue(tmp.exists(), "Database file not created")
        self.assertTrue(table_exists("prime_trade_log", db_path=tmp))
        self.assertTrue(table_exists("prime_ops_health", db_path=tmp))

        trade_cols = get_table_columns("prime_trade_log", db_path=tmp)
        self.assertIn("log_id", trade_cols)
        self.assertIn("strategy", trade_cols)
        self.assertIn("symbol", trade_cols)
        self.assertIn("price_at_scan", trade_cols)
        self.assertIn("trade_factors", trade_cols)
        self.assertIn("dark_pool_eval", trade_cols)
        self.assertIn("status", trade_cols)

        health_cols = get_table_columns("prime_ops_health", db_path=tmp)
        self.assertIn("event_type", health_cols)
        self.assertIn("component", health_cols)
        self.assertIn("severity", health_cols)

    def test_init_is_idempotent(self):
        from prime_data.prime_db import init_db, table_exists

        tmp = Path(tempfile.mkdtemp()) / "test_prime2.db"
        init_db(db_path=tmp)
        init_db(db_path=tmp)
        self.assertTrue(table_exists("prime_trade_log", db_path=tmp))


class TestGitIgnore(unittest.TestCase):
    """AC 0.6 — .gitignore excludes config.json and ops_config.json."""

    def test_gitignore_contents(self):
        gitignore = PROJECT_ROOT / ".gitignore"
        self.assertTrue(gitignore.exists())
        content = gitignore.read_text()
        self.assertIn("config.json", content)
        self.assertIn("ops_config.json", content)
        self.assertIn("__pycache__/", content)
        self.assertIn("*.pyc", content)
        self.assertIn("data/", content)
        self.assertIn("logs/", content)


if __name__ == "__main__":
    unittest.main()
