"""
PRIME v1.0 database layer.
All DB access goes through this module — no other module imports sqlite3 directly.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

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
