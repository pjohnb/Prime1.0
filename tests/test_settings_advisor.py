"""
Sprint 9 Item 5 (ML-18) acceptance tests -- AI Settings Advisor.
Covers suggestion render, API failure fallback, no history graceful,
config read-only enforced, side-by-side display data.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_settings_advisor import (
    _fallback_settings,
    _read_ops_config,
    get_settings_suggestions,
)


class TestReadOpsConfig(unittest.TestCase):

    def test_reads_valid_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"scan_schedule": {"uoa": {"times_et": ["10:00"]}}}, f)
            f.flush()
            config = _read_ops_config(Path(f.name))
        self.assertIn("scan_schedule", config)
        Path(f.name).unlink()

    def test_missing_file_returns_empty(self):
        config = _read_ops_config(Path("/nonexistent/ops_config.json"))
        self.assertEqual(config, {})

    def test_invalid_json_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json{{{")
            f.flush()
            config = _read_ops_config(Path(f.name))
        self.assertEqual(config, {})
        Path(f.name).unlink()


class TestFallbackSettings(unittest.TestCase):

    def test_low_conversion_suggests_lower_threshold(self):
        history = [{"score": 5.0, "strategy": "UOA"} for _ in range(20)]
        result = _fallback_settings(history, {}, "test")
        self.assertTrue(result["_fallback"])
        threshold_suggestions = [s for s in result["suggestions"]
                                 if "threshold" in s["parameter"].lower()]
        self.assertTrue(len(threshold_suggestions) >= 1)

    def test_no_history_suggests_check_schedule(self):
        result = _fallback_settings([], {}, "test")
        schedule_suggestions = [s for s in result["suggestions"]
                                if "schedule" in s["parameter"].lower()]
        self.assertTrue(len(schedule_suggestions) >= 1)

    def test_reasonable_settings_no_change(self):
        history = [{"score": 7.0, "strategy": "UOA", "trade_id": f"t{i}"}
                   for i in range(10)]
        result = _fallback_settings(history, {}, "test")
        self.assertTrue(any("no changes" in s["parameter"].lower()
                            for s in result["suggestions"]))


class TestGetSettingsSuggestions(unittest.TestCase):

    def test_no_api_key_returns_fallback(self):
        result = get_settings_suggestions([], api_key=None)
        self.assertTrue(result["_fallback"])
        self.assertIn("no API key", result["_fallback_reason"])

    def test_result_has_required_fields(self):
        result = get_settings_suggestions([], api_key=None)
        self.assertIn("suggestions", result)
        self.assertIn("timestamp", result)
        self.assertIsInstance(result["suggestions"], list)
        for s in result["suggestions"]:
            self.assertIn("parameter", s)
            self.assertIn("current_value", s)
            self.assertIn("suggested_value", s)
            self.assertIn("rationale", s)

    def test_api_import_error_fallback(self):
        with patch.dict("sys.modules", {"anthropic": None}):
            result = get_settings_suggestions([], api_key="fake-key")
        self.assertTrue(result["_fallback"])

    def test_api_exception_fallback(self):
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value.messages.create.side_effect = RuntimeError("API down")
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = get_settings_suggestions([], api_key="fake-key")
        self.assertTrue(result["_fallback"])
        self.assertIn("API down", result["_fallback_reason"])

    def test_config_not_modified(self):
        original = {"scan_schedule": {"uoa": {"times_et": ["10:00"]}}}
        config_copy = json.loads(json.dumps(original))
        get_settings_suggestions([], current_config=config_copy, api_key=None)
        self.assertEqual(config_copy, original)

    def test_explicit_config_used(self):
        config = {"scan_schedule": {"uoa": {"score_threshold": 6.0}}}
        result = get_settings_suggestions([], current_config=config, api_key=None)
        self.assertIn("suggestions", result)

    def test_with_scan_history(self):
        history = [
            {"symbol": "AAPL", "strategy": "UOA", "score": 7.5, "trade_id": "t1"},
            {"symbol": "MSFT", "strategy": "PEAD", "score": 6.0, "trade_id": None},
        ]
        result = get_settings_suggestions(history, api_key=None)
        self.assertIn("suggestions", result)


if __name__ == "__main__":
    unittest.main()
