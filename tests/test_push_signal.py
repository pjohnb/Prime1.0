"""
Ops Sprint 2 Phase 2 -- Per-Signal Push tests.
Covers: alert assembly, advisory fallback, scheduler trigger (mocked scanner output).
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_notifications.prime_push_signal import (
    build_signal_alert,
    push_signal_alerts,
    _format_signal_alert_text,
    _process_single_signal,
)


class TestBuildSignalAlert(unittest.TestCase):
    """Alert assembly validation."""

    def _sample_signal(self):
        return {
            "symbol": "AAPL",
            "strategy": "UOA",
            "score": 8.5,
            "price_at_scan": 190.0,
            "direction": "LONG",
        }

    def _sample_factors(self):
        return {
            "duration": {"class": "ST", "confidence": "HIGH", "rationale": "Short-term"},
            "entry": {"method": "IMMEDIATE_FULL", "trigger": "", "rationale": "High score"},
            "nullifier": {"status": "CLEAR", "flags": [], "rationale": "No flags"},
            "exit_triggers": [
                {"type": "STOP_LOSS", "status": "ARMED", "value": "185.50",
                 "description": "2% stop at $185.50"},
                {"type": "PRICE_TARGET", "status": "ARMED", "value": "195.70",
                 "description": "3% target at $195.70"},
            ],
            "maintenance_flags": ["Monitor VIX"],
        }

    def _sample_advisory(self):
        return {
            "recommendation": "ENTER",
            "conviction": "HIGH",
            "risk_narrative": "Strong UOA signal with clear dark pool profile.",
            "confidence_note": "Would downgrade on VIX spike above 25",
        }

    def test_alert_has_required_fields(self):
        alert = build_signal_alert(
            self._sample_signal(), self._sample_factors(), self._sample_advisory()
        )
        self.assertEqual(alert["symbol"], "AAPL")
        self.assertEqual(alert["strategy"], "UOA")
        self.assertEqual(alert["composite_score"], 8.5)
        self.assertIn("duration", alert)
        self.assertIn("entry", alert)
        self.assertIn("nullifier", alert)
        self.assertIn("exit_triggers", alert)
        self.assertIn("advisory", alert)
        self.assertIn("stop_advisory", alert)
        self.assertIn("timestamp", alert)

    def test_advisory_section_populated(self):
        alert = build_signal_alert(
            self._sample_signal(), self._sample_factors(), self._sample_advisory()
        )
        self.assertEqual(alert["advisory"]["recommendation"], "ENTER")
        self.assertEqual(alert["advisory"]["conviction"], "HIGH")
        self.assertIn("Strong UOA", alert["advisory"]["narrative"])

    def test_stop_advisory_extracted(self):
        alert = build_signal_alert(
            self._sample_signal(), self._sample_factors(), self._sample_advisory()
        )
        self.assertIn("$185.50", alert["stop_advisory"])

    def test_empty_factors_handled(self):
        alert = build_signal_alert(self._sample_signal(), {}, {})
        self.assertEqual(alert["symbol"], "AAPL")
        self.assertEqual(alert["advisory"]["narrative"], "Advisory unavailable")

    def test_format_plaintext(self):
        alert = build_signal_alert(
            self._sample_signal(), self._sample_factors(), self._sample_advisory()
        )
        text = _format_signal_alert_text(alert)
        self.assertIn("AAPL", text)
        self.assertIn("UOA", text)
        self.assertIn("ENTER", text)
        self.assertIn("STOP_LOSS", text)


class TestAdvisoryFallback(unittest.TestCase):
    """Claude advisory failure does not prevent alert delivery."""

    def test_advisory_unavailable_on_failure(self):
        signal = {"symbol": "MSFT", "strategy": "PEAD", "score": 7.0, "price_at_scan": 415.0}
        factors = {"nullifier": {"status": "CLEAR"}}
        advisory = {
            "recommendation": "MONITOR",
            "conviction": "LOW",
            "risk_narrative": "Advisory unavailable",
            "_fallback": True,
        }
        alert = build_signal_alert(signal, factors, advisory)
        self.assertEqual(alert["advisory"]["narrative"], "Advisory unavailable")
        self.assertEqual(alert["advisory"]["recommendation"], "MONITOR")

    def test_process_signal_with_fallback_advisory(self):
        signal = {"symbol": "MSFT", "strategy": "PEAD", "score": 7.0,
                  "price_at_scan": 415.0, "direction": "LONG"}

        with patch("prime_notifications.prime_push_signal._executor") as mock_exec:
            alert = _process_single_signal(signal)

        self.assertIsNotNone(alert)
        self.assertEqual(alert["symbol"], "MSFT")
        self.assertIn("advisory", alert)


class TestSchedulerIntegration(unittest.TestCase):
    """Scheduler trigger with mocked scanner output."""

    @patch("prime_notifications.prime_push_signal.push_signal_alerts")
    @patch("prime_notifications.prime_notifier.send_digest")
    @patch("prime_notifications.prime_digest.assemble_digest")
    @patch("prime_data.prime_db.get_open_positions")
    def test_post_scan_notify_triggers_alerts(
        self, mock_positions, mock_assemble, mock_send, mock_push
    ):
        from prime_ops.prime_scheduler import post_scan_notify

        mock_positions.return_value = []
        mock_assemble.return_value = ({"scanner": "uoa", "signal_count": 2}, "text")
        mock_send.return_value = True
        mock_push.return_value = []

        scan_data = {
            "signals": [
                {"symbol": "AAPL", "strategy": "UOA", "score": 8.0, "price_at_scan": 190.0},
                {"symbol": "MSFT", "strategy": "UOA", "score": 7.5, "price_at_scan": 415.0},
            ],
            "signals_found": 2,
        }

        post_scan_notify("uoa", scan_data)

        mock_assemble.assert_called_once()
        mock_send.assert_called_once()
        mock_push.assert_called_once()
        pushed_signals = mock_push.call_args[0][0]
        self.assertEqual(len(pushed_signals), 2)

    @patch("prime_notifications.prime_push_signal.push_signal_alerts")
    @patch("prime_notifications.prime_notifier.send_digest")
    @patch("prime_notifications.prime_digest.assemble_digest")
    @patch("prime_data.prime_db.get_open_positions")
    def test_post_scan_notify_no_signals(
        self, mock_positions, mock_assemble, mock_send, mock_push
    ):
        from prime_ops.prime_scheduler import post_scan_notify

        mock_positions.return_value = []
        mock_assemble.return_value = ({"scanner": "pead", "signal_count": 0}, "text")
        mock_send.return_value = True

        post_scan_notify("pead", {"signals": []})

        mock_send.assert_called_once()
        mock_push.assert_not_called()

    @patch("prime_data.prime_db.get_open_positions")
    def test_post_scan_notify_handles_errors(self, mock_positions):
        from prime_ops.prime_scheduler import post_scan_notify

        mock_positions.side_effect = Exception("DB error")
        post_scan_notify("uoa", {"signals": []})


if __name__ == "__main__":
    unittest.main()
