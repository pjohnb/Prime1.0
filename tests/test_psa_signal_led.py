"""
Sprint 18 Item 1 (PSA signal-led retrofit) acceptance tests.

A predictive trigger (UOA call surge or PEAD beat in prime_signals) must fire
before PSA reaches APPROVED; technically-strong candidates without a trigger are
WATCH. use_signal_led_psa=false reverts to legacy technical-only (all APPROVED).
"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup
from prime_scanners.prime_psa_scanner import apply_signal_led_psa
from prime_intelligence import prime_signal_triggers as trig

SCAN_TS = "2026-06-03T12:50:00"


def _scan_result(symbols):
    return {"scan_time": SCAN_TS,
            "signals": [{"symbol": s, "score": 50.0, "direction": "LONG"} for s in symbols]}


class _Base(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).parent / "_test_psa_sl.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self.on_cfg = Path(__file__).parent / "_psa_on.json"
        self.off_cfg = Path(__file__).parent / "_psa_off.json"
        self.on_cfg.write_text(json.dumps({"use_signal_led_psa": True}))
        self.off_cfg.write_text(json.dumps({"use_signal_led_psa": False}))

    def tearDown(self):
        for p in (self.db, self.on_cfg, self.off_cfg):
            if p.exists():
                p.unlink()

    def _add_uoa_call(self, symbol):
        insert_signal_dedup(symbol=symbol, strategy="UOA", scan_ts="2026-06-03 12:40",
                            direction="LONG", tier="STRONG", status="APPROVED",
                            factors=json.dumps({"call_put_ratio": 3.0, "total_volume": 50000}),
                            db_path=self.db)

    def _add_pead_beat(self, symbol):
        insert_signal_dedup(symbol=symbol, strategy="PEAD", scan_ts="2026-06-02 12:40",
                            direction="LONG", status="APPROVED",
                            factors=json.dumps({"eps_surprise_pct": 8.0, "days_since_earnings": 1}),
                            db_path=self.db)


class TestTriggers(_Base):
    def test_uoa_call_trigger_detected(self):
        self._add_uoa_call("AAA")
        ref = datetime.fromisoformat(SCAN_TS)
        self.assertTrue(trig.uoa_call_trigger("AAA", self.db, ref))
        self.assertEqual(trig.psa_trigger_source("AAA", self.db, ref), "UOA_CALL")

    def test_pead_beat_trigger_detected(self):
        self._add_pead_beat("BBB")
        ref = datetime.fromisoformat(SCAN_TS)
        self.assertTrue(trig.pead_long_trigger("BBB", self.db, ref))
        self.assertEqual(trig.psa_trigger_source("BBB", self.db, ref), "PEAD_BEAT")

    def test_no_trigger_none(self):
        ref = datetime.fromisoformat(SCAN_TS)
        self.assertEqual(trig.psa_trigger_source("CCC", self.db, ref), "NONE")

    def test_put_dominant_uoa_not_a_call_trigger(self):
        insert_signal_dedup(symbol="DDD", strategy="UOA", scan_ts="2026-06-03 12:40",
                            direction="SHORT", tier="STRONG", status="APPROVED",
                            factors=json.dumps({"call_put_ratio": 0.3}), db_path=self.db)
        ref = datetime.fromisoformat(SCAN_TS)
        self.assertFalse(trig.uoa_call_trigger("DDD", self.db, ref))


class TestApplySignalLed(_Base):
    def test_uoa_trigger_approves(self):
        self._add_uoa_call("AAA")
        out = apply_signal_led_psa(_scan_result(["AAA"]), db_path=self.db, config_path=self.on_cfg)
        sig = out["signals"][0]
        self.assertEqual(sig["approval_status"], "APPROVED")
        self.assertEqual(sig["trigger_source"], "UOA_CALL")
        self.assertEqual(out["approved_count"], 1)

    def test_pead_trigger_approves(self):
        self._add_pead_beat("BBB")
        out = apply_signal_led_psa(_scan_result(["BBB"]), db_path=self.db, config_path=self.on_cfg)
        self.assertEqual(out["signals"][0]["approval_status"], "APPROVED")
        self.assertEqual(out["signals"][0]["trigger_source"], "PEAD_BEAT")

    def test_no_trigger_is_watch_not_approved(self):
        out = apply_signal_led_psa(_scan_result(["CCC"]), db_path=self.db, config_path=self.on_cfg)
        sig = out["signals"][0]
        self.assertEqual(sig["approval_status"], "WATCH")
        self.assertEqual(sig["trigger_source"], "NONE")
        self.assertEqual(out["watch_count"], 1)

    def test_toggle_off_reverts_to_legacy(self):
        # No trigger present, but toggle off -> legacy technical-only APPROVES all.
        out = apply_signal_led_psa(_scan_result(["CCC"]), db_path=self.db, config_path=self.off_cfg)
        sig = out["signals"][0]
        self.assertEqual(sig["approval_status"], "APPROVED")
        self.assertEqual(sig["trigger_source"], "NONE")
        self.assertFalse(out["signal_led"])

    def test_mixed_batch(self):
        self._add_uoa_call("AAA")
        out = apply_signal_led_psa(_scan_result(["AAA", "CCC"]), db_path=self.db, config_path=self.on_cfg)
        by = {s["symbol"]: s["approval_status"] for s in out["signals"]}
        self.assertEqual(by["AAA"], "APPROVED")
        self.assertEqual(by["CCC"], "WATCH")


if __name__ == "__main__":
    unittest.main()
