"""
PRIME v1.0 Health Monitor -- scanner execution monitoring and alerting.

Checks prime_ops_health table for scanner heartbeats, flags stale or
failed scanners, and reports data feed quality. Alert delivery writes
to log/file for now (push notification is Ops Sprint 2).

Usage:
  python prime_ops/prime_health_monitor.py --check     Run health check
  python prime_ops/prime_health_monitor.py --dashboard  Print status dashboard
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config
from prime_data.prime_db import get_ops_events, log_ops_event, init_db

logger = logging.getLogger(__name__)

SCANNERS = ["psa_scanner", "uoa_scanner", "pead_scanner", "srs_scanner", "mmr_scanner", "index_scanner"]

# Max staleness before a scanner is flagged (in minutes)
STALE_THRESHOLDS = {
    "psa_scanner": 120,     # runs every 90 min
    "uoa_scanner": 1440,    # runs once daily
    "pead_scanner": 1440,
    "srs_scanner": 1440,
    "mmr_scanner": 1440,
    "index_scanner": 1440,
}


# ---------------------------------------------------------------------------
# Health check logic
# ---------------------------------------------------------------------------

def check_scanner_health(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    # AUDIT-020: log_ops_event now writes machine-local ET (datetime.now()),
    # not UTC -- compare against local time to avoid a false STALE reading.
    now = datetime.now()
    results = []

    for scanner in SCANNERS:
        events = get_ops_events(component=scanner, limit=5, db_path=db_path)

        if not events:
            results.append({
                "scanner": scanner,
                "status": "NEVER_RUN",
                "last_event": None,
                "age_minutes": None,
                "detail": "No events recorded",
            })
            continue

        latest = events[0]
        event_type = latest.get("event_type", "")
        ts_str = latest.get("timestamp", "")
        detail = latest.get("detail", "")
        severity = latest.get("severity", "INFO")

        try:
            event_time = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            event_time = None

        age_minutes = None
        if event_time:
            age_minutes = (now - event_time).total_seconds() / 60.0

        threshold = STALE_THRESHOLDS.get(scanner, 1440)

        if event_type == "SCAN_ERROR" or severity == "ERROR":
            status = "ERROR"
        elif age_minutes is not None and age_minutes > threshold:
            status = "STALE"
        elif event_type == "SCAN_COMPLETE":
            status = "HEALTHY"
        elif event_type == "SCAN_START":
            status = "RUNNING"
        else:
            status = "UNKNOWN"

        results.append({
            "scanner": scanner,
            "status": status,
            "last_event": event_type,
            "last_time": ts_str,
            "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
            "detail": detail,
            "severity": severity,
        })

    return results


def check_data_feed_quality(db_path: Optional[Path] = None) -> Dict[str, Any]:
    events = get_ops_events(limit=50, db_path=db_path)

    scan_completes = [
        e for e in events
        if e.get("event_type") == "SCAN_COMPLETE"
    ]

    total_scans = len(scan_completes)
    scans_with_signals = 0
    total_signals = 0

    for e in scan_completes:
        detail = e.get("detail", "")
        if "signals=" in detail:
            try:
                sig_count = int(detail.split("signals=")[1].split()[0])
                total_signals += sig_count
                if sig_count > 0:
                    scans_with_signals += 1
            except (ValueError, IndexError):
                pass

    return {
        "recent_scans": total_scans,
        "scans_with_signals": scans_with_signals,
        "total_signals": total_signals,
        "signal_rate": round(scans_with_signals / total_scans * 100, 1) if total_scans > 0 else 0,
    }


_SECTORS_META_PATH = PROJECT_ROOT / "data" / "sectors_constituents_meta.json"
_SECTORS_JSON_PATH = PROJECT_ROOT / "data" / "sectors_constituents.json"
_SECTORS_STALE_DAYS = 8


def check_sectors_file_age() -> Optional[Dict[str, str]]:
    """Return a WARNING alert if sectors_constituents.json has not been refreshed in 8 days.

    Reads last_refresh_utc from the companion metadata file written by
    prime_sectors_refresh.  Falls back to the JSON file mtime when metadata
    is absent (first run before any refresh has executed).
    """
    if _SECTORS_META_PATH.exists():
        try:
            meta = json.loads(_SECTORS_META_PATH.read_text(encoding="utf-8"))
            ts_str = meta.get("last_refresh_utc", "")
            if not ts_str:
                return None
            last_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            age_days = (datetime.utcnow() - last_dt).days
            source = "metadata"
        except Exception:
            return None
    elif _SECTORS_JSON_PATH.exists():
        mtime = datetime.utcfromtimestamp(_SECTORS_JSON_PATH.stat().st_mtime)
        age_days = (datetime.utcnow() - mtime).days
        source = "file mtime (no refresh metadata)"
    else:
        return None

    if age_days > _SECTORS_STALE_DAYS:
        return {
            "level": "WARNING",
            "scanner": "sectors_refresh",
            "message": (
                f"sectors_constituents.json not refreshed in {age_days} days "
                f"({source}) — weekly refresh job may not be running"
            ),
        }
    return None


def generate_alerts(health: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    alerts = []
    for h in health:
        if h["status"] == "NEVER_RUN":
            alerts.append({
                "level": "WARNING",
                "scanner": h["scanner"],
                "message": f"{h['scanner']} has never run",
            })
        elif h["status"] == "ERROR":
            alerts.append({
                "level": "CRITICAL",
                "scanner": h["scanner"],
                "message": f"{h['scanner']} last event was ERROR: {h.get('detail', '')}",
            })
        elif h["status"] == "STALE":
            alerts.append({
                "level": "WARNING",
                "scanner": h["scanner"],
                "message": (
                    f"{h['scanner']} is stale -- last seen {h.get('age_minutes', '?')} "
                    f"min ago (threshold: {STALE_THRESHOLDS.get(h['scanner'], '?')} min)"
                ),
            })
    # CIL #35: sectors file staleness check
    sectors_alert = check_sectors_file_age()
    if sectors_alert:
        alerts.append(sectors_alert)
    return alerts


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_check():
    health = check_scanner_health()
    feed = check_data_feed_quality()
    alerts = generate_alerts(health)

    print(f"\nPRIME Health Check -- {datetime.now().strftime('%Y-%m-%d %H:%M ET')}\n")

    print(f"  {'Scanner':<18} {'Status':<12} {'Last Event':<16} {'Age (min)':<12}")
    print(f"  {'-'*18} {'-'*12} {'-'*16} {'-'*12}")
    for h in health:
        age = f"{h['age_minutes']:.0f}" if h.get("age_minutes") is not None else "-"
        print(f"  {h['scanner']:<18} {h['status']:<12} {h.get('last_event', '-'):<16} {age:<12}")

    print(f"\n  Data Feed Quality:")
    print(f"    Recent scans: {feed['recent_scans']}")
    print(f"    Scans with signals: {feed['scans_with_signals']} ({feed['signal_rate']}%)")
    print(f"    Total signals: {feed['total_signals']}")

    if alerts:
        print(f"\n  Alerts ({len(alerts)}):")
        for a in alerts:
            print(f"    [{a['level']}] {a['message']}")
    else:
        print(f"\n  No alerts -- all scanners healthy.")

    log_ops_event(
        "HEALTH_CHECK",
        "health_monitor",
        detail=f"alerts={len(alerts)} scanners_checked={len(health)}",
    )

    return alerts


def cmd_dashboard():
    health = check_scanner_health()
    feed = check_data_feed_quality()

    dashboard = {
        "timestamp": datetime.now().isoformat(),
        "scanners": health,
        "data_feed": feed,
        "alerts": generate_alerts(health),
    }

    print(json.dumps(dashboard, indent=2, default=str))
    return dashboard


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="PRIME v1.0 Health Monitor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Run health check")
    group.add_argument("--dashboard", action="store_true", help="Print JSON dashboard")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [HEALTH] %(levelname)s %(message)s",
    )

    init_db()

    if args.check:
        cmd_check()
    elif args.dashboard:
        cmd_dashboard()


if __name__ == "__main__":
    main()
