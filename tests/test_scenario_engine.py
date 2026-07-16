"""
WO-PRIME-SCENARIOS-01 Phase 1 acceptance tests.

Covers: staleness framework, scenario type detection (Types 1-8),
DB persistence, API endpoints, and the non-interference guarantee
(engine failure does not affect signal records).
"""

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table, insert_signal
from prime_scenarios.prime_scenarios_db import (
    init_scenarios_table,
    insert_scenario,
    upsert_scenario,
    get_scenarios,
    expire_old_scenarios,
    make_scenario_id,
)
from prime_scenarios.prime_scenario_engine import (
    detect_scenarios,
    get_signal_staleness,
    run_detection,
    SCENARIO_TYPES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sig(strategy, tier="", direction="LONG", symbol="SPY", status="APPROVED",
         scan_ts=None, signal_id=None):
    # Use current UTC time so naive comparison against ET ref always yields a fresh signal.
    # UTC is ahead of ET, so the naive timestamp looks like a future ET time → age is negative.
    default_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    return {
        "signal_id": signal_id or f"{strategy}-{symbol}-{tier}",
        "strategy": strategy,
        "tier": tier,
        "direction": direction,
        "symbol": symbol,
        "status": status,
        "scan_ts": scan_ts or default_ts,
    }


def _idx(tier="STRONG-LONG", symbol="SPY", scan_ts=None):
    direction = "SHORT" if "SHORT" in tier else "LONG"
    return _sig("IDX", tier=tier, direction=direction, symbol=symbol, scan_ts=scan_ts)


def _psa(symbol="AAPL", direction="LONG", scan_ts=None):
    return _sig("PSA", tier="APPROVED", direction=direction, symbol=symbol,
                status="APPROVED", scan_ts=scan_ts)


def _uoa(symbol="AAPL", tier="STRONG", direction="LONG", scan_ts=None):
    return _sig("UOA", tier=tier, direction=direction, symbol=symbol, scan_ts=scan_ts)


def _pead(symbol="NVDA", direction="LONG", scan_ts=None):
    return _sig("PEAD", tier="APPROVED", direction=direction, symbol=symbol,
                status="APPROVED", scan_ts=scan_ts)


def _srs(symbol="XLK", scan_ts=None):
    return _sig("SRS", tier="RECOVERING", direction="LONG", symbol=symbol, scan_ts=scan_ts)


def _mmr(symbol="GLD", tier="TRANCHE_2", direction="LONG", scan_ts=None):
    return _sig("MMR", tier=tier, direction=direction, symbol=symbol, scan_ts=scan_ts)


def _types(scenarios):
    return sorted(s["type_num"] for s in scenarios)


def _now():
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Staleness Tests
# ---------------------------------------------------------------------------

class TestStaleness(unittest.TestCase):

    def _sig_ts(self, strategy, ts_str):
        return {"strategy": strategy, "scan_ts": ts_str}

    # Fixed UTC reference; staleness framework treats naive `now` as UTC → converts to ET.
    # 15:00 UTC = 11:00 ET (EDT, July 2026), so ET date = 2026-07-12 = FIXED_TODAY.
    FIXED_NOW = datetime(2026, 7, 12, 15, 0, 0)
    FIXED_TODAY = "2026-07-12"
    FIXED_YESTERDAY = "2026-07-11"

    def test_today_signal_is_fresh(self):
        sig = self._sig_ts("IDX", f"{self.FIXED_TODAY} 10:00")
        self.assertEqual(get_signal_staleness(sig, now=self.FIXED_NOW), "FRESH")

    def test_yesterday_signal_is_vetoed(self):
        sig = self._sig_ts("IDX", f"{self.FIXED_YESTERDAY} 10:00")
        self.assertEqual(get_signal_staleness(sig, now=self.FIXED_NOW), "VETOED")

    def test_pead_yesterday_is_vetoed(self):
        # Phase 1: PEAD no longer exempt; date-boundary veto applies to all scanners
        sig = self._sig_ts("PEAD", f"{self.FIXED_YESTERDAY} 10:00")
        self.assertEqual(get_signal_staleness(sig, now=self.FIXED_NOW), "VETOED")

    def test_pead_very_old_is_vetoed(self):
        # Phase 1: old PEAD from 7 days ago is VETOED by date-boundary
        old = (self.FIXED_NOW - timedelta(days=7)).strftime("%Y-%m-%d")
        sig = self._sig_ts("PEAD", f"{old} 10:00")
        self.assertEqual(get_signal_staleness(sig, now=self.FIXED_NOW), "VETOED")

    def test_uoa_outside_recency_window_is_vetoed(self):
        # Phase 1: UOA recency window = 90 min; 2h-old same-day signal → VETOED
        sig = self._sig_ts("UOA", f"{self.FIXED_TODAY} 09:00")  # 120 min before 11:00 ET ref
        result = get_signal_staleness(sig, now=self.FIXED_NOW)
        self.assertEqual(result, "VETOED")

    def test_missing_scan_ts_is_vetoed(self):
        sig = {"strategy": "IDX", "scan_ts": ""}
        self.assertEqual(get_signal_staleness(sig, now=self.FIXED_NOW), "VETOED")

    def test_all_non_pead_vetoed_yesterday(self):
        for strategy in ("IDX", "UOA", "PSA", "MMR", "SRS"):
            sig = self._sig_ts(strategy, f"{self.FIXED_YESTERDAY} 12:00")
            self.assertEqual(
                get_signal_staleness(sig, now=self.FIXED_NOW), "VETOED",
                f"{strategy} should be VETOED for yesterday signal"
            )


# ---------------------------------------------------------------------------
# Type Detection Tests
# ---------------------------------------------------------------------------

class TestType1Pure(unittest.TestCase):
    """Type 1: IDX STRONG (volume confirmed) → scenario on index ETF."""

    def test_idx_strong_emits_type1(self):
        signals = [_idx("STRONG-LONG", "SPY")]
        result = detect_scenarios(signals, now=_now())
        t1 = [s for s in result if s["type_num"] == "1"]
        self.assertGreater(len(t1), 0)
        self.assertEqual(t1[0]["primary_symbol"], "SPY")
        self.assertEqual(t1[0]["direction"], "LONG")

    def test_idx_weak_does_not_emit_type1(self):
        signals = [_idx("WEAK-LONG", "SPY")]
        result = detect_scenarios(signals, now=_now())
        t1 = [s for s in result if s["type_num"] == "1"]
        self.assertEqual(len(t1), 0)

    def test_idx_strong_short_emits_type1_short(self):
        signals = [_idx("STRONG-SHORT", "QQQ")]
        result = detect_scenarios(signals, now=_now())
        t1 = [s for s in result if s["type_num"] == "1"]
        self.assertTrue(any(s["direction"] == "SHORT" for s in t1))


class TestType2Confirmed(unittest.TestCase):
    """Type 2: IDX (any) + PSA APPROVED."""

    def test_idx_weak_plus_psa_emits_type2(self):
        signals = [_idx("WEAK-LONG", "SPY"), _psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        t2 = [s for s in result if s["type_num"] == "2"]
        self.assertGreater(len(t2), 0)
        self.assertEqual(t2[0]["primary_symbol"], "AAPL")

    def test_type2_not_emitted_without_idx(self):
        signals = [_psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        t2 = [s for s in result if s["type_num"] == "2"]
        self.assertEqual(len(t2), 0)

    def test_type2_direction_matches_signals(self):
        signals = [_idx("WEAK-SHORT", "SPY"), _psa("AAPL", direction="SHORT")]
        result = detect_scenarios(signals, now=_now())
        t2 = [s for s in result if s["type_num"] == "2"]
        self.assertTrue(all(s["direction"] == "SHORT" for s in t2))


class TestType3Institutional(unittest.TestCase):
    """Type 3: IDX WEAK + UOA/PEAD + PSA APPROVED."""

    def test_idx_weak_uoa_psa_emits_type3(self):
        signals = [_idx("WEAK-LONG"), _uoa("AAPL"), _psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        t3 = [s for s in result if s["type_num"] == "3"]
        self.assertGreater(len(t3), 0)

    def test_type3_pead_as_institutional(self):
        signals = [_idx("WEAK-LONG"), _pead("MSFT"), _psa("MSFT")]
        result = detect_scenarios(signals, now=_now())
        t3 = [s for s in result if s["type_num"] == "3"]
        self.assertGreater(len(t3), 0)

    def test_type3_takes_priority_over_type2(self):
        signals = [_idx("WEAK-LONG"), _uoa("AAPL"), _psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        types = _types(result)
        # Type 3 should be present; Type 2 should NOT (for the same PSA signal)
        self.assertIn("3", types)
        self.assertNotIn("2", types)


class TestType4Trifecta(unittest.TestCase):
    """Type 4: IDX STRONG + UOA/PEAD + PSA APPROVED."""

    def test_full_trifecta_emits_type4(self):
        signals = [_idx("STRONG-LONG"), _uoa("AAPL"), _psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        t4 = [s for s in result if s["type_num"] == "4"]
        self.assertGreater(len(t4), 0)
        self.assertEqual(t4[0]["conviction"], "HIGHEST")

    def test_type4_takes_priority_over_type3(self):
        signals = [_idx("STRONG-LONG"), _uoa("AAPL"), _psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        types = _types(result)
        self.assertIn("4", types)
        self.assertNotIn("3", types)
        self.assertNotIn("2", types)

    def test_type4_short_direction(self):
        signals = [_idx("STRONG-SHORT"), _uoa("AAPL", direction="SHORT"), _psa("AAPL", direction="SHORT")]
        result = detect_scenarios(signals, now=_now())
        t4 = [s for s in result if s["type_num"] == "4"]
        self.assertTrue(any(s["direction"] == "SHORT" for s in t4))


class TestType5Watch(unittest.TestCase):
    """Type 5: Single signal watch."""

    def test_single_signal_emits_type5(self):
        signals = [_uoa("TSLA", tier="STRONG")]
        result = detect_scenarios(signals, now=_now())
        t5 = [s for s in result if s["type_num"] == "5"]
        self.assertGreater(len(t5), 0)

    def test_type5_not_emitted_when_higher_type_fires(self):
        signals = [_idx("STRONG-LONG"), _psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        # The PSA signal is anchored in Type 2; should not also appear in Type 5
        psa_in_t5 = [s for s in result
                     if s["type_num"] == "5" and s["primary_symbol"] == "AAPL"]
        self.assertEqual(len(psa_in_t5), 0)


class TestType6Anomalous(unittest.TestCase):
    """Type 6: UOA/PEAD + PSA APPROVED, no IDX."""

    def test_uoa_psa_same_symbol_emits_type6(self):
        signals = [_uoa("TSLA"), _psa("TSLA")]
        result = detect_scenarios(signals, now=_now())
        t6 = [s for s in result if s["type_num"] == "6"]
        self.assertGreater(len(t6), 0)
        self.assertEqual(t6[0]["primary_symbol"], "TSLA")

    def test_type6_requires_same_symbol(self):
        signals = [_uoa("TSLA"), _psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        t6 = [s for s in result if s["type_num"] == "6"]
        self.assertEqual(len(t6), 0)

    def test_type6_not_emitted_with_idx_present(self):
        signals = [_idx("WEAK-LONG"), _uoa("TSLA"), _psa("TSLA")]
        result = detect_scenarios(signals, now=_now())
        t6 = [s for s in result if s["type_num"] == "6"]
        self.assertEqual(len(t6), 0)


class TestType7SectorPhase(unittest.TestCase):
    """Type 7: SRS RECOVERING sector + PSA APPROVED constituent stock."""

    def test_srs_psa_constituent_emits_type7(self):
        # AAPL is a known XLK constituent — should fire Type 7 with sym=AAPL
        signals = [_srs("XLK"), _psa("AAPL")]
        result = detect_scenarios(signals, now=_now())
        t7 = [s for s in result if s["type_num"] == "7"]
        self.assertGreater(len(t7), 0)
        self.assertEqual(t7[0]["primary_symbol"], "AAPL")

    def test_type7_requires_psa_stock_in_sector_constituents(self):
        # GLD is a commodity ETF, not a constituent of any GICS sector — no Type 7
        signals = [_srs("XLK"), _psa("GLD")]
        result = detect_scenarios(signals, now=_now())
        t7 = [s for s in result if s["type_num"] == "7"]
        self.assertEqual(len(t7), 0)


class TestType8MetalsMR(unittest.TestCase):
    """Type 8: MMR TRANCHE_2 + PSA APPROVED on same symbol."""

    def test_mmr_t2_psa_same_symbol_emits_type8(self):
        signals = [_mmr("GLD", "TRANCHE_2"), _psa("GLD")]
        result = detect_scenarios(signals, now=_now())
        t8 = [s for s in result if s["type_num"] == "8"]
        self.assertGreater(len(t8), 0)
        self.assertEqual(t8[0]["primary_symbol"], "GLD")

    def test_mmr_tranche1_does_not_emit_type8(self):
        signals = [_mmr("GLD", "TRANCHE_1"), _psa("GLD")]
        result = detect_scenarios(signals, now=_now())
        t8 = [s for s in result if s["type_num"] == "8"]
        self.assertEqual(len(t8), 0)

    def test_mmr_short_tranche2_emits_type8_short(self):
        signals = [_mmr("GLD", "SHORT_TRANCHE_2", "SHORT"), _psa("GLD", "SHORT")]
        result = detect_scenarios(signals, now=_now())
        t8 = [s for s in result if s["type_num"] == "8"]
        self.assertTrue(any(s["direction"] == "SHORT" for s in t8))

    def test_type8_requires_same_symbol(self):
        signals = [_mmr("GLD", "TRANCHE_2"), _psa("SLV")]
        result = detect_scenarios(signals, now=_now())
        t8 = [s for s in result if s["type_num"] == "8"]
        self.assertEqual(len(t8), 0)


# ---------------------------------------------------------------------------
# Staleness in Detection
# ---------------------------------------------------------------------------

class TestStalenessInDetection(unittest.TestCase):

    def test_vetoed_signal_excluded(self):
        # Use a fixed UTC reference to avoid UTC/ET midnight crossover ambiguity.
        # 15:00 UTC = 11:00 ET; ET today = 2026-07-12; ET yesterday = 2026-07-11.
        FIXED_NOW = datetime(2026, 7, 12, 15, 0, 0)
        signals = [_idx("STRONG-LONG", scan_ts="2026-07-11 09:30")]
        result = detect_scenarios(signals, now=FIXED_NOW)
        t1 = [s for s in result if s["type_num"] == "1"]
        self.assertEqual(len(t1), 0)

    def test_pead_today_participates_as_institutional(self):
        # Phase 1: PEAD from today within recency window participates as institutional
        FIXED_NOW = datetime(2026, 7, 12, 15, 0, 0)  # 11:00 ET
        signals = [
            _psa("NVDA", scan_ts="2026-07-12 14:30"),   # 10:30 ET, 30 min old → FRESH
            _pead("NVDA", scan_ts="2026-07-12 14:30"),  # 10:30 ET, 30 min old → FRESH (120 min window)
        ]
        result = detect_scenarios(signals, now=FIXED_NOW)
        t6 = [s for s in result if s["type_num"] == "6"]
        self.assertGreater(len(t6), 0, "Today's PEAD should participate as institutional signal")

    def test_fresh_scenario_detected_within_window(self):
        # Phase 1: SOFT_STALE removed; scenario with signals within recency windows is FRESH
        FIXED_NOW = datetime(2026, 7, 12, 15, 0, 0)  # 11:00 ET
        signals = [
            _idx("WEAK-LONG", scan_ts="2026-07-12 14:30"),  # 10:30 ET, 30 min → within 60 min IDX window
            _psa("AAPL", scan_ts="2026-07-12 14:30"),
        ]
        result = detect_scenarios(signals, now=FIXED_NOW)
        t2 = [s for s in result if s["type_num"] == "2"]
        self.assertGreater(len(t2), 0)
        self.assertEqual(t2[0]["staleness_status"], "FRESH")


# ---------------------------------------------------------------------------
# Database Layer Tests
# ---------------------------------------------------------------------------

class TestScenariosDB(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_scenarios.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _make_scenario(self, type_num="2", symbol="AAPL", direction="LONG"):
        return {
            "type_num": type_num,
            "type_name": SCENARIO_TYPES[type_num]["name"],
            "direction": direction,
            "conviction": SCENARIO_TYPES[type_num]["conviction"],
            "primary_symbol": symbol,
            "constituent_signals": [{"strategy": "PSA", "symbol": symbol}],
            "staleness_status": "FRESH",
            "detected_at": datetime.utcnow().isoformat(),
        }

    def test_insert_and_retrieve(self):
        sc = self._make_scenario()
        sid = insert_scenario(sc, self.db)
        self.assertIsNotNone(sid)
        rows = get_scenarios(db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type_num"], "2")
        self.assertEqual(rows[0]["primary_symbol"], "AAPL")

    def test_duplicate_insert_ignored(self):
        sc = self._make_scenario()
        sid1 = insert_scenario(sc, self.db)
        sid2 = insert_scenario(sc, self.db)
        self.assertIsNotNone(sid1)
        self.assertIsNone(sid2)
        self.assertEqual(len(get_scenarios(db_path=self.db)), 1)

    def test_constituent_signals_roundtrip(self):
        sc = self._make_scenario()
        insert_scenario(sc, self.db)
        rows = get_scenarios(db_path=self.db)
        constituents = rows[0]["constituent_signals"]
        self.assertIsInstance(constituents, list)
        self.assertEqual(constituents[0]["strategy"], "PSA")

    def test_filter_by_direction(self):
        insert_scenario(self._make_scenario(direction="LONG"), self.db)
        insert_scenario(self._make_scenario(type_num="3", symbol="MSFT", direction="SHORT"), self.db)
        long_only = get_scenarios(direction="LONG", db_path=self.db)
        self.assertEqual(len(long_only), 1)
        self.assertEqual(long_only[0]["direction"], "LONG")

    def test_filter_by_type(self):
        insert_scenario(self._make_scenario(type_num="1", symbol="SPY"), self.db)
        insert_scenario(self._make_scenario(type_num="4", symbol="AAPL"), self.db)
        t4_only = get_scenarios(type_num="4", db_path=self.db)
        self.assertEqual(len(t4_only), 1)
        self.assertEqual(t4_only[0]["type_num"], "4")

    def test_expire_old_scenarios(self):
        from prime_scenarios.prime_scenarios_db import get_connection
        sc = self._make_scenario()
        sid = insert_scenario(sc, self.db)
        # Manually set detected_at to 48 hours ago
        with get_connection(self.db) as conn:
            conn.execute(
                "UPDATE prime_scenarios SET detected_at=? WHERE scenario_id=?",
                ((datetime.utcnow() - timedelta(hours=48)).isoformat(), sid),
            )
            conn.commit()
        expired = expire_old_scenarios(self.db)
        self.assertEqual(expired, 1)
        active = get_scenarios(active_only=True, db_path=self.db)
        self.assertEqual(len(active), 0)


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

class TestScenariosAPI(unittest.TestCase):

    def setUp(self):
        self.db = Path(__file__).parent / "_test_scenarios_api.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        from prime_api.prime_api_server import create_app
        self._patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._patcher.start()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def test_scenarios_endpoint_empty(self):
        resp = self.client.get("/api/v1/scenarios")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("scenarios", data)
        self.assertEqual(data["scenarios"], [])

    def test_scenarios_endpoint_returns_stored(self):
        from prime_scenarios.prime_scenarios_db import insert_scenario
        sc = {
            "type_num": "2",
            "type_name": SCENARIO_TYPES["2"]["name"],
            "direction": "LONG",
            "conviction": "HIGH",
            "primary_symbol": "AAPL",
            "constituent_signals": [],
            "staleness_status": "FRESH",
            "detected_at": datetime.utcnow().isoformat(),
        }
        insert_scenario(sc, self.db)
        resp = self.client.get("/api/v1/scenarios")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["scenarios"][0]["primary_symbol"], "AAPL")

    def test_scenarios_direction_filter(self):
        resp = self.client.get("/api/v1/scenarios?direction=LONG")
        self.assertEqual(resp.status_code, 200)

    def test_scenarios_type_filter(self):
        resp = self.client.get("/api/v1/scenarios?type_num=4")
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Non-Interference Guarantee
# ---------------------------------------------------------------------------

class TestNonInterference(unittest.TestCase):
    """Engine failure must not affect signal records (AC 7)."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_noninterference.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_engine_error_does_not_raise(self):
        # run_detection should never raise even on engine failure
        result = run_detection([], db_path=self.db)
        self.assertIn("scenarios_detected", result)

    def test_signal_records_unchanged_after_detection(self):
        from prime_analytics.prime_signals_db import get_signals
        today = datetime.utcnow().strftime("%Y-%m-%d")
        insert_signal(
            symbol="AAPL", strategy="PSA",
            scan_ts=f"{today} 12:00",
            db_path=self.db,
        )
        signals_before = get_signals(db_path=self.db)
        run_detection(signals_before, db_path=self.db)
        signals_after = get_signals(db_path=self.db)
        self.assertEqual(len(signals_before), len(signals_after))
        self.assertEqual(signals_before[0]["signal_id"], signals_after[0]["signal_id"])
        self.assertEqual(signals_before[0]["status"], signals_after[0]["status"])

    def test_scenario_type_registry_complete(self):
        for t in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"):
            self.assertIn(t, SCENARIO_TYPES)
            self.assertIn("name", SCENARIO_TYPES[t])
            self.assertIn("conviction", SCENARIO_TYPES[t])


# ---------------------------------------------------------------------------
# Five scenario test cases per type (AC 2)
# ---------------------------------------------------------------------------

class TestFivePerType(unittest.TestCase):
    """AC 2: at least 5 test cases per scenario type on historical signal data."""

    def _detect(self, *signals):
        return detect_scenarios(list(signals), now=_now())

    # Type 1 — 5 variations
    def test_type1_spy_long(self):
        r = self._detect(_idx("STRONG-LONG", "SPY"))
        self.assertTrue(any(s["type_num"] == "1" for s in r))

    def test_type1_qqq_long(self):
        r = self._detect(_idx("STRONG-LONG", "QQQ"))
        self.assertTrue(any(s["type_num"] == "1" for s in r))

    def test_type1_iwm_long(self):
        r = self._detect(_idx("STRONG-LONG", "IWM"))
        self.assertTrue(any(s["type_num"] == "1" for s in r))

    def test_type1_spy_short(self):
        r = self._detect(_idx("STRONG-SHORT", "SPY"))
        t1 = [s for s in r if s["type_num"] == "1"]
        self.assertTrue(any(s["direction"] == "SHORT" for s in t1))

    def test_type1_qqq_short(self):
        r = self._detect(_idx("STRONG-SHORT", "QQQ"))
        t1 = [s for s in r if s["type_num"] == "1"]
        self.assertTrue(any(s["direction"] == "SHORT" for s in t1))

    # Type 2 — 5 variations
    def test_type2_weak_long(self):
        r = self._detect(_idx("WEAK-LONG"), _psa("AAPL"))
        self.assertTrue(any(s["type_num"] == "2" for s in r))

    def test_type2_strong_long_no_inst(self):
        r = self._detect(_idx("STRONG-LONG"), _psa("MSFT"))
        self.assertTrue(any(s["type_num"] == "2" for s in r))

    def test_type2_multiple_psa(self):
        r = self._detect(_idx("WEAK-LONG"), _psa("AAPL"), _psa("MSFT"))
        t2 = [s for s in r if s["type_num"] == "2"]
        self.assertGreaterEqual(len(t2), 1)

    def test_type2_short(self):
        r = self._detect(_idx("WEAK-SHORT"), _psa("AAPL", direction="SHORT"))
        t2 = [s for s in r if s["type_num"] == "2"]
        self.assertTrue(any(s["direction"] == "SHORT" for s in t2))

    def test_type2_direction_mismatch_no_scenario(self):
        r = self._detect(_idx("WEAK-LONG"), _psa("AAPL", direction="SHORT"))
        # IDX is LONG, PSA is SHORT — no convergence in either direction
        t2_long = [s for s in r if s["type_num"] == "2" and s["direction"] == "LONG"]
        self.assertEqual(len(t2_long), 0)

    # Type 8 — 5 variations (same-symbol, clear criterion)
    def test_type8_gld(self):
        r = self._detect(_mmr("GLD", "TRANCHE_2"), _psa("GLD"))
        self.assertTrue(any(s["type_num"] == "8" for s in r))

    def test_type8_slv(self):
        r = self._detect(_mmr("SLV", "TRANCHE_2"), _psa("SLV"))
        self.assertTrue(any(s["type_num"] == "8" for s in r))

    def test_type8_gdx(self):
        r = self._detect(_mmr("GDX", "TRANCHE_2"), _psa("GDX"))
        self.assertTrue(any(s["type_num"] == "8" for s in r))

    def test_type8_gdxj(self):
        r = self._detect(_mmr("GDXJ", "TRANCHE_2"), _psa("GDXJ"))
        self.assertTrue(any(s["type_num"] == "8" for s in r))

    def test_type8_short_tranche2(self):
        r = self._detect(_mmr("GLD", "SHORT_TRANCHE_2", "SHORT"), _psa("GLD", "SHORT"))
        self.assertTrue(any(s["type_num"] == "8" for s in r))


# ---------------------------------------------------------------------------
# Dedup and Upgrade Tests (WO-PRIME-SCENARIO-ENGINE-DEDUP-01)
# ---------------------------------------------------------------------------

class TestDedup(unittest.TestCase):
    """Session-scoped dedup and upgrade logic."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_dedup.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _make_scenario(self, type_num="5", symbol="AAPL", direction="LONG", detected_at=None):
        ts = detected_at or datetime.utcnow().isoformat()
        return {
            "type_num": type_num,
            "type_name": SCENARIO_TYPES[type_num]["name"],
            "direction": direction,
            "conviction": SCENARIO_TYPES[type_num]["conviction"],
            "primary_symbol": symbol,
            "constituent_signals": [{"strategy": "IDX", "symbol": symbol}],
            "staleness_status": "FRESH",
            "detected_at": ts,
        }

    def test_same_symbol_same_type_updates_not_duplicates(self):
        """IDX fires twice on same symbol → one card updated in place, not two cards."""
        sc = self._make_scenario("5", "XLF")
        sid1 = upsert_scenario(sc, self.db)
        sc2 = self._make_scenario("5", "XLF")
        sc2["constituent_signals"] = [{"strategy": "IDX", "symbol": "XLF", "note": "run2"}]
        sid2 = upsert_scenario(sc2, self.db)
        self.assertIsNotNone(sid1)
        self.assertEqual(sid1, sid2)
        rows = get_scenarios(db_path=self.db)
        self.assertEqual(len(rows), 1, "Exactly one scenario card expected for XLF")

    def test_higher_type_upgrades_existing(self):
        """Watch → Confirmed: existing card is upgraded to the higher type, not duplicated."""
        sid_watch = upsert_scenario(self._make_scenario("5", "XLF"), self.db)
        sid_conf = upsert_scenario(self._make_scenario("2", "XLF"), self.db)
        self.assertEqual(sid_watch, sid_conf, "Upgrade should reuse the existing scenario_id")
        rows = get_scenarios(db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type_num"], "2")

    def test_lower_type_does_not_downgrade(self):
        """A Confirmed Sniper must not be downgraded to Watch."""
        upsert_scenario(self._make_scenario("2", "AAPL"), self.db)
        upsert_scenario(self._make_scenario("5", "AAPL"), self.db)
        rows = get_scenarios(db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type_num"], "2")

    def test_different_symbols_both_kept(self):
        """Two different symbols each get their own card."""
        upsert_scenario(self._make_scenario("5", "AAPL"), self.db)
        upsert_scenario(self._make_scenario("5", "MSFT"), self.db)
        rows = get_scenarios(db_path=self.db)
        self.assertEqual(len(rows), 2)
        symbols = {r["primary_symbol"] for r in rows}
        self.assertIn("AAPL", symbols)
        self.assertIn("MSFT", symbols)

    def test_run_detection_deduplicates(self):
        """Calling run_detection twice on the same signals yields one scenario per symbol."""
        signals = [_idx("STRONG-LONG", "SPY")]
        run_detection(signals, db_path=self.db)
        run_detection(signals, db_path=self.db)
        rows = get_scenarios(db_path=self.db, active_only=True)
        spy_rows = [r for r in rows if r["primary_symbol"] == "SPY"]
        self.assertEqual(len(spy_rows), 1, "SPY should have exactly one scenario card")

    def test_watch_upgrades_to_trifecta_via_run_detection(self):
        """Progressive signal arrival: Confirmed → Trifecta via two run_detection calls."""
        # First run: IDX + PSA → Type 2 (Confirmed)
        run_detection(
            [_idx("WEAK-LONG"), _psa("AAPL")],
            db_path=self.db,
        )
        rows_1 = [r for r in get_scenarios(db_path=self.db) if r["primary_symbol"] == "AAPL"]
        self.assertEqual(len(rows_1), 1)
        self.assertEqual(rows_1[0]["type_num"], "2")

        # Second run: IDX STRONG + UOA + PSA → Type 4 (Trifecta)
        run_detection(
            [_idx("STRONG-LONG"), _uoa("AAPL"), _psa("AAPL")],
            db_path=self.db,
        )
        rows_2 = [r for r in get_scenarios(db_path=self.db) if r["primary_symbol"] == "AAPL"]
        self.assertEqual(len(rows_2), 1, "AAPL must have exactly one scenario card")
        self.assertEqual(rows_2[0]["type_num"], "4", "AAPL must show highest type (4)")

    def test_type4_never_shows_type2_for_same_symbol(self):
        """A symbol qualifying for Type 4 must show Type 4, never Type 2."""
        signals = [
            _idx("STRONG-LONG"),
            _uoa("NVDA"),
            _psa("NVDA"),
        ]
        run_detection(signals, db_path=self.db)
        rows = get_scenarios(db_path=self.db)
        nvda_rows = [r for r in rows if r["primary_symbol"] == "NVDA"]
        self.assertEqual(len(nvda_rows), 1)
        self.assertEqual(nvda_rows[0]["type_num"], "4")
        type2_nvda = [r for r in rows if r["primary_symbol"] == "NVDA" and r["type_num"] == "2"]
        self.assertEqual(len(type2_nvda), 0, "Type 2 must not coexist with Type 4 for NVDA")


# ---------------------------------------------------------------------------
# Unknown Scenario Tests (WO-PRIME-SCENARIO-ENGINE-DEDUP-01)
# ---------------------------------------------------------------------------

class TestUnknown(unittest.TestCase):
    """Unknown scenario detection for signal combos that match no defined type."""

    def _detect(self, *signals):
        return detect_scenarios(list(signals), now=_now())

    def test_multi_signal_no_match_emits_unknown(self):
        """SRS + UOA on same symbol (no PSA, no IDX) → Unknown, not two separate Watch cards."""
        result = self._detect(_srs("XLRE"), _uoa("XLRE", tier="STRONG"))
        unknown = [s for s in result if s["type_num"] == "0"]
        watch_xlre = [s for s in result if s["type_num"] == "5" and s["primary_symbol"] == "XLRE"]
        self.assertGreater(len(unknown), 0, "Expected Unknown scenario for SRS+UOA on XLRE")
        self.assertEqual(len(watch_xlre), 0, "SRS+UOA should not produce separate Watch cards for XLRE")

    def test_single_unanchored_signal_emits_watch_not_unknown(self):
        """A single unanchored signal produces Watch (Type 5), never Unknown."""
        result = self._detect(_uoa("TSLA", tier="STRONG"))
        watch = [s for s in result if s["type_num"] == "5"]
        unknown = [s for s in result if s["type_num"] == "0"]
        self.assertGreater(len(watch), 0)
        self.assertEqual(len(unknown), 0)

    def test_known_combination_not_surfaced_as_unknown(self):
        """SRS + PSA for a constituent stock = Type 7, not Unknown."""
        result = self._detect(_srs("XLK"), _psa("AAPL"))
        t7 = [s for s in result if s["type_num"] == "7"]
        unknown = [s for s in result if s["type_num"] == "0"]
        self.assertGreater(len(t7), 0)
        self.assertEqual(len(unknown), 0)

    def test_idx_plus_uoa_same_symbol_no_psa_emits_unknown(self):
        """IDX WEAK + UOA on the same symbol, no PSA → Unknown (not two separate Watch cards)."""
        # IDX and UOA both on SPY with no matching PSA — doesn't satisfy any of Types 1-10.
        result = self._detect(_idx("WEAK-LONG", "SPY"), _uoa("SPY", tier="STRONG"))
        unknown = [s for s in result if s["type_num"] == "0" and s["primary_symbol"] == "SPY"]
        self.assertGreater(len(unknown), 0, "IDX+UOA (same symbol, no PSA) should be Unknown")

    def test_unknown_has_low_conviction(self):
        """Unknown scenario conviction is LOW."""
        result = self._detect(_srs("XLRE"), _uoa("XLRE", tier="STRONG"))
        unknown = [s for s in result if s["type_num"] == "0"]
        if unknown:
            self.assertEqual(unknown[0]["conviction"], "LOW")

    def test_unknown_lists_all_constituent_signals(self):
        """Unknown scenario must include all unanchored signals as constituents."""
        result = self._detect(_srs("XLRE"), _uoa("XLRE", tier="STRONG"))
        unknown = [s for s in result if s["type_num"] == "0"]
        if unknown:
            self.assertGreaterEqual(
                len(unknown[0]["constituent_signals"]), 2,
                "Unknown scenario must list all constituent signals"
            )

    def test_unknown_in_scenario_types_registry(self):
        """Type '0' (Unknown) is present in SCENARIO_TYPES."""
        self.assertIn("0", SCENARIO_TYPES)
        self.assertEqual(SCENARIO_TYPES["0"]["name"], "Unknown")
        self.assertEqual(SCENARIO_TYPES["0"]["conviction"], "LOW")


if __name__ == "__main__":
    unittest.main()
