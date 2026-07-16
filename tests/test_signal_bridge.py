"""
Sprint 14 Item 1 (Scanner Bridge) acceptance tests.

Mocks each v0.9 scanner output format (UOA, PSA, PEAD, MMR, SRS), verifies the
bridge inserts approved signals into prime_signals with the correct field
mapping, filters out non-approved rows, and is idempotent on re-run (signal_id
dedup).
"""

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import (
    get_signals,
    init_signals_table,
    insert_signal_dedup,
    make_signal_id,
)
from prime_bridge import prime_signal_bridge as bridge


class _BridgeTestBase(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_bridge.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _signals(self, **kw):
        return get_signals(db_path=self.db, **kw)


class TestDedup(_BridgeTestBase):
    def test_deterministic_id_stable(self):
        a = make_signal_id("UOA", "spy", "2026-06-02 12:50")
        b = make_signal_id("UOA", "SPY", "2026-06-02 12:50")
        self.assertEqual(a, b)  # symbol normalised to upper

    def test_insert_then_dup_skipped(self):
        first = insert_signal_dedup("SPY", "UOA", "2026-06-02 12:50", db_path=self.db)
        self.assertIsNotNone(first)
        second = insert_signal_dedup("SPY", "UOA", "2026-06-02 12:50", db_path=self.db)
        self.assertIsNone(second)
        self.assertEqual(len(self._signals()), 1)


class TestUOA(_BridgeTestBase):
    ROWS = [
        {"date": "2026-06-02", "time": "12:50", "symbol": "SPY", "group": "Macro",
         "tier": "STRONG", "sizzle_index": "259.5", "direction": "SHORT",
         "underlying_price": "759.69", "data_source": "TS", "call_put_ratio": "1.2",
         "total_volume": "25951523"},
        {"date": "2026-06-02", "time": "12:50", "symbol": "AAPL", "group": "Top50",
         "tier": "WATCH", "sizzle_index": "4.2", "direction": "LONG",
         "underlying_price": "312.93", "data_source": "TS", "call_put_ratio": "2.0",
         "total_volume": "60000"},
        {"date": "2026-06-02", "time": "12:50", "symbol": "NOPE", "group": "Top50",
         "tier": "NONE", "sizzle_index": "1.0", "direction": "LONG",
         "underlying_price": "10.0", "data_source": "TS"},
    ]

    def test_inserts_approved_only(self):
        n = bridge.bridge_uoa_rows(self.ROWS, db_path=self.db)
        self.assertEqual(n, 2)  # STRONG + WATCH, NONE excluded
        syms = {s["symbol"] for s in self._signals()}
        self.assertEqual(syms, {"SPY", "AAPL"})

    def test_field_mapping(self):
        bridge.bridge_uoa_rows(self.ROWS, db_path=self.db)
        spy = next(s for s in self._signals() if s["symbol"] == "SPY")
        self.assertEqual(spy["strategy"], "UOA")
        self.assertEqual(spy["scan_ts"], "2026-06-02 12:50")
        self.assertEqual(spy["tier"], "STRONG")
        self.assertEqual(spy["direction"], "SHORT")
        self.assertEqual(spy["status"], "APPROVED")
        self.assertEqual(spy["instrument_type"], "EQUITY")
        self.assertAlmostEqual(spy["entry_price"], 759.69, places=2)
        self.assertAlmostEqual(spy["score"], 259.5, places=1)
        self.assertEqual(json.loads(spy["factors"])["group"], "Macro")

    def test_idempotent_rerun(self):
        bridge.bridge_uoa_rows(self.ROWS, db_path=self.db)
        n2 = bridge.bridge_uoa_rows(self.ROWS, db_path=self.db)
        self.assertEqual(n2, 0)
        self.assertEqual(len(self._signals()), 2)


class TestPSA(_BridgeTestBase):
    DATA = {
        "scan_time": "2026-06-02T10:30:00",
        "signals": [
            {"symbol": "MSFT", "price_at_scan": 420.0, "direction": "LONG",
             "score": 8.5, "momentum_pct": 8.5, "volume_pct": 70.0,
             "volatility_pct": 30.0, "patterns": ["higher_highs"],
             "trigger_source": "NONE", "approval_status": "APPROVED",
             "dk_status": "NEUTRAL"},
            {"symbol": "CMG", "price_at_scan": 34.85, "direction": "LONG",
             "score": 930.1, "momentum_pct": 930.1, "volume_pct": 43.2,
             "volatility_pct": 582.0, "patterns": ["volume_expansion"],
             "trigger_source": "NONE", "approval_status": "WATCH",
             "dk_status": "NEUTRAL"},
            {"symbol": "SUPPR", "price_at_scan": 10.0, "direction": "LONG",
             "score": 5.0, "momentum_pct": 5.0, "volume_pct": 20.0,
             "volatility_pct": 10.0, "patterns": [],
             "trigger_source": "NONE", "approval_status": "SUPPRESSED",
             "suppression_reason": "DK_NULLIFYING", "dk_status": "NULLIFYING"},
        ],
    }

    def test_delivers_approved_and_watch(self):
        n = bridge.bridge_psa_result(self.DATA, db_path=self.db)
        self.assertEqual(n, 2)
        symbols = {s["symbol"] for s in self._signals()}
        self.assertEqual(symbols, {"MSFT", "CMG"})

    def test_approved_signal_fields(self):
        bridge.bridge_psa_result(self.DATA, db_path=self.db)
        sig = next(s for s in self._signals() if s["symbol"] == "MSFT")
        self.assertEqual(sig["strategy"], "PSA")
        self.assertEqual(sig["scan_ts"], "2026-06-02T10:30:00")
        self.assertAlmostEqual(sig["score"], 8.5, places=1)
        self.assertAlmostEqual(sig["entry_price"], 420.0, places=1)

    def test_watch_signal_delivered(self):
        bridge.bridge_psa_result(self.DATA, db_path=self.db)
        sig = next(s for s in self._signals() if s["symbol"] == "CMG")
        self.assertEqual(sig["strategy"], "PSA")
        self.assertAlmostEqual(sig["score"], 930.1, places=1)

    def test_suppressed_signal_dropped_with_warning(self):
        with self.assertLogs("prime_bridge.prime_signal_bridge", level="WARNING") as cm:
            bridge.bridge_psa_result(self.DATA, db_path=self.db)
        symbols = {s["symbol"] for s in self._signals()}
        self.assertNotIn("SUPPR", symbols)
        self.assertTrue(any("SUPPRESSED" in msg and "SUPPR" in msg for msg in cm.output))


class TestPEAD(_BridgeTestBase):
    ROWS = [
        {"symbol": "NVDA", "scan_timestamp": "2026-06-02T09:30:00", "direction": "LONG",
         "score": "72.0", "eps_surprise_pct": "15.0", "price_reaction_pct": "3.2",
         "days_since_earnings": 1, "earnings_date": "2026-06-01",
         "price_at_scan": "880.5", "above_threshold": 1},
        {"symbol": "LOW", "scan_timestamp": "2026-06-02T09:30:00", "direction": "SHORT",
         "score": "20.0", "price_at_scan": "200.0", "above_threshold": 0},
    ]

    def test_inserts_above_threshold_only(self):
        n = bridge.bridge_pead_rows(self.ROWS, db_path=self.db)
        self.assertEqual(n, 1)
        sig = self._signals()[0]
        self.assertEqual(sig["symbol"], "NVDA")
        self.assertEqual(sig["strategy"], "PEAD")
        self.assertEqual(sig["direction"], "LONG")
        self.assertAlmostEqual(sig["entry_price"], 880.5, places=1)


class TestMMR(_BridgeTestBase):
    ROWS = [
        {"symbol": "SLV", "price": "31.2", "tranche": "TRANCHE_2", "confidence": "HIGH",
         "rsi": "28.0", "pct_from_sma": "-6.5", "vol_surge_mult": "1.8",
         "scan_ts": "2026-06-02T11:00:00"},
        {"symbol": "GLD", "price": "210.0", "tranche": "WATCH", "confidence": "LOW",
         "vol_surge_mult": "1.1", "scan_ts": "2026-06-02T11:00:00"},
        {"symbol": "GLD", "price": "212.0", "tranche": "SHORT_TRANCHE_2", "confidence": "HIGH",
         "rsi": "72.0", "pct_from_sma": "6.8", "vol_surge_mult": "2.1",
         "scan_ts": "2026-06-02T11:00:00"},
        {"symbol": "GDX", "price": "38.5", "tranche": "SHORT_TRANCHE_1", "confidence": "MEDIUM",
         "rsi": "67.0", "pct_from_sma": "5.2", "vol_surge_mult": "1.6",
         "scan_ts": "2026-06-02T11:00:00"},
    ]

    def test_inserts_tranches_only(self):
        n = bridge.bridge_mmr_rows(self.ROWS, db_path=self.db)
        self.assertEqual(n, 3)  # TRANCHE_2, SHORT_TRANCHE_2, SHORT_TRANCHE_1 in; WATCH out
        tiers = {s["tier"] for s in self._signals()}
        self.assertIn("TRANCHE_2", tiers)
        self.assertIn("SHORT_TRANCHE_2", tiers)
        self.assertIn("SHORT_TRANCHE_1", tiers)

    def test_long_tranche_direction(self):
        bridge.bridge_mmr_rows(self.ROWS, db_path=self.db)
        slv = next(s for s in self._signals() if s["symbol"] == "SLV")
        self.assertEqual(slv["strategy"], "MMR")
        self.assertEqual(slv["tier"], "TRANCHE_2")
        self.assertEqual(slv["direction"], "LONG")
        self.assertAlmostEqual(slv["entry_price"], 31.2, places=1)

    def test_short_tranche_direction(self):
        bridge.bridge_mmr_rows(self.ROWS, db_path=self.db)
        gld = next(s for s in self._signals() if s["symbol"] == "GLD")
        self.assertEqual(gld["strategy"], "MMR")
        self.assertEqual(gld["tier"], "SHORT_TRANCHE_2")
        self.assertEqual(gld["direction"], "SHORT")

    def test_watch_excluded(self):
        bridge.bridge_mmr_rows(self.ROWS, db_path=self.db)
        symbols = [s["symbol"] for s in self._signals()]
        # GLD appears as SHORT_TRANCHE_2 but not as WATCH
        watch_sigs = [s for s in self._signals() if s["tier"] == "WATCH"]
        self.assertEqual(len(watch_sigs), 0)


class TestSRS(_BridgeTestBase):
    DATA = {
        "scan_time": "2026-06-02T08:00:00",
        "sectors": {
            "Technology": {"etf": "XLK", "phase": "RECOVERING",
                           "metrics": {"close": 197.29, "chg_2d_pct": 1.8, "chg_5d_pct": 2.0}},
            "Energy": {"etf": "XLE", "phase": "DECLINING",
                       "metrics": {"close": 90.0, "chg_2d_pct": -2.0, "chg_5d_pct": -4.0}},
            "Financials": {"etf": "XLF", "phase": "BOTTOMING",
                           "metrics": {"close": 45.0, "chg_2d_pct": 0.1, "chg_5d_pct": -2.0}},
        },
    }

    def test_inserts_recovering_only(self):
        n = bridge.bridge_srs_result(self.DATA, db_path=self.db)
        self.assertEqual(n, 1)  # only RECOVERING
        sig = self._signals()[0]
        self.assertEqual(sig["symbol"], "XLK")
        self.assertEqual(sig["strategy"], "SRS")
        self.assertEqual(sig["sector"], "Technology")
        self.assertAlmostEqual(sig["entry_price"], 197.29, places=2)


class TestIngestLatest(_BridgeTestBase):
    """End-to-end: ingest_latest discovers files in a temp scan dir + PEAD DB."""

    def setUp(self):
        super().setUp()
        self.scan_dir = Path(__file__).parent / "_test_scan_results"
        self.scan_dir.mkdir(exist_ok=True)
        self.mon_db = Path(__file__).parent / "_test_monitoring.db"
        for p in self.scan_dir.glob("*"):
            p.unlink()
        if self.mon_db.exists():
            self.mon_db.unlink()

        # UOA CSV
        (self.scan_dir / "live_signals_20260602_1250.csv").write_text(
            "date,time,symbol,group,tier,sizzle_index,direction,underlying_price,data_source\n"
            "2026-06-02,12:50,SPY,Macro,STRONG,259.5,SHORT,759.69,TS\n",
            encoding="utf-8")
        # PSA JSON (v1.0 format)
        (self.scan_dir / "psa_scan_20260602_1030_ET.json").write_text(
            json.dumps({
                "scan_time": "2026-06-02T10:30:00",
                "signals": [
                    {"symbol": "MSFT", "price_at_scan": 420.0, "direction": "LONG",
                     "score": 8.5, "momentum_pct": 8.5, "volume_pct": 70.0,
                     "volatility_pct": 30.0, "patterns": ["higher_highs"],
                     "trigger_source": "NONE", "approval_status": "APPROVED",
                     "dk_status": "NEUTRAL"},
                ],
            }),
            encoding="utf-8")
        # MMR CSV (one LONG + one SHORT row)
        (self.scan_dir / "mmr_signals_20260602_1100.csv").write_text(
            "symbol,price,pct_from_sma,rsi,vol_surge_mult,tranche,confidence,direction,scan_ts\n"
            "SLV,31.2,-6.5,28.0,1.8,TRANCHE_2,HIGH,LONG,2026-06-02T11:00:00\n"
            "GLD,212.0,6.8,72.0,2.1,SHORT_TRANCHE_2,HIGH,SHORT,2026-06-02T11:00:00\n",
            encoding="utf-8")
        # SRS JSON
        (self.scan_dir / "srs_scan_20260602_0800_ET.json").write_text(
            json.dumps(TestSRS.DATA), encoding="utf-8")
        # PEAD monitoring DB
        conn = sqlite3.connect(str(self.mon_db))
        conn.execute("""CREATE TABLE pead_signals (
            symbol TEXT, scan_timestamp TEXT, direction TEXT, score REAL,
            eps_surprise_pct REAL, price_reaction_pct REAL, days_since_earnings INTEGER,
            earnings_date TEXT, price_at_scan REAL, above_threshold INTEGER)""")
        conn.execute("INSERT INTO pead_signals VALUES "
                     "('NVDA','2026-06-02T09:30:00','LONG',72.0,15.0,3.2,1,'2026-06-01',880.5,1)")
        conn.commit()
        conn.close()

    def tearDown(self):
        for p in self.scan_dir.glob("*"):
            p.unlink()
        self.scan_dir.rmdir()
        if self.mon_db.exists():
            self.mon_db.unlink()
        super().tearDown()

    def test_ingest_all_scanners(self):
        results = bridge.ingest_latest(
            scan_dir=self.scan_dir, monitoring_db=self.mon_db, db_path=self.db)
        self.assertEqual(results, {"UOA": 1, "PSA": 1, "PEAD": 1, "MMR": 2, "SRS": 1, "MTFA": 0})
        self.assertEqual(len(self._signals()), 6)
        strategies = {s["strategy"] for s in self._signals()}
        self.assertEqual(strategies, {"UOA", "PSA", "PEAD", "MMR", "SRS"})
        mmr_sigs = [s for s in self._signals() if s["strategy"] == "MMR"]
        directions = {s["direction"] for s in mmr_sigs}
        self.assertEqual(directions, {"LONG", "SHORT"})

    def test_ingest_idempotent(self):
        bridge.ingest_latest(scan_dir=self.scan_dir, monitoring_db=self.mon_db, db_path=self.db)
        results = bridge.ingest_latest(
            scan_dir=self.scan_dir, monitoring_db=self.mon_db, db_path=self.db)
        self.assertEqual(sum(results.values()), 0)
        self.assertEqual(len(self._signals()), 6)


if __name__ == "__main__":
    unittest.main()
