"""
PRIME v1.0 ML Training Dataset Builder (Sprint 26 Item 5).

Joins prime_signals (features) with prime_trade_log (outcomes) to produce
labelled training rows for future ML model training. The Score column in the
signals table will be activated once 200+ outcome rows accumulate.

Join logic: signal within 2 calendar days of trade entry, same symbol.
Outcome fields: entry_price, exit_price, pnl_pct, hold_minutes, exit_reason, win.
"""

import csv
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_config.prime_config import get_config

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CSV  = _PROJECT_ROOT / "data" / "ml_training_dataset.csv"


def _db_path(override: Optional[Path] = None) -> Path:
    return override if override is not None else get_config().db_path


def get_training_rows(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Join closed trades with their originating signals (within 2-day window).

    Returns rows with all signal features + trade outcome labels.
    A 'win' column is 1 when pnl_dollars > 0, else 0.
    """
    path = _db_path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT
                s.signal_id,
                s.symbol,
                s.strategy,
                s.tier,
                s.trigger_source,
                s.dk_status,
                s.dk_conviction,
                s.guidance_flag,
                s.entry_price         AS signal_price,
                s.score               AS signal_score,
                s.scan_ts,
                s.instrument_type,
                t.log_id,
                t.direction,
                t.entry_price         AS trade_entry_price,
                t.exit_price,
                t.pnl_pct,
                t.pnl_dollars,
                t.hold_minutes,
                t.exit_reason,
                t.entry_time,
                t.exit_time,
                CASE WHEN t.pnl_dollars > 0 THEN 1 ELSE 0 END AS win
            FROM prime_signals s
            JOIN prime_trade_log t
                ON s.symbol = t.symbol
                AND t.status = 'CLOSED'
                AND t.exit_price IS NOT NULL
                AND ABS(julianday(s.scan_ts) - julianday(t.entry_time)) <= 2
            ORDER BY s.scan_ts DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_row_count(db_path: Optional[Path] = None) -> int:
    """Count available training rows without fetching them all."""
    path = _db_path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM prime_signals s
            JOIN prime_trade_log t
                ON s.symbol = t.symbol
                AND t.status = 'CLOSED'
                AND t.exit_price IS NOT NULL
                AND ABS(julianday(s.scan_ts) - julianday(t.entry_time)) <= 2
        """).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def export_csv(
    rows: Optional[List[Dict[str, Any]]] = None,
    output_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Path:
    """Write training rows to CSV. Fetches fresh rows when rows=None."""
    if rows is None:
        rows = get_training_rows(db_path=db_path)
    out = output_path or _DEFAULT_CSV
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out.write_text("# no training data yet\n", encoding="utf-8")
        return out
    fieldnames = list(rows[0].keys())
    with open(str(out), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out
