"""
Sprint 16 Item 1 (ANTHROPIC_API_KEY startup routine) acceptance tests.

Verifies the three resolution scenarios for prime_startup.ensure_anthropic_api_key:
  1. key present in env            -> passes silently, source="env"
  2. key missing from env, in cfg  -> loads into env, warns, source="ops_config"
  3. key missing from both         -> warns clearly, does not crash, present=False
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import prime_startup

ENV_VAR = "ANTHROPIC_API_KEY"


class TestEnsureAnthropicApiKey(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get(ENV_VAR)
        os.environ.pop(ENV_VAR, None)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        os.environ.pop(ENV_VAR, None)
        if self._saved is not None:
            os.environ[ENV_VAR] = self._saved

    def _write_ops_config(self, payload: dict) -> Path:
        p = self.tmp / "ops_config.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_key_present_in_env_passes_silently(self):
        os.environ[ENV_VAR] = "sk-env-key"
        cfg = self._write_ops_config({"anthropic_api_key": "sk-config-key"})
        result = prime_startup.ensure_anthropic_api_key(ops_config_path=cfg)
        self.assertTrue(result["present"])
        self.assertEqual(result["source"], "env")
        # env value is authoritative -- config never overrides it
        self.assertEqual(os.environ[ENV_VAR], "sk-env-key")

    def test_key_missing_env_present_in_config_loads_and_warns(self):
        cfg = self._write_ops_config({"anthropic_api_key": "sk-config-key"})
        result = prime_startup.ensure_anthropic_api_key(ops_config_path=cfg)
        self.assertTrue(result["present"])
        self.assertEqual(result["source"], "ops_config")
        # config value is loaded into the environment for downstream AI calls
        self.assertEqual(os.environ[ENV_VAR], "sk-config-key")

    def test_key_missing_from_both_warns_does_not_crash(self):
        cfg = self._write_ops_config({"anthropic_api_key": ""})
        result = prime_startup.ensure_anthropic_api_key(ops_config_path=cfg)
        self.assertFalse(result["present"])
        self.assertIsNone(result["source"])
        self.assertNotIn(ENV_VAR, os.environ)

    def test_missing_ops_config_file_does_not_crash(self):
        result = prime_startup.ensure_anthropic_api_key(
            ops_config_path=self.tmp / "nonexistent.json")
        self.assertFalse(result["present"])
        self.assertIsNone(result["source"])

    def test_run_startup_checks_returns_summary(self):
        cfg = self._write_ops_config({"anthropic_api_key": "sk-config-key"})
        summary = prime_startup.run_startup_checks(ops_config_path=cfg)
        self.assertIn("anthropic_api_key", summary)
        self.assertTrue(summary["anthropic_api_key"]["present"])


if __name__ == "__main__":
    unittest.main()
