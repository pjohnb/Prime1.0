"""
Claude Advisor acceptance tests -- advisory generation and fallback.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_claude_advisor import generate_advisory


class TestClaudeAdvisoryFallback(unittest.TestCase):
    """AC 4.6 -- Claude advisory generated (falls back gracefully without API key)."""

    def _sample_factor_eval(self, nullifier_status="CLEAR", score=7.0):
        return {
            "strategy": "MTS",
            "symbol": "GLD",
            "direction": "LONG",
            "signal_score": score,
            "duration": {"class": "MT", "confidence": "MEDIUM", "rationale": "metals thesis"},
            "entry": {"method": "IMMEDIATE_FULL", "trigger": "", "rationale": "high score"},
            "exit_triggers": [],
            "nullifier": {"status": nullifier_status, "flags": [], "rationale": ""},
            "maintenance_flags": ["Monitor gold/silver ratio"],
        }

    def test_no_api_key_returns_fallback(self):
        adv = generate_advisory(self._sample_factor_eval(), api_key=None)
        self.assertTrue(adv.get("_fallback"))
        self.assertIn("recommendation", adv)
        self.assertIn("timestamp", adv)

    def test_fallback_enter_for_high_score(self):
        adv = generate_advisory(self._sample_factor_eval(score=8.0), api_key=None)
        self.assertEqual(adv["recommendation"], "ENTER")

    def test_fallback_nullify_for_nullified_signal(self):
        adv = generate_advisory(self._sample_factor_eval(nullifier_status="NULLIFIED"), api_key=None)
        self.assertEqual(adv["recommendation"], "NULLIFY")

    def test_fallback_monitor_for_suspect(self):
        adv = generate_advisory(self._sample_factor_eval(nullifier_status="SUSPECT"), api_key=None)
        self.assertEqual(adv["recommendation"], "MONITOR")

    def test_advisory_never_crashes(self):
        adv = generate_advisory({}, api_key="invalid_key_that_will_fail")
        self.assertIn("recommendation", adv)
        self.assertIn("timestamp", adv)


class TestAdvisoryAtSignalTime(unittest.TestCase):
    """AC 4.6 -- advisory generated at signal time, stored before GUI opens."""

    def test_advisory_has_timestamp(self):
        adv = generate_advisory({"signal_score": 5.0, "nullifier": {"status": "CLEAR"}})
        self.assertIn("timestamp", adv)
        self.assertGreater(len(adv["timestamp"]), 0)


if __name__ == "__main__":
    unittest.main()
