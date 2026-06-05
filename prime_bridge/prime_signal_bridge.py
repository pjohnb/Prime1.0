"""
PRIME Sprint 14 Item 1 -- v0.9 Scanner -> v1.0 DB Bridge.

The v0.9 scanners (PSA, PEAD, UOA, MTS, SRS) write their output to CSV/JSON
files in C:\\Dev\\PRIME\\scan_results and to the prime_ai_monitoring.db SQLite
database. This bridge intercepts that output, maps APPROVED signals to the
v1.0 prime_signals schema, and writes them to prime_trades.db -- so every scan
auto-populates the Lovable UI Signals tab with no manual import step.

Design:
  * strategy column = scanner name (UOA / PEAD / SRS / PSA / MTS) so the UI
    strategy filter works; scanner-specific grouping (e.g. UOA "group") is
    preserved inside the factors JSON blob.
  * instrument_type = "EQUITY" (matches the UI type filter and table default).
  * Deduplication via a deterministic signal_id (see make_signal_id) + an
    INSERT OR IGNORE in insert_signal_dedup(); re-ingesting a scan is a no-op.

Each adapter (bridge_uoa_rows, bridge_pead_rows, ...) is pure mapping logic and
returns the number of NEW signals inserted. ingest_latest() discovers the most
recent output for each scanner and bridges them all; it is the function wired
into run_scan.bat and exposed via the module CLI.

Usage (wired as the final step of the v0.9 scan pipeline):
    cd /d C:\\Dev\\PRIME1.0
    python -m prime_bridge.prime_signal_bridge --ingest-latest
"""

import argparse
import csv
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup

logger = logging.getLogger(__name__)

# Canonical v0.9 output locations (frozen repo).
V09_SCAN_RESULTS = Path(r"C:\Dev\PRIME\scan_results")
V09_MONITORING_DB = Path(r"C:\Dev\PRIME\prime_ai_monitoring.db")

# Approval gates per scanner (the value(s) that mean "tradeable signal").
UOA_APPROVED_TIERS = ("STRONG", "WATCH")
MTS_APPROVED_TRANCHES = ("TRANCHE_1", "TRANCHE_2")
SRS_APPROVED_PHASES = ("RECOVERING",)

INSTRUMENT_TYPE = "EQUITY"


def _to_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _insert(signal: Dict[str, Any], db_path: Optional[Path]) -> bool:
    """Insert one canonical signal dict; return True if a new row was written."""
    result = insert_signal_dedup(
        symbol=signal["symbol"],
        strategy=signal["strategy"],
        scan_ts=signal["scan_ts"],
        entry_price=signal.get("entry_price") or 0.0,
        score=signal.get("score") or 0.0,
        sector=signal.get("sector", "Unknown"),
        tier=signal.get("tier", ""),
        status=signal.get("status", "APPROVED"),
        direction=signal.get("direction", "LONG"),
        factors=json.dumps(signal.get("factors", {})),
        instrument_type=signal.get("instrument_type", INSTRUMENT_TYPE),
        trigger_source=signal.get("trigger_source"),
        db_path=db_path,
    )
    return result is not None


# ---------------------------------------------------------------------------
# Per-scanner adapters -- each takes raw scanner output, returns insert count.
# ---------------------------------------------------------------------------

def bridge_uoa_rows(rows: List[Dict[str, Any]], db_path: Optional[Path] = None) -> int:
    """UOA live_signals CSV rows. Approved = tier in STRONG/WATCH."""
    count = 0
    for row in rows:
        tier = (row.get("tier") or "").strip().upper()
        if tier not in UOA_APPROVED_TIERS:
            continue
        date = (row.get("date") or "").strip()
        time = (row.get("time") or "").strip()
        direction = (row.get("direction") or "LONG").strip().upper()
        # Sprint 23 Item 3: map UOA direction to trigger_source.
        trigger_source = "UOA_PUT" if direction == "SHORT" else "UOA_CALL"
        signal = {
            "symbol": (row.get("symbol") or "").strip(),
            "strategy": "UOA",
            "scan_ts": f"{date} {time}".strip(),
            "entry_price": _to_float(row.get("underlying_price")),
            "score": _to_float(row.get("sizzle_index")),
            "tier": tier,
            "direction": direction,
            "status": "APPROVED",
            "trigger_source": trigger_source,
            "factors": {
                "source": (row.get("data_source") or "").strip(),
                "group": (row.get("group") or "").strip(),
                "call_put_ratio": _to_float(row.get("call_put_ratio"), None),
                "total_volume": _to_float(row.get("total_volume"), None),
            },
        }
        if signal["symbol"] and _insert(signal, db_path):
            count += 1
    return count


def bridge_psa_rows(
    rows: List[Dict[str, Any]],
    scan_ts: str,
    db_path: Optional[Path] = None,
) -> int:
    """PSA CSV rows (Symbol,Momentum%,...,Approved). Approved = Approved==YES."""
    count = 0
    for row in rows:
        approved = (row.get("Approved") or "").strip().upper() == "YES"
        if not approved:
            continue
        signal = {
            "symbol": (row.get("Symbol") or "").strip(),
            "strategy": "PSA",
            "scan_ts": scan_ts,
            "entry_price": 0.0,  # PSA CSV carries no price
            "score": _to_float(row.get("Momentum%")),
            "tier": "",
            "direction": "LONG",
            "status": "APPROVED",
            # Sprint 23 Item 3: PSA is a pattern-only scanner (no external trigger).
            "trigger_source": "PSA_ONLY",
            "factors": {
                "momentum_pct": _to_float(row.get("Momentum%"), None),
                "volume_pct": _to_float(row.get("Volume%"), None),
                "volatility_pct": _to_float(row.get("Volatility%"), None),
                "consecutive": row.get("Consecutive"),
            },
        }
        if signal["symbol"] and _insert(signal, db_path):
            count += 1
    return count


def bridge_pead_rows(rows: List[Dict[str, Any]], db_path: Optional[Path] = None) -> int:
    """PEAD pead_signals rows. Approved = above_threshold == 1."""
    count = 0
    for row in rows:
        if int(row.get("above_threshold") or 0) != 1:
            continue
        direction = (row.get("direction") or "LONG").strip().upper()
        # Sprint 23 Item 3: LONG = earnings beat, SHORT = earnings miss/cut.
        trigger_source = "PEAD_BEAT" if direction == "LONG" else "PEAD_MISS"
        signal = {
            "symbol": (row.get("symbol") or "").strip(),
            "strategy": "PEAD",
            "scan_ts": (row.get("scan_timestamp") or "").strip(),
            "entry_price": _to_float(row.get("price_at_scan")),
            "score": _to_float(row.get("score")),
            "tier": "",
            "direction": direction,
            "status": "APPROVED",
            "trigger_source": trigger_source,
            "factors": {
                "eps_surprise_pct": _to_float(row.get("eps_surprise_pct"), None),
                "price_reaction_pct": _to_float(row.get("price_reaction_pct"), None),
                "days_since_earnings": row.get("days_since_earnings"),
                "earnings_date": row.get("earnings_date"),
            },
        }
        if signal["symbol"] and _insert(signal, db_path):
            count += 1
    return count


def bridge_mts_rows(rows: List[Dict[str, Any]], db_path: Optional[Path] = None) -> int:
    """MTS CSV rows. Approved = tranche in TRANCHE_1/TRANCHE_2 (WATCH excluded)."""
    count = 0
    for row in rows:
        tranche = (row.get("tranche") or "").strip().upper()
        if tranche not in MTS_APPROVED_TRANCHES:
            continue
        signal = {
            "symbol": (row.get("symbol") or "").strip(),
            "strategy": "MTS",
            "scan_ts": (row.get("scan_ts") or "").strip(),
            "entry_price": _to_float(row.get("price")),
            "score": _to_float(row.get("vol_surge_mult")),
            "tier": tranche,
            "direction": "LONG",
            "status": "APPROVED",
            "factors": {
                "confidence": (row.get("confidence") or "").strip(),
                "rsi": _to_float(row.get("rsi"), None),
                "pct_from_sma": _to_float(row.get("pct_from_sma"), None),
            },
        }
        if signal["symbol"] and _insert(signal, db_path):
            count += 1
    return count


def bridge_srs_result(data: Dict[str, Any], db_path: Optional[Path] = None) -> int:
    """SRS scan JSON. Approved = per-sector phase RECOVERING (LONG candidates)."""
    count = 0
    scan_ts = (data.get("scan_time") or "").strip()
    for sector_name, sec in (data.get("sectors") or {}).items():
        phase = (sec.get("phase") or "").strip().upper()
        if phase not in SRS_APPROVED_PHASES:
            continue
        metrics = sec.get("metrics") or {}
        signal = {
            "symbol": (sec.get("etf") or "").strip(),
            "strategy": "SRS",
            "scan_ts": scan_ts,
            "entry_price": _to_float(metrics.get("close")),
            "score": _to_float(metrics.get("chg_2d_pct")),
            "tier": phase,
            "direction": "LONG",
            "status": "APPROVED",
            "sector": sector_name,
            "factors": {
                "phase": phase,
                "chg_5d_pct": metrics.get("chg_5d_pct"),
                "chg_2d_pct": metrics.get("chg_2d_pct"),
            },
        }
        if signal["symbol"] and _insert(signal, db_path):
            count += 1
    return count


# ---------------------------------------------------------------------------
# File / DB discovery helpers
# ---------------------------------------------------------------------------

def _latest(scan_dir: Path, pattern: str) -> Optional[Path]:
    files = sorted(scan_dir.glob(pattern))
    return files[-1] if files else None


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _psa_scan_ts(path: Path) -> str:
    """Derive scan_ts from a PSA filename: psa_YYYYMMDD_HHMM_ET.csv."""
    m = re.search(r"(\d{8})_(\d{4})", path.name)
    if m:
        d, t = m.group(1), m.group(2)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:]}"
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _read_pead_latest(monitoring_db: Path) -> List[Dict[str, Any]]:
    """Read the most recent scan's approved PEAD signals from monitoring DB."""
    conn = sqlite3.connect(str(monitoring_db))
    conn.row_factory = sqlite3.Row
    try:
        latest = conn.execute(
            "SELECT MAX(scan_timestamp) FROM pead_signals"
        ).fetchone()[0]
        if not latest:
            return []
        rows = conn.execute(
            "SELECT * FROM pead_signals WHERE scan_timestamp = ? AND above_threshold = 1",
            (latest,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def ingest_latest(
    scan_dir: Path = V09_SCAN_RESULTS,
    monitoring_db: Path = V09_MONITORING_DB,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Discover and bridge the latest output for every scanner.

    Each scanner is handled independently: a missing file or a parse error for
    one scanner is logged and skipped, never aborting the others -- this runs
    unattended at the end of the scan pipeline.
    """
    init_signals_table(db_path)
    scan_dir = Path(scan_dir)
    results: Dict[str, int] = {"UOA": 0, "PSA": 0, "PEAD": 0, "MTS": 0, "SRS": 0}

    def _try(name: str, fn):
        try:
            results[name] = fn()
            logger.info("bridge %s: %d new signals", name, results[name])
        except Exception as e:  # pragma: no cover - defensive pipeline guard
            logger.warning("bridge %s skipped: %s", name, e)

    uoa = _latest(scan_dir, "live_signals_*.csv")
    if uoa:
        _try("UOA", lambda: bridge_uoa_rows(_read_csv(uoa), db_path))

    psa = _latest(scan_dir, "psa_*.csv")
    if psa:
        _try("PSA", lambda: bridge_psa_rows(_read_csv(psa), _psa_scan_ts(psa), db_path))

    mts = _latest(scan_dir, "mts_signals_*.csv")
    if mts:
        _try("MTS", lambda: bridge_mts_rows(_read_csv(mts), db_path))

    srs = _latest(scan_dir, "srs_scan_*.json")
    if srs:
        _try("SRS", lambda: bridge_srs_result(
            json.loads(srs.read_text(encoding="utf-8")), db_path))

    if Path(monitoring_db).exists():
        _try("PEAD", lambda: bridge_pead_rows(_read_pead_latest(Path(monitoring_db)), db_path))

    return results


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Bridge v0.9 scanner output into the v1.0 DB.")
    parser.add_argument("--ingest-latest", action="store_true",
                        help="Bridge the latest output for every scanner.")
    parser.add_argument("--scan-dir", default=str(V09_SCAN_RESULTS),
                        help="v0.9 scan_results directory.")
    parser.add_argument("--monitoring-db", default=str(V09_MONITORING_DB),
                        help="v0.9 prime_ai_monitoring.db path (PEAD source).")
    args = parser.parse_args(argv)

    results = ingest_latest(
        scan_dir=Path(args.scan_dir),
        monitoring_db=Path(args.monitoring_db),
    )
    total = sum(results.values())
    print("Scanner bridge: {0} new signals  {1}".format(
        total, "  ".join(f"{k}={v}" for k, v in results.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
