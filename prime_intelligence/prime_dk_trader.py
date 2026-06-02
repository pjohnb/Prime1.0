"""
PRIME v1.0 DK Trader (Sprint 15 Item 1).

Promotes the dark-pool (DK) nullifier widget into a first-class strategy.
Builds on prime_dark_pool.score_dk_signal() and writes DK strategy rows to
prime_signals, then propagates a DK verdict onto every other strategy's
signals and suppresses symbols the DK layer nullifies.

Classification:
    SIGNAL    -- dk_status CONFIRMING: bullish off-exchange accumulation.
    NULLIFIER -- dk_status NULLIFYING: activity that suppresses other signals.
    (NEUTRAL / UNAVAILABLE produce no DK row.)

dk_status propagation onto non-DK signals:
    CONFIRMED -- a DK SIGNAL exists for the symbol (DK agrees).
    NULLIFIED -- a DK NULLIFIER exists for the symbol (DK opposes).
    PENDING   -- no DK verdict for the symbol.

Nullifier suppression: any APPROVED non-DK signal whose symbol carries an
active DK NULLIFIER is flipped to status='SUPPRESSED', removing it from the
PSA/PEAD/UOA/MTS pipeline output.

The Tkinter "DK Trader tab" named in the work order lives in the frozen v0.9
prime_gui_app.py; v1.0's UI is the Lovable web app, where strategy="DK" rows
surface automatically in the Signals tab (dynamic strategy filter + DK badge).
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from prime_data.prime_db import get_connection
from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup
from prime_intelligence.prime_dark_pool import score_dk_signal

logger = logging.getLogger("prime_dk_trader")

# DK row tiers (also used as the strategy-level classification label).
TIER_SIGNAL = "SIGNAL"
TIER_NULLIFIER = "NULLIFIER"


def classify_dk(dk: Dict[str, Any]) -> Optional[str]:
    """Map a score_dk_signal() result to a DK classification, or None.

    CONFIRMING -> SIGNAL (bullish), NULLIFYING -> NULLIFIER (suppressive).
    NEUTRAL / UNAVAILABLE -> None (no tradeable/suppressive DK verdict).
    """
    status = (dk or {}).get("dk_status")
    if status == "CONFIRMING":
        return TIER_SIGNAL
    if status == "NULLIFYING":
        return TIER_NULLIFIER
    return None


def _get_watchlist() -> List[str]:
    try:
        from prime_scanners.prime_uoa_scanner import get_watchlist
        return get_watchlist()
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
                "AVGO", "JPM", "UNH", "V", "MA", "HD", "PG", "XOM"]


def _write_dk_row(symbol: str, dk: Dict[str, Any], classification: str,
                  scan_ts: str, db_path: Optional[Path]) -> Optional[str]:
    """Insert a strategy='DK' signal row and set its dk_score/dk_status."""
    status = "APPROVED" if classification == TIER_SIGNAL else "NULLIFIER"
    import json
    signal_id = insert_signal_dedup(
        symbol=symbol,
        strategy="DK",
        scan_ts=scan_ts,
        score=dk.get("dk_score") or 0.0,
        tier=classification,
        status=status,
        direction="LONG",
        factors=json.dumps({"dk_status": dk.get("dk_status"),
                            "detail": dk.get("detail", {})}),
        instrument_type="EQUITY",
        db_path=db_path,
    )
    if signal_id is not None:
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE prime_signals SET dk_score=?, dk_status=? WHERE signal_id=?",
                (dk.get("dk_score"), dk.get("dk_status"), signal_id),
            )
            conn.commit()
    return signal_id


def run_dk_trader_scan(
    symbols: Optional[List[str]] = None,
    scan_ts: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Scan symbols, classify DK activity, and write DK strategy rows.

    Returns a summary with the SIGNAL and NULLIFIER symbol lists.
    """
    init_signals_table(db_path)
    if symbols is None:
        symbols = _get_watchlist()
    if scan_ts is None:
        scan_ts = datetime.utcnow().isoformat()

    summary: Dict[str, Any] = {
        "scanned": 0, "signals": [], "nullifiers": [],
        "neutral": [], "unavailable": [], "errors": [], "scan_ts": scan_ts,
    }
    for symbol in symbols:
        try:
            dk = score_dk_signal(symbol)
            summary["scanned"] += 1
            cls = classify_dk(dk)
            if cls == TIER_SIGNAL:
                _write_dk_row(symbol, dk, cls, scan_ts, db_path)
                summary["signals"].append(symbol)
            elif cls == TIER_NULLIFIER:
                _write_dk_row(symbol, dk, cls, scan_ts, db_path)
                summary["nullifiers"].append(symbol)
            elif dk.get("dk_status") == "UNAVAILABLE":
                summary["unavailable"].append(symbol)
            else:
                summary["neutral"].append(symbol)
        except Exception as e:
            logger.error("DK trader scan error for %s: %s", symbol, e)
            summary["errors"].append({"symbol": symbol, "error": str(e)})

    logger.info("DK trader scan: %d scanned, %d signals, %d nullifiers",
                summary["scanned"], len(summary["signals"]), len(summary["nullifiers"]))
    return summary


def get_active_nullifiers(db_path: Optional[Path] = None) -> Set[str]:
    """Symbols carrying an active DK NULLIFIER row."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM prime_signals "
            "WHERE strategy='DK' AND tier=?", (TIER_NULLIFIER,),
        ).fetchall()
        return {r[0] for r in rows}


def get_dk_signals(db_path: Optional[Path] = None) -> Set[str]:
    """Symbols carrying a DK SIGNAL row."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM prime_signals "
            "WHERE strategy='DK' AND tier=?", (TIER_SIGNAL,),
        ).fetchall()
        return {r[0] for r in rows}


def propagate_dk_status(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Set dk_status on all non-DK signals based on DK verdicts.

    CONFIRMED if a DK SIGNAL exists for the symbol, NULLIFIED if a DK NULLIFIER
    exists (NULLIFIER takes precedence), otherwise PENDING.
    """
    nullifiers = get_active_nullifiers(db_path)
    signals = get_dk_signals(db_path) - nullifiers
    counts = {"CONFIRMED": 0, "NULLIFIED": 0, "PENDING": 0}
    with get_connection(db_path) as conn:
        if nullifiers:
            q = ",".join("?" for _ in nullifiers)
            counts["NULLIFIED"] = conn.execute(
                f"UPDATE prime_signals SET dk_status='NULLIFIED' "
                f"WHERE strategy!='DK' AND symbol IN ({q})", tuple(nullifiers),
            ).rowcount
        if signals:
            q = ",".join("?" for _ in signals)
            counts["CONFIRMED"] = conn.execute(
                f"UPDATE prime_signals SET dk_status='CONFIRMED' "
                f"WHERE strategy!='DK' AND symbol IN ({q})", tuple(signals),
            ).rowcount
        verdict = nullifiers | signals
        if verdict:
            q = ",".join("?" for _ in verdict)
            counts["PENDING"] = conn.execute(
                f"UPDATE prime_signals SET dk_status='PENDING' "
                f"WHERE strategy!='DK' AND symbol NOT IN ({q})", tuple(verdict),
            ).rowcount
        else:
            counts["PENDING"] = conn.execute(
                "UPDATE prime_signals SET dk_status='PENDING' WHERE strategy!='DK'"
            ).rowcount
        conn.commit()
    return counts


def apply_nullifier_suppression(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Suppress APPROVED non-DK signals whose symbol is DK-nullified.

    Flips matching rows to status='SUPPRESSED'. Returns count + symbols.
    """
    nullifiers = get_active_nullifiers(db_path)
    if not nullifiers:
        return {"suppressed": 0, "symbols": []}
    q = ",".join("?" for _ in nullifiers)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            f"UPDATE prime_signals SET status='SUPPRESSED' "
            f"WHERE strategy!='DK' AND status='APPROVED' AND symbol IN ({q})",
            tuple(nullifiers),
        )
        conn.commit()
        suppressed = cur.rowcount
    logger.info("DK suppression: %d signals suppressed for %d nullified symbols",
                suppressed, len(nullifiers))
    return {"suppressed": suppressed, "symbols": sorted(nullifiers)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = run_dk_trader_scan()
    propagate_dk_status()
    sup = apply_nullifier_suppression()
    print(f"DK Trader: {len(s['signals'])} SIGNAL, {len(s['nullifiers'])} NULLIFIER; "
          f"{sup['suppressed']} suppressed")
