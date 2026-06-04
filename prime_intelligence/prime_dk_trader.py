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

dk_status propagation onto non-DK signals (Sprint 20 three-state; PENDING retired):
    CONFIRMING -- institutional dark-pool BUYING on the symbol (a DK SIGNAL row).
    NULLIFYING -- institutional dark-pool SELLING on the symbol (a DK NULLIFIER row).
    NEUTRAL    -- no significant DK activity for the symbol.
dk_status is the ABSOLUTE dark-pool direction; the direction-aware EFFECT (upgrade
vs suppress, per the Section 3 reference table) is applied by the PSA and short
scanners. dk_conviction (0.0-1.0) is propagated alongside dk_status.

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
from prime_intelligence.prime_dark_pool import score_dk_signal, score_dk_prints
from prime_data.prime_dk_feed import get_dk_prints

logger = logging.getLogger("prime_dk_trader")

# DK row tiers (also used as the strategy-level classification label).
TIER_SIGNAL = "SIGNAL"
TIER_NULLIFIER = "NULLIFIER"

# Sprint 20 Item 1: three-state dk_status vocabulary (PENDING retired).
DK_CONFIRMING = "CONFIRMING"   # institutional dark-pool buying
DK_NULLIFYING = "NULLIFYING"   # institutional dark-pool selling
DK_NEUTRAL = "NEUTRAL"         # no significant DK activity

# Map a DK row's tier to the absolute three-state dark-pool direction.
_TIER_TO_STATE = {TIER_SIGNAL: DK_CONFIRMING, TIER_NULLIFIER: DK_NULLIFYING}


def _compute_conviction(dk: Dict[str, Any],
                        matured: Optional[Dict[str, Any]] = None) -> float:
    """DK confidence in [0,1] for a CONFIRMING/NULLIFYING row (Sprint 20 Item 1).

    Blends the legacy composite dk_score and the matured print_score. A NULLIFYING
    short-volume override carries high conviction even when dk_score is 0.
    """
    dk_score = dk.get("dk_score") or 0.0
    print_score = (matured or {}).get("print_score") or 0.0
    base = max(dk_score, print_score) / 100.0
    if (dk.get("dk_status") == DK_NULLIFYING) and base < 0.5:
        base = 0.5  # a hard NULLIFYING override is high-conviction by construction
    return round(min(max(base, 0.0), 1.0), 3)


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


def _combine_verdicts(dk: Dict[str, Any], matured: Dict[str, Any]) -> Optional[str]:
    """Combine the legacy composite verdict with the matured prints verdict.

    The matured prints (volume_ratio / price_proximity / repeat_activity) refine
    the classification:
      * a matured NULLIFIER always wins (off-exchange volume working against the
        move suppresses regardless of the composite);
      * a matured SIGNAL upgrades a non-nullified symbol to SIGNAL;
      * otherwise the legacy classify_dk() verdict stands.

    When the matured verdict drives the classification, dk's dk_status/dk_score
    are updated in place so the written row stays internally consistent.
    """
    base = classify_dk(dk)
    verdict = (matured or {}).get("verdict")

    if verdict == TIER_NULLIFIER:
        dk["dk_status"] = "NULLIFYING"
        dk["dk_score"] = 0.0
        return TIER_NULLIFIER
    if verdict == TIER_SIGNAL and base != TIER_NULLIFIER:
        if base != TIER_SIGNAL:
            dk["dk_status"] = "CONFIRMING"
            dk["dk_score"] = max(dk.get("dk_score") or 0.0,
                                 matured.get("print_score") or 0.0)
        return TIER_SIGNAL
    return base


def _get_watchlist() -> List[str]:
    try:
        from prime_scanners.prime_uoa_scanner import get_watchlist
        return get_watchlist()
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
                "AVGO", "JPM", "UNH", "V", "MA", "HD", "PG", "XOM"]


def _write_dk_row(symbol: str, dk: Dict[str, Any], classification: str,
                  scan_ts: str, db_path: Optional[Path],
                  matured: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Insert a strategy='DK' signal row and set its dk_score/dk_status.

    When `matured` (a score_dk_prints() result) is provided, its three factors
    (volume_ratio, price_proximity, repeat_activity) are recorded in factors.
    """
    status = "APPROVED" if classification == TIER_SIGNAL else "NULLIFIER"
    import json
    factors: Dict[str, Any] = {"dk_status": dk.get("dk_status"),
                               "detail": dk.get("detail", {})}
    if matured is not None:
        factors["matured"] = {
            "volume_ratio": matured.get("volume_ratio"),
            "price_proximity": matured.get("price_proximity"),
            "repeat_activity": matured.get("repeat_activity"),
            "print_score": matured.get("print_score"),
            "verdict": matured.get("verdict"),
        }
    signal_id = insert_signal_dedup(
        symbol=symbol,
        strategy="DK",
        scan_ts=scan_ts,
        score=dk.get("dk_score") or 0.0,
        tier=classification,
        status=status,
        direction="LONG",
        factors=json.dumps(factors),
        instrument_type="EQUITY",
        db_path=db_path,
    )
    if signal_id is not None:
        conviction = _compute_conviction(dk, matured)
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE prime_signals SET dk_score=?, dk_status=?, dk_conviction=? "
                "WHERE signal_id=?",
                (dk.get("dk_score"), dk.get("dk_status"), conviction, signal_id),
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
    prints_date = scan_ts[:10] if scan_ts and len(scan_ts) >= 10 else None
    for symbol in symbols:
        try:
            dk = score_dk_signal(symbol)
            summary["scanned"] += 1

            # Matured factors from the single DK data entry point (prime_dk_feed).
            prints = get_dk_prints([symbol], prints_date)
            ref_price = (dk.get("detail") or {}).get("ref_price")
            matured = score_dk_prints(prints, reference_price=ref_price)

            # Combine the legacy composite verdict with the matured prints verdict.
            cls = _combine_verdicts(dk, matured)

            if cls == TIER_SIGNAL:
                _write_dk_row(symbol, dk, cls, scan_ts, db_path, matured)
                summary["signals"].append(symbol)
            elif cls == TIER_NULLIFIER:
                _write_dk_row(symbol, dk, cls, scan_ts, db_path, matured)
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


def _dk_verdicts(db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Per-symbol DK verdict {symbol: {state, conviction}} from DK rows.

    NULLIFYING takes precedence over CONFIRMING for the same symbol. Conviction
    is the DK row's dk_conviction.
    """
    out: Dict[str, Dict[str, Any]] = {}
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, tier, dk_conviction FROM prime_signals WHERE strategy='DK'"
        ).fetchall()
    for r in rows:
        sym = r["symbol"]
        state = _TIER_TO_STATE.get(r["tier"])
        if state is None:
            continue
        prev = out.get(sym)
        # NULLIFYING wins; otherwise first/any CONFIRMING.
        if prev is None or (state == DK_NULLIFYING and prev["state"] != DK_NULLIFYING):
            out[sym] = {"state": state, "conviction": r["dk_conviction"]}
    return out


def propagate_dk_status(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Set dk_status + dk_conviction on all non-DK signals from DK verdicts.

    Sprint 20 three-state: CONFIRMING if institutional buying (a DK SIGNAL row),
    NULLIFYING if institutional selling (a DK NULLIFIER row, precedence), else
    NEUTRAL. dk_conviction is copied from the DK row (NULL for NEUTRAL).
    Returns counts keyed by the three states.
    """
    verdicts = _dk_verdicts(db_path)
    counts = {DK_CONFIRMING: 0, DK_NULLIFYING: 0, DK_NEUTRAL: 0}
    with get_connection(db_path) as conn:
        for sym, v in verdicts.items():
            counts[v["state"]] += conn.execute(
                "UPDATE prime_signals SET dk_status=?, dk_conviction=? "
                "WHERE strategy!='DK' AND symbol=?",
                (v["state"], v["conviction"], sym),
            ).rowcount
        if verdicts:
            q = ",".join("?" for _ in verdicts)
            counts[DK_NEUTRAL] += conn.execute(
                f"UPDATE prime_signals SET dk_status='NEUTRAL', dk_conviction=NULL "
                f"WHERE strategy!='DK' AND symbol NOT IN ({q})", tuple(verdicts),
            ).rowcount
        else:
            counts[DK_NEUTRAL] += conn.execute(
                "UPDATE prime_signals SET dk_status='NEUTRAL', dk_conviction=NULL "
                "WHERE strategy!='DK'"
            ).rowcount
        conn.commit()
    return counts


def get_dk_status(symbol: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Absolute DK verdict for a symbol: {dk_status, dk_conviction}.

    CONFIRMING (buying) / NULLIFYING (selling, precedence) / NEUTRAL (none).
    Used by the PSA + short scanners and the AI advisory layer (Sprint 20).
    """
    v = _dk_verdicts(db_path).get((symbol or "").upper())
    if v is None:
        return {"dk_status": DK_NEUTRAL, "dk_conviction": None}
    return {"dk_status": v["state"], "dk_conviction": v["conviction"]}


def get_dk_status_counts(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Count non-DK signals by propagated dk_status (Sprint 20 dashboard/briefing).

    Returns {CONFIRMING, NEUTRAL, NULLIFYING}.
    """
    counts = {DK_CONFIRMING: 0, DK_NEUTRAL: 0, DK_NULLIFYING: 0}
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT dk_status, COUNT(*) AS n FROM prime_signals "
            "WHERE strategy!='DK' GROUP BY dk_status"
        ).fetchall()
    for r in rows:
        state = r["dk_status"] if r["dk_status"] in counts else DK_NEUTRAL
        counts[state] += r["n"]
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
