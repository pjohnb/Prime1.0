"""
WO-PRIME-PSA-UNIVERSE-01 — tests for resolve_psa_universe() and config wiring.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_psa_scanner import (
    _MAG7,
    DEFAULT_UNIVERSE,
    resolve_psa_universe,
)

_DATA_DIR = PROJECT_ROOT / "data"


class TestResolveUniverseMag7(unittest.TestCase):
    def test_mag7_returns_seven_symbols(self):
        result = resolve_psa_universe("mag7")
        self.assertEqual(result, list(_MAG7))
        self.assertEqual(len(result), 7)

    def test_mag7_contains_expected_tickers(self):
        result = resolve_psa_universe("mag7")
        for sym in ("AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA"):
            self.assertIn(sym, result)


class TestResolveUniverseSp500(unittest.TestCase):
    def test_sp500_loads_from_file(self):
        result = resolve_psa_universe("sp500")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 400)

    def test_sp500_contains_common_symbols(self):
        result = resolve_psa_universe("sp500")
        for sym in ("AAPL", "MSFT", "JPM", "JNJ"):
            self.assertIn(sym, result)


class TestResolveUniverseSp500ExMag7(unittest.TestCase):
    def test_ex_mag7_excludes_mag7(self):
        result = resolve_psa_universe("sp500_ex_mag7")
        for sym in _MAG7:
            self.assertNotIn(sym, result)

    def test_ex_mag7_still_large(self):
        result = resolve_psa_universe("sp500_ex_mag7")
        self.assertGreater(len(result), 400)

    def test_ex_mag7_contains_non_mag7_sp500(self):
        result = resolve_psa_universe("sp500_ex_mag7")
        self.assertIn("JPM", result)


class TestResolveUniverseRussell2000(unittest.TestCase):
    def test_russell2000_loads(self):
        result = resolve_psa_universe("russell2000")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 100)

    def test_russell2000_no_overlap_with_mag7(self):
        result = resolve_psa_universe("russell2000")
        for sym in _MAG7:
            self.assertNotIn(sym, result)


class TestResolveUniverseAllSectors(unittest.TestCase):
    def test_all_sectors_no_duplicates(self):
        result = resolve_psa_universe("all_sectors")
        self.assertEqual(len(result), len(set(result)))

    def test_all_sectors_covers_multiple_etfs(self):
        result = resolve_psa_universe("all_sectors")
        sectors = json.loads((_DATA_DIR / "sectors_constituents.json").read_text())
        for etf_syms in sectors.values():
            self.assertTrue(any(s in result for s in etf_syms))


class TestResolveUniverseSector(unittest.TestCase):
    def test_xlk_returns_tech_symbols(self):
        result = resolve_psa_universe("sector", sector="XLK")
        self.assertIn("AAPL", result)
        self.assertIn("MSFT", result)

    def test_xlf_returns_finance_symbols(self):
        result = resolve_psa_universe("sector", sector="XLF")
        self.assertIn("JPM", result)

    def test_unknown_sector_falls_back_to_default(self):
        result = resolve_psa_universe("sector", sector="XYZZZZ")
        self.assertEqual(result, list(DEFAULT_UNIVERSE))


class TestResolveUniverseCustom(unittest.TestCase):
    def test_custom_parses_csv(self):
        result = resolve_psa_universe("custom", custom="AAPL, MSFT, NVDA")
        self.assertEqual(result, ["AAPL", "MSFT", "NVDA"])

    def test_custom_uppercases(self):
        result = resolve_psa_universe("custom", custom="aapl,msft")
        self.assertEqual(result, ["AAPL", "MSFT"])

    def test_custom_empty_falls_back_to_default(self):
        result = resolve_psa_universe("custom", custom="")
        self.assertEqual(result, list(DEFAULT_UNIVERSE))

    def test_custom_strips_whitespace(self):
        result = resolve_psa_universe("custom", custom="  AAPL ,  TSLA  ")
        self.assertEqual(result, ["AAPL", "TSLA"])


class TestResolveUniverseFileMissing(unittest.TestCase):
    def test_missing_file_returns_default(self):
        bad_path = PROJECT_ROOT / "data" / "__nonexistent__.json"
        with patch("prime_scanners.prime_psa_scanner._DATA_DIR", bad_path.parent / "__nope__"):
            result = resolve_psa_universe("sp500")
        self.assertEqual(result, list(DEFAULT_UNIVERSE))


class TestRunPsaScanUsesConfig(unittest.TestCase):
    """run_psa_scan() uses resolve_psa_universe() from config when universe=None."""

    def test_run_psa_scan_calls_resolve_when_no_universe(self):
        from prime_scanners.prime_psa_scanner import run_psa_scan

        captured = []

        def fake_resolve(mode, custom, sector):
            captured.append((mode, custom, sector))
            return ["AAPL"]

        mock_cfg = MagicMock()
        mock_cfg.ops.psa_universe = "mag7"
        mock_cfg.ops.psa_universe_custom = ""
        mock_cfg.ops.psa_universe_sector = "XLK"

        # _fetch_bars is the Polygon call — short-circuit to avoid real network
        with patch("prime_scanners.prime_psa_scanner.resolve_psa_universe", side_effect=fake_resolve), \
             patch("prime_scanners.prime_psa_scanner.get_config", return_value=mock_cfg), \
             patch("prime_scanners.prime_psa_scanner.fetch_bars", return_value=[]):
            run_psa_scan(api_key="test", universe=None)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], ("mag7", "", "XLK"))


if __name__ == "__main__":
    unittest.main()
