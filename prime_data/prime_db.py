"""
PRIME v1.0 database layer.
All DB access goes through this module — no other module imports sqlite3 directly.
"""

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_config.prime_config import get_config

logger = logging.getLogger(__name__)


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
    signal_id           TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Sprint 31 / CIL-041-031: ML training dataset. One row per APPROVED signal;
# capture fields are written at scan time by prime_ml.prime_ml_capture_v2 and
# the outcome fields are filled in by update_ml_outcome() on trade close.
# Column order must match prime_ml.prime_ml_capture_v2.ML_COLUMNS.
_PRIME_ML_DATASET_SCHEMA = """
CREATE TABLE IF NOT EXISTS prime_ml_dataset (
    signal_id           TEXT PRIMARY KEY,
    scanner             TEXT,
    symbol              TEXT,
    direction           TEXT,
    score               REAL,
    tier                TEXT,
    dk_status           TEXT,
    dk_conviction       REAL,
    entry_price         REAL,
    price_at_scan       REAL,
    sizzle_index        REAL,
    rsi                 REAL,
    pct_from_sma        REAL,
    eps_surprise        REAL,
    guidance_flag       TEXT,
    borrow_rate         REAL,
    market_regime       TEXT,
    capture_ts          TEXT,
    exit_price          REAL,
    pnl_dollars         REAL,
    pnl_pct             REAL,
    hold_minutes        INTEGER,
    exit_reason         TEXT,
    outcome_captured_at TEXT
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
        conn.execute(_PRIME_ML_DATASET_SCHEMA)
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_dedup
               ON prime_trade_log (symbol, strategy, entry_time)
               WHERE status = 'OPEN'"""
        )
        conn.commit()
    finally:
        conn.close()

    init_batch_summary_table(db_path)
    from prime_analytics.prime_signals_db import init_signals_table
    init_signals_table(db_path)

    # Sprint 24 Item 4: trailing stop columns (idempotent migrations)
    _migrate_add_column_trade_log(db_path, "trailing_stop_pct", "REAL")
    _migrate_add_column_trade_log(db_path, "trailing_stop_high_water", "REAL")
    # Sprint 26 Item 2: explicit stop/target/time stop columns
    _migrate_add_column_trade_log(db_path, "stop_price", "REAL")
    _migrate_add_column_trade_log(db_path, "target_price", "REAL")
    _migrate_add_column_trade_log(db_path, "time_stop_minutes", "INTEGER")
    # Sprint 27 Item 2: stop_type (FIXED or TRAILING)
    _migrate_add_column_trade_log(db_path, "stop_type", "TEXT")
    # Sprint 27 Item 3: limit_price for LIMIT order type
    _migrate_add_column_trade_log(db_path, "limit_price", "REAL")
    # Sprint 29 PORT-02: sector for ETF/COLLECTIVE_INVESTMENT classification
    _migrate_add_column_trade_log(db_path, "sector", "TEXT")
    # Sprint 29 H-02: originating signal linkage for History tab
    _migrate_add_column_trade_log(db_path, "signal_id", "TEXT")
    # Sprint 30 PM-04: automated trailing-stop exit state (CIL-097).
    # trailing_stop_active toggles on at the gain trigger; trailing_stop_peak is
    # the rolling high watermark used to compute the trail exit price.
    _migrate_add_column_trade_log(db_path, "trailing_stop_active", "INTEGER DEFAULT 0")
    _migrate_add_column_trade_log(db_path, "trailing_stop_peak", "REAL")

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


def _migrate_add_column_trade_log(
    db_path: Optional[Path], column: str, col_type: str
) -> None:
    """Idempotent ALTER TABLE for prime_trade_log."""
    try:
        path = _db_path(db_path)
        conn = sqlite3.connect(str(path))
        try:
            existing = [row[1] for row in conn.execute("PRAGMA table_info(prime_trade_log)").fetchall()]
            if column not in existing:
                conn.execute(f"ALTER TABLE prime_trade_log ADD COLUMN {column} {col_type}")
                conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


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
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    time_stop_minutes: Optional[int] = None,
    stop_type: str = "FIXED",
    limit_price: Optional[float] = None,
    sector: Optional[str] = None,
    signal_id: Optional[str] = None,
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
                advisory_history, dark_pool_eval, trade_source,
                stop_price, target_price, time_stop_minutes, stop_type, limit_price, sector,
                signal_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                log_id, strategy, symbol, direction, mode, order_type, shares,
                entry_price, entry_time, score, eps_beat_pct, signal_source,
                order_id, account, routed_to, notes, mata_batch_id, "OPEN",
                price_at_scan, trade_factors, claude_advisory, advisory_timestamp,
                advisory_history, dark_pool_eval, trade_source,
                stop_price, target_price, time_stop_minutes, stop_type or "FIXED",
                limit_price, sector, signal_id,
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


def get_pnl_history(days: int = 7, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Daily realized P&L for the last `days` calendar days (Sprint 22 Item 3).

    Returns a list of {day: YYYY-MM-DD, pnl: float} dicts ordered ascending,
    covering only days with closed trades. Used by the Dashboard sparkline.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""SELECT date(exit_time) AS day, SUM(pnl_dollars) AS pnl
                FROM prime_trade_log
                WHERE status='CLOSED' AND exit_time IS NOT NULL
                  AND exit_time >= date('now', '-{days - 1} days')
                GROUP BY day ORDER BY day ASC LIMIT ?""",
            (days,),
        ).fetchall()
        return [dict(r) for r in rows]


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


def update_ml_outcome(
    signal_id: str,
    exit_price: Optional[float],
    pnl_dollars: Optional[float],
    pnl_pct: Optional[float],
    hold_minutes: Optional[int],
    exit_reason: Optional[str],
    db_path: Optional[Path] = None,
) -> bool:
    """Fill in the outcome fields on a prime_ml_dataset row (Sprint 31 / CIL-043).

    Closes the signal-to-outcome loop: matches the capture row by signal_id and
    stamps the realized exit. No-op (returns False) if no row matches the
    signal_id -- e.g. SCHWAB_IMPORT trades that never had a captured signal.
    """
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """UPDATE prime_ml_dataset SET
                exit_price=?, pnl_dollars=?, pnl_pct=?,
                hold_minutes=?, exit_reason=?, outcome_captured_at=?
            WHERE signal_id=?""",
            (exit_price, pnl_dollars, pnl_pct, hold_minutes, exit_reason,
             datetime.utcnow().isoformat(), signal_id),
        )
        conn.commit()
        return cursor.rowcount > 0


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
    """Close a trade record.

    After writing exit fields, mirror the outcome into prime_ml_dataset via
    update_ml_outcome() when the record carries a signal_id (Sprint 31 /
    CIL-043). ML outcome capture is best-effort -- any failure is logged and
    never blocks the trade close.
    """
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
        row = conn.execute(
            "SELECT signal_id FROM prime_trade_log WHERE log_id=?", (log_id,)
        ).fetchone()

    signal_id = row["signal_id"] if row else None
    if signal_id is not None:
        try:
            update_ml_outcome(
                signal_id, exit_price, pnl_dollars, pnl_pct,
                hold_minutes, exit_reason, db_path=db_path,
            )
        except Exception as e:  # noqa: BLE001 - never block the trade close
            logger.warning("ML outcome update failed for signal %s: %s",
                           signal_id, e)
    else:
        logger.debug("No signal_id for SCHWAB_IMPORT trade %s", log_id)


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


def delete_trade(log_id: str, db_path: Optional[Path] = None) -> bool:
    """Hard-delete a single OPEN trade record. Returns True if a row was deleted.

    Only removes records with status=OPEN -- closed records are permanent.
    Caller must enforce PAPER-mode and source restrictions before invoking.
    """
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM prime_trade_log WHERE log_id=? AND status='OPEN'",
            (log_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_trailing_stop(
    log_id: str,
    trailing_stop_pct: Optional[float],
    db_path: Optional[Path] = None,
) -> bool:
    """Set or clear the trailing_stop_pct on an OPEN trade. Returns True on success."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE prime_trade_log SET trailing_stop_pct=?, trailing_stop_high_water=NULL"
            " WHERE log_id=? AND status='OPEN'",
            (trailing_stop_pct, log_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def set_trailing_stop_active(
    log_id: str,
    active: bool,
    peak: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Activate/deactivate the automated trailing stop and seed its peak (PM-04).

    Writes trailing_stop_active (0/1) and, when activating, trailing_stop_peak.
    Returns True if a row was updated. Only touches OPEN records.
    """
    with get_connection(db_path) as conn:
        if peak is not None:
            cursor = conn.execute(
                "UPDATE prime_trade_log SET trailing_stop_active=?, trailing_stop_peak=?"
                " WHERE log_id=? AND status='OPEN'",
                (1 if active else 0, peak, log_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE prime_trade_log SET trailing_stop_active=?"
                " WHERE log_id=? AND status='OPEN'",
                (1 if active else 0, log_id),
            )
        conn.commit()
        return cursor.rowcount > 0


def update_trailing_stop_peak(
    log_id: str,
    peak: float,
    db_path: Optional[Path] = None,
) -> bool:
    """Raise the rolling trailing_stop_peak high watermark for an OPEN trade (PM-04)."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE prime_trade_log SET trailing_stop_peak=?"
            " WHERE log_id=? AND status='OPEN'",
            (peak, log_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def _recent_trade_exists(
    symbol: str,
    event_type: str,
    since_iso: str,
    db_path: Optional[Path] = None,
) -> bool:
    """Return True if a prime_ops_health event of event_type exists for symbol at/after since_iso.

    Sprint 30 PM-04. Used by the automated exit logic to (a) fire DAY_COUNT_ALERT
    only once per day and (b) avoid double-submitting an automated exit for the
    same symbol within a single market session.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM prime_ops_health"
            " WHERE event_type=? AND symbol=? AND timestamp >= ? LIMIT 1",
            (event_type, (symbol or "").upper(), since_iso),
        ).fetchone()
        return row is not None


def _recent_open_trade_exists(
    symbol: str,
    strategy: str,
    window_seconds: int = 60,
    db_path: Optional[Path] = None,
) -> bool:
    """Return True if an OPEN prime_trade_log record for the same symbol and
    strategy has an entry_time within the last window_seconds seconds.

    Sprint 30 Thread 3 (CIL-095). Used by the /trades POST route to reject
    rapid duplicate submissions (double-click / submit-handler race). Named
    distinctly from _recent_trade_exists() (PM-04, prime_ops_health) to avoid
    a collision. Follows the module convention of self-managing the connection
    via db_path rather than taking a live conn.
    """
    cutoff = datetime.now() - timedelta(seconds=window_seconds)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT entry_time FROM prime_trade_log"
            " WHERE symbol=? AND strategy=? AND status='OPEN'",
            ((symbol or "").upper(), strategy),
        ).fetchall()
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row[0]))
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            return True
    return False


def set_trade_stop_target(
    log_id: str,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    time_stop_minutes: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Set stop_price, target_price, time_stop_minutes on an OPEN trade. Returns True on success."""
    updates = []
    vals = []
    if stop_price is not None:
        updates.append("stop_price=?")
        vals.append(stop_price)
    if target_price is not None:
        updates.append("target_price=?")
        vals.append(target_price)
    if time_stop_minutes is not None:
        updates.append("time_stop_minutes=?")
        vals.append(time_stop_minutes)
    if not updates:
        return False
    vals.append(log_id)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE prime_trade_log SET {', '.join(updates)} WHERE log_id=? AND status='OPEN'",
            vals,
        )
        conn.commit()
        return cursor.rowcount > 0


def get_closed_trades(
    limit: int = 500,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return CLOSED trade records ordered by exit_time desc."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prime_trade_log WHERE status='CLOSED' ORDER BY exit_time DESC LIMIT ?",
            (limit,),
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


def _hold_minutes(entry_time: Optional[str], close_ts: str) -> int:
    """Whole minutes between entry_time and close_ts (ISO-8601). 0 if unparseable."""
    try:
        start = datetime.fromisoformat(str(entry_time))
        end = datetime.fromisoformat(str(close_ts))
        return max(int((end - start).total_seconds() // 60), 0)
    except (TypeError, ValueError):
        return 0


def close_trade_manual(
    log_id: str,
    exit_price: float,
    exit_reason: str = "MANUAL",
    close_ts: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Close a position from a manual UI action (Sprint 16 Item 5).

    Computes realized P&L (direction-aware) and hold_minutes from the stored
    trade, then writes via close_trade(). Returns a summary dict, or None if the
    log_id is unknown.
    """
    trade = get_trade(log_id, db_path=db_path)
    if not trade:
        return None
    if close_ts is None:
        close_ts = datetime.utcnow().isoformat()

    entry_price = trade.get("entry_price") or trade.get("price_at_scan") or 0.0
    shares = trade.get("shares") or 0
    direction = (trade.get("direction") or "LONG").upper()

    if direction == "SHORT":
        pnl_dollars = (entry_price - exit_price) * shares
    else:
        pnl_dollars = (exit_price - entry_price) * shares
    pnl_pct = (pnl_dollars / (entry_price * shares) * 100.0) if entry_price and shares else 0.0
    hold_min = _hold_minutes(trade.get("entry_time"), close_ts)

    close_trade(log_id, exit_price, close_ts, exit_reason,
                round(pnl_dollars, 2), round(pnl_pct, 2), hold_min, db_path=db_path)
    return {
        "log_id": log_id,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_pct": round(pnl_pct, 2),
        "hold_minutes": hold_min,
        "status": "CLOSED",
    }


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


def get_latest_signal_for_symbol(
    symbol: str,
    scanner: str,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Most recent APPROVED prime_signals row for a symbol + scanner.

    Sprint 32 Thread 3 (PM-HEALTH-01). Returns a dict of all columns for the
    latest (by scan_ts) APPROVED signal where symbol and strategy match, or
    None when no such signal exists. Lets the position monitor re-evaluate the
    originating thesis for an open position.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM prime_signals"
            " WHERE symbol=? AND strategy=? AND status='APPROVED'"
            " ORDER BY scan_ts DESC LIMIT 1",
            ((symbol or "").upper(), scanner),
        ).fetchone()
        return dict(row) if row else None


def get_open_positions_with_signal_context(
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """OPEN positions joined to their originating signal's context.

    Sprint 32 Thread 3 (PM-HEALTH-01). Returns every OPEN prime_trade_log row
    LEFT JOINed to prime_signals on signal_id, exposing the scanner (strategy),
    dk_status, score and tier that produced the entry. SCHWAB_IMPORT positions
    carry no signal_id, so their scanner/dk_status/score/tier come back NULL.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT
                   t.log_id      AS log_id,
                   t.symbol      AS symbol,
                   t.direction   AS direction,
                   t.entry_price AS entry_price,
                   t.entry_time  AS entry_time,
                   t.signal_id   AS signal_id,
                   s.strategy    AS scanner,
                   s.dk_status   AS dk_status,
                   s.score       AS score,
                   s.tier        AS tier
               FROM prime_trade_log t
               LEFT JOIN prime_signals s ON t.signal_id = s.signal_id
               WHERE t.status='OPEN'"""
        ).fetchall()
        return [dict(row) for row in rows]


# Required exit fields every CLOSED prime_trade_log record must carry.
_CLOSED_REQUIRED_FIELDS = (
    "exit_price", "exit_time", "exit_reason",
    "pnl_dollars", "pnl_pct", "hold_minutes",
)


def check_closed_trade_completeness(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return CLOSED prime_trade_log records missing any required exit field.

    Sprint 31 Thread 3 (CIL-074). A complete close carries exit_price,
    exit_time, exit_reason, pnl_dollars, pnl_pct and hold_minutes (with
    status='CLOSED'). Any record where one of those is NULL is returned,
    annotated with a 'missing_fields' list so /api/v1/health can surface the
    count as 'incomplete_exits'. Returns an empty list on a clean database.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prime_trade_log WHERE status='CLOSED'"
        ).fetchall()
    incomplete: List[Dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        missing = [f for f in _CLOSED_REQUIRED_FIELDS if rec.get(f) is None]
        if missing:
            rec["missing_fields"] = missing
            incomplete.append(rec)
    return incomplete


# Minimum closed-trade count for a strategy's effectiveness metrics to be valid.
MIN_EFFECTIVENESS_TRADES = 5


def _effectiveness_row(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute effectiveness metrics for a list of CLOSED trade rows.

    Returns insufficient_data=True with null metrics when there are fewer than
    MIN_EFFECTIVENESS_TRADES records.
    """
    n = len(records)
    if n < MIN_EFFECTIVENESS_TRADES:
        return {
            "trade_count": n,
            "insufficient_data": True,
            "win_rate_pct": None,
            "avg_pnl_pct": None,
            "avg_hold_minutes": None,
            "best_trade_pct": None,
            "worst_trade_pct": None,
        }
    pnls = [float(r["pnl_pct"]) for r in records if r["pnl_pct"] is not None]
    holds = [int(r["hold_minutes"]) for r in records if r["hold_minutes"] is not None]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trade_count": n,
        "insufficient_data": False,
        "win_rate_pct": round(wins / len(pnls) * 100, 1) if pnls else 0.0,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 1) if pnls else 0.0,
        "avg_hold_minutes": round(sum(holds) / len(holds)) if holds else 0,
        "best_trade_pct": round(max(pnls), 1) if pnls else 0.0,
        "worst_trade_pct": round(min(pnls), 1) if pnls else 0.0,
    }


def _get_effectiveness_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Strategy effectiveness over CLOSED trades. (CIL-063, Sprint 31 Thread 3.)

    Groups CLOSED prime_trade_log records by strategy and computes win rate,
    average P&L %, average hold, and best/worst trade %. A strategy with fewer
    than MIN_EFFECTIVENESS_TRADES closed trades is returned with
    insufficient_data=True and null metric fields. Also returns an 'overall'
    aggregate across all CLOSED trades and an 'as_of' ISO timestamp.
    """
    with get_connection(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT strategy, pnl_pct, hold_minutes"
            " FROM prime_trade_log WHERE status='CLOSED'"
        ).fetchall()]

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row.get("strategy") or "UNKNOWN", []).append(row)

    by_strategy = []
    for strategy in sorted(groups):
        entry = {"strategy": strategy}
        entry.update(_effectiveness_row(groups[strategy]))
        by_strategy.append(entry)

    overall = {"strategy": "ALL"}
    overall.update(_effectiveness_row(rows))

    return {
        "by_strategy": by_strategy,
        "overall": overall,
        "as_of": datetime.now().isoformat(),
    }


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
