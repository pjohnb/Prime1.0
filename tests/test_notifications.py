"""
Ops Sprint 2 Phase 1 -- Push Notifications tests.
Covers: assemble_digest structure, file fallback, SMTP path (mocked), footer accuracy.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_notifications.prime_digest import assemble_digest, _get_next_scan_time
from prime_notifications.prime_notifier import send_digest, send_signal_alert, _write_digest_file


class TestAssembleDigest(unittest.TestCase):
    """Digest structure and content validation."""

    def _sample_signals(self):
        return [
            {"symbol": "AAPL", "strategy": "UOA", "score": 8.5, "price_at_scan": 190.0,
             "trade_factors": json.dumps({"duration": {"class": "ST"}, "entry": {"method": "IMMEDIATE_FULL"},
                                          "nullifier": {"status": "CLEAR"}})},
            {"symbol": "MSFT", "strategy": "PEAD", "score": 7.2, "price_at_scan": 415.0,
             "trade_factors": json.dumps({"duration": {"class": "MT"}, "entry": {"method": "SCALED"},
                                          "nullifier": {"status": "SUSPECT"}})},
            {"symbol": "TSLA", "strategy": "MTS", "score": 6.0, "price_at_scan": 200.0,
             "trade_factors": "{}"},
        ]

    def _sample_positions(self):
        return [
            {"symbol": "GLD", "trade_source": "LEGACY", "entry_price": 309.67,
             "current_price": 312.0, "shares": 10},
            {"symbol": "NIO", "trade_source": "LEGACY", "entry_price": 3.645,
             "current_price": 3.80, "shares": 100},
        ]

    def test_digest_returns_tuple(self):
        digest, text = assemble_digest("uoa", self._sample_signals(), self._sample_positions())
        self.assertIsInstance(digest, dict)
        self.assertIsInstance(text, str)

    def test_digest_has_required_keys(self):
        digest, _ = assemble_digest("uoa", self._sample_signals(), self._sample_positions())
        self.assertIn("scanner", digest)
        self.assertIn("timestamp", digest)
        self.assertIn("signal_count", digest)
        self.assertIn("signals", digest)
        self.assertIn("open_positions", digest)
        self.assertIn("next_scan_time", digest)

    def test_signals_sorted_by_score_desc(self):
        digest, _ = assemble_digest("uoa", self._sample_signals(), [])
        scores = [s["composite_score"] for s in digest["signals"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_max_10_signals(self):
        signals = [{"symbol": f"SYM{i}", "strategy": "UOA", "score": float(i),
                     "price_at_scan": 100.0} for i in range(15)]
        digest, _ = assemble_digest("uoa", signals, [])
        self.assertLessEqual(len(digest["signals"]), 10)

    def test_signal_row_structure(self):
        digest, _ = assemble_digest("uoa", self._sample_signals(), [])
        sig = digest["signals"][0]
        self.assertIn("symbol", sig)
        self.assertIn("strategy", sig)
        self.assertIn("composite_score", sig)
        self.assertIn("factor_flags", sig)
        self.assertIn("entry_price", sig)

    def test_position_row_structure(self):
        digest, _ = assemble_digest("uoa", [], self._sample_positions())
        pos = digest["open_positions"][0]
        self.assertIn("symbol", pos)
        self.assertIn("trade_source", pos)
        self.assertIn("entry_price", pos)
        self.assertIn("current_price", pos)
        self.assertIn("unrealized_pnl", pos)

    def test_unrealized_pnl_calculation(self):
        digest, _ = assemble_digest("uoa", [], self._sample_positions())
        gld = [p for p in digest["open_positions"] if p["symbol"] == "GLD"][0]
        expected = round((312.0 - 309.67) * 10, 2)
        self.assertAlmostEqual(gld["unrealized_pnl"], expected, places=2)

    def test_plaintext_contains_scanner_name(self):
        _, text = assemble_digest("pead", [], [])
        self.assertIn("PEAD", text)

    def test_empty_signals_and_positions(self):
        digest, text = assemble_digest("uoa", [], [])
        self.assertEqual(digest["signal_count"], 0)
        self.assertEqual(len(digest["signals"]), 0)
        self.assertIn("(none)", text)


class TestDigestFooter(unittest.TestCase):
    """Footer reads next scan time live from ops_config.json."""

    def test_footer_reads_from_ops_config(self):
        digest, text = assemble_digest("uoa", [], [])
        self.assertIn("next_scan_time", digest)
        self.assertNotEqual(digest["next_scan_time"], "")
        self.assertIn("Next scan:", text)

    def test_footer_reflects_config_change(self):
        tmp_dir = Path(tempfile.mkdtemp())
        ops_path = tmp_dir / "ops_config.json"
        ops_path.write_text(json.dumps({
            "scan_schedule": {
                "test_scanner": {"times_et": ["14:00", "16:00"], "days": "weekdays"}
            },
            "notification_channels": "TBD",
            "health_check_interval": 900,
        }))

        with patch("prime_notifications.prime_digest._PROJECT_ROOT", tmp_dir):
            digest1, _ = assemble_digest("test_scanner", [], [])
            time1 = digest1["next_scan_time"]

            new_config = json.loads(ops_path.read_text())
            new_config["scan_schedule"]["test_scanner"]["times_et"] = ["09:00", "11:00"]
            ops_path.write_text(json.dumps(new_config))

            digest2, _ = assemble_digest("test_scanner", [], [])
            time2 = digest2["next_scan_time"]

        # time1 must reflect the first config (one of its scan times); which one
        # is "next" depends on the wall clock, so accept either rather than
        # assuming the test runs before 14:00 ET.
        self.assertTrue("14:00" in time1 or "16:00" in time1)
        self.assertNotEqual(time1, time2)

    def test_unknown_scanner_shows_not_scheduled(self):
        digest, _ = assemble_digest("nonexistent_scanner_xyz", [], [])
        self.assertEqual(digest["next_scan_time"], "Not scheduled")


class TestNotifierFileFallback(unittest.TestCase):
    """File fallback when SMTP not configured."""

    def test_file_fallback_writes_digest(self):
        tmp_dir = Path(tempfile.mkdtemp())
        digest = {"scanner": "uoa", "signal_count": 3}
        text = "Test digest content"

        with patch("prime_notifications.prime_notifier._DIGEST_DIR", tmp_dir):
            with patch("prime_notifications.prime_notifier._get_smtp_config", return_value=None):
                result = send_digest(digest, text)

        self.assertTrue(result)
        files = list(tmp_dir.glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertIn("uoa", files[0].name)
        self.assertEqual(files[0].read_text(encoding="utf-8"), text)

    def test_signal_alert_file_fallback(self):
        tmp_dir = Path(tempfile.mkdtemp())
        alert = {"symbol": "AAPL", "strategy": "UOA"}
        text = "Signal alert content"

        with patch("prime_notifications.prime_notifier._DIGEST_DIR", tmp_dir):
            with patch("prime_notifications.prime_notifier._get_smtp_config", return_value=None):
                result = send_signal_alert(alert, text)

        self.assertTrue(result)
        files = list(tmp_dir.glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertIn("AAPL", files[0].name)


class TestNotifierSMTP(unittest.TestCase):
    """SMTP path (mocked)."""

    @patch("prime_notifications.prime_notifier._send_smtp")
    @patch("prime_notifications.prime_notifier._get_smtp_config")
    def test_smtp_delivery(self, mock_config, mock_smtp):
        mock_config.return_value = {
            "host": "smtp.test.com", "from_addr": "a@b.com",
            "to_addr": "c@d.com", "port": 587,
        }
        mock_smtp.return_value = True

        digest = {"scanner": "uoa", "signal_count": 1}
        result = send_digest(digest, "test text")

        self.assertTrue(result)
        mock_smtp.assert_called_once()

    @patch("prime_notifications.prime_notifier._send_smtp")
    @patch("prime_notifications.prime_notifier._get_smtp_config")
    def test_smtp_failure_falls_back_to_file(self, mock_config, mock_smtp):
        mock_config.return_value = {
            "host": "smtp.test.com", "from_addr": "a@b.com",
            "to_addr": "c@d.com", "port": 587,
        }
        mock_smtp.side_effect = Exception("SMTP down")

        tmp_dir = Path(tempfile.mkdtemp())
        digest = {"scanner": "pead", "signal_count": 0}

        with patch("prime_notifications.prime_notifier._DIGEST_DIR", tmp_dir):
            result = send_digest(digest, "fallback test")

        self.assertTrue(result)
        files = list(tmp_dir.glob("*.txt"))
        self.assertEqual(len(files), 1)


if __name__ == "__main__":
    unittest.main()
