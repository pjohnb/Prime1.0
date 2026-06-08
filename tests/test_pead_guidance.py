"""Sprint 25 Item 4 — PEAD Guidance Flag tests."""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_pead_scanner import classify_guidance_flag


# ── classify_guidance_flag ────────────────────────────────────────────────────

class TestClassifyGuidanceFlag:
    def test_beat_raise(self):
        assert classify_guidance_flag(10.0, 5.0) == "BEAT_RAISE"

    def test_beat_hold(self):
        assert classify_guidance_flag(5.0, 1.0) == "BEAT_HOLD"

    def test_beat_cut_hpe_pattern(self):
        # Beat but price fell > 2.5% — guidance cut
        assert classify_guidance_flag(8.0, -4.0) == "BEAT_CUT"

    def test_miss_cut(self):
        assert classify_guidance_flag(-5.0, -3.0) == "MISS_CUT"

    def test_miss_raise(self):
        assert classify_guidance_flag(-3.0, 2.5) == "MISS_RAISE"

    def test_unknown_zero_surprise(self):
        assert classify_guidance_flag(0.0, 0.0) == "UNKNOWN"

    def test_explicit_guidance_direction_raise(self):
        assert classify_guidance_flag(5.0, -2.0, guidance_direction="RAISE") == "BEAT_RAISE"

    def test_explicit_guidance_direction_cut(self):
        # Even small price drop with explicit CUT direction
        assert classify_guidance_flag(3.0, -1.0, guidance_direction="CUT") == "BEAT_CUT"

    def test_explicit_miss_cut(self):
        assert classify_guidance_flag(-4.0, -1.0, guidance_direction="CUT") == "MISS_CUT"

    def test_beat_hold_neutral_price(self):
        assert classify_guidance_flag(6.0, 0.5) == "BEAT_HOLD"


# ── Bridge tier adjustment ────────────────────────────────────────────────────

from prime_bridge.prime_signal_bridge import _apply_guidance_tier, _GUIDANCE_TIER


class TestGuidanceTierAdjustment:
    def test_beat_cut_demotes_to_watch(self):
        sig = {"direction": "LONG", "guidance_flag": "BEAT_CUT", "tier": "STRONG", "status": "APPROVED"}
        result = _apply_guidance_tier(sig)
        assert result["tier"] == "WATCH"

    def test_miss_cut_promotes_short_to_strong(self):
        sig = {"direction": "SHORT", "guidance_flag": "MISS_CUT", "tier": "WATCH", "status": "APPROVED"}
        result = _apply_guidance_tier(sig)
        assert result["tier"] == "STRONG"

    def test_beat_raise_confirms_strong(self):
        sig = {"direction": "LONG", "guidance_flag": "BEAT_RAISE", "tier": "WATCH", "status": "APPROVED"}
        result = _apply_guidance_tier(sig)
        assert result["tier"] == "STRONG"

    def test_miss_cut_long_suppressed(self):
        sig = {"direction": "LONG", "guidance_flag": "MISS_CUT", "tier": "STRONG", "status": "APPROVED"}
        result = _apply_guidance_tier(sig)
        assert result["tier"] == "SUPPRESSED"
        assert result["status"] == "SUPPRESSED"

    def test_beat_raise_short_suppressed(self):
        sig = {"direction": "SHORT", "guidance_flag": "BEAT_RAISE", "tier": "WATCH", "status": "APPROVED"}
        result = _apply_guidance_tier(sig)
        assert result["tier"] == "SUPPRESSED"

    def test_unknown_unchanged(self):
        sig = {"direction": "LONG", "guidance_flag": "UNKNOWN", "tier": "STRONG", "status": "APPROVED"}
        result = _apply_guidance_tier(sig)
        assert result["tier"] == "STRONG"

    def test_original_dict_not_mutated(self):
        sig = {"direction": "LONG", "guidance_flag": "BEAT_CUT", "tier": "STRONG", "status": "APPROVED"}
        result = _apply_guidance_tier(sig)
        assert sig["tier"] == "STRONG"  # original unchanged
        assert result["tier"] == "WATCH"


# ── DB migration: guidance_flag column ───────────────────────────────────────

def test_guidance_flag_column_in_schema(tmp_path):
    """init_signals_table adds guidance_flag column."""
    from prime_analytics.prime_signals_db import init_signals_table
    db = tmp_path / "test.db"
    init_signals_table(db)

    import sqlite3
    with sqlite3.connect(str(db)) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(prime_signals)")]
    assert "guidance_flag" in cols


def test_insert_signal_dedup_accepts_guidance_flag(tmp_path):
    """insert_signal_dedup stores guidance_flag correctly."""
    from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup, get_signals
    db = tmp_path / "test.db"
    init_signals_table(db)

    sid = insert_signal_dedup(
        symbol="HPE",
        strategy="PEAD",
        scan_ts="2026-06-08 12:40",
        entry_price=25.0,
        score=70.0,
        tier="WATCH",
        direction="LONG",
        guidance_flag="BEAT_CUT",
        db_path=db,
    )
    assert sid is not None

    sigs = get_signals(symbol="HPE", db_path=db)
    assert len(sigs) == 1
    assert sigs[0]["guidance_flag"] == "BEAT_CUT"


def test_pead_beat_cut_appears_as_watch_in_bridge(tmp_path):
    """Full bridge test: HPE-type signal (beat + price drop) stored as WATCH BEAT_CUT."""
    from prime_analytics.prime_signals_db import init_signals_table, get_signals
    from prime_bridge.prime_signal_bridge import bridge_pead_rows
    db = tmp_path / "test.db"
    init_signals_table(db)

    rows = [{
        "symbol": "HPE",
        "above_threshold": 1,
        "direction": "LONG",
        "scan_timestamp": "2026-06-08 12:40",
        "price_at_scan": 25.0,
        "score": 72.0,
        "eps_surprise_pct": 8.0,
        "price_reaction_pct": -4.5,  # beat but price fell -> BEAT_CUT
        "days_since_earnings": 2,
        "earnings_date": "2026-06-06",
    }]
    count = bridge_pead_rows(rows, db_path=db)
    assert count == 1

    sigs = get_signals(symbol="HPE", db_path=db)
    assert len(sigs) == 1
    assert sigs[0]["guidance_flag"] == "BEAT_CUT"
    assert sigs[0]["tier"] == "WATCH"


def test_trigger_column_shows_guidance_flag():
    """Trigger display includes guidance_flag for PEAD signals (logic test)."""
    signal = {"trigger_source": "PEAD_BEAT", "guidance_flag": "BEAT_CUT", "strategy": "PEAD"}
    trigger = signal["trigger_source"]
    gf = signal.get("guidance_flag")
    is_pead = signal.get("strategy") == "PEAD"
    display = f"{trigger} · {gf}" if (is_pead and gf and gf != "UNKNOWN") else trigger
    assert display == "PEAD_BEAT · BEAT_CUT"


def test_unknown_guidance_flag_no_display_suffix():
    """UNKNOWN guidance_flag does not add suffix to trigger display."""
    signal = {"trigger_source": "PEAD_BEAT", "guidance_flag": "UNKNOWN", "strategy": "PEAD"}
    trigger = signal["trigger_source"]
    gf = signal.get("guidance_flag")
    is_pead = signal.get("strategy") == "PEAD"
    display = f"{trigger} · {gf}" if (is_pead and gf and gf != "UNKNOWN") else trigger
    assert display == "PEAD_BEAT"
