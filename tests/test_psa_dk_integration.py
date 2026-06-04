"""
Sprint 20 Item 2 acceptance tests: PSA + Short Scanner DK Integration.

PSA: CONFIRMING -> WATCH->STRONG; APPROVED -> dk_confirming=True;
     NULLIFYING -> SUPPRESSED; NEUTRAL -> unchanged.
Short: NULLIFYING -> tier WATCH->STRONG; CONFIRMING -> dk_blocked;
       NEUTRAL -> unchanged.
"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table, get_signals
from prime_scanners.prime_psa_scanner import _apply_dk_modifier_psa
from prime_intelligence import prime_short_scanner as ss

RTH_NOW = datetime(2026, 6, 4, 11, 0, 0)


def _scan_result(signals):
    """Helper: build a minimal PSA scan_result dict with pre-set approval_status."""
    return {"scan_time": "2026-06-04T12:50:00",
            "signals": list(signals)}


def _sig(symbol, approval_status="APPROVED"):
    return {"symbol": symbol, "score": 50.0, "direction": "LONG",
            "approval_status": approval_status, "trigger_source": "UOA_CALL"}


def _falling_bars(n=60, start=200.0, step=-1.0):
    return [{"close": start + i * step, "volume": 1_000_000} for i in range(n)]


def _flat_spy(n=60, px=100.0):
    return [{"close": px, "volume": 1_000_000} for _ in range(n)]


_GOOD_UOA = {"put_call_ratio": 3.0, "put_premium": 500_000, "dte": 14,
             "put_volume": 40_000, "put_vol_avg_20d": 10_000}


class TestPSADkModifier(unittest.TestCase):
    """_apply_dk_modifier_psa() direct unit tests -- no DB needed."""

    def _run(self, signals, dk_map):
        """Run the DK modifier with a mocked get_dk_status."""
        def _mock_get(symbol, db_path=None):
            return dk_map.get(symbol, {"dk_status": "NEUTRAL", "dk_conviction": None})
        result = _scan_result(signals)
        with patch("prime_scanners.prime_psa_scanner.get_dk_status", _mock_get):
            # Import the internal helper directly; bypass module-level guard.
            from prime_scanners import prime_psa_scanner as psa
            orig = None
            try:
                from prime_intelligence.prime_dk_trader import get_dk_status as _gds
                orig = _gds
            except Exception:
                pass
            import prime_scanners.prime_psa_scanner as _m
            _m_real = _m.get_dk_status if hasattr(_m, "get_dk_status") else None
        with patch("prime_intelligence.prime_dk_trader.get_dk_status", _mock_get):
            return _apply_dk_modifier_psa(result, db_path=None)

    def _run_direct(self, signals, dk_map):
        def _mock(symbol, db_path=None):
            return dk_map.get(symbol.upper(),
                              {"dk_status": "NEUTRAL", "dk_conviction": None})
        result = _scan_result(signals)
        with patch("prime_intelligence.prime_dk_trader.get_dk_status", _mock):
            return _apply_dk_modifier_psa(result, db_path=None)

    def test_watch_plus_confirming_becomes_strong(self):
        out = self._run_direct(
            [_sig("AAA", approval_status="WATCH")],
            {"AAA": {"dk_status": "CONFIRMING", "dk_conviction": 0.8}})
        sig = out["signals"][0]
        self.assertEqual(sig["approval_status"], "STRONG")
        self.assertEqual(sig["dk_status"], "CONFIRMING")
        self.assertAlmostEqual(sig["dk_conviction"], 0.8)
        self.assertEqual(out["dk_watch_upgraded"], 1)

    def test_approved_plus_confirming_gets_flag(self):
        out = self._run_direct(
            [_sig("AAA", approval_status="APPROVED")],
            {"AAA": {"dk_status": "CONFIRMING", "dk_conviction": 0.7}})
        sig = out["signals"][0]
        self.assertEqual(sig["approval_status"], "APPROVED")
        self.assertTrue(sig.get("dk_confirming"))
        self.assertEqual(out["dk_watch_upgraded"], 0)

    def test_nullifying_suppresses(self):
        out = self._run_direct(
            [_sig("BBB", approval_status="APPROVED")],
            {"BBB": {"dk_status": "NULLIFYING", "dk_conviction": 0.9}})
        sig = out["signals"][0]
        self.assertEqual(sig["approval_status"], "SUPPRESSED")
        self.assertEqual(sig["suppression_reason"], "DK_NULLIFYING")
        self.assertEqual(out["dk_nullified_count"], 1)

    def test_neutral_passes_unchanged(self):
        out = self._run_direct(
            [_sig("CCC", approval_status="APPROVED")],
            {"CCC": {"dk_status": "NEUTRAL", "dk_conviction": None}})
        sig = out["signals"][0]
        self.assertEqual(sig["approval_status"], "APPROVED")
        self.assertFalse(sig.get("dk_confirming", False))
        self.assertIsNone(sig.get("suppression_reason"))

    def test_dk_status_and_conviction_stamped_on_all(self):
        out = self._run_direct(
            [_sig("AAA"), _sig("BBB"), _sig("CCC")],
            {
                "AAA": {"dk_status": "CONFIRMING", "dk_conviction": 0.75},
                "BBB": {"dk_status": "NULLIFYING", "dk_conviction": 0.9},
                "CCC": {"dk_status": "NEUTRAL",    "dk_conviction": None},
            })
        by = {s["symbol"]: s for s in out["signals"]}
        self.assertEqual(by["AAA"]["dk_status"], "CONFIRMING")
        self.assertEqual(by["BBB"]["dk_status"], "NULLIFYING")
        self.assertEqual(by["CCC"]["dk_status"], "NEUTRAL")
        self.assertIsNone(by["CCC"]["dk_conviction"])


class TestShortDkIntegration(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_short_dk2.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self.bars = {"SPY": _flat_spy(), "WEAK": _falling_bars()}

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _run(self, **kw):
        # Default dk_signals=set() so the scan doesn't load from DB; callers
        # override with dk_verdicts={...} or dk_signals={...} as needed.
        defaults = dict(symbols=["WEAK"], bars_by_symbol=self.bars,
                        borrow_fn=lambda s: {"borrowable": True, "rate_pct": 1.0},
                        dk_signals=set(), now=RTH_NOW, db_path=self.db)
        defaults.update(kw)
        return ss.run_short_scan(**defaults)

    def test_nullifying_upgrades_watch_to_strong(self):
        s = self._run(
            uoa_by_symbol={"WEAK": _GOOD_UOA},
            dk_verdicts={"WEAK": {"state": "NULLIFYING", "conviction": 0.85}})
        self.assertIn("WEAK", s["written"])
        self.assertIn("WEAK", s["dk_upgraded"])
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "STRONG")
        f = json.loads(row["factors"])
        self.assertEqual(f["dk_state"], "NULLIFYING")

    def test_confirming_blocks_short(self):
        s = self._run(
            uoa_by_symbol={"WEAK": _GOOD_UOA},
            dk_verdicts={"WEAK": {"state": "CONFIRMING", "conviction": 0.9}})
        self.assertIn("WEAK", s["dk_blocked"])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])

    def test_neutral_passes_unchanged(self):
        s = self._run(
            uoa_by_symbol={"WEAK": _GOOD_UOA},
            dk_verdicts={"WEAK": {"state": "NEUTRAL", "conviction": None}})
        self.assertIn("WEAK", s["written"])
        self.assertEqual(s["dk_upgraded"], [])
        self.assertEqual(s["dk_blocked"], [])
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        self.assertEqual(row["tier"], "WATCH")  # single trigger -> WATCH, no upgrade

    def test_legacy_dk_signals_compat(self):
        # dk_signals set (old interface) treated as CONFIRMING -> blocks short.
        s = self._run(
            uoa_by_symbol={"WEAK": _GOOD_UOA},
            dk_signals={"WEAK"})
        self.assertIn("WEAK", s["dk_blocked"])
        self.assertEqual(get_signals(strategy="SHORT", db_path=self.db), [])

    def test_both_triggers_nullifying_still_strong(self):
        _GOOD_PEAD = {"earnings_miss": True, "guidance_cut": True,
                      "days_since_earnings": 2, "still_elevated": True}
        s = self._run(
            uoa_by_symbol={"WEAK": _GOOD_UOA},
            pead_by_symbol={"WEAK": _GOOD_PEAD},
            dk_verdicts={"WEAK": {"state": "NULLIFYING", "conviction": 0.9}})
        row = get_signals(strategy="SHORT", db_path=self.db)[0]
        # both triggers already produce STRONG; NULLIFYING does not downgrade
        self.assertEqual(row["tier"], "STRONG")

    def test_dk_upgraded_in_summary(self):
        s = self._run(
            uoa_by_symbol={"WEAK": _GOOD_UOA},
            dk_verdicts={"WEAK": {"state": "NULLIFYING", "conviction": 0.7}})
        self.assertIn("dk_upgraded", s)
        self.assertIn("WEAK", s["dk_upgraded"])


if __name__ == "__main__":
    unittest.main()
