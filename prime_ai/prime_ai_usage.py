"""
PRIME v1.0 AI Usage Tracking (Sprint 26 Item 6).

Logs every Claude API call to prime_data/prime_ai_usage.db.
Provides aggregated cost stats for the Dashboard AI Cost card and
the Settings AI Usage section. Uses Anthropic's exact field names:
input_tokens, output_tokens (matching the Anthropic Console).

Cost rate: $3.00/1M input tokens, $15.00/1M output tokens
(claude-sonnet-4-6 pricing as of 2026-06-24).
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "prime_ai_usage.db"

INPUT_COST_PER_M  = 3.0
OUTPUT_COST_PER_M = 15.0


def _get_db_path(db_path: Optional[Path] = None) -> Path:
    return db_path or _DEFAULT_DB


def init_usage_db(db_path: Optional[Path] = None) -> None:
    path = _get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                feature       TEXT NOT NULL,
                model         TEXT NOT NULL,
                input_tokens  INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd      REAL NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_usage_ts ON ai_usage(timestamp)
        """)
        conn.commit()
    finally:
        conn.close()


def log_usage(
    feature: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    db_path: Optional[Path] = None,
) -> None:
    """Insert one usage row. Swallows all exceptions (never blocks advisory path)."""
    try:
        init_usage_db(db_path)
        cost = (input_tokens * INPUT_COST_PER_M + output_tokens * OUTPUT_COST_PER_M) / 1_000_000
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        path = _get_db_path(db_path)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                "INSERT INTO ai_usage (timestamp, feature, model, input_tokens, output_tokens, cost_usd) "
                "VALUES (?,?,?,?,?,?)",
                (ts, feature, model, input_tokens, output_tokens, round(cost, 8)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def get_usage_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return aggregated usage stats for the /ai/usage endpoint."""
    try:
        init_usage_db(db_path)
        path = _get_db_path(db_path)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            now = datetime.now(tz=timezone.utc)
            today  = now.strftime("%Y-%m-%d")
            week_start  = now.strftime("%Y-%m-%d") if True else ""

            def _agg(where: str, params=()):
                row = conn.execute(
                    f"SELECT SUM(cost_usd) as c FROM ai_usage WHERE {where}", params
                ).fetchone()
                return round(float(row["c"] or 0), 6)

            today_cost  = _agg("date(timestamp)=date(?)", (today,))
            week_cost   = _agg("timestamp >= datetime('now','-7 days')")
            month_cost  = _agg("timestamp >= datetime('now','-30 days')")
            total_cost  = _agg("1=1")

            by_feature_rows = conn.execute("""
                SELECT feature,
                       COUNT(*) as calls,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(cost_usd) as cost_usd
                FROM ai_usage
                WHERE timestamp >= datetime('now','-30 days')
                GROUP BY feature ORDER BY cost_usd DESC
            """).fetchall()
            by_feature: List[Dict[str, Any]] = [
                {
                    "feature":       r["feature"],
                    "calls":         r["calls"],
                    "input_tokens":  r["input_tokens"] or 0,
                    "output_tokens": r["output_tokens"] or 0,
                    "cost_usd":      round(float(r["cost_usd"] or 0), 6),
                }
                for r in by_feature_rows
            ]

            recent_rows = conn.execute(
                "SELECT * FROM ai_usage ORDER BY id DESC LIMIT 10"
            ).fetchall()
            recent: List[Dict[str, Any]] = [dict(r) for r in recent_rows]

            return {
                "today_cost":  today_cost,
                "week_cost":   week_cost,
                "month_cost":  month_cost,
                "total_cost":  total_cost,
                "by_feature":  by_feature,
                "recent_calls": recent,
            }
        finally:
            conn.close()
    except Exception as e:
        return {
            "today_cost": 0.0, "week_cost": 0.0,
            "month_cost": 0.0, "total_cost": 0.0,
            "by_feature": [], "recent_calls": [], "error": str(e),
        }
