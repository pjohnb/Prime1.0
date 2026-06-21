"""
PRIME v1.0 PEAD Outcome Tracker (CIL-058).

Summarises realised outcomes of closed PEAD trades so the guidance_flag and
EPS-surprise classifications can be validated against actual win-rate and P&L.

Closed PEAD trades are read from prime_trade_log and joined back to their
originating signal in prime_signals (via signal_id) to recover the
guidance_flag and eps_surprise that drove the entry. Both fields are written by
the scanner's direct-persistence path (CIL-046/047): guidance_flag is a column,
eps_surprise lives in the signal's factors JSON. eps_surprise falls back to the
trade's own eps_beat_pct when the signal join is unavailable.

CLI:  python -m prime_ml.prime_pead_outcome_tracker
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from prime_config.prime_config import get_config


def _db_path(override: Optional[Path] = None) -> Path:
    return override if override is not None else get_config().db_path


def _is_win(pnl_dollars: Optional[float], pnl_pct: Optional[float]) -> bool:
    """A trade is a win when its realised P&L is positive. Prefers dollars,
    falls back to percent when dollars are not recorded."""
    basis = pnl_dollars if pnl_dollars is not None else pnl_pct
    return (basis or 0.0) > 0.0


def _decile_label(position: int, total: int) -> str:
    """1-based decile label (D1..D10) for an item at `position` (0-based) within
    a rank-sorted population of size `total`."""
    decile = int(position * 10 / total) + 1
    return f"D{min(decile, 10)}"


def _summarise(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    """Reduce grouped rows to {trade_count, win_rate_pct, avg_pnl_pct,
    avg_hold_minutes}."""
    count = len(rows)
    wins = sum(1 for r in rows if _is_win(r["pnl_dollars"], r["pnl_pct"]))
    avg_pnl = sum((r["pnl_pct"] or 0.0) for r in rows) / count if count else 0.0
    avg_hold = sum((r["hold_minutes"] or 0) for r in rows) / count if count else 0.0
    return {
        "trade_count": count,
        "win_rate_pct": round(wins / count * 100, 1) if count else 0.0,
        "avg_pnl_pct": round(avg_pnl, 2),
        "avg_hold_minutes": round(avg_hold, 1),
    }


def get_pead_outcome_summary(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Summarise closed PEAD trade outcomes.

    Returns a dict with:
      by_guidance_flag        -- list of {guidance_flag, trade_count,
                                 win_rate_pct, avg_pnl_pct, avg_hold_minutes}
      by_eps_surprise_decile  -- list of {decile_label, trade_count, avg_pnl_pct}
      overall                 -- {trade_count, win_rate_pct, avg_pnl_pct}
      as_of                   -- ISO timestamp of computation

    Returns the empty shape (no error) when there is no trade history.
    """
    empty: Dict[str, Any] = {
        "by_guidance_flag": [],
        "by_eps_surprise_decile": [],
        "overall": {"trade_count": 0, "win_rate_pct": 0.0, "avg_pnl_pct": 0.0},
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }

    path = _db_path(db_path)
    if not path.exists():
        return empty

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute("""
                SELECT
                    t.log_id,
                    t.pnl_dollars,
                    t.pnl_pct,
                    t.hold_minutes,
                    COALESCE(s.guidance_flag,
                             json_extract(s.factors, '$.guidance_flag'),
                             'UNKNOWN')                          AS guidance_flag,
                    COALESCE(json_extract(s.factors, '$.eps_surprise'),
                             t.eps_beat_pct)                     AS eps_surprise
                FROM prime_trade_log t
                LEFT JOIN prime_signals s ON t.signal_id = s.signal_id
                WHERE t.strategy = 'PEAD' AND t.status = 'CLOSED'
                ORDER BY t.exit_time DESC
            """).fetchall()
        except sqlite3.OperationalError:
            # Tables not yet created (fresh DB) -> treat as empty history.
            return empty
    finally:
        conn.close()

    if not rows:
        return empty

    # --- by guidance_flag -----------------------------------------------------
    by_flag: Dict[str, List[sqlite3.Row]] = {}
    for r in rows:
        by_flag.setdefault(r["guidance_flag"] or "UNKNOWN", []).append(r)
    by_guidance_flag = [
        {"guidance_flag": flag, **_summarise(group)}
        for flag, group in sorted(by_flag.items())
    ]

    # --- by eps_surprise decile ----------------------------------------------
    scored = sorted(
        (r for r in rows if r["eps_surprise"] is not None),
        key=lambda r: r["eps_surprise"],
    )
    decile_groups: Dict[str, List[sqlite3.Row]] = {}
    n = len(scored)
    for i, r in enumerate(scored):
        decile_groups.setdefault(_decile_label(i, n), []).append(r)
    by_eps_surprise_decile = [
        {
            "decile_label": label,
            "trade_count": len(group),
            "avg_pnl_pct": round(
                sum((r["pnl_pct"] or 0.0) for r in group) / len(group), 2
            ),
        }
        for label, group in sorted(
            decile_groups.items(), key=lambda kv: int(kv[0][1:])
        )
    ]

    # --- overall --------------------------------------------------------------
    overall_full = _summarise(rows)
    overall = {
        "trade_count": overall_full["trade_count"],
        "win_rate_pct": overall_full["win_rate_pct"],
        "avg_pnl_pct": overall_full["avg_pnl_pct"],
    }

    return {
        "by_guidance_flag": by_guidance_flag,
        "by_eps_surprise_decile": by_eps_surprise_decile,
        "overall": overall,
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


def main(argv: Optional[List[str]] = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PRIME v1.0 PEAD outcome summary")
    parser.add_argument(
        "--db", default=None,
        help="Override DB path (defaults to the configured prime DB)",
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db) if args.db else None
    print(json.dumps(get_pead_outcome_summary(db_path=db_path), indent=2, default=str))


if __name__ == "__main__":
    main()
