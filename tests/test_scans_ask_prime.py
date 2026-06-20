"""
Sprint 29 UI-AskPrime-01 -- /advisory/scan-explain endpoint acceptance tests.

Tests: endpoint returns 200; explanation is a non-empty string; graceful
degradation when API key is absent; unknown scanner uses generic template.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table


def _mock_config():
    cfg = MagicMock()
    cfg.trading_mode = "PAPER"
    cfg.api_token = "test-token-abc123"
    cfg.ops.anthropic_api_key = ""
    cfg.ops.max_position_pct = 0.15
    cfg.ops.max_sector_pct = 0.30
    return cfg


class TestScanExplainEndpoint(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_scan_explain.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config", return_value=_mock_config()
        )
        self._cfg_patcher.start()

        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._db_patcher.stop()
        self._cfg_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def _post(self, scanner="mts", signal_count=0, log_excerpt="", rejection_summary=""):
        return self.client.post(
            "/api/v1/advisory/scan-explain",
            json={
                "scanner": scanner,
                "run_ts": "2026-06-19T09:30:00",
                "signal_count": signal_count,
                "log_excerpt": log_excerpt,
                "rejection_summary": rejection_summary,
            },
            content_type="application/json",
        )

    def test_no_api_key_returns_graceful_message(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            resp = self._post(scanner="mts", signal_count=0)
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertIn("explanation", d)
        self.assertIn("Advisory unavailable", d["explanation"])

    def test_mts_scanner_returns_explanation(self):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="MTS scan found 0 signals because RSI was above 60 on all candidates.")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            resp = self._post(scanner="mts", signal_count=0, log_excerpt="RSI=65 filtered")

        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertIn("explanation", d)
        self.assertTrue(len(d["explanation"]) > 0)

    def test_unknown_scanner_uses_generic_template(self):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="Generic scanner explanation here.")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            resp = self._post(scanner="newscanner", signal_count=2)

        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertIn("explanation", d)

    def test_claude_api_exception_degrades_gracefully(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            resp = self._post(scanner="uoa", signal_count=0)

        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertIn("explanation", d)
        self.assertIn("Advisory unavailable", d["explanation"])

    def test_psa_scanner_accepted(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            resp = self._post(scanner="psa", signal_count=3)
        self.assertEqual(resp.status_code, 200)

    def test_short_scanner_accepted(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            resp = self._post(scanner="short", signal_count=1)
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
