"""
PRIME v1.0 P&L Data Integrity Audit (CIL-PNL-INT).

One-time audit: reads all CLOSED records, flags any with NULL or zero
fill_price, fill_qty, or realized_pnl. Writes report to logs/.
"""

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import get_connection, init_db


def run_audit(db_path=None):
    """Audit all CLOSED trades for P&L data integrity.

    Returns list of flagged records and writes report to logs/.
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prime_trade_log WHERE status='CLOSED'"
        ).fetchall()

    flagged = []
    for row in rows:
        r = dict(row)
        issues = []
        if r.get("exit_price") is None or r.get("exit_price") == 0:
            issues.append("exit_price NULL or 0")
        if r.get("shares") is None or r.get("shares") == 0:
            issues.append("shares NULL or 0")
        if r.get("pnl_dollars") is None:
            issues.append("pnl_dollars NULL")
        if issues:
            flagged.append({
                "log_id": r["log_id"],
                "symbol": r["symbol"],
                "strategy": r["strategy"],
                "issues": issues,
            })

    # Write report
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d")
    report_path = logs_dir / f"pnl_audit_{ts}.txt"

    with open(report_path, "w") as f:
        f.write(f"PRIME P&L Audit Report -- {datetime.now().isoformat()}\n")
        f.write(f"Total CLOSED trades: {len(rows)}\n")
        f.write(f"Flagged records: {len(flagged)}\n\n")
        if flagged:
            for rec in flagged:
                f.write(f"  {rec['log_id']} | {rec['symbol']} | {rec['strategy']} | "
                        f"{', '.join(rec['issues'])}\n")
        else:
            f.write("  All records clean -- no integrity issues found.\n")

    return {"total_closed": len(rows), "flagged": flagged, "report_path": str(report_path)}


if __name__ == "__main__":
    result = run_audit()
    print(f"Audit complete: {result['total_closed']} closed trades, "
          f"{len(result['flagged'])} flagged")
    print(f"Report: {result['report_path']}")
