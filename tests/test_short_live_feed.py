"""
Sprint 18 Item 2 (Short scanner live feed wiring) acceptance tests.

Verifies the short scanner reads its UOA-put / PEAD-miss primary triggers from
prime_signals (the live feed) when no trigger dicts are injected, the Schwab
locate live call is wired with the fail-safe preserved, and the integration:
a borrowable symbol with a put trigger produces a signal; a non-borrowable one
is hard-blocked.
"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup, get_signals
from prime_intelligence import prime_short_scanner as ss
from prime_intelligence import prime_signal_triggers as trig
from prime_trading import prime_schwab_borrow as borrow

RTH_NOW = datetime(2026, 6, 3, 11, 0, 0)
SCAN_TS = "2026-06-03T12:45:00"


def _falling_bars(n=60, start=200.0, step=-1.0):
    return [{"close": start + i * step, "volume": 1_000_000} for i in range(n)]


def _flat_spy(n=60, px=100.0):
    return [{"close": px, "volume": 1_000_000} for _ in range(n)]


class TestLiveTriggerFeed(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_short_live.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _add_uoa_put(self, symbol):
        insert_signal_dedup(symbol=symbol, strategy="UOA", scan_ts="2026-06-03 12:40",
                            direction="SHORT", tier="STRONG", status="APPROVED",
                            factors=json.dumps({"call_put_ratio": 0.3}), db_path=self.db)

    def _add_pead_miss(self, symbol):
        insert_signal_dedup(symbol=symbol, strategy="PEAD", scan_ts="2026-06-02 12:40",
                            direction="SHORT", status="APPROVED",
                            factors=json.dumps({"eps_surprise_pct": -6.0}), db_path=self.db)

    def test_live_uoa_put_feed_read(self):
        self._add_uoa_put("WEAK")
        ref = datetime.fromisoformat(SCAN_TS)
        self.assertTrue(trig.uoa_put_signal_present("WEAK", self.db, ref))
        self.assertEqual(trig.short_primary_triggers_from_signals("WEAK", self.db, ref),
                         ["UOA_PUT"])

    def test_live_pead_miss_feed_read(self):
        self._add_pead_miss("WEAK")
        ref = datetime.fromisoformat(SCAN_TS)
        self.assertIn("PEAD_MISS", trig.short_primary_triggers_from_signals("WEAK", self.db, ref))

    def test_scan_uses_live_feed_and_writes_signal(self):
        self._add_uoa_put("WEAK")
        s = ss.run_short_scan(
            symbols=["WEAK"], scan_ts=SCAN_TS,
            bars_by_symbol={"SPY": _flat_spy(), "WEAK": _falling_bars()},
            borrow_fn=lambda sym: {"borrowable": True, "rate_pct": 1.0},
            now=RTH_NOW, db_path=self.db)
        self.assertEqual(s["written"], ["WEAK"])
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["direction"], "SHORT")
        self.assertIn("UOA_PUT", json.loads(row["factors"])["trigger_source"])

    def test_live_feed_trigger_is_reduced_contract(self):
        # CALC-SHORT-2: the live feed only has bare direction/ratio evidence
        # (upstream UOA/PEAD scanners don't populate premium/DTE/volume), so
        # every live-feed-triggered signal is REDUCED, never FULL.
        self._add_uoa_put("WEAK")
        ss.run_short_scan(
            symbols=["WEAK"], scan_ts=SCAN_TS,
            bars_by_symbol={"SPY": _flat_spy(), "WEAK": _falling_bars()},
            borrow_fn=lambda sym: {"borrowable": True, "rate_pct": 1.0},
            now=RTH_NOW, db_path=self.db)
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        factors = json.loads(row["factors"])
        self.assertEqual(factors["trigger_contract"], "REDUCED")

    def test_live_feed_watch_reduced_contract_score(self):
        self._add_uoa_put("WEAK")
        ss.run_short_scan(
            symbols=["WEAK"], scan_ts=SCAN_TS,
            bars_by_symbol={"SPY": _flat_spy(), "WEAK": _falling_bars()},
            borrow_fn=lambda sym: {"borrowable": True, "rate_pct": 1.0},
            now=RTH_NOW, db_path=self.db)
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "WATCH")
        self.assertAlmostEqual(row["score"], 33.3)

    def test_live_feed_strong_reduced_contract_score_lower_than_full(self):
        self._add_uoa_put("WEAK")
        self._add_pead_miss("WEAK")
        ss.run_short_scan(
            symbols=["WEAK"], scan_ts=SCAN_TS,
            bars_by_symbol={"SPY": _flat_spy(), "WEAK": _falling_bars()},
            borrow_fn=lambda sym: {"borrowable": True, "rate_pct": 1.0},
            now=RTH_NOW, db_path=self.db)
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "STRONG")
        self.assertEqual(row["score"], 50.0)
        self.assertLess(row["score"], 100.0)  # FULL-contract STRONG scores 100

    def test_no_signal_in_db_rejects(self):
        s = ss.run_short_scan(
            symbols=["WEAK"], scan_ts=SCAN_TS,
            bars_by_symbol={"SPY": _flat_spy(), "WEAK": _falling_bars()},
            borrow_fn=lambda sym: {"borrowable": True}, now=RTH_NOW, db_path=self.db)
        self.assertIn("WEAK", s["rejected"])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])

    def test_integration_borrowable_passes_unborrowable_blocked(self):
        self._add_uoa_put("SPYX")    # borrowable
        self._add_uoa_put("PENNY")   # not borrowable
        locate = {"SPYX": True, "PENNY": False}
        s = ss.run_short_scan(
            symbols=["SPYX", "PENNY"], scan_ts=SCAN_TS,
            bars_by_symbol={"SPY": _flat_spy(), "SPYX": _falling_bars(), "PENNY": _falling_bars()},
            borrow_fn=lambda sym: {"borrowable": locate.get(sym, False), "rate_pct": 1.0},
            now=RTH_NOW, db_path=self.db)
        self.assertIn("SPYX", s["written"])
        self.assertIn("PENNY", s["borrow_blocked"])
        syms = {r["symbol"] for r in get_signals(strategy="SHORT", db_path=self.db)}
        self.assertIn("SPYX", syms)
        self.assertNotIn("PENNY", syms)


class TestSchwabLocateWiring(unittest.TestCase):
    def test_locate_via_get_locate(self):
        class FakeClient:
            def get_locate(self, sym):
                return {"borrowable": True, "rate_pct": 1.5}
        out = borrow._schwab_locate(FakeClient(), "AAPL")
        self.assertTrue(out["borrowable"])
        self.assertEqual(out["rate_pct"], 1.5)

    def test_locate_missing_endpoint_raises(self):
        class NoLocate:
            client = None
        with self.assertRaises(RuntimeError):
            borrow._schwab_locate(NoLocate(), "AAPL")

    def test_check_borrow_failsafe_when_locate_errors(self):
        def boom(_):
            raise RuntimeError("api error")
        out = borrow.check_borrow("AAPL", borrow_fn=boom)
        self.assertFalse(out["borrowable"])
        self.assertEqual(out["source"], "unavailable")


if __name__ == "__main__":
    unittest.main()
