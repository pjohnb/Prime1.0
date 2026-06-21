"""
Sprint 33 Thread 1 (CIL-037) -- Schwab credentials sourced from config.json.

Schwab API credentials (app key/secret, callback URL, token path) live in the
gitignored config.json `schwab_snapshot` section and are read from there by the
Schwab client and schwab_auth_v2.py -- NOT from environment variables. These
tests lock that contract in so a regression cannot silently reintroduce an
environment-variable dependency.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import SchwabSnapshotConfig, PrimeConfig


class TestSchwabCredentialsLoadedFromConfig(unittest.TestCase):

    def _cfg_with_schwab(self):
        cfg = MagicMock(spec=PrimeConfig)
        cfg.schwab_snapshot = SchwabSnapshotConfig(
            schwab_app_key="APPKEY123",
            schwab_app_secret="APPSECRET456",
            schwab_token_path="C:/tokens/schwab_token.json",
            schwab_callback_url="https://127.0.0.1",
        )
        return cfg

    def test_schwab_credentials_loaded_from_config(self):
        from prime_trading.prime_schwab import SchwabClient
        with patch("prime_trading.prime_schwab.get_config", return_value=self._cfg_with_schwab()):
            client = SchwabClient()
        self.assertEqual(client.app_key, "APPKEY123")
        self.assertEqual(client.app_secret, "APPSECRET456")
        self.assertEqual(client.token_path, "C:/tokens/schwab_token.json")
        self.assertEqual(client.callback_url, "https://127.0.0.1")

    def test_schwab_credential_fields_present_in_schema(self):
        # The dataclass carries all four credential fields (config-backed schema).
        fields = set(SchwabSnapshotConfig.__dataclass_fields__)
        for f in ("schwab_app_key", "schwab_app_secret",
                  "schwab_token_path", "schwab_callback_url"):
            self.assertIn(f, fields)

    def test_no_schwab_env_var_dependency(self):
        # SchwabClient must construct with credentials from config even when no
        # SCHWAB_* environment variables are set.
        import os
        from prime_trading.prime_schwab import SchwabClient
        schwab_env = {k: v for k, v in os.environ.items() if "SCHWAB" in k.upper()}
        try:
            for k in schwab_env:
                del os.environ[k]
            with patch("prime_trading.prime_schwab.get_config",
                       return_value=self._cfg_with_schwab()):
                client = SchwabClient()
            self.assertEqual(client.app_key, "APPKEY123")
        finally:
            os.environ.update(schwab_env)


if __name__ == "__main__":
    unittest.main()
