"""
PRIME v1.0 Batch Entry Analyzer (ML-Pattern-15).

Analyzes all approvals from a scan run as a cohort. Identifies
sector concentration, correlated entries, and portfolio-level batch risk.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from prime_intelligence.prime_portfolio_factor import sector_map

logger = logging.getLogger(__name__)

MAX_SECTOR_CONCENTRATION = 0.40
PORTFOLIO_VALUE = 100_000.0


def analyze_batch(
    approved_signals: List[Dict[str, Any]],
    portfolio_value: float = PORTFOLIO_VALUE,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Analyze a batch of approved signals from a scan run.

    Returns batch summary with sector concentration, correlation flags,
    aggregate risk, and batch score.
    """
    batch_id = str(uuid4())
    scan_ts = datetime.utcnow().isoformat()

    if not approved_signals:
        summary = {
            "batch_id": batch_id,
            "scan_ts": scan_ts,
            "signal_count": 0,
            "sector_concentration": {},
            "concentration_breach": False,
            "correlation_flags": [],
            "aggregate_risk": 0.0,
            "batch_score": 100.0,
        }
        _persist(summary, db_path)
        return summary

    # Sector concentration
    sector_counts: Dict[str, int] = {}
    symbols = []
    total_max_loss = 0.0

    for sig in approved_signals:
        sym = sig.get("symbol", "???")
        symbols.append(sym)
        sector = sector_map(sym)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        total_max_loss += sig.get("max_loss", 0) or (sig.get("price_at_scan", 0) * sig.get("shares", 0) * 0.03)

    total = len(approved_signals)
    sector_pct = {s: c / total for s, c in sector_counts.items()}
    max_sector = max(sector_pct, key=sector_pct.get) if sector_pct else "N/A"
    concentration_breach = sector_pct.get(max_sector, 0) > MAX_SECTOR_CONCENTRATION

    # Correlation flags (simplified -- flag same-sector pairs)
    correlation_flags = []
    seen = set()
    for i, s1 in enumerate(symbols):
        for s2 in symbols[i+1:]:
            if sector_map(s1) == sector_map(s2):
                pair = tuple(sorted([s1, s2]))
                if pair not in seen:
                    seen.add(pair)
                    correlation_flags.append(f"{s1}/{s2} same sector ({sector_map(s1)})")

    # Aggregate risk
    aggregate_risk = (total_max_loss / portfolio_value * 100) if portfolio_value > 0 else 0

    # Batch score: 100 base, penalize concentration and correlation
    score = 100.0
    if concentration_breach:
        score -= 30
    score -= min(len(correlation_flags) * 5, 30)
    if aggregate_risk > 5:
        score -= 20
    elif aggregate_risk > 3:
        score -= 10
    score = max(score, 0)

    summary = {
        "batch_id": batch_id,
        "scan_ts": scan_ts,
        "signal_count": total,
        "sector_concentration": sector_pct,
        "concentration_breach": concentration_breach,
        "max_sector": max_sector,
        "correlation_flags": correlation_flags,
        "aggregate_risk": round(aggregate_risk, 2),
        "batch_score": round(score, 1),
    }

    _persist(summary, db_path)
    _tag_signals(approved_signals, batch_id, score, db_path)

    return summary


def _persist(summary: Dict[str, Any], db_path: Optional[Path] = None) -> None:
    try:
        from prime_data.prime_db import write_batch_summary
        write_batch_summary(
            batch_id=summary["batch_id"],
            scan_ts=summary["scan_ts"],
            signal_count=summary["signal_count"],
            sector_concentration=json.dumps(summary.get("sector_concentration", {})),
            correlation_flags=json.dumps(summary.get("correlation_flags", [])),
            aggregate_risk=summary.get("aggregate_risk", 0),
            batch_score=summary.get("batch_score", 0),
            db_path=db_path,
        )
    except Exception as e:
        logger.warning("Failed to persist batch summary: %s", e)


def _tag_signals(signals: List[Dict[str, Any]], batch_id: str, batch_score: float,
                 db_path: Optional[Path] = None) -> None:
    try:
        from prime_data.prime_db import get_connection
        with get_connection(db_path) as conn:
            for sig in signals:
                sid = sig.get("signal_id")
                if sid:
                    conn.execute(
                        "UPDATE prime_signals SET batch_id=?, batch_score=? WHERE signal_id=?",
                        (batch_id, batch_score, sid),
                    )
            conn.commit()
    except Exception as e:
        logger.debug("Failed to tag signals with batch_id: %s", e)
