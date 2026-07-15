"""
PRIME v1.0 Scenarios Database Layer (WO-PRIME-SCENARIOS-01).

Stores and retrieves detected scenario convergence events.
Scanner signal records are never modified by this layer.
"""

import hashlib
import json
import logging
import pytz
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_data.prime_db import get_connection

logger = logging.getLogger(__name__)

_ET = pytz.timezone("America/New_York")

# ---------------------------------------------------------------------------
# Scenario type priority for upgrade decisions (higher = more conviction)
# ---------------------------------------------------------------------------

_TYPE_PRIORITY: Dict[str, int] = {
    "0":  0,   # Unknown — review only
    "5":  1,   # Watch — single signal
    "1":  5,   # Pure — IDX STRONG alone
    "2":  6,   # Confirmed — IDX + PSA
    "6":  7,   # Anomalous — UOA/PEAD + PSA (no IDX)
    "7":  8,   # Sector Phase — SRS + PSA
    "8":  9,   # Metals MR — MMR T2 + PSA
    "9":  10,  # Timeframe Confluence — MTFA + confirming
    "3":  15,  # Institutional — IDX + UOA/PEAD + PSA
    "4":  20,  # Trifecta — IDX STRONG + UOA/PEAD + PSA
    "10": 25,  # Ultimate — IDX STRONG + UOA/PEAD + PSA + MTFA
}

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
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    session_date        TEXT NOT NULL DEFAULT ''
)
"""

_PRIME_SCENARIOS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_scenarios_symbol_type
ON prime_scenarios (primary_symbol, type_num, detected_at)
"""

_PRIME_SCENARIOS_DEDUP_INDEX = """
CREATE INDEX IF NOT EXISTS idx_scenarios_symbol_session
ON prime_scenarios (primary_symbol, session_date, is_active)
"""


def init_scenarios_table(db_path: Optional[Path] = None) -> None:
    """Create the prime_scenarios table and indexes (idempotent)."""
    with get_connection(db_path) as conn:
        conn.execute(_PRIME_SCENARIOS_SCHEMA)
        conn.execute(_PRIME_SCENARIOS_INDEX)
        conn.commit()
    # Migration must run before the dedup index so session_date exists on old DBs.
    _migrate_add_session_date(db_path)
    with get_connection(db_path) as conn:
        conn.execute(_PRIME_SCENARIOS_DEDUP_INDEX)
        conn.commit()


def _migrate_add_session_date(db_path: Optional[Path] = None) -> None:
    """Add session_date column to prime_scenarios if not present (idempotent)."""
    try:
        with get_connection(db_path) as conn:
            existing = [
                row[1]
                for row in conn.execute("PRAGMA table_info(prime_scenarios)").fetchall()
            ]
            if "session_date" not in existing:
                conn.execute(
                    "ALTER TABLE prime_scenarios ADD COLUMN session_date TEXT NOT NULL DEFAULT ''"
                )
                # Backfill from detected_at UTC date (close enough for historical rows)
                conn.execute(
                    "UPDATE prime_scenarios SET session_date = date(detected_at) "
                    "WHERE session_date = '' OR session_date IS NULL"
                )
                conn.commit()
    except Exception as exc:
        logger.warning("session_date migration skipped: %s", exc)


def _session_date_from_ts(ts_str: str) -> str:
    """Return ET date string (YYYY-MM-DD) for a UTC ISO timestamp."""
    try:
        clean = ts_str.split("Z")[0].split("+")[0].strip()
        dt_utc = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
        et = dt_utc.astimezone(_ET)
        return et.strftime("%Y-%m-%d")
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d")


def make_scenario_id(type_num: str, primary_symbol: str, direction: str, detected_at: str) -> str:
    """Deterministic scenario_id from the natural key."""
    key = f"{type_num}:{primary_symbol.upper()}:{direction}:{detected_at}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def insert_scenario(
    scenario: Dict[str, Any],
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a scenario record (INSERT OR IGNORE). Returns scenario_id if inserted, None on duplicate."""
    scenario_id = scenario.get("scenario_id") or make_scenario_id(
        scenario["type_num"],
        scenario["primary_symbol"],
        scenario["direction"],
        scenario["detected_at"],
    )
    session_date = _session_date_from_ts(scenario["detected_at"])
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO prime_scenarios
                   (scenario_id, type_num, type_name, direction, conviction,
                    primary_symbol, constituent_signals, staleness_status,
                    is_active, detected_at, session_date)
                   VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
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
                    session_date,
                ),
            )
            conn.commit()
        return scenario_id if cursor.rowcount > 0 else None
    except Exception as e:
        logger.warning("insert_scenario failed: %s", e)
        return None


def upsert_scenario(
    scenario: Dict[str, Any],
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Upsert a scenario with session-scoped dedup and upgrade logic.

    Rules enforced (WO-PRIME-SCENARIO-ENGINE-DEDUP-01):
    - One active scenario per symbol per ET session (day boundary).
    - Same type: update constituent_signals and detected_at in place.
    - Higher-priority type: upgrade existing record to the new type.
    - Lower-priority type: no-op — never downgrade an active scenario.
    - PEAD scenarios are naturally exempt: they carry prior-day session_dates
      that do not conflict with today's new scenarios.

    Returns the scenario_id of the inserted or updated record, or None when the
    existing record already has higher conviction (no change made).
    """
    primary_symbol = scenario["primary_symbol"].upper()
    detected_at = scenario["detected_at"]
    session_date = _session_date_from_ts(detected_at)
    type_num = scenario["type_num"]
    new_priority = _TYPE_PRIORITY.get(type_num, 0)

    try:
        with get_connection(db_path) as conn:
            row = conn.execute(
                """SELECT scenario_id, type_num FROM prime_scenarios
                   WHERE primary_symbol = ? AND session_date = ? AND is_active = 1
                   LIMIT 1""",
                (primary_symbol, session_date),
            ).fetchone()

            if row is None:
                # No existing scenario for this symbol in this session — insert fresh.
                scenario_id = scenario.get("scenario_id") or make_scenario_id(
                    type_num, primary_symbol, scenario["direction"], detected_at
                )
                conn.execute(
                    """INSERT INTO prime_scenarios
                       (scenario_id, type_num, type_name, direction, conviction,
                        primary_symbol, constituent_signals, staleness_status,
                        is_active, detected_at, session_date)
                       VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
                    (
                        scenario_id,
                        type_num,
                        scenario["type_name"],
                        scenario["direction"],
                        scenario["conviction"],
                        primary_symbol,
                        json.dumps(scenario.get("constituent_signals", [])),
                        scenario.get("staleness_status", "FRESH"),
                        detected_at,
                        session_date,
                    ),
                )
                conn.commit()
                return scenario_id

            existing_id = row["scenario_id"]
            existing_type = row["type_num"]
            existing_priority = _TYPE_PRIORITY.get(existing_type, 0)

            if new_priority > existing_priority:
                # Upgrade: replace type, keep existing scenario_id so the UI card
                # updates in place rather than appearing as a new card.
                conn.execute(
                    """UPDATE prime_scenarios
                       SET type_num=?, type_name=?, direction=?, conviction=?,
                           constituent_signals=?, staleness_status=?, detected_at=?
                       WHERE scenario_id=?""",
                    (
                        type_num,
                        scenario["type_name"],
                        scenario["direction"],
                        scenario["conviction"],
                        json.dumps(scenario.get("constituent_signals", [])),
                        scenario.get("staleness_status", "FRESH"),
                        detected_at,
                        existing_id,
                    ),
                )
                conn.commit()
                logger.info(
                    "Scenario upgraded: %s %s Type %s → Type %s",
                    primary_symbol, session_date, existing_type, type_num,
                )
                return existing_id

            elif new_priority == existing_priority:
                # Same type: refresh constituent signals and timestamp only.
                conn.execute(
                    """UPDATE prime_scenarios
                       SET constituent_signals=?, staleness_status=?, detected_at=?
                       WHERE scenario_id=?""",
                    (
                        json.dumps(scenario.get("constituent_signals", [])),
                        scenario.get("staleness_status", "FRESH"),
                        detected_at,
                        existing_id,
                    ),
                )
                conn.commit()
                return existing_id

            else:
                # Lower priority — never downgrade an active scenario.
                return None

    except Exception as exc:
        logger.warning("upsert_scenario failed: %s", exc)
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
