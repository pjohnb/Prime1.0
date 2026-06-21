"""
PRIME v1.0 Unusual Options Activity Scanner (UOA).
Ported from v0.9 prime_uoa_scanner.py + prime_uoa_enhancements.py.

Data source: Schwab options chain API (schwab-py get_option_chain).
TradeStation is retired (Sprint 25 / RETIRED.md). TS credential code
removed -- missing Schwab credentials yield 0 signals, never rc=1.

Scans 101 symbols for unusual options volume. Signals are scored by
sizzle index (current volume / historical baseline), classified by DTE
horizon (ST/MT/LT), and checked for covered-call noise before output.

Standalone: python prime_scanners/prime_uoa_scanner.py
"""

import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date as date_type
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_ABSOLUTE_VOLUME = 50_000
STRONG_THRESHOLD = 5.0
WATCH_THRESHOLD = 4.0
MAX_WORKERS = 15
OPTIONS_EXPIRY_DAYS = 60
DIRECTION_RATIO_THRESHOLD = 1.5
DEFAULT_BASELINE = 100_000

QUOTE_TIMEOUT = 8

# DTE classification bands (locked -- do not adjust without owner sign-off)
_ST_MAX_DTE = 10
_MT_MAX_DTE = 30

# Covered-call detection thresholds (locked)
_CC_VOL_OI_THRESHOLD = 1.5
_CC_STRIKE_BAND_PCT = 0.02
_CC_CLUSTER_THRESHOLD = 0.50

# Universe
MACRO_SYMBOLS = ["SPY"]

TOP50_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "BRK.B",
    "UNH", "XOM", "JNJ", "JPM", "V", "PG", "MA", "HD", "CVX", "MRK",
    "ABBV", "COST", "KO", "PEP", "AVGO", "TMO", "MCD", "CSCO", "ACN",
    "LLY", "ABT", "NKE", "ADBE", "DHR", "WMT", "CRM", "ORCL", "VZ",
    "TXN", "NEE", "BMY", "PM", "UPS", "RTX", "QCOM", "HON", "INTC",
    "AMGN", "SBUX", "LOW", "IBM", "CAT",
]

SP100_SYMBOLS = [
    "INTU", "NOW", "AMAT", "BKNG", "ISRG", "ADP", "GILD", "VRTX",
    "REGN", "ADI", "PANW", "LRCX", "MDLZ", "SYK", "MU", "KLAC",
    "PYPL", "MELI", "AXP", "ABNB", "NXPI", "SNPS", "CDNS", "MAR",
    "CRWD", "ORLY", "CTAS", "ADSK", "FTNT", "MNST", "WDAY", "MRVL",
    "CHTR", "PAYX", "CPRT", "ROST", "MCHP", "DXCM", "IDXX", "FAST",
    "ODFL", "KDP", "TEAM", "CSGP", "EXC", "BKR", "ON", "GEHC",
    "DASH", "COIN",
]


# ---------------------------------------------------------------------------
# Schwab client (replaces TS token management)
# ---------------------------------------------------------------------------

def _get_schwab_client():
    """Return a connected schwab-py Client, or None if unavailable."""
    try:
        import schwab
        cfg = get_config()
        ss = cfg.schwab_snapshot
        if not ss.schwab_token_path or not ss.schwab_app_key:
            logger.warning("UOA: Schwab credentials not configured -- 0 signals")
            return None
        client = schwab.auth.client_from_token_file(
            token_path=ss.schwab_token_path,
            api_key=ss.schwab_app_key,
            app_secret=ss.schwab_app_secret,
        )
        return client
    except Exception as e:
        logger.warning("UOA: Schwab client unavailable (%s) -- 0 signals", e)
        return None


# ---------------------------------------------------------------------------
# Schwab options chain fetch
# ---------------------------------------------------------------------------

def fetch_options_volume(
    symbol: str, today: datetime, client
) -> Optional[Dict[str, Any]]:
    """
    Fetch total/call/put options volume for a symbol via Schwab options chain.

    Uses schwab-py client.get_option_chain(). Aggregates volume across all
    strikes within OPTIONS_EXPIRY_DAYS. Returns None on failure or no data.
    """
    if client is None:
        return None

    exp_cutoff = (today + timedelta(days=OPTIONS_EXPIRY_DAYS)).date()

    try:
        resp = client.get_option_chain(
            symbol,
            include_underlying_quote=True,
        )
        if resp.status_code != 200:
            logger.warning("%s: Schwab options chain HTTP %s", symbol, resp.status_code)
            return None

        data = resp.json()

        call_vol = 0
        put_vol = 0
        legs: List[Dict[str, Any]] = []

        for exp_map_key, opt_type in (("callExpDateMap", "CALL"), ("putExpDateMap", "PUT")):
            for exp_key, strikes in data.get(exp_map_key, {}).items():
                # exp_key format: "2026-06-20:25" (date:dte)
                exp_date_str = exp_key.split(":")[0]
                try:
                    exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue

                if exp_date > exp_cutoff:
                    continue

                dte = (exp_date - today.date()).days

                for _strike_str, contracts in strikes.items():
                    for contract in contracts:
                        vol = int(contract.get("totalVolume", 0) or 0)
                        oi = contract.get("openInterest")
                        strike = contract.get("strikePrice")

                        if opt_type == "CALL":
                            call_vol += vol
                        else:
                            put_vol += vol

                        if vol > 0:
                            legs.append({
                                "dte": max(dte, 0),
                                "volume": vol,
                                "open_interest": oi,
                                "strike": strike,
                                "option_type": opt_type,
                            })

        total = call_vol + put_vol
        if total == 0:
            return None

        return {
            "total_volume": total,
            "call_volume": call_vol,
            "put_volume": put_vol,
            "legs": legs,
        }

    except Exception as e:
        logger.warning("%s: options fetch error: %s", symbol, e)
        return None


def fetch_underlying_quote(symbol: str, client) -> float:
    """Fetch underlying last-trade price via Schwab quote endpoint."""
    if client is None:
        return 0.0
    try:
        resp = client.get_quote(symbol)
        if resp.status_code != 200:
            return 0.0
        data = resp.json()
        quote_data = data.get(symbol, {})
        price = (
            quote_data.get("quote", {}).get("lastPrice")
            or quote_data.get("quote", {}).get("closePrice")
            or 0.0
        )
        return float(price)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# UOA-ENH-001: DTE Classifier
# ---------------------------------------------------------------------------

def classify_dte(legs: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [lg for lg in legs if lg.get("dte") is not None and lg.get("volume", 0) > 0]
    if not valid:
        return {
            "dte_class": "UNKNOWN",
            "weighted_dte": 0.0,
            "confidence": 0.0,
            "dominant_dte": 0,
            "rationale": "No valid option legs with volume",
        }

    total_vol = sum(lg["volume"] for lg in valid)
    weighted_dte = sum(lg["dte"] * lg["volume"] for lg in valid) / total_vol

    dominant = max(valid, key=lambda lg: lg["volume"])
    confidence = dominant["volume"] / total_vol if total_vol > 0 else 0.0

    if weighted_dte <= _ST_MAX_DTE:
        cls = "ST"
        rationale = f"Weighted DTE {weighted_dte:.0f}d <= {_ST_MAX_DTE} -> short-term"
    elif weighted_dte <= _MT_MAX_DTE:
        cls = "MT"
        rationale = f"Weighted DTE {weighted_dte:.0f}d in {_ST_MAX_DTE+1}-{_MT_MAX_DTE} -> medium-term"
    else:
        cls = "LT"
        rationale = f"Weighted DTE {weighted_dte:.0f}d > {_MT_MAX_DTE} -> long-term institutional"

    return {
        "dte_class": cls,
        "weighted_dte": round(weighted_dte, 1),
        "confidence": round(confidence, 3),
        "dominant_dte": dominant["dte"],
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# UOA-ENH-001: Covered Call Detector
# ---------------------------------------------------------------------------

def detect_covered_call(
    current_price: float,
    legs: List[Dict[str, Any]],
    dte_class: str,
) -> Dict[str, Any]:
    call_legs = [lg for lg in legs if lg.get("option_type") == "CALL"]
    if not call_legs:
        return {"status": "UNAVAILABLE", "vol_oi_ratio": None,
                "strike_cluster_pct": None, "rationale": "No call legs"}

    oi_legs = [lg for lg in call_legs if lg.get("open_interest")]
    total_call_vol = sum(lg["volume"] for lg in call_legs)

    vol_oi_ratio = None
    if oi_legs:
        total_oi = sum(int(lg["open_interest"]) for lg in oi_legs)
        if total_oi > 0:
            vol_oi_ratio = total_call_vol / total_oi

    if not current_price or current_price <= 0:
        return {
            "status": "UNAVAILABLE",
            "vol_oi_ratio": round(vol_oi_ratio, 3) if vol_oi_ratio else None,
            "strike_cluster_pct": None,
            "rationale": "No underlying price available",
        }

    upper_band = current_price * (1 + _CC_STRIKE_BAND_PCT)
    band_vol = sum(
        lg["volume"]
        for lg in call_legs
        if lg.get("strike") and current_price <= lg["strike"] <= upper_band
    )
    strike_cluster_pct = band_vol / total_call_vol if total_call_vol > 0 else 0.0

    if vol_oi_ratio is not None and vol_oi_ratio < _CC_VOL_OI_THRESHOLD and strike_cluster_pct >= _CC_CLUSTER_THRESHOLD:
        status = "NULLIFIED" if dte_class == "ST" else "SUSPECT"
        rationale = (
            f"CC pattern: vol/OI={vol_oi_ratio:.2f} < {_CC_VOL_OI_THRESHOLD} "
            f"AND cluster={strike_cluster_pct:.0%} >= {_CC_CLUSTER_THRESHOLD:.0%}"
        )
    else:
        status = "CLEAR"
        rationale = "No covered-call pattern detected"

    return {
        "status": status,
        "vol_oi_ratio": round(vol_oi_ratio, 3) if vol_oi_ratio else None,
        "strike_cluster_pct": round(strike_cluster_pct, 3),
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# Per-symbol scanner
# ---------------------------------------------------------------------------

def scan_symbol(
    symbol: str,
    baseline: float,
    today: datetime,
    group: str,
    client,
) -> Optional[Dict[str, Any]]:
    opts = fetch_options_volume(symbol, today, client)
    if opts is None:
        return None

    total = opts["total_volume"]
    if total == 0 or baseline <= 0:
        return None

    sizzle = total / baseline

    if sizzle < WATCH_THRESHOLD or total < MIN_ABSOLUTE_VOLUME:
        return None

    call_vol = opts["call_volume"]
    put_vol = opts["put_volume"]
    cp_ratio = call_vol / put_vol if put_vol > 0 else 999.0
    direction = "LONG" if cp_ratio > DIRECTION_RATIO_THRESHOLD else "SHORT"
    tier = "STRONG" if sizzle >= STRONG_THRESHOLD else "WATCH"

    # CIL-039: D-NOW (Direction Now) numeric score. The signed call/put-side
    # imbalance normalised to [-1.0, +1.0] -- the continuous momentum value
    # behind the categorical `direction` label. Exposed for ML training (AE-01)
    # so D-NOW can be used as a continuous feature, not just LONG/SHORT.
    # total > 0 is guaranteed above.
    dnow_score = round((call_vol - put_vol) / total, 4)

    # CIL-040: A-B raw volume. The raw call-minus-put side volume differential
    # (positive = call/ask-side, negative = put/bid-side institutional
    # positioning). A directional ML feature (AE-01) that distinguishes
    # call-side vs put-side flow more precisely than the categorical direction.
    ab_volume_raw = call_vol - put_vol

    legs = opts.get("legs", [])
    dte_result = classify_dte(legs)

    price = fetch_underlying_quote(symbol, client)
    cc_result = detect_covered_call(price, legs, dte_result["dte_class"])

    return {
        "symbol": symbol,
        "group": group,
        "tier": tier,
        "sizzle_index": round(sizzle, 2),
        "total_volume": total,
        "call_volume": call_vol,
        "put_volume": put_vol,
        "call_put_ratio": round(cp_ratio, 2),
        "dnow_score": dnow_score,
        "ab_volume_raw": ab_volume_raw,
        "direction": direction,
        "baseline_volume": int(baseline),
        "score": round(sizzle, 1),
        "price_at_scan": price,
        "weighted_dte": dte_result["weighted_dte"],
        "dte_classification": dte_result,
        "covered_call_eval": cc_result,
    }


# ---------------------------------------------------------------------------
# Baseline loading
# ---------------------------------------------------------------------------

def load_baselines(db_paths: List[Path]) -> Dict[str, float]:
    baselines: Dict[str, float] = {}
    for db_path in db_paths:
        if not db_path.exists():
            logger.warning("Baseline DB not found: %s", db_path)
            continue
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT symbol, AVG(total_volume) as avg_volume "
                "FROM daily_options_volume GROUP BY symbol"
            ).fetchall()
            for row in rows:
                sym = row["symbol"]
                avg = row["avg_volume"]
                if sym and avg and avg > 0:
                    baselines[sym] = float(avg)
            conn.close()
        except Exception as e:
            logger.warning("Failed to load baselines from %s: %s", db_path, e)
    return baselines


# ---------------------------------------------------------------------------
# CIL-046: Direct signal persistence (bypasses the bridge)
# ---------------------------------------------------------------------------

UOA_APPROVED_TIERS = ("STRONG", "WATCH")


def persist_uoa_signals(
    signals: List[Dict[str, Any]],
    scan_ts: str,
    db_path: Optional[Path] = None,
) -> int:
    """Write APPROVED UOA signals straight to prime_signals (CIL-046).

    Mirrors prime_signal_bridge.bridge_uoa_rows' field mapping but is driven by
    the scanner's own result dicts, so a scan persists without the CSV->bridge
    round-trip. Approved = tier in STRONG/WATCH. Deduplication is handled by
    insert_signal_dedup's deterministic signal_id (strategy|symbol|scan_ts), so
    re-running a scan with the same scan_ts never creates duplicate rows.

    Returns the number of new rows inserted (duplicates skipped).
    """
    from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup

    init_signals_table(db_path)
    inserted = 0
    for s in signals:
        tier = (s.get("tier") or "").strip().upper()
        if tier not in UOA_APPROVED_TIERS:
            continue
        symbol = (s.get("symbol") or "").strip()
        if not symbol:
            continue
        direction = (s.get("direction") or "LONG").strip().upper()
        # Sprint 23 Item 3 convention: call dominance -> LONG/UOA_CALL,
        # put dominance -> SHORT/UOA_PUT.
        trigger_source = "UOA_PUT" if direction == "SHORT" else "UOA_CALL"
        factors = json.dumps({
            "source": "schwab",
            "group": s.get("group", ""),
            "call_put_ratio": s.get("call_put_ratio"),
            "total_volume": s.get("total_volume"),
        })
        result = insert_signal_dedup(
            symbol=symbol,
            strategy="UOA",
            scan_ts=scan_ts,
            entry_price=s.get("price_at_scan") or 0.0,
            score=s.get("sizzle_index") or 0.0,
            tier=tier,
            status="APPROVED",
            direction=direction,
            factors=factors,
            instrument_type="EQUITY",
            trigger_source=trigger_source,
            db_path=db_path,
        )
        if result is not None:
            inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Main scan orchestrator
# ---------------------------------------------------------------------------

def run_uoa_scan(
    baselines: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    scan_time = datetime.now()

    if scan_time.weekday() >= 5:
        logger.info("Weekend -- skipping UOA scan")
        return {
            "scan_time": scan_time.isoformat(),
            "scanner": "prime_uoa_scanner",
            "version": "1.0",
            "skipped": True,
            "reason": "weekend",
            "signals": [],
        }

    client = _get_schwab_client()
    if not client:
        logger.warning("UOA: Schwab options data unavailable -- continuing with 0 signals")
        return {
            "scan_time": scan_time.isoformat(),
            "scanner": "prime_uoa_scanner",
            "version": "1.0",
            "skipped": False,
            "reason": "schwab_unavailable",
            "signals_found": 0,
            "tier1_count": 0,
            "tier2_count": 0,
            "macro_count": 0,
            "signals": [],
        }

    if baselines is None:
        baselines = {}

    all_symbols = []
    for sym in MACRO_SYMBOLS:
        all_symbols.append((sym, "Macro"))
    for sym in TOP50_SYMBOLS:
        all_symbols.append((sym, "Top50"))
    for sym in SP100_SYMBOLS:
        all_symbols.append((sym, "SP100"))

    signals: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for sym, group in all_symbols:
            bl = baselines.get(sym, DEFAULT_BASELINE)
            f = pool.submit(scan_symbol, sym, bl, scan_time, group, client)
            futures[f] = sym

        for f in as_completed(futures):
            sym = futures[f]
            try:
                result = f.result()
                if result is not None:
                    signals.append(result)
            except Exception as e:
                logger.warning("%s: scan error: %s", sym, e)

    signals.sort(key=lambda s: s["sizzle_index"], reverse=True)

    # CIL-046: persist approved signals directly to prime_signals (bypass bridge).
    try:
        persisted = persist_uoa_signals(
            signals, scan_time.strftime("%Y-%m-%d %H:%M:%S")
        )
        logger.info("UOA: %d signal(s) persisted to prime_signals", persisted)
    except Exception as e:
        logger.warning("UOA: direct signal persistence failed: %s", e)

    tier1 = [s for s in signals if s["tier"] == "STRONG"]
    tier2 = [s for s in signals if s["tier"] == "WATCH"]
    macro = [s for s in signals if s["group"] == "Macro"]

    logger.info(
        "UOA complete: %d signals (Tier1=%d Tier2=%d Macro=%d)",
        len(signals), len(tier1), len(tier2), len(macro),
    )

    return {
        "scan_time": scan_time.isoformat(),
        "scanner": "prime_uoa_scanner",
        "version": "1.0",
        "universe_size": len(all_symbols),
        "thresholds": {
            "strong": STRONG_THRESHOLD,
            "watch": WATCH_THRESHOLD,
            "min_volume": MIN_ABSOLUTE_VOLUME,
        },
        "signals_found": len(signals),
        "tier1_count": len(tier1),
        "tier2_count": len(tier2),
        "macro_count": len(macro),
        "signals": signals,
    }


def save_results(scan_data: Dict) -> Path:
    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"uoa_scan_{ts}_ET.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=2, default=str)
    logger.info("Results saved: %s", out)
    return out


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [UOA] %(levelname)s %(message)s",
    )

    from prime_data.prime_db import init_db, log_ops_event

    init_db()

    log_ops_event("SCAN_START", "uoa_scanner")

    scan_data = run_uoa_scan()

    if scan_data.get("skipped"):
        print(f"\nUOA Scan skipped: {scan_data.get('reason')}")
    elif scan_data.get("reason") == "schwab_unavailable":
        print("\nUOA Scan: Schwab unavailable -- 0 signals (graceful)")
    else:
        print(f"\nUOA Scan: {scan_data['signals_found']} signals "
              f"(Tier1={scan_data['tier1_count']} Tier2={scan_data['tier2_count']})")
        print(f"APPROVED: {scan_data['signals_found']} stocks")
        for s in scan_data["signals"]:
            cc = s.get("covered_call_eval", {})
            cc_tag = ""
            if cc.get("status") == "NULLIFIED":
                cc_tag = " **NULLIFIED**"
            elif cc.get("status") == "SUSPECT":
                cc_tag = " [SUSPECT]"
            dte = s.get("dte_classification", {})
            print(
                f"  {s['symbol']:<6} {s['tier']:<6} sizzle={s['sizzle_index']:5.1f}x  "
                f"vol={s['total_volume']:>8,}  C/P={s['call_put_ratio']:5.1f}  "
                f"{s['direction']:<5}  DTE={dte.get('dte_class','?')}{cc_tag}"
            )

        save_results(scan_data)

    log_ops_event(
        "SCAN_COMPLETE",
        "uoa_scanner",
        detail=f"signals={scan_data.get('signals_found', 0)}",
    )


if __name__ == "__main__":
    main()
