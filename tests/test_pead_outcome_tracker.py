"""CIL-058: PEAD outcome tracker -- grouping, deciles, overall, empty history."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _seed_trade(conn, log_id, signal_id, symbol, pnl_dollars, pnl_pct, hold_min):
    conn.execute(
        """
        INSERT INTO prime_trade_log
          (log_id, symbol, direction, shares, entry_price, exit_price, strategy,
           account, trade_source, status, entry_time, exit_time,
           pnl_dollars, pnl_pct, hold_minutes, mode, order_type, signal_id)
        VALUES (?, ?, 'LONG', 10, 100.0, ?, 'PEAD',
                'PAPER', 'PAPER', 'CLOSED', '2026-06-01 09:30:00', '2026-06-02 10:00:00',
                ?, ?, ?, 'PAPER', 'MARKET', ?)
        """,
        (log_id, symbol, 100.0 + pnl_pct, pnl_dollars, pnl_pct, hold_min, signal_id),
    )


@pytest.fixture()
def populated_db(tmp_path):
    db = tmp_path / "pead_outcomes.db"
    from prime_data.prime_db import init_db
    from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup

    init_db(db_path=db)
    init_signals_table(db_path=db)

    # Four PEAD signals with known guidance_flag + eps_surprise, each with a
    # matching closed trade (win/loss mix).
    seeds = [
        # symbol, guidance_flag, eps_surprise, pnl_dollars, pnl_pct, hold
        ("AAA", "BEAT_RAISE", 12.0, 150.0, 6.0, 120),   # win
        ("BBB", "BEAT_RAISE", 8.0, -40.0, -2.0, 90),    # loss
        ("CCC", "MISS_CUT", -15.0, 75.0, 3.0, 200),     # win
        ("DDD", "BEAT_HOLD", 4.0, -10.0, -0.5, 60),     # loss
    ]
    # Phase 1: insert signals (each opens its own connection via the db layer).
    sids = []
    for i, (sym, flag, eps, _pnl_d, _pnl_p, _hold) in enumerate(seeds):
        sid = insert_signal_dedup(
            symbol=sym,
            strategy="PEAD",
            scan_ts=f"2026-06-01 09:0{i}:00",
            entry_price=100.0,
            score=70.0,
            tier="STRONG",
            status="APPROVED",
            direction="LONG" if eps > 0 else "SHORT",
            factors=json.dumps({"eps_surprise": eps, "guidance_flag": flag}),
            trigger_source="PEAD_BEAT" if eps > 0 else "PEAD_MISS",
            guidance_flag=flag,
            db_path=db,
        )
        sids.append(sid)

    # Phase 2: insert matching closed trades on a single fresh connection.
    conn = sqlite3.connect(db)
    for i, (sym, _flag, _eps, pnl_d, pnl_p, hold) in enumerate(seeds):
        _seed_trade(conn, f"t-{i}", sids[i], sym, pnl_d, pnl_p, hold)
    conn.commit()
    conn.close()
    return db


class TestEmptyHistory:

    def test_nonexistent_db_no_error(self, tmp_path):
        from prime_ml.prime_pead_outcome_tracker import get_pead_outcome_summary
        out = get_pead_outcome_summary(db_path=tmp_path / "nope.db")
        assert out["overall"]["trade_count"] == 0
        assert out["by_guidance_flag"] == []
        assert out["by_eps_surprise_decile"] == []

    def test_empty_initialised_db(self, tmp_path):
        from prime_data.prime_db import init_db
        from prime_ml.prime_pead_outcome_tracker import get_pead_outcome_summary
        db = tmp_path / "empty.db"
        init_db(db_path=db)
        out = get_pead_outcome_summary(db_path=db)
        assert out["overall"]["trade_count"] == 0


class TestStructuredOutput:

    def test_returns_expected_keys(self, populated_db):
        from prime_ml.prime_pead_outcome_tracker import get_pead_outcome_summary
        out = get_pead_outcome_summary(db_path=populated_db)
        for key in ("by_guidance_flag", "by_eps_surprise_decile", "overall", "as_of"):
            assert key in out
        assert isinstance(out["by_guidance_flag"], list)
        assert isinstance(out["overall"], dict)

    def test_overall_counts_all_closed_pead(self, populated_db):
        from prime_ml.prime_pead_outcome_tracker import get_pead_outcome_summary
        out = get_pead_outcome_summary(db_path=populated_db)
        assert out["overall"]["trade_count"] == 4
        # 2 wins of 4 -> 50%
        assert out["overall"]["win_rate_pct"] == 50.0


class TestGrouping:

    def test_grouping_by_guidance_flag(self, populated_db):
        from prime_ml.prime_pead_outcome_tracker import get_pead_outcome_summary
        out = get_pead_outcome_summary(db_path=populated_db)
        flags = {g["guidance_flag"]: g for g in out["by_guidance_flag"]}
        assert set(flags) == {"BEAT_RAISE", "MISS_CUT", "BEAT_HOLD"}
        # BEAT_RAISE has two trades (one win, one loss) -> 50% win rate.
        assert flags["BEAT_RAISE"]["trade_count"] == 2
        assert flags["BEAT_RAISE"]["win_rate_pct"] == 50.0
        # MISS_CUT single win -> 100%.
        assert flags["MISS_CUT"]["trade_count"] == 1
        assert flags["MISS_CUT"]["win_rate_pct"] == 100.0

    def test_decile_buckets_present(self, populated_db):
        from prime_ml.prime_pead_outcome_tracker import get_pead_outcome_summary
        out = get_pead_outcome_summary(db_path=populated_db)
        deciles = out["by_eps_surprise_decile"]
        assert deciles, "expected at least one eps_surprise decile bucket"
        assert sum(d["trade_count"] for d in deciles) == 4
        for d in deciles:
            assert d["decile_label"].startswith("D")


class TestCli:

    def test_cli_runs(self, populated_db):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "prime_ml.prime_pead_outcome_tracker",
             "--db", str(populated_db)],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        parsed = json.loads(result.stdout)
        assert "overall" in parsed
        assert parsed["overall"]["trade_count"] == 4
