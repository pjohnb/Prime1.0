"""
PRIME v1.0 Unusual Options Activity Scanner (UOA).
Ported from v0.9 prime_uoa_scanner.py + prime_uoa_enhancements.py.

Scans 101 symbols for unusual options volume via TradeStation streaming
options chain API. Signals are scored by sizzle index (current volume /
historical baseline), classified by DTE horizon (ST/MT/LT), and checked
for covered-call noise before output.

Standalone: python prime_scanners/prime_uoa_scanner.py
"""

import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

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
MAX_STREAM_CONTRACTS = 2000
DIRECTION_RATIO_THRESHOLD = 1.5
DEFAULT_BASELINE = 100_000

API_TIMEOUT_CONNECT = 10
API_TIMEOUT_READ = 20
QUOTE_TIMEOUT = 8
TOKEN_REFRESH_BUFFER = 120

TS_AUTH_URL = "https://signin.tradestation.com/oauth/token"

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
# Token management (thread-safe)
# ---------------------------------------------------------------------------

_token_lock = threading.Lock()
_cached_token: Optional[str] = None
_cached_expiry: Optional[datetime] = None


def _refresh_ts_token(cfg) -> Optional[str]:
    global _cached_token, _cached_expiry
    ts = cfg.tradestation
    if not ts.client_id or not ts.refresh_token:
        return None
    try:
        r = requests.post(TS_AUTH_URL, data={
            "grant_type": "refresh_token",
            "client_id": ts.client_id,
            "client_secret": ts.client_secret,
            "refresh_token": ts.refresh_token,
        }, timeout=10)
        if r.status_code != 200:
            logger.error("TS token refresh failed: HTTP %s", r.status_code)
            return None
        body = r.json()
        _cached_token = body.get("access_token")
        expires_in = body.get("expires_in", 1200)
        _cached_expiry = datetime.now() + timedelta(seconds=expires_in)
        return _cached_token
    except Exception as e:
        logger.error("TS token refresh error: %s", e)
        return None


def get_ts_token() -> Optional[str]:
    global _cached_token, _cached_expiry
    with _token_lock:
        if _cached_token and _cached_expiry:
            if datetime.now() < _cached_expiry - timedelta(seconds=TOKEN_REFRESH_BUFFER):
                return _cached_token
        cfg = get_config()
        ts = cfg.tradestation
        if ts.access_token and ts.token_expiry:
            try:
                exp = datetime.fromisoformat(ts.token_expiry)
                if datetime.now() < exp - timedelta(seconds=TOKEN_REFRESH_BUFFER):
                    _cached_token = ts.access_token
                    _cached_expiry = exp
                    return _cached_token
            except ValueError:
                pass
        return _refresh_ts_token(cfg)


# ---------------------------------------------------------------------------
# TradeStation API
# ---------------------------------------------------------------------------

def fetch_options_volume(
    symbol: str, today: datetime, token: str
) -> Optional[Dict[str, Any]]:
    cfg = get_config()
    base = cfg.tradestation.api_base_url if hasattr(cfg.tradestation, "api_base_url") else "https://api.tradestation.com/v3"
    if not base:
        base = "https://api.tradestation.com/v3"
    url = f"{base}/marketdata/stream/options/chains/{symbol}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"StrikeProximity": 200}

    exp_cutoff = today + timedelta(days=OPTIONS_EXPIRY_DAYS)

    try:
        r = requests.get(
            url, headers=headers, params=params, stream=True,
            timeout=(API_TIMEOUT_CONNECT, API_TIMEOUT_READ),
        )
        if r.status_code == 401:
            logger.warning("%s: 401 auth failure -- token may be expired", symbol)
            return None
        if r.status_code != 200:
            logger.warning("%s: HTTP %s from options chain", symbol, r.status_code)
            return None

        call_vol = 0
        put_vol = 0
        legs: List[Dict[str, Any]] = []
        contract_count = 0

        for raw_line in r.iter_lines():
            if not raw_line:
                continue
            contract_count += 1
            if contract_count > MAX_STREAM_CONTRACTS:
                break

            try:
                contract = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            contract_legs = contract.get("Legs", [])
            if not contract_legs:
                continue

            leg = contract_legs[0]
            exp_str = leg.get("Expiration", "")
            if not exp_str:
                continue

            try:
                exp_date = datetime.fromisoformat(exp_str.replace("Z", "+00:00")).date()
            except ValueError:
                continue

            if exp_date > exp_cutoff.date():
                continue

            vol = int(contract.get("Volume", 0) or 0)
            side = leg.get("Side", "").upper()

            if side == "CALL":
                call_vol += vol
            elif side == "PUT":
                put_vol += vol

            if vol > 0:
                dte = (exp_date - today.date()).days
                legs.append({
                    "dte": max(dte, 0),
                    "volume": vol,
                    "open_interest": contract.get("OpenInterest") or leg.get("OpenInterest"),
                    "strike": leg.get("StrikePrice"),
                    "option_type": "CALL" if side == "CALL" else "PUT",
                })

        r.close()

        total = call_vol + put_vol
        return {
            "total_volume": total,
            "call_volume": call_vol,
            "put_volume": put_vol,
            "legs": legs,
        }

    except requests.RequestException as e:
        logger.warning("%s: options fetch error: %s", symbol, e)
        return None


def fetch_underlying_quote(symbol: str, token: str) -> float:
    cfg = get_config()
    base = cfg.tradestation.api_base_url if hasattr(cfg.tradestation, "api_base_url") else "https://api.tradestation.com/v3"
    if not base:
        base = "https://api.tradestation.com/v3"
    url = f"{base}/marketdata/quotes/{symbol}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers, timeout=QUOTE_TIMEOUT)
        if r.status_code != 200:
            return 0.0
        data = r.json()
        quotes = data.get("Quotes", [])
        if quotes:
            return float(quotes[0].get("Last", 0) or 0)
        return 0.0
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
    token: str,
) -> Optional[Dict[str, Any]]:
    opts = fetch_options_volume(symbol, today, token)
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

    legs = opts.get("legs", [])
    dte_result = classify_dte(legs)

    price = fetch_underlying_quote(symbol, token)
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
            from prime_data.prime_db import get_connection
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

    token = get_ts_token()
    if not token:
        logger.error("No valid TradeStation token -- cannot scan")
        return {
            "scan_time": scan_time.isoformat(),
            "scanner": "prime_uoa_scanner",
            "version": "1.0",
            "skipped": True,
            "reason": "no_ts_token",
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
            f = pool.submit(scan_symbol, sym, bl, scan_time, group, token)
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

    cfg = get_config()
    ts = cfg.tradestation
    if not ts.access_token and not ts.refresh_token:
        logger.error("No TradeStation credentials in config.json")
        sys.exit(1)

    from prime_data.prime_db import init_db, log_ops_event

    init_db()

    log_ops_event("SCAN_START", "uoa_scanner")

    scan_data = run_uoa_scan()

    if scan_data.get("skipped"):
        print(f"\nUOA Scan skipped: {scan_data.get('reason')}")
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
