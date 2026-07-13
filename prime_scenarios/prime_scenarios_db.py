"""
PRIME v1.0 Scenarios Database Layer (WO-PRIME-SCENARIOS-01).

Stores and retrieves detected scenario convergence events.
Scanner signal records are never modified by this layer.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_data.prime_db import get_connection

logger = logging.getLogger(__name__)

_PRIME_SCENARIOS_SCHEMA = """
CREATE TABLE IF NOT EXISTS prime_scenarios (
    scenario_id         TEXT PRIMARY KEY,
    type_num            TEXT NOT NULL,
    type_name           TEXT NOT NULL,
    direction           TEXT NOT NULL,
    conviction          TEXT NOT NULL,
    primary_symbol      TEXT NOT NULL,
    constituent_signals TEXT NOT NULL DEFAULT '[]',
    staleness_status    TEXT NOT NULL DEFAULT 'FRESH',
    is_active           INTEGER NOT NULL DEFAULT 1,
    detected_at         TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_PRIME_SCENARIOS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_scenarios_symbol_type
ON prime_scenarios (primary_symbol, type_num, detected_at)
"""


def init_scenarios_table(db_path: Optional[Path] = None) -> None:
    """Create the prime_scenarios table and index (idempotent)."""
    with get_connection(db_path) as conn:
        conn.execute(_PRIME_SCENARIOS_SCHEMA)
        conn.execute(_PRIME_SCENARIOS_INDEX)
        conn.commit()


def make_scenario_id(type_num: str, primary_symbol: str, direction: str, detected_at: str) -> str:
    """Deterministic scenario_id from the natural key."""
    key = f"{type_num}:{primary_symbol.upper()}:{direction}:{detected_at}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def insert_scenario(
    scenario: Dict[str, Any],
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a scenario record. Returns scenario_id if inserted, None on duplicate."""
    scenario_id = scenario.get("scenario_id") or make_scenario_id(
        scenario["type_num"],
        scenario["primary_symbol"],
        scenario["direction"],
        scenario["detected_at"],
    )
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO prime_scenarios
                   (scenario_id, type_num, type_name, direction, conviction,
                    primary_symbol, constituent_signals, staleness_status,
                    is_active, detected_at)
                   VALUES (?,?,?,?,?,?,?,?,1,?)""",
                (
                    scenario_id,
                    scenario["type_num"],
                    scenario["type_name"],
                    scenario["direction"],
                    scenario["conviction"],
                    scenario["primary_symbol"].upper(),
                    json.dumps(scenario.get("constituent_signals", [])),
                    scenario.get("staleness_status", "FRESH"),
                    scenario["detected_at"],
                ),
            )
            conn.commit()
        return scenario_id if cursor.rowcount > 0 else None
    except Exception as e:
        logger.warning("insert_scenario failed: %s", e)
        return None


def get_scenarios(
    limit: int = 100,
    active_only: bool = True,
    direction: Optional[str] = None,
    type_num: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return scenarios ordered most-recently-detected first."""
    clauses = []
    params: List[Any] = []
    if active_only:
        clauses.append("is_active = 1")
    if direction:
        clauses.append("direction = ?")
        params.append(direction)
    if type_num:
        clauses.append("type_num = ?")
        params.append(type_num)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM prime_scenarios {where} ORDER BY detected_at DESC LIMIT ?",
            params,
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        try:
            d["constituent_signals"] = json.loads(d.get("constituent_signals") or "[]")
        except Exception:
            d["constituent_signals"] = []
        result.append(d)
    return result


def expire_old_scenarios(db_path: Optional[Path] = None) -> int:
    """Deactivate scenarios older than 24 hours. Returns row count updated."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """UPDATE prime_scenarios SET is_active = 0
               WHERE is_active = 1
               AND datetime(detected_at) < datetime('now', '-24 hours')"""
        )
        conn.commit()
        return cursor.rowcount
