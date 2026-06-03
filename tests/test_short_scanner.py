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


if __name__ == "__main__":
    unittest.main()
