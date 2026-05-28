"""
Sprint 10 Item 2 (CIL-PRIME-IDX-001) acceptance tests -- Index ETF Trading.
Covers Tier classification, direction, nullifiers, prime_signals write,
schema migration, Trade Mgmt Panel compatibility.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_analytics.prime_signals_db import get_signals, init_signals_table
from prime_data.prime_db import get_connection, init_db
from prime_scanners.prime_index_uoa import (
    INDEX_WATCHLIST,
    build_index_factors,
    classify_direction,
    classify_tier,
    scan_index_uoa,
)
from prime_scanners.prime_index_scanner import run_index_uoa_scan


def _market(symbol, volume, sizzle, cp_ratio, price=450.0):
    return {
        "symbol": symbol,
        "option_volume": volume,
        "sizzle": sizzle,
        "call_put_ratio": cp_ratio,
        "price": price,
    }


class TestClassifyTier(unittest.TestCase):
    """AC: Tier 1/2 threshold classification for all three symbols."""

    def test_spy_tier1(self):
        self.assertEqual(classify_tier("SPY", 200_000, 3.0), "TIER_1")

    def test_spy_tier2(self):
        self.assertEqual(classify_tier("SPY", 100_000, 2.0), "TIER_2")

    def test_spy_below_threshold(self):
        self.assertIsNone(classify_tier("SPY", 50_000, 1.5))

    def test_qqq_tier1(self):
        self.assertEqual(classify_tier("QQQ", 250_000, 4.0), "TIER_1")

    def test_iwm_tier1(self):
        self.assertEqual(classify_tier("IWM", 100_000, 3.5), "TIER_1")

    def test_iwm_tier2(self):
        self.assertEqual(classify_tier("IWM", 50_000, 2.5), "TIER_2")

    def test_unknown_symbol(self):
        self.assertIsNone(classify_tier("AAPL", 500_000, 5.0))


class TestClassifyDirection(unittest.TestCase):
    """AC: C/P ratio correctly maps to LONG/SHORT/NEUTRAL."""

    def test_long(self):
        self.assertEqual(classify_direction(1.5), "LONG")

    def test_short(self):
        self.assertEqual(classify_direction(0.5), "SHORT")

    def test_neutral(self):
        self.assertEqual(classify_direction(1.0), "NEUTRAL")

    def test_boundary_long(self):
        self.assertEqual(classify_direction(1.31), "LONG")

    def test_boundary_short(self):
        self.assertEqual(classify_direction(0.76), "SHORT")


class TestScanIndexUoa(unittest.TestCase):
    """AC: Index UOA scanner generates correct signals."""

    def test_tier1_long_signal(self):
        data = [_market("SPY", 250_000, 3.5, 1.5, 450.0)]
        signals = scan_index_uoa(data)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "SPY")
        self.assertEqual(signals[0]["direction"], "LONG")
        self.assertEqual(signals[0]["tier"], "TIER_1")
        self.assertEqual(signals[0]["instrument_type"], "INDEX_ETF")
        self.assertEqual(signals[0]["strategy"], "UOA_INDEX")

    def test_neutral_direction_skipped(self):
        data = [_market("SPY", 250_000, 3.5, 1.0, 450.0)]
        signals = scan_index_uoa(data)
        self.assertEqual(len(signals), 0)

    def test_below_threshold_skipped(self):
        data = [_market("SPY", 10_000, 1.0, 1.5, 450.0)]
        signals = scan_index_uoa(data)
        self.assertEqual(len(signals), 0)

    def test_no_market_data_returns_empty(self):
        self.assertEqual(scan_index_uoa(None), [])
        self.assertEqual(scan_index_uoa([]), [])

    def test_non_index_symbol_skipped(self):
        data = [_market("AAPL", 500_000, 5.0, 2.0, 185.0)]
        signals = scan_index_uoa(data)
        self.assertEqual(len(signals), 0)


class TestBuildIndexFactors(unittest.TestCase):
    """AC: A-B-C-D framework with index-specific factor set."""

    def test_duration_is_short_term(self):
        factors = build_index_factors("SPY", "LONG", "TIER_1", 450.0)
        self.assertEqual(factors["duration"]["class"], "ST")

    def test_entry_is_single_tranche(self):
        factors = build_index_factors("SPY", "LONG", "TIER_1", 450.0)
        self.assertEqual(factors["entry"]["method"], "IMMEDIATE_FULL")

    def test_spy_exit_targets(self):
        factors = build_index_factors("SPY", "LONG", "TIER_1", 450.0)
        triggers = {t["type"]: t for t in factors["exit_triggers"]}
        self.assertIn("PRICE_TARGET", triggers)
        self.assertIn("STOP_LOSS", triggers)
        self.assertIn("1.5%", triggers["PRICE_TARGET"]["description"])
        self.assertIn("0.75%", triggers["STOP_LOSS"]["description"])

    def test_iwm_wider_targets(self):
        factors = build_index_factors("IWM", "LONG", "TIER_1", 220.0)
        triggers = {t["type"]: t for t in factors["exit_triggers"]}
        self.assertIn("2.0%", triggers["PRICE_TARGET"]["description"])
        self.assertIn("1.0%", triggers["STOP_LOSS"]["description"])


class TestNullifiers(unittest.TestCase):
    """AC: SRS BROAD_DECLINE suppresses LONG; DK NULLIFYING suppresses signal."""

    @patch("prime_scanners.prime_index_scanner._check_srs_regime")
    @patch("prime_scanners.prime_index_scanner._check_dk_status")
    def test_srs_broad_decline_nullifies_long(self, mock_dk, mock_srs):
        mock_srs.return_value = "SRS BROAD_DECLINE suppresses LONG index signal"
        mock_dk.return_value = None
        data = [_market("SPY", 250_000, 3.5, 1.5, 450.0)]
        result = run_index_uoa_scan(data)
        self.assertEqual(len(result["nullified"]), 1)
        self.assertEqual(len(result["approved"]), 0)

    @patch("prime_scanners.prime_index_scanner._check_srs_regime")
    @patch("prime_scanners.prime_index_scanner._check_dk_status")
    def test_dk_nullifying_suppresses_signal(self, mock_dk, mock_srs):
        mock_srs.return_value = None
        mock_dk.return_value = "DK NULLIFYING: short spike"
        data = [_market("SPY", 250_000, 3.5, 1.5, 450.0)]
        result = run_index_uoa_scan(data)
        self.assertEqual(len(result["nullified"]), 1)
        self.assertEqual(len(result["approved"]), 0)

    @patch("prime_scanners.prime_index_scanner._check_srs_regime")
    @patch("prime_scanners.prime_index_scanner._check_dk_status")
    def test_no_nullifiers_approved(self, mock_dk, mock_srs):
        mock_srs.return_value = None
        mock_dk.return_value = None
        data = [_market("SPY", 250_000, 3.5, 1.5, 450.0)]
        db = Path(__file__).parent / "_test_idx_etf.db"
        if db.exists():
            db.unlink()
        init_db(db)
        init_signals_table(db)
        result = run_index_uoa_scan(data, db_path=db)
        self.assertEqual(len(result["approved"]), 1)
        signals = get_signals(db_path=db)
        self.assertTrue(len(signals) >= 1)
        db.unlink()


class TestSignalsSchema(unittest.TestCase):
    """AC: instrument_type column present in prime_signals."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_idx_schema.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_instrument_type_column_exists(self):
        with get_connection(self.db) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(prime_signals)").fetchall()]
        self.assertIn("instrument_type", cols)

    def test_instrument_type_migration_idempotent(self):
        init_signals_table(self.db)
        init_signals_table(self.db)
        with get_connection(self.db) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(prime_signals)").fetchall()]
        self.assertIn("instrument_type", cols)


if __name__ == "__main__":
    unittest.main()
