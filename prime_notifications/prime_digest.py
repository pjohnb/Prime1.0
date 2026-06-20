"""
PRIME v1.0 Scan Completion Digest (Ops Sprint 2, Phase 1).

Assembles the scan completion digest: scanner name, timestamp, signal count,
top signals ranked by composite score, open position summary.

Footer reads next scan time live from ops_config.json -- never hardcoded.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_SIGNALS_PER_DIGEST = 10


def _load_ops_config() -> Dict[str, Any]:
    ops_path = _PROJECT_ROOT / "ops_config.json"
    if not ops_path.exists():
        return {}
    with open(ops_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_next_scan_time(scanner_name: str) -> str:
    """Read next scheduled scan time live from ops_config.json."""
    ops = _load_ops_config()
    schedule = ops.get("scan_schedule", {})
    scanner_sched = schedule.get(scanner_name, {})
    times = scanner_sched.get("times_et", [])
    days = scanner_sched.get("days", "weekdays")

    if not times:
        return "Not scheduled"

    now = datetime.now()
    current_time = now.strftime("%H:%M")

    future_times = [t for t in sorted(times) if t > current_time]
    if future_times:
        next_time = future_times[0]
        return f"{next_time} ET today ({days})"

    next_time = sorted(times)[0]
    return f"{next_time} ET next trading day ({days})"


# CIL-091 (Sprint 30 Thread 3): all-scanner schedule summary for the footer.
# Canonical display order; any scanner present in config but not listed here is
# appended in config order.
_SCAN_SUMMARY_ORDER = ["psa", "uoa", "pead", "srs", "idx", "mts", "short"]
_SCAN_SUMMARY_FALLBACK = (
    "PSA: 09:45/11:20/12:50/14:20 ET | UOA+PEAD+SRS: 12:40 ET | "
    "IDX+MTS: 12:45 ET | SHORT: 12:50 ET"
)


def _format_scan_schedule_summary() -> str:
    """Build an all-scanner schedule summary line from ops_config.json.

    Scanners that share an identical times_et list are grouped together
    (e.g. "UOA+PEAD+SRS: 12:40 ET"). Falls back to a static string if
    ops_config.json is unavailable or has no scan_schedule. (CIL-091.)
    """
    schedule = _load_ops_config().get("scan_schedule", {})
    if not schedule:
        return _SCAN_SUMMARY_FALLBACK

    ordered = [s for s in _SCAN_SUMMARY_ORDER if s in schedule]
    ordered += [s for s in schedule if s not in _SCAN_SUMMARY_ORDER]

    groups: List[Tuple[Tuple[str, ...], List[str]]] = []
    for code in ordered:
        times = tuple(schedule.get(code, {}).get("times_et", []))
        if not times:
            continue
        for g_times, g_codes in groups:
            if g_times == times:
                g_codes.append(code.upper())
                break
        else:
            groups.append((times, [code.upper()]))

    if not groups:
        return _SCAN_SUMMARY_FALLBACK

    return " | ".join(
        f"{'+'.join(codes)}: {'/'.join(times)} ET" for times, codes in groups
    )


def assemble_digest(
    scanner_name: str,
    signals: List[Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    run_timestamp: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """Assemble a scan completion digest.

    Returns (structured_dict, formatted_plaintext_string).
    """
    if run_timestamp is None:
        run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S ET")

    sorted_signals = sorted(
        signals, key=lambda s: s.get("score", 0.0), reverse=True
    )[:MAX_SIGNALS_PER_DIGEST]

    signal_rows = []
    for sig in sorted_signals:
        factors = sig.get("trade_factors", {})
        if isinstance(factors, str):
            try:
                factors = json.loads(factors)
            except (json.JSONDecodeError, TypeError):
                factors = {}

        factor_flags = []
        if factors.get("nullifier", {}).get("status") == "NULLIFIED":
            factor_flags.append("NULLIFIED")
        if factors.get("duration", {}).get("class"):
            factor_flags.append(factors["duration"]["class"])
        if factors.get("entry", {}).get("method"):
            factor_flags.append(factors["entry"]["method"])

        signal_rows.append({
            "symbol": sig.get("symbol", "???"),
            "strategy": sig.get("strategy", "???"),
            "composite_score": sig.get("score", 0.0),
            "factor_flags": factor_flags[:3],
            "entry_price": sig.get("price_at_scan", 0.0),
        })

    position_rows = []
    for pos in open_positions:
        current_price = pos.get("current_price", pos.get("entry_price", 0.0))
        entry_price = pos.get("entry_price") or pos.get("price_at_scan", 0.0)
        unrealized = 0.0
        if entry_price and current_price:
            unrealized = (current_price - entry_price) * pos.get("shares", 0)

        position_rows.append({
            "symbol": pos.get("symbol", "???"),
            "trade_source": pos.get("trade_source", "PAPER"),
            "entry_price": entry_price,
            "current_price": current_price,
            "unrealized_pnl": round(unrealized, 2),
        })

    next_scan = _get_next_scan_time(scanner_name)

    # CIL-TS-001: include token refresh count metric
    try:
        from prime_trading.prime_ts_auth import get_refresh_count
        token_refresh_count = get_refresh_count()
    except Exception:
        token_refresh_count = 0

    digest = {
        "scanner": scanner_name,
        "timestamp": run_timestamp,
        "signal_count": len(signals),
        "signals": signal_rows,
        "open_positions": position_rows,
        "next_scan_time": next_scan,
        "scan_schedule_summary": _format_scan_schedule_summary(),
        "token_refresh_count": token_refresh_count,
    }

    text = _format_plaintext(digest)
    return digest, text


def _format_plaintext(digest: Dict[str, Any]) -> str:
    lines = [
        f"PRIME Scan Digest -- {digest['scanner'].upper()}",
        f"Run: {digest['timestamp']}",
        f"Signals: {digest['signal_count']}",
        "",
        "--- Top Signals ---",
    ]

    if not digest["signals"]:
        lines.append("  (none)")
    else:
        lines.append(f"  {'Symbol':<8} {'Strategy':<8} {'Score':>6}  {'Factors':<30}  {'Entry':>10}")
        lines.append(f"  {'-'*8} {'-'*8} {'-'*6}  {'-'*30}  {'-'*10}")
        for sig in digest["signals"]:
            flags_str = ", ".join(sig["factor_flags"]) if sig["factor_flags"] else "--"
            lines.append(
                f"  {sig['symbol']:<8} {sig['strategy']:<8} {sig['composite_score']:>6.1f}  "
                f"{flags_str:<30}  ${sig['entry_price']:>9.2f}"
            )

    lines.append("")
    lines.append("--- Open Positions ---")
    if not digest["open_positions"]:
        lines.append("  (none)")
    else:
        lines.append(f"  {'Symbol':<8} {'Source':<8} {'Entry':>10}  {'Current':>10}  {'Unreal P&L':>12}")
        lines.append(f"  {'-'*8} {'-'*8} {'-'*10}  {'-'*10}  {'-'*12}")
        for pos in digest["open_positions"]:
            lines.append(
                f"  {pos['symbol']:<8} {pos['trade_source']:<8} "
                f"${pos['entry_price']:>9.2f}  ${pos['current_price']:>9.2f}  "
                f"${pos['unrealized_pnl']:>11.2f}"
            )

    lines.append("")
    lines.append(f"Next scan: {digest['next_scan_time']}")
    summary = digest.get("scan_schedule_summary") or _format_scan_schedule_summary()
    lines.append(f"Next scheduled scans: {summary}")
    lines.append(f"TS token refreshes: {digest.get('token_refresh_count', 0)}")
    lines.append("")

    return "\n".join(lines)
