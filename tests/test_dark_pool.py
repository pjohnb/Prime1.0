"""
Item 3 (CIL-PRIME-DK-001) acceptance tests -- Dark Pool Scanner.
Verifies all three manipulation patterns and integration rules.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_dark_pool import DarkPoolEvaluation, DarkPoolScanner


class TestDarkPoolEvaluation(unittest.TestCase):
    """AC 3.1 -- DarkPoolEvaluation produced for every signal."""

    def setUp(self):
        self.scanner = DarkPoolScanner()

    def test_clean_signal_returns_clear(self):
        signal = {
            "symbol": "AAPL",
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 185.50,
            "session_open_price": 184.00,
            "duration_class": "MT",
        }
        result = self.scanner.evaluate("AAPL", signal)
        self.assertIsInstance(result, DarkPoolEvaluation)
        self.assertEqual(result.status, "CLEAR")
        self.assertEqual(result.flag_count, 0)
        self.assertFalse(result.suspect)
        self.assertFalse(result.nullified)

    def test_evaluation_has_all_required_fields(self):
        signal = {"direction": "LONG", "price_at_scan": 100.0}
        result = self.scanner.evaluate("TEST", signal)
        d = result.to_dict()
        required = ["symbol", "timestamp", "flags", "flag_count",
                     "suspect", "nullified", "status", "rationale"]
        for key in required:
            self.assertIn(key, d)


class TestDataSourceProxies(unittest.TestCase):
    """AC 3.2 -- All five data source proxies implemented or stubbed."""

    def test_all_five_sources_registered(self):
        scanner = DarkPoolScanner()
        source_names = [name for name, _ in scanner.DATA_SOURCES]
        self.assertIn("finra_ats_volume", source_names)
        self.assertIn("tape_prints", source_names)
        self.assertIn("short_volume", source_names)
        self.assertIn("spread_data", source_names)
        self.assertIn("print_direction", source_names)
        self.assertEqual(len(scanner.DATA_SOURCES), 5)


class TestManipulationPatterns(unittest.TestCase):
    """AC 3.3 -- All three manipulation patterns evaluated per signal."""

    def setUp(self):
        self.scanner = DarkPoolScanner()

    def test_pattern1_price_spike_into_signal(self):
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 106.0,
            "session_open_price": 100.0,
            "duration_class": "MT",
        }
        result = self.scanner.evaluate("SPIKE", signal)
        self.assertIn("PRICE_SPIKE_INTO_SIGNAL", result.flags)
        self.assertTrue(result.suspect)

    def test_pattern1_no_flag_when_move_small(self):
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 101.5,
            "session_open_price": 100.0,
            "duration_class": "MT",
        }
        result = self.scanner.evaluate("SMALL", signal)
        self.assertNotIn("PRICE_SPIKE_INTO_SIGNAL", result.flags)

    def test_pattern2_call_volume_price_extended(self):
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 104.0,
            "session_open_price": 100.0,
            "duration_class": "MT",
        }
        result = self.scanner.evaluate("EXTEND", signal)
        self.assertIn("CALL_VOLUME_PRICE_EXTENDED", result.flags)

    def test_pattern2_no_flag_for_short_direction(self):
        signal = {
            "direction": "SHORT",
            "strategy": "UOA",
            "price_at_scan": 104.0,
            "session_open_price": 100.0,
            "duration_class": "MT",
        }
        result = self.scanner.evaluate("SHORTDIR", signal)
        self.assertNotIn("CALL_VOLUME_PRICE_EXTENDED", result.flags)

    def test_pattern3_block_print_against_direction(self):
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 100.0,
            "session_open_price": 100.0,
            "duration_class": "MT",
            "block_prints": [{"side": "SELL", "size": 50000}],
        }
        result = self.scanner.evaluate("BLOCK", signal)
        self.assertIn("BLOCK_PRINT_AGAINST_DIRECTION", result.flags)

    def test_pattern3_no_flag_when_block_small(self):
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 100.0,
            "session_open_price": 100.0,
            "duration_class": "MT",
            "block_prints": [{"side": "SELL", "size": 500}],
        }
        result = self.scanner.evaluate("SMALLBLK", signal)
        self.assertNotIn("BLOCK_PRINT_AGAINST_DIRECTION", result.flags)

    def test_all_three_checkers_registered(self):
        self.assertEqual(len(DarkPoolScanner.PATTERN_CHECKERS), 3)


class TestIntegrationRuleSuspectST(unittest.TestCase):
    """AC 3.4 -- SUSPECT + ST = NULLIFIED."""

    def test_single_flag_st_is_nullified(self):
        scanner = DarkPoolScanner()
        # 2.5% move: triggers Pattern 1 (>2%) but not Pattern 2 (>=3%)
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 102.5,
            "session_open_price": 100.0,
            "duration_class": "ST",
        }
        result = scanner.evaluate("STNULL", signal)
        self.assertEqual(result.flag_count, 1)
        self.assertTrue(result.nullified)
        self.assertEqual(result.status, "NULLIFIED")

    def test_single_flag_lt_is_suspect_not_nullified(self):
        scanner = DarkPoolScanner()
        # 2.5% move: triggers Pattern 1 only
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 102.5,
            "session_open_price": 100.0,
            "duration_class": "LT",
        }
        result = scanner.evaluate("LTSUS", signal)
        self.assertEqual(result.flag_count, 1)
        self.assertTrue(result.suspect)
        self.assertFalse(result.nullified)
        self.assertEqual(result.status, "SUSPECT")


class TestIntegrationRuleDualFlags(unittest.TestCase):
    """AC 3.5 -- Two or more flags = hard NULLIFIED."""

    def test_two_flags_hard_nullified(self):
        scanner = DarkPoolScanner()
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 106.0,
            "session_open_price": 100.0,
            "duration_class": "LT",
            "block_prints": [{"side": "SELL", "size": 50000}],
        }
        result = scanner.evaluate("DUAL", signal)
        self.assertGreaterEqual(result.flag_count, 2)
        self.assertTrue(result.nullified)
        self.assertEqual(result.status, "NULLIFIED")
        self.assertIn("HARD NULLIFIED", result.rationale)

    def test_two_flags_nullified_regardless_of_duration(self):
        for duration in ["ST", "MT", "LT"]:
            scanner = DarkPoolScanner()
            signal = {
                "direction": "LONG",
                "strategy": "UOA",
                "price_at_scan": 106.0,
                "session_open_price": 100.0,
                "duration_class": duration,
                "block_prints": [{"side": "SELL", "size": 50000}],
            }
            result = scanner.evaluate(f"DUAL_{duration}", signal)
            self.assertTrue(
                result.nullified,
                f"Expected NULLIFIED for 2+ flags at duration={duration}",
            )


class TestDarkPoolEvalStorage(unittest.TestCase):
    """AC 3.6 -- dark_pool_eval stored via prime_data layer."""

    def test_to_dict_is_json_serializable(self):
        import json
        scanner = DarkPoolScanner()
        signal = {
            "direction": "LONG",
            "strategy": "UOA",
            "price_at_scan": 106.0,
            "session_open_price": 100.0,
            "duration_class": "ST",
        }
        result = scanner.evaluate("STORE", signal)
        serialized = json.dumps(result.to_dict())
        self.assertIsInstance(serialized, str)
        parsed = json.loads(serialized)
        self.assertEqual(parsed["symbol"], "STORE")


class TestScannerFailureFallback(unittest.TestCase):
    """AC 3.8 -- Scanner failure falls back to CLEAR with warning flag."""

    def test_exception_in_pattern_check_does_not_crash(self):
        scanner = DarkPoolScanner()
        signal = {
            "direction": "LONG",
            "price_at_scan": "not_a_number",
            "session_open_price": None,
        }
        result = scanner.evaluate("ERRSYM", signal)
        self.assertIn(result.status, ("CLEAR",))

    def test_forced_exception_returns_clear_with_warning(self):
        scanner = DarkPoolScanner()

        original_method = scanner._run_pattern_checks

        def _exploding_checks(*args, **kwargs):
            raise RuntimeError("Simulated scanner failure")

        scanner._run_pattern_checks = _exploding_checks
        result = scanner.evaluate("BOOM", {"direction": "LONG"})
        self.assertEqual(result.status, "CLEAR")
        self.assertIn("Scanner error", result.warning)

        scanner._run_pattern_checks = original_method


if __name__ == "__main__":
    unittest.main()
