"""
Sprint 17 Item 1 (Short-Side Signal-Led Scanner) acceptance tests.

Signal-led: a primary trigger (UOA_PUT or PEAD_MISS) is REQUIRED; technical
weakness alone is REJECTED. Covers: UOA-put alone -> WATCH, PEAD-miss alone ->
WATCH, both -> STRONG, technical-only -> REJECTED, DK SIGNAL hard-block, borrow
hard-block (Principle 1) + ops_health logging, and trigger_source population.
"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db, get_ops_events
from prime_analytics.prime_signals_db import init_signals_table, get_signals
from prime_intelligence import prime_short_scanner as ss

RTH_NOW = datetime(2026, 6, 3, 11, 0, 0)  # Wed 11:00 -> regular hours


def _falling_bars(n=60, start=200.0, step=-1.0):
    # steadily falling -> price below 50-SMA, weak vs a flat SPY
    return [{"close": start + i * step, "volume": 1_000_000} for i in range(n)]


def _flat_spy(n=60, px=100.0):
    return [{"close": px, "volume": 1_000_000} for _ in range(n)]


_GOOD_UOA = {"put_call_ratio": 3.0, "put_premium": 500_000, "dte": 14,
             "put_volume": 40_000, "put_vol_avg_20d": 10_000}
_GOOD_PEAD = {"earnings_miss": True, "guidance_cut": True,
              "days_since_earnings": 2, "still_elevated": True}


class TestPrimaryTriggers(unittest.TestCase):
    def test_uoa_put_trigger_fires(self):
        self.assertTrue(ss.uoa_put_trigger(_GOOD_UOA))

    def test_uoa_put_trigger_needs_all(self):
        self.assertFalse(ss.uoa_put_trigger({**_GOOD_UOA, "put_call_ratio": 1.5}))
        self.assertFalse(ss.uoa_put_trigger({**_GOOD_UOA, "dte": 60}))
        self.assertFalse(ss.uoa_put_trigger({**_GOOD_UOA, "put_volume": 11_000}))
        self.assertFalse(ss.uoa_put_trigger({**_GOOD_UOA, "put_premium": 1000}))

    def test_pead_short_trigger_fires(self):
        self.assertTrue(ss.pead_short_trigger(_GOOD_PEAD))

    def test_pead_needs_all(self):
        self.assertFalse(ss.pead_short_trigger({**_GOOD_PEAD, "guidance_cut": False}))
        self.assertFalse(ss.pead_short_trigger({**_GOOD_PEAD, "days_since_earnings": 9}))
        self.assertFalse(ss.pead_short_trigger({**_GOOD_PEAD, "still_elevated": False}))


class TestClassification(unittest.TestCase):
    def test_both_triggers_strong(self):
        v = ss.classify_short(["UOA_PUT", "PEAD_MISS"], confirms=True)
        self.assertEqual(v["classification"], "STRONG_SHORT")
        self.assertEqual(v["tier"], "STRONG")

    def test_one_trigger_watch(self):
        v = ss.classify_short(["UOA_PUT"], confirms=True)
        self.assertEqual(v["classification"], "WATCH")
        self.assertEqual(v["tier"], "WATCH")

    def test_no_trigger_rejected(self):
        self.assertIsNone(ss.classify_short([], confirms=True))

    def test_trigger_without_confirmation_rejected(self):
        self.assertIsNone(ss.classify_short(["UOA_PUT"], confirms=False))


class TestScanOrchestration(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_short_scan.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self.bars = {"SPY": _flat_spy(), "WEAK": _falling_bars()}

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _run(self, **kw):
        defaults = dict(symbols=["WEAK"], bars_by_symbol=self.bars,
                        borrow_fn=lambda s: {"borrowable": True, "rate_pct": 1.0},
                        dk_signals=set(), now=RTH_NOW, db_path=self.db)
        defaults.update(kw)
        return ss.run_short_scan(**defaults)

    def test_uoa_alone_is_watch(self):
        s = self._run(uoa_by_symbol={"WEAK": _GOOD_UOA})
        self.assertEqual(s["written"], ["WEAK"])
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "WATCH")
        self.assertEqual(row["direction"], "SHORT")
        self.assertEqual(json.loads(row["factors"])["trigger_source"], "UOA_PUT")

    # -- CALC-SHORT-2/4: trigger contract disclosure + non-zero score --

    def test_injected_evidence_is_full_contract_score_100(self):
        # Injected uoa_by_symbol/pead_by_symbol carry premium/DTE/volume --
        # the documented/tested contract -- so trigger_contract == FULL.
        s = self._run(uoa_by_symbol={"WEAK": _GOOD_UOA},
                      pead_by_symbol={"WEAK": _GOOD_PEAD})
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        factors = json.loads(row["factors"])
        self.assertEqual(factors["trigger_contract"], "FULL")
        self.assertEqual(row["score"], 100.0)

    def test_strong_signal_score_nonzero(self):
        self._run(uoa_by_symbol={"WEAK": _GOOD_UOA}, pead_by_symbol={"WEAK": _GOOD_PEAD})
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "STRONG")
        self.assertGreater(row["score"], 0)

    def test_watch_signal_score_nonzero(self):
        self._run(uoa_by_symbol={"WEAK": _GOOD_UOA})
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "WATCH")
        self.assertGreater(row["score"], 0)

    def test_pead_alone_is_watch(self):
        s = self._run(pead_by_symbol={"WEAK": _GOOD_PEAD})
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "WATCH")
        self.assertEqual(json.loads(row["factors"])["trigger_source"], "PEAD_MISS")

    def test_both_triggers_strong(self):
        s = self._run(uoa_by_symbol={"WEAK": _GOOD_UOA},
                      pead_by_symbol={"WEAK": _GOOD_PEAD})
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "STRONG")
        ts = json.loads(row["factors"])["trigger_source"]
        self.assertIn("UOA_PUT", ts)
        self.assertIn("PEAD_MISS", ts)

    def test_technical_only_rejected(self):
        # No trigger data -> technical-only -> never enters prime_signals.
        s = self._run()
        self.assertIn("WEAK", s["rejected"])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])

    def test_trigger_without_confirmation_rejected(self):
        # Trigger fires but the stock is NOT below its 50-SMA (rising) -> unconfirmed.
        rising = {"SPY": _flat_spy(), "WEAK": _falling_bars(start=100.0, step=2.0)}
        s = self._run(uoa_by_symbol={"WEAK": _GOOD_UOA}, bars_by_symbol=rising)
        self.assertIn("WEAK", s["unconfirmed"])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])

    def test_dk_signal_hard_blocks(self):
        s = self._run(uoa_by_symbol={"WEAK": _GOOD_UOA}, dk_signals={"WEAK"})
        self.assertIn("WEAK", s["dk_blocked"])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])
        events = get_ops_events(component="short_scanner", db_path=self.db)
        self.assertTrue(any("dk_bullish_block" in (e["detail"] or "") for e in events))

    def test_borrow_unavailable_hard_blocks_and_logs(self):
        s = self._run(uoa_by_symbol={"WEAK": _GOOD_UOA},
                      borrow_fn=lambda sym: {"borrowable": False})
        self.assertIn("WEAK", s["borrow_blocked"])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])
        events = get_ops_events(component="short_scanner", db_path=self.db)
        self.assertTrue(any("borrow_unavailable" in (e["detail"] or "") for e in events))

    def test_borrow_rate_stored_on_signal(self):
        self._run(uoa_by_symbol={"WEAK": _GOOD_UOA},
                  borrow_fn=lambda sym: {"borrowable": True, "rate_pct": 2.5})
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["borrow_rate_pct"], 2.5)

    def test_outside_rth_blocks_all(self):
        after_hours = datetime(2026, 6, 3, 18, 0, 0)
        s = self._run(uoa_by_symbol={"WEAK": _GOOD_UOA}, now=after_hours)
        self.assertTrue(s["rth_blocked"])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])

    # -- AUDIT-032: default scan_ts is machine-local (ET), not UTC --
    def test_default_scan_ts_is_local_not_utc(self):
        s = self._run(uoa_by_symbol={"WEAK": _GOOD_UOA}, scan_ts=None)
        before = datetime.now()
        recorded = datetime.fromisoformat(s["scan_ts"])
        self.assertLess(abs((recorded - before).total_seconds()), 5)


class TestScanDedup(unittest.TestCase):
    """AUDIT-032: SHORT scan uses upsert_signal_by_session so two runs on the
    same calendar day update one row instead of inserting a duplicate."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_short_dedup.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self.bars = {"SPY": _flat_spy(), "WEAK": _falling_bars()}

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _run(self, scan_ts):
        return ss.run_short_scan(
            symbols=["WEAK"], bars_by_symbol=self.bars,
            uoa_by_symbol={"WEAK": _GOOD_UOA},
            borrow_fn=lambda s: {"borrowable": True, "rate_pct": 1.0},
            dk_signals=set(), now=RTH_NOW, db_path=self.db, scan_ts=scan_ts,
        )

    def test_second_run_same_day_does_not_add_rows(self):
        self._run("2026-06-03T08:00:00")
        self._run("2026-06-03T12:50:00")
        rows = get_signals(strategy="SHORT", db_path=self.db)
        self.assertEqual(len(rows), 1)

    def test_second_run_updates_scan_ts(self):
        self._run("2026-06-03T08:00:00")
        self._run("2026-06-03T12:50:00")
        rows = get_signals(strategy="SHORT", db_path=self.db)
        self.assertEqual(rows[0]["symbol"], "WEAK")
        self.assertIn("12:50", rows[0]["scan_ts"])

    def test_different_day_creates_new_row(self):
        self._run("2026-06-03T08:00:00")
        self._run("2026-06-04T08:00:00")
        rows = get_signals(strategy="SHORT", db_path=self.db)
        self.assertEqual(len(rows), 2)


class TestSpyBenchmarkFetchFailure(unittest.TestCase):
    """CALC-SHORT-1: a SPY benchmark fetch failure must be an ops-visible
    ERROR, never silently masqueraded as normal 'unconfirmed' rejections."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_short_spy_fail.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _run(self, bars_by_symbol):
        return ss.run_short_scan(
            symbols=["WEAK"], bars_by_symbol=bars_by_symbol,
            uoa_by_symbol={"WEAK": _GOOD_UOA},
            borrow_fn=lambda sym: {"borrowable": True, "rate_pct": 1.0},
            now=RTH_NOW, db_path=self.db,
        )

    def test_missing_spy_key_aborts_with_rc_1(self):
        s = self._run({"WEAK": _falling_bars()})
        self.assertTrue(s["spy_fetch_failed"])
        self.assertEqual(s["rc"], 1)
        self.assertEqual(s["scanned"], 0)  # aborted before scanning any symbol
        self.assertEqual(s["unconfirmed"], [])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])

    def test_empty_spy_bars_aborts_with_rc_1(self):
        s = self._run({"SPY": [], "WEAK": _falling_bars()})
        self.assertTrue(s["spy_fetch_failed"])
        self.assertEqual(s["rc"], 1)
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])

    def test_spy_failure_logs_error_event(self):
        self._run({"WEAK": _falling_bars()})
        events = get_ops_events(component="short_scanner", db_path=self.db)
        error_events = [e for e in events if e.get("severity") == "ERROR"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("SPY benchmark fetch FAILED", error_events[0]["detail"])

    def test_normal_spy_fetch_unchanged(self):
        s = self._run({"SPY": _flat_spy(), "WEAK": _falling_bars()})
        self.assertFalse(s["spy_fetch_failed"])
        self.assertEqual(s["rc"], 0)
        self.assertEqual(s["written"], ["WEAK"])

    def test_spy_failure_surfaces_as_health_degraded(self):
        from prime_ops.prime_health_monitor import check_scanner_health
        self._run({"WEAK": _falling_bars()})
        health = check_scanner_health(db_path=self.db)
        short_health = next(h for h in health if h["scanner"] == "short_scanner")
        self.assertEqual(short_health["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
