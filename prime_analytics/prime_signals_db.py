"""
PRIME v1.0 Unified Signals Database Layer (CIL-101).

All signal records from every scanner land in the prime_signals table.
Trade linkage via trade_id FK (nullable). All aggregates computed at
query time -- no cached computed columns.

DB access goes through prime_data/prime_db.py connection helpers.
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from prime_data.prime_db import get_connection, init_db as _init_base_db

logger = logging.getLogger(__name__)

_PRIME_SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS prime_signals (
    signal_id   TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    scan_ts     TEXT NOT NULL,
    entry_price REAL,
    score       REAL DEFAULT 0.0,
    sector      TEXT DEFAULT 'Unknown',
    tier        TEXT DEFAULT '',
    status      TEXT DEFAULT 'NEW',
    trade_id    TEXT,
    direction   TEXT DEFAULT 'LONG',
    factors     TEXT DEFAULT '{}',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_PRIME_SIGNALS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_signals_symbol_strategy
ON prime_signals (symbol, strategy, scan_ts)
"""


def init_signals_table(db_path: Optional[Path] = None) -> None:
    """Create the prime_signals table and apply migrations if needed."""
    with get_connection(db_path) as conn:
        conn.execute(_PRIME_SIGNALS_SCHEMA)
        conn.execute(_PRIME_SIGNALS_INDEX)
        # DK-001 migration: add dk_score and dk_status columns.
        # Sprint 20 Item 1: dk_status is a three-state quality modifier
        # (CONFIRMING / NEUTRAL / NULLIFYING); NEUTRAL is the default and the
        # retired PENDING state. dk_conviction is a 0.0-1.0 confidence score.
        _migrate_add_column(conn, "prime_signals", "dk_score", "REAL")
        _migrate_add_column(conn, "prime_signals", "dk_status", "TEXT DEFAULT 'NEUTRAL'")
        _migrate_add_column(conn, "prime_signals", "dk_conviction", "REAL")
        # IDX-001 migration: add instrument_type column
        _migrate_add_column(conn, "prime_signals", "instrument_type", "TEXT DEFAULT 'EQUITY'")
        # IDX-OPT-001 migration: add option fields
        _migrate_add_column(conn, "prime_signals", "option_legs", "TEXT DEFAULT '[]'")
        _migrate_add_column(conn, "prime_signals", "max_loss", "REAL")
        _migrate_add_column(conn, "prime_signals", "dte_at_entry", "INTEGER")
        # ML-15 migration: batch fields
        _migrate_add_column(conn, "prime_signals", "batch_id", "TEXT")
        _migrate_add_column(conn, "prime_signals", "batch_score", "REAL")
        # ML-16 migration: entry timing
        _migrate_add_column(conn, "prime_signals", "entry_timing", "TEXT DEFAULT 'UNKNOWN'")
        # CIL-STAGE0-TAB migration: rejection fields
        _migrate_add_column(conn, "prime_signals", "rejection_reason", "TEXT")
        _migrate_add_column(conn, "prime_signals", "rejection_stage", "TEXT")
        # Sprint 17 Item 3 migration: borrow rate for confirmed-borrowable shorts
        _migrate_add_column(conn, "prime_signals", "borrow_rate_pct", "REAL")
        # Sprint 20 Item 1: retire PENDING and the old CONFIRMED/NULLIFIED names;
        # rename existing dk_status rows to the three-state vocabulary. Idempotent.
        conn.execute("UPDATE prime_signals SET dk_status='NEUTRAL' WHERE dk_status='PENDING'")
        conn.execute("UPDATE prime_signals SET dk_status='CONFIRMING' WHERE dk_status='CONFIRMED'")
        conn.execute("UPDATE prime_signals SET dk_status='NULLIFYING' WHERE dk_status='NULLIFIED'")
        conn.commit()


def _migrate_add_column(conn, table: str, column: str, col_type: str) -> None:
    """Idempotent ALTER TABLE -- adds column if it doesn't exist."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
        pass  # Column already exists


def insert_signal(
    symbol: str,
    strategy: str,
    scan_ts: str,
    entry_price: float = 0.0,
    score: float = 0.0,
    sector: str = "Unknown",
    tier: str = "",
    status: str = "NEW",
    trade_id: Optional[str] = None,
    direction: str = "LONG",
    factors: str = "{}",
    db_path: Optional[Path] = None,
) -> str:
    """Insert a signal record. Returns signal_id."""
    signal_id = str(uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO prime_signals
                (signal_id, symbol, strategy, scan_ts, entry_price, score,
                 sector, tier, status, trade_id, direction, factors)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (signal_id, symbol.upper(), strategy, scan_ts, entry_price, score,
             sector, tier, status, trade_id, direction, factors),
        )
        conn.commit()
    return signal_id


def make_signal_id(strategy: str, symbol: str, scan_ts: str) -> str:
    """Deterministic signal_id from the natural key (strategy, symbol, scan_ts).

    The same scan re-ingested produces the same id, so INSERT OR IGNORE in
    insert_signal_dedup() makes bridge re-runs idempotent (no duplicates).
    """
    raw = f"{strategy}|{symbol.upper()}|{scan_ts}"
    return "sig_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def insert_signal_dedup(
    symbol: str,
    strategy: str,
    scan_ts: str,
    entry_price: float = 0.0,
    score: float = 0.0,
    sector: str = "Unknown",
    tier: str = "",
    status: str = "NEW",
    direction: str = "LONG",
    factors: str = "{}",
    instrument_type: str = "EQUITY",
    signal_id: Optional[str] = None,
    borrow_rate_pct: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a signal with a deterministic id, skipping exact duplicates.

    Returns the signal_id if a new row was inserted, or None if a row with the
    same deterministic id already existed (duplicate skipped). Used by the
    scanner bridge so every scan can be re-ingested safely. borrow_rate_pct is
    populated for confirmed-borrowable SHORT signals (Sprint 17 Item 3).
    """
    if signal_id is None:
        signal_id = make_signal_id(strategy, symbol, scan_ts)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO prime_signals
                (signal_id, symbol, strategy, scan_ts, entry_price, score,
                 sector, tier, status, direction, factors, instrument_type,
                 borrow_rate_pct)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (signal_id, symbol.upper(), strategy, scan_ts, entry_price, score,
             sector, tier, status, direction, factors, instrument_type,
             borrow_rate_pct),
        )
        conn.commit()
        inserted = cursor.rowcount > 0
    return signal_id if inserted else None


def get_signals(
    strategy: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    sector: Optional[str] = None,
    limit: int = 500,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Fetch signals with optional filters. Ordered by scan_ts desc."""
    clauses = []
    params: list = []
    if strategy:
        clauses.append("strategy = ?")
        params.append(strategy)
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper())
    if status:
        clauses.append("status = ?")
        params.append(status)
    if sector:
        clauses.append("sector = ?")
        params.append(sector)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM prime_signals {where} ORDER BY scan_ts DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_distinct_strategies(db_path: Optional[Path] = None) -> List[str]:
    """Return the distinct, non-empty strategy names present in prime_signals,
    sorted alphabetically. Used to populate the UI strategy filter dynamically."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT strategy FROM prime_signals "
            "WHERE strategy IS NOT NULL AND strategy != '' ORDER BY strategy"
        ).fetchall()
        return [row[0] for row in rows]


def link_signal_to_trade(
    signal_id: str,
    trade_id: str,
    db_path: Optional[Path] = None,
) -> None:
    """Link a signal to a trade log entry."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE prime_signals SET trade_id=?, status='TRADED' WHERE signal_id=?",
            (trade_id, signal_id),
        )
        conn.commit()


def get_analytics_summary(
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compute live analytics summary. All aggregates at query time."""
    clauses = []
    params: list = []

    base = """
        SELECT
            s.strategy,
            COUNT(DISTINCT s.signal_id) as signal_count,
            COUNT(DISTINCT s.trade_id) as traded_count,
            AVG(s.score) as avg_score,
            COUNT(DISTINCT CASE WHEN t.pnl_dollars > 0 THEN t.log_id END) as wins,
            COUNT(DISTINCT CASE WHEN t.pnl_dollars <= 0 AND t.status='CLOSED' THEN t.log_id END) as losses,
            COALESCE(SUM(t.pnl_dollars), 0) as total_pnl,
            AVG(t.hold_minutes) as avg_hold_minutes
        FROM prime_signals s
        LEFT JOIN prime_trade_log t ON s.trade_id = t.log_id
    """

    if strategy:
        clauses.append("s.strategy = ?")
        params.append(strategy)
    if date_from:
        clauses.append("s.scan_ts >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("s.scan_ts <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    group = "GROUP BY s.strategy" if not strategy else ""
    query = f"{base} {where} {group}"

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        if not rows:
            return {"strategies": [], "total_pnl": 0, "total_signals": 0}

        strategies = []
        total_pnl = 0.0
        total_signals = 0
        for row in rows:
            row_d = dict(row)
            wins = row_d.get("wins", 0) or 0
            losses = row_d.get("losses", 0) or 0
            total = wins + losses
            win_rate = (wins / total * 100) if total > 0 else 0.0

            entry = {
                "strategy": row_d["strategy"],
                "signal_count": row_d["signal_count"],
                "traded_count": row_d["traded_count"],
                "avg_score": round(row_d["avg_score"] or 0, 2),
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 1),
                "total_pnl": round(row_d["total_pnl"] or 0, 2),
                "avg_hold_minutes": round(row_d["avg_hold_minutes"] or 0, 0),
            }
            strategies.append(entry)
            total_pnl += entry["total_pnl"]
            total_signals += entry["signal_count"]

        return {
            "strategies": strategies,
            "total_pnl": round(total_pnl, 2),
            "total_signals": total_signals,
        }


def get_sector_analytics(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Performance grouped by GICS sector. Live computation."""
    query = """
        SELECT
            s.sector,
            COUNT(DISTINCT s.signal_id) as signal_count,
            COUNT(DISTINCT s.trade_id) as traded_count,
            AVG(s.score) as avg_score,
            COALESCE(SUM(t.pnl_dollars), 0) as total_pnl,
            COUNT(DISTINCT CASE WHEN t.pnl_dollars > 0 THEN t.log_id END) as wins,
            COUNT(DISTINCT CASE WHEN t.pnl_dollars <= 0 AND t.status='CLOSED' THEN t.log_id END) as losses
        FROM prime_signals s
        LEFT JOIN prime_trade_log t ON s.trade_id = t.log_id
        GROUP BY s.sector
        ORDER BY total_pnl DESC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(query).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            wins = d.get("wins", 0) or 0
            losses = d.get("losses", 0) or 0
            total = wins + losses
            d["win_rate"] = round((wins / total * 100) if total > 0 else 0.0, 1)
            d["total_pnl"] = round(d["total_pnl"] or 0, 2)
            d["avg_score"] = round(d["avg_score"] or 0, 2)
            results.append(d)
        return results


def get_factor_analysis(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Factor analysis: entry quality, stop accuracy, duration breakdown."""
    with get_connection(db_path) as conn:
        duration_rows = conn.execute("""
            SELECT
                json_extract(s.factors, '$.duration.class') as dur_class,
                COUNT(*) as count,
                AVG(s.score) as avg_score
            FROM prime_signals s
            WHERE s.factors != '{}'
            GROUP BY dur_class
        """).fetchall()

        entry_rows = conn.execute("""
            SELECT
                json_extract(s.factors, '$.entry.method') as entry_method,
                COUNT(*) as count,
                COALESCE(SUM(t.pnl_dollars), 0) as total_pnl
            FROM prime_signals s
            LEFT JOIN prime_trade_log t ON s.trade_id = t.log_id
            WHERE s.factors != '{}'
            GROUP BY entry_method
        """).fetchall()

    return {
        "duration_breakdown": [
            {"class": (dict(r).get("dur_class") or "Unknown"),
             "count": dict(r)["count"],
             "avg_score": round(dict(r)["avg_score"] or 0, 2)}
            for r in duration_rows
        ],
        "entry_method_breakdown": [
            {"method": (dict(r).get("entry_method") or "Unknown"),
             "count": dict(r)["count"],
             "total_pnl": round(dict(r)["total_pnl"] or 0, 2)}
            for r in entry_rows
        ],
    }
