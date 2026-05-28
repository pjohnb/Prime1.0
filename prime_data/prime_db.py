"""
PRIME v1.0 database layer.
All DB access goes through this module — no other module imports sqlite3 directly.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_config.prime_config import get_config


_PRIME_TRADE_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS prime_trade_log (
    log_id              TEXT PRIMARY KEY,
    strategy            TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,
    mode                TEXT NOT NULL,
    order_type          TEXT NOT NULL,
    shares              INTEGER NOT NULL,
    entry_price         REAL,
    entry_time          TIMESTAMP NOT NULL,
    exit_price          REAL,
    exit_time           TIMESTAMP,
    exit_reason         TEXT,
    pnl_dollars         REAL,
    pnl_pct             REAL,
    hold_minutes        INTEGER,
    score               REAL,
    eps_beat_pct        REAL,
    signal_source       TEXT,
    order_id            TEXT,
    account             TEXT,
    routed_to           TEXT,
    notes               TEXT,
    mata_batch_id       TEXT,
    status              TEXT NOT NULL DEFAULT 'OPEN',
    price_at_scan       REAL,
    trade_factors       TEXT NOT NULL DEFAULT '{}',
    claude_advisory     TEXT NOT NULL DEFAULT '',
    advisory_timestamp  TEXT NOT NULL DEFAULT '',
    advisory_history    TEXT NOT NULL DEFAULT '[]',
    dark_pool_eval      TEXT NOT NULL DEFAULT '{}',
    trade_source        TEXT NOT NULL DEFAULT 'PAPER',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_PRIME_OPS_HEALTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS prime_ops_health (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    component       TEXT NOT NULL,
    symbol          TEXT,
    detail          TEXT,
    severity        TEXT NOT NULL DEFAULT 'INFO',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _db_path(override: Optional[Path] = None) -> Path:
    if override is not None:
        return override
    return get_config().db_path


def init_db(db_path: Optional[Path] = None) -> Path:
    """Create the database and all tables. Returns the resolved path."""
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(_PRIME_TRADE_LOG_SCHEMA)
        conn.execute(_PRIME_OPS_HEALTH_SCHEMA)
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_dedup
               ON prime_trade_log (symbol, strategy, entry_time)
               WHERE status = 'OPEN'"""
        )
        conn.commit()
    finally:
        conn.close()
    return path


@contextmanager
def get_connection(db_path: Optional[Path] = None):
    """Yield a sqlite3 connection with WAL mode and row_factory."""
    path = _db_path(db_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def table_exists(table_name: str, db_path: Optional[Path] = None) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row[0] > 0


def get_table_columns(table_name: str, db_path: Optional[Path] = None) -> list[str]:
    with get_connection(db_path) as conn:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Trade log CRUD
# ---------------------------------------------------------------------------

class TradeRecordError(Exception):
    """Raised when a trade record fails validation."""


def insert_trade(
    strategy: str,
    symbol: str,
    direction: str,
    mode: str,
    order_type: str,
    shares: int,
    entry_time: str,
    price_at_scan: float,
    score: float = 0.0,
    entry_price: Optional[float] = None,
    eps_beat_pct: Optional[float] = None,
    signal_source: Optional[str] = None,
    order_id: Optional[str] = None,
    account: Optional[str] = None,
    routed_to: Optional[str] = None,
    notes: Optional[str] = None,
    mata_batch_id: Optional[str] = None,
    trade_factors: str = "{}",
    claude_advisory: str = "",
    advisory_timestamp: str = "",
    advisory_history: str = "[]",
    dark_pool_eval: str = "{}",
    trade_source: str = "PAPER",
    db_path: Optional[Path] = None,
) -> str:
    """Insert a new trade record. Returns the generated log_id.

    price_at_scan is required and must be > 0. This is the v1.0 architectural
    fix for FIX-4: scanners must capture the market price at signal time and
    pass it here. The DB layer rejects records without a valid price.
    """
    if not price_at_scan or price_at_scan <= 0:
        raise TradeRecordError(
            f"price_at_scan must be > 0 for {symbol}, got {price_at_scan}. "
            "Scanners must capture the live price at signal time."
        )

    log_id = str(uuid.uuid4())

    _VALID_SOURCES = ("PAPER", "LIVE", "LEGACY", "SCHWAB_IMPORT", "INDEX_ETF")
    if trade_source not in _VALID_SOURCES:
        raise TradeRecordError(
            f"trade_source must be one of {_VALID_SOURCES}, got '{trade_source}'"
        )

    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO prime_trade_log (
                log_id, strategy, symbol, direction, mode, order_type, shares,
                entry_price, entry_time, score, eps_beat_pct, signal_source,
                order_id, account, routed_to, notes, mata_batch_id, status,
                price_at_scan, trade_factors, claude_advisory, advisory_timestamp,
                advisory_history, dark_pool_eval, trade_source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                log_id, strategy, symbol, direction, mode, order_type, shares,
                entry_price, entry_time, score, eps_beat_pct, signal_source,
                order_id, account, routed_to, notes, mata_batch_id, "OPEN",
                price_at_scan, trade_factors, claude_advisory, advisory_timestamp,
                advisory_history, dark_pool_eval, trade_source,
            ),
        )
        conn.commit()

    return log_id


def get_open_trades(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return all OPEN trade records as dicts."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prime_trade_log WHERE status = 'OPEN'"
        ).fetchall()
        return [dict(row) for row in rows]


def get_open_positions(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return all OPEN positions regardless of trade_source (LEGACY, PAPER, LIVE)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prime_trade_log WHERE status = 'OPEN'"
        ).fetchall()
        return [dict(row) for row in rows]


def update_trade_source(
    log_id: str,
    trade_source: str,
    db_path: Optional[Path] = None,
) -> None:
    """Update trade_source for an existing record."""
    _VALID_SOURCES = ("PAPER", "LIVE", "LEGACY", "SCHWAB_IMPORT", "INDEX_ETF")
    if trade_source not in _VALID_SOURCES:
        raise TradeRecordError(
            f"trade_source must be one of {_VALID_SOURCES}, got '{trade_source}'"
        )
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE prime_trade_log SET trade_source=? WHERE log_id=?",
            (trade_source, log_id),
        )
        conn.commit()


def close_trade(
    log_id: str,
    exit_price: float,
    exit_time: str,
    exit_reason: str,
    pnl_dollars: float,
    pnl_pct: float,
    hold_minutes: int,
    db_path: Optional[Path] = None,
) -> None:
    """Close a trade record."""
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE prime_trade_log SET
                exit_price=?, exit_time=?, exit_reason=?,
                pnl_dollars=?, pnl_pct=?, hold_minutes=?,
                status='CLOSED'
            WHERE log_id=?""",
            (exit_price, exit_time, exit_reason, pnl_dollars, pnl_pct,
             hold_minutes, log_id),
        )
        conn.commit()


def close_trade_with_fill(
    log_id: str,
    fill_price: float,
    fill_qty: int,
    close_ts: str,
    exit_reason: str = "FILL",
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Close a trade using actual fill data. Computes realized_pnl from fill.

    realized_pnl = (fill_price - entry_price) * fill_qty for LONG
    realized_pnl = (entry_price - fill_price) * fill_qty for SHORT
    """
    trade = get_trade(log_id, db_path=db_path)
    if not trade:
        return None

    entry_price = trade.get("entry_price") or trade.get("price_at_scan", 0)
    direction = trade.get("direction", "LONG").upper()

    if direction == "SHORT":
        realized_pnl = (entry_price - fill_price) * fill_qty
    else:
        realized_pnl = (fill_price - entry_price) * fill_qty

    pnl_pct = (realized_pnl / (entry_price * fill_qty) * 100) if entry_price and fill_qty else 0

    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE prime_trade_log SET
                exit_price=?, exit_time=?, exit_reason=?,
                pnl_dollars=?, pnl_pct=?, shares=?,
                status='CLOSED'
            WHERE log_id=?""",
            (fill_price, close_ts, exit_reason, round(realized_pnl, 2),
             round(pnl_pct, 2), fill_qty, log_id),
        )
        conn.commit()

    return {
        "log_id": log_id,
        "fill_price": fill_price,
        "fill_qty": fill_qty,
        "realized_pnl": round(realized_pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
    }


def get_trade(log_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM prime_trade_log WHERE log_id=?", (log_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_trades(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return all trade records (all statuses) ordered by entry_time desc."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prime_trade_log ORDER BY entry_time DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def bulk_delete_trades(log_ids: List[str], db_path: Optional[Path] = None) -> int:
    """Delete multiple trade records in a single transaction. Returns count deleted."""
    if not log_ids:
        return 0
    with get_connection(db_path) as conn:
        placeholders = ",".join("?" for _ in log_ids)
        cursor = conn.execute(
            f"DELETE FROM prime_trade_log WHERE log_id IN ({placeholders})",
            log_ids,
        )
        conn.commit()
        return cursor.rowcount


def get_open_by_symbol(symbol: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return all OPEN records for a given symbol."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prime_trade_log WHERE symbol=? AND status='OPEN'",
            (symbol.upper(),),
        ).fetchall()
        return [dict(row) for row in rows]


def close_trade_reconcile(
    log_id: str,
    close_reason: str = "SCHWAB_RECONCILE",
    db_path: Optional[Path] = None,
) -> None:
    """Close a trade during reconciliation (no exit price/P&L calc)."""
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE prime_trade_log SET
                status='CLOSED', exit_reason=?, exit_time=?
            WHERE log_id=?""",
            (close_reason, datetime.utcnow().isoformat(), log_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Signal deduplication
# ---------------------------------------------------------------------------

def upsert_signal(
    strategy: str,
    symbol: str,
    scan_date: str,
    direction: str = "LONG",
    mode: str = "PAPER",
    order_type: str = "MARKET",
    shares: int = 0,
    price_at_scan: float = 0.0,
    score: float = 0.0,
    signal_source: Optional[str] = None,
    trade_factors: str = "{}",
    claude_advisory: str = "",
    dark_pool_eval: str = "{}",
    trade_source: str = "PAPER",
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a signal record only if no duplicate exists for (symbol, strategy, scan_date).

    Returns log_id if inserted, None if duplicate skipped.
    """
    with get_connection(db_path) as conn:
        existing = conn.execute(
            """SELECT log_id FROM prime_trade_log
               WHERE symbol=? AND strategy=? AND date(entry_time)=date(?)
               AND status='OPEN'""",
            (symbol, strategy, scan_date),
        ).fetchone()

        if existing:
            return None

    return insert_trade(
        strategy=strategy,
        symbol=symbol,
        direction=direction,
        mode=mode,
        order_type=order_type,
        shares=shares,
        entry_time=scan_date,
        price_at_scan=price_at_scan,
        score=score,
        signal_source=signal_source,
        trade_factors=trade_factors,
        claude_advisory=claude_advisory,
        dark_pool_eval=dark_pool_eval,
        trade_source=trade_source,
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# Batch summary (ML-15)
# ---------------------------------------------------------------------------

_PRIME_BATCH_SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS prime_batch_summary (
    batch_id            TEXT PRIMARY KEY,
    scan_ts             TEXT NOT NULL,
    signal_count        INTEGER DEFAULT 0,
    sector_concentration TEXT DEFAULT '{}',
    correlation_flags   TEXT DEFAULT '[]',
    aggregate_risk      REAL DEFAULT 0.0,
    batch_score         REAL DEFAULT 0.0,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def init_batch_summary_table(db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(_PRIME_BATCH_SUMMARY_SCHEMA)
        conn.commit()


def write_batch_summary(
    batch_id: str,
    scan_ts: str,
    signal_count: int,
    sector_concentration: str = "{}",
    correlation_flags: str = "[]",
    aggregate_risk: float = 0.0,
    batch_score: float = 0.0,
    db_path: Optional[Path] = None,
) -> None:
    init_batch_summary_table(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO prime_batch_summary
                (batch_id, scan_ts, signal_count, sector_concentration,
                 correlation_flags, aggregate_risk, batch_score)
            VALUES (?,?,?,?,?,?,?)""",
            (batch_id, scan_ts, signal_count, sector_concentration,
             correlation_flags, aggregate_risk, batch_score),
        )
        conn.commit()


def get_latest_batch_summary(db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    init_batch_summary_table(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM prime_batch_summary ORDER BY scan_ts DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Stage0 rejections (CIL-STAGE0-TAB)
# ---------------------------------------------------------------------------

def write_stage0_rejection(
    symbol: str,
    reason: str,
    scan_ts: str,
    strategy: str = "UOA",
    db_path: Optional[Path] = None,
) -> str:
    """Write a Stage0 rejection to prime_signals with rejection detail."""
    from prime_analytics.prime_signals_db import init_signals_table, insert_signal
    init_signals_table(db_path)
    signal_id = insert_signal(
        symbol=symbol,
        strategy=strategy,
        scan_ts=scan_ts,
        status="REJECTED_STAGE0",
        db_path=db_path,
    )
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE prime_signals SET rejection_reason=?, rejection_stage=? WHERE signal_id=?",
            (reason, "STAGE0", signal_id),
        )
        conn.commit()
    return signal_id


# ---------------------------------------------------------------------------
# Ops health logging
# ---------------------------------------------------------------------------

def log_ops_event(
    event_type: str,
    component: str,
    symbol: Optional[str] = None,
    detail: Optional[str] = None,
    severity: str = "INFO",
    db_path: Optional[Path] = None,
) -> None:
    """Write an event to prime_ops_health."""
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO prime_ops_health
                (timestamp, event_type, component, symbol, detail, severity)
            VALUES (?,?,?,?,?,?)""",
            (datetime.utcnow().isoformat(), event_type, component,
             symbol, detail, severity),
        )
        conn.commit()


def get_ops_events(
    component: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        if component:
            rows = conn.execute(
                "SELECT * FROM prime_ops_health WHERE component=? "
                "ORDER BY id DESC LIMIT ?",
                (component, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM prime_ops_health ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
