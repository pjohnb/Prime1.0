"""
PRIME v1.0 Short-Side Signal-Led Scanner (Sprint 17 Item 1, revised).

Signal-led architecture: a PREDICTIVE PRIMARY TRIGGER must identify a short
candidate BEFORE the move; technical criteria only CONFIRM the setup is not
already exhausted. Technical weakness alone is explicitly NOT a valid trigger
and is REJECTED (never enters prime_signals).

PRIMARY TRIGGERS (at least one required):
  * UOA_PUT  -- unusual put activity: put/call ratio > 2.0, put premium >
    PUT_PREMIUM_MIN, DTE <= 30, put volume > 3x 20-day avg put volume.
  * PEAD_MISS -- earnings miss + guidance cut within the last 5 trading days,
    stock still elevated vs pre-earnings price, drift window open.

TECHNICAL CONFIRMATION (all required once a trigger fires):
  * price below the 50-day SMA;
  * relative strength ratio vs SPY below 0.95 (underperforming by >5%);
  * no DK SIGNAL present (hard-block -- never short into institutional buying).

CLASSIFICATION:
  * both triggers + confirmation         -> STRONG_SHORT (tier STRONG)
  * one trigger + confirmation           -> WATCH         (tier WATCH)
  * triggers fire but confirmation fails -> REJECTED (setup exhausted)
  * technical only, no trigger           -> REJECTED (never enters prime_signals)

Gates applied before a signal is written: RTH only (Pattern 22), DK-bullish
hard-block, and the Schwab borrow hard-block (no borrow = no signal). Writes
strategy="SHORT", direction="SHORT", tier="STRONG"|"WATCH", trigger_source in
factors, borrow_rate_pct populated. run_short_scan() takes injected bars /
trigger data / borrow_fn / dk_signals / now so it is fully testable offline.

Assumption (documented per Absolute Authority): the work order's put-premium
threshold was unspecified in the source; PUT_PREMIUM_MIN defaults to $250,000.
"""

import json
import logging
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup
from prime_intelligence.prime_index_scanner import compute_sma, SMA_FAST
from prime_trading.prime_schwab_borrow import check_borrow

logger = logging.getLogger("prime_short_scanner")

BENCHMARK = "SPY"

# UOA put trigger thresholds.
PUT_CALL_RATIO_MIN = 2.0
PUT_PREMIUM_MIN = 250_000          # $ -- source threshold unspecified; documented default
PUT_DTE_MAX = 30
PUT_VOLUME_SURGE = 3.0             # x 20-day avg put volume

# PEAD short trigger.
PEAD_DRIFT_WINDOW_DAYS = 5

# Technical confirmation.
RS_RATIO_MAX = 0.95                # RS ratio vs SPY below this confirms weakness
RS_LOOKBACK = 20

STRONG_SHORT = "STRONG_SHORT"
WATCH = "WATCH"

RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)


# ---------------------------------------------------------------------------
# RTH / universe
# ---------------------------------------------------------------------------

def is_regular_hours(now: Optional[datetime] = None) -> bool:
    """True only during 09:30-16:00 ET on a weekday (Pattern 22)."""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return RTH_OPEN <= now.time() <= RTH_CLOSE


def _default_universe() -> List[str]:
    try:
        from prime_scanners.prime_uoa_scanner import get_watchlist
        return get_watchlist()
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
                "AVGO", "JPM", "UNH", "V", "MA", "HD", "PG", "XOM"]


# ---------------------------------------------------------------------------
# Primary triggers (pure)
# ---------------------------------------------------------------------------

def uoa_put_trigger(uoa: Optional[Dict[str, Any]]) -> bool:
    """UOA put trigger: heavy, near-dated, surging put activity."""
    if not uoa:
        return False
    pc = float(uoa.get("put_call_ratio", 0) or 0)
    premium = float(uoa.get("put_premium", 0) or 0)
    dte = uoa.get("dte")
    vol = float(uoa.get("put_volume", 0) or 0)
    avg = float(uoa.get("put_vol_avg_20d", 0) or 0)
    return (
        pc > PUT_CALL_RATIO_MIN
        and premium > PUT_PREMIUM_MIN
        and dte is not None and dte <= PUT_DTE_MAX
        and avg > 0 and vol > PUT_VOLUME_SURGE * avg
    )


def pead_short_trigger(pead: Optional[Dict[str, Any]]) -> bool:
    """PEAD short trigger: earnings miss + guidance cut, still elevated, in-window."""
    if not pead:
        return False
    days_since = pead.get("days_since_earnings")
    return (
        bool(pead.get("earnings_miss"))
        and bool(pead.get("guidance_cut"))
        and days_since is not None and 0 <= days_since <= PEAD_DRIFT_WINDOW_DAYS
        and bool(pead.get("still_elevated"))
    )


def primary_triggers(uoa: Optional[Dict[str, Any]],
                     pead: Optional[Dict[str, Any]]) -> List[str]:
    """Return the list of fired primary triggers (UOA_PUT / PEAD_MISS)."""
    fired = []
    if uoa_put_trigger(uoa):
        fired.append("UOA_PUT")
    if pead_short_trigger(pead):
        fired.append("PEAD_MISS")
    return fired


# ---------------------------------------------------------------------------
# Technical confirmation (pure)
# ---------------------------------------------------------------------------

def relative_strength_ratio(sym_closes: List[float], spy_closes: List[float],
                            lookback: int = RS_LOOKBACK) -> Optional[float]:
    """RS ratio vs SPY: (1+sym_return) / (1+spy_return) over lookback. <0.95 weak."""
    if len(sym_closes) <= lookback or len(spy_closes) <= lookback:
        return None
    s0, s1 = sym_closes[-(lookback + 1)], sym_closes[-1]
    b0, b1 = spy_closes[-(lookback + 1)], spy_closes[-1]
    if s0 <= 0 or b0 <= 0:
        return None
    sym_factor = s1 / s0
    spy_factor = b1 / b0
    if spy_factor <= 0:
        return None
    return sym_factor / spy_factor


def compute_confirmation_metrics(closes: List[float],
                                 spy_closes: List[float]) -> Optional[Dict[str, Any]]:
    """Compute the technical-confirmation metrics. None if insufficient bars."""
    if len(closes) < SMA_FAST:
        return None
    price = closes[-1]
    sma50 = compute_sma(closes, SMA_FAST)
    rs_ratio = relative_strength_ratio(closes, spy_closes)
    return {
        "price": round(price, 4),
        "sma50": round(sma50, 4) if sma50 is not None else None,
        "rs_ratio": round(rs_ratio, 4) if rs_ratio is not None else None,
    }


def technical_confirms(metrics: Dict[str, Any]) -> bool:
    """All technical confirmations: price below 50-SMA AND RS ratio < 0.95."""
    price = metrics.get("price")
    sma50 = metrics.get("sma50")
    rs_ratio = metrics.get("rs_ratio")
    if price is None or sma50 is None or rs_ratio is None:
        return False
    return price < sma50 and rs_ratio < RS_RATIO_MAX


def classify_short(triggers: List[str], confirms: bool) -> Optional[Dict[str, Any]]:
    """Signal-led classification. Returns verdict dict, or None when REJECTED.

    No trigger -> None (technical-only is never a signal).
    Trigger(s) without confirmation -> None (setup exhausted).
    Both triggers + confirmation -> STRONG_SHORT; one + confirmation -> WATCH.
    """
    if not triggers or not confirms:
        return None
    if len(triggers) >= 2:
        classification, tier = STRONG_SHORT, "STRONG"
    else:
        classification, tier = WATCH, "WATCH"
    return {
        "classification": classification,
        "tier": tier,
        "trigger_source": "+".join(triggers),
        "triggers": triggers,
        "direction": "SHORT",
    }


# ---------------------------------------------------------------------------
# Scan orchestration
# ---------------------------------------------------------------------------

def run_short_scan(
    symbols: Optional[List[str]] = None,
    scan_ts: Optional[str] = None,
    db_path: Optional[Path] = None,
    bars_by_symbol: Optional[Dict[str, List[Dict]]] = None,
    uoa_by_symbol: Optional[Dict[str, Dict[str, Any]]] = None,
    pead_by_symbol: Optional[Dict[str, Dict[str, Any]]] = None,
    borrow_fn=None,
    dk_signals: Optional[Set[str]] = None,
    now: Optional[datetime] = None,
    enforce_rth: bool = True,
) -> Dict[str, Any]:
    """Signal-led short scan with RTH, DK-bullish, and borrow hard-gates."""
    from prime_data.prime_db import log_ops_event

    init_signals_table(db_path)
    if symbols is None:
        symbols = _default_universe()
    if scan_ts is None:
        scan_ts = datetime.utcnow().isoformat()
    uoa_by_symbol = uoa_by_symbol or {}
    pead_by_symbol = pead_by_symbol or {}
    now = now or datetime.now()

    summary: Dict[str, Any] = {
        "scan_ts": scan_ts, "scanned": 0, "written": [], "rejected": [],
        "unconfirmed": [], "dk_blocked": [], "borrow_blocked": [],
        "errors": [], "rth_blocked": False,
    }

    if enforce_rth and not is_regular_hours(now):
        summary["rth_blocked"] = True
        log_ops_event("SHORT_SCAN_SKIP", "short_scanner",
                      detail="outside regular trading hours (Pattern 22)",
                      severity="INFO", db_path=db_path)
        return summary

    if dk_signals is None:
        try:
            from prime_intelligence.prime_dk_trader import get_dk_signals
            dk_signals = get_dk_signals(db_path)
        except Exception:
            dk_signals = set()

    def _bars(sym):
        return bars_by_symbol.get(sym) if bars_by_symbol is not None else None

    spy_bars = _bars(BENCHMARK)
    spy_closes = [b["close"] for b in spy_bars] if spy_bars else []

    for symbol in symbols:
        if symbol == BENCHMARK:
            continue
        try:
            summary["scanned"] += 1

            # PRIMARY TRIGGER -- required. Technical-only candidates never enter.
            triggers = primary_triggers(uoa_by_symbol.get(symbol),
                                        pead_by_symbol.get(symbol))
            if not triggers:
                summary["rejected"].append(symbol)
                continue

            bars = _bars(symbol)
            if not bars:
                summary["errors"].append({"symbol": symbol, "error": "no_data"})
                continue
            metrics = compute_confirmation_metrics([b["close"] for b in bars], spy_closes)
            if metrics is None:
                summary["errors"].append({"symbol": symbol, "error": "insufficient_bars"})
                continue

            # TECHNICAL CONFIRMATION -- all required, else the setup is exhausted.
            if not technical_confirms(metrics):
                summary["unconfirmed"].append(symbol)
                continue

            # Gate: DK bullish hard-block (never short into accumulation).
            if symbol in (dk_signals or set()):
                summary["dk_blocked"].append(symbol)
                log_ops_event("SHORT_BLOCK", "short_scanner", symbol=symbol,
                              detail="dk_bullish_block", severity="INFO", db_path=db_path)
                continue

            verdict = classify_short(triggers, True)  # confirmed above
            if verdict is None:
                summary["rejected"].append(symbol)
                continue

            # Gate: borrow hard-block (no borrow = no signal).
            borrow = check_borrow(symbol, borrow_fn=borrow_fn)
            if not borrow.get("borrowable"):
                summary["borrow_blocked"].append(symbol)
                log_ops_event("SHORT_BLOCK", "short_scanner", symbol=symbol,
                              detail="borrow_unavailable", severity="WARN", db_path=db_path)
                continue

            factors = {
                "classification": verdict["classification"],
                "trigger_source": verdict["trigger_source"],
                "triggers": verdict["triggers"],
                "metrics": metrics,
                "borrow_source": borrow.get("source"),
            }
            insert_signal_dedup(
                symbol=symbol, strategy="SHORT", scan_ts=scan_ts,
                entry_price=metrics["price"], score=0.0,
                sector="Unknown", tier=verdict["tier"], status="APPROVED",
                direction="SHORT", factors=json.dumps(factors),
                instrument_type="EQUITY", borrow_rate_pct=borrow.get("rate_pct"),
                db_path=db_path,
            )
            summary["written"].append(symbol)
        except Exception as e:
            logger.error("short scan error for %s: %s", symbol, e)
            summary["errors"].append({"symbol": symbol, "error": str(e)})

    logger.info("SHORT scan: %d scanned, %d written, %d rejected, %d unconfirmed, "
                "%d dk-blocked, %d borrow-blocked", summary["scanned"],
                len(summary["written"]), len(summary["rejected"]),
                len(summary["unconfirmed"]), len(summary["dk_blocked"]),
                len(summary["borrow_blocked"]))
    return summary


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [SHORT] %(levelname)s %(message)s")
    from prime_config.prime_config import get_config
    from prime_data.prime_db import init_db, log_ops_event

    cfg = get_config()
    if not cfg.polygon_api_key:
        logger.error("polygon_api_key not found in config.json")
        sys.exit(1)
    init_db()
    log_ops_event("SCAN_START", "short_scanner",
                  detail="signal-led short scan (requires UOA/PEAD trigger)")
    # Live trigger data (UOA/PEAD) and bars are supplied by the scan pipeline
    # after the UOA and PEAD scans complete; run with injected data in tests.
    summary = run_short_scan()
    log_ops_event("SCAN_COMPLETE", "short_scanner",
                  detail="written={0} borrow_blocked={1} dk_blocked={2}".format(
                      len(summary["written"]), len(summary["borrow_blocked"]),
                      len(summary["dk_blocked"])))
    print("SHORT Scan: {0} scanned, {1} signals, {2} rejected, {3} dk-blocked, "
          "{4} borrow-blocked".format(
              summary["scanned"], len(summary["written"]), len(summary["rejected"]),
              len(summary["dk_blocked"]), len(summary["borrow_blocked"])))


if __name__ == "__main__":
    main()
