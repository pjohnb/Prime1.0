"""
PRIME v1.0 Metals Trading Strategy Scanner (MTS).
Ported from v0.9 prime_mts_scanner.py (773 lines).

Mean-reversion strategy for precious metals ETFs and mining equities.
Two-phase staged entry: oversold screen (Phase 1) then momentum
confirmation (Phase 2).

Targets: SLV, GLD, GDX, GDXJ, NEM, WPM, AG, PAAS, HL, FR
Context: Gold/Silver ratio for macro positioning.

Standalone: python prime_scanners/prime_mts_scanner.py
"""

import json
import logging
import sys
import time
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

POLYGON_BASE = "https://api.polygon.io"
API_TIMEOUT = 10
API_DELAY = 0.25

MTS_TARGETS = ["SLV", "GLD", "GDX", "GDXJ", "NEM", "WPM", "AG", "PAAS", "HL", "FR"]
GS_RATIO_SYMBOLS = ("GLD", "SLV")

MA_PERIOD = 20
RSI_PERIOD = 14
BARS_NEEDED = 60

OVERSOLD_THRESHOLD_PCT = -5.0
RSI_OVERSOLD = 35
VOL_SURGE_MULT = 1.5

GS_RATIO_HIGH = 80.0
GS_RATIO_NORMAL = 65.0

TIER_TRANCHE_2 = "TRANCHE_2"
TIER_TRANCHE_1 = "TRANCHE_1"
TIER_WATCH = "WATCH"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _polygon_get(endpoint: str, params: Dict, api_key: str) -> Optional[Dict]:
    params["apiKey"] = api_key
    try:
        r = requests.get(
            f"{POLYGON_BASE}{endpoint}", params=params, timeout=API_TIMEOUT
        )
        if r.status_code == 200:
            return r.json()
        logger.warning("Polygon %s -> HTTP %s", endpoint, r.status_code)
        return None
    except Exception as e:
        logger.warning("Polygon %s failed: %s", endpoint, e)
        return None


def fetch_daily_bars(symbol: str, lookback_days: int, api_key: str) -> List[Dict]:
    today = datetime.now().date()
    from_date = today - timedelta(days=lookback_days + 10)

    data = _polygon_get(
        f"/v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{today}",
        {"adjusted": "true", "sort": "asc", "limit": lookback_days + 10},
        api_key,
    )
    if not data or not data.get("results"):
        return []

    bars = []
    for r in data["results"]:
        bars.append({
            "date": datetime.utcfromtimestamp(r["t"] / 1000).strftime("%Y-%m-%d"),
            "open": r.get("o", 0),
            "high": r.get("h", 0),
            "low": r.get("l", 0),
            "close": r.get("c", 0),
            "volume": r.get("v", 0),
        })
    return bars


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------

def calc_sma(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_rsi(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = deltas[-(period + 20):]  # extra warmup

    gains = [d if d > 0 else 0 for d in recent[:period]]
    losses = [-d if d < 0 else 0 for d in recent[:period]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for d in recent[period:]:
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def calc_avg_volume(volumes: List[float], period: int) -> Optional[float]:
    if len(volumes) < period:
        return None
    return sum(volumes[-period:]) / period


# ---------------------------------------------------------------------------
# Signal evaluation
# ---------------------------------------------------------------------------

def evaluate_signal(
    symbol: str,
    bars: List[Dict],
    gs_ratio: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    if len(bars) < BARS_NEEDED:
        logger.debug("%s: insufficient bars (%d < %d)", symbol, len(bars), BARS_NEEDED)
        return None

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    price = closes[-1]
    sma20 = calc_sma(closes, MA_PERIOD)
    rsi = calc_rsi(closes, RSI_PERIOD)
    avg_vol = calc_avg_volume(volumes, MA_PERIOD)

    if sma20 is None or rsi is None or avg_vol is None or price <= 0:
        return None

    pct_from_sma = ((price - sma20) / sma20) * 100.0
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 0.0

    week52_high = max(highs[-252:]) if len(highs) >= 252 else max(highs)
    week52_low = min(lows[-252:]) if len(lows) >= 252 else min(lows)
    pct_from_52h = ((price - week52_high) / week52_high) * 100.0 if week52_high > 0 else 0.0

    # Phase 1: oversold screen
    phase1_oversold = pct_from_sma <= OVERSOLD_THRESHOLD_PCT
    phase1_rsi = rsi <= RSI_OVERSOLD
    phase1_volume = vol_ratio >= VOL_SURGE_MULT
    phase1_met = phase1_oversold and phase1_rsi and phase1_volume

    # Phase 2: momentum confirmation
    price_bounce = len(closes) >= 2 and closes[-1] > closes[-2]
    rsi_prev = calc_rsi(closes[:-1], RSI_PERIOD) if len(closes) > RSI_PERIOD + 2 else None
    rsi_rising = rsi_prev is not None and rsi > rsi_prev
    phase2_met = phase1_met and price_bounce and rsi_rising

    if phase2_met:
        tier = TIER_TRANCHE_2
        confidence = "HIGH"
    elif phase1_met:
        tier = TIER_TRANCHE_1
        confidence = "MEDIUM"
    elif phase1_oversold:
        tier = TIER_WATCH
        confidence = "LOW"
    else:
        return None

    gs_context = ""
    if gs_ratio is not None:
        if gs_ratio >= GS_RATIO_HIGH:
            gs_context = f"BULLISH_SILVER (ratio={gs_ratio:.1f} >= {GS_RATIO_HIGH})"
        elif gs_ratio >= GS_RATIO_NORMAL:
            gs_context = f"NORMAL (ratio={gs_ratio:.1f})"
        else:
            gs_context = f"SILVER_RICH (ratio={gs_ratio:.1f} < {GS_RATIO_NORMAL})"

    return {
        "symbol": symbol,
        "price_at_scan": round(price, 2),
        "direction": "LONG",
        "score": round(rsi, 1),
        "tier": tier,
        "confidence": confidence,
        "pct_from_sma": round(pct_from_sma, 2),
        "sma20": round(sma20, 2),
        "rsi": round(rsi, 1),
        "vol_surge": round(vol_ratio, 2),
        "phase1_met": phase1_met,
        "phase2_met": phase2_met,
        "week52_high": round(week52_high, 2),
        "week52_low": round(week52_low, 2),
        "pct_from_52h": round(pct_from_52h, 2),
        "gs_ratio": round(gs_ratio, 2) if gs_ratio else None,
        "gs_context": gs_context,
    }


# ---------------------------------------------------------------------------
# Gold/Silver ratio
# ---------------------------------------------------------------------------

def fetch_gs_ratio(api_key: str) -> Optional[float]:
    gld_bars = fetch_daily_bars("GLD", 5, api_key)
    slv_bars = fetch_daily_bars("SLV", 5, api_key)
    time.sleep(API_DELAY)

    if not gld_bars or not slv_bars:
        return None

    gld_price = gld_bars[-1]["close"]
    slv_price = slv_bars[-1]["close"]

    if slv_price <= 0:
        return None

    return gld_price / slv_price


# ---------------------------------------------------------------------------
# Main scan orchestrator
# ---------------------------------------------------------------------------

def run_mts_scan(api_key: str) -> Dict[str, Any]:
    scan_time = datetime.now()

    logger.info("MTS SCAN -- %s", scan_time.strftime("%Y-%m-%d %H:%M ET"))

    gs_ratio = fetch_gs_ratio(api_key)
    if gs_ratio:
        logger.info("Gold/Silver ratio: %.1f", gs_ratio)

    signals: List[Dict[str, Any]] = []
    all_results: Dict[str, Any] = {}

    for symbol in MTS_TARGETS:
        bars = fetch_daily_bars(symbol, BARS_NEEDED, api_key)
        time.sleep(API_DELAY)

        if not bars:
            all_results[symbol] = {"status": "NO_DATA"}
            continue

        signal = evaluate_signal(symbol, bars, gs_ratio)
        if signal:
            signals.append(signal)
            all_results[symbol] = {"status": signal["tier"], "signal": signal}
        else:
            all_results[symbol] = {"status": "NO_SIGNAL"}

    signals.sort(key=lambda s: (
        s["tier"] == TIER_TRANCHE_2,
        s["tier"] == TIER_TRANCHE_1,
        -s["rsi"],
    ), reverse=True)

    t2 = [s for s in signals if s["tier"] == TIER_TRANCHE_2]
    t1 = [s for s in signals if s["tier"] == TIER_TRANCHE_1]
    watch = [s for s in signals if s["tier"] == TIER_WATCH]

    logger.info(
        "MTS complete: %d signals (T2=%d T1=%d Watch=%d)",
        len(signals), len(t2), len(t1), len(watch),
    )

    return {
        "scan_time": scan_time.isoformat(),
        "scanner": "prime_mts_scanner",
        "version": "1.0",
        "targets": MTS_TARGETS,
        "gs_ratio": round(gs_ratio, 2) if gs_ratio else None,
        "thresholds": {
            "oversold_pct": OVERSOLD_THRESHOLD_PCT,
            "rsi_oversold": RSI_OVERSOLD,
            "vol_surge_mult": VOL_SURGE_MULT,
        },
        "signals_found": len(signals),
        "tranche2_count": len(t2),
        "tranche1_count": len(t1),
        "watch_count": len(watch),
        "signals": signals,
        "results": all_results,
    }


def save_results(scan_data: Dict) -> Path:
    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"mts_scan_{ts}_ET.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=2, default=str)
    logger.info("Results saved: %s", out)
    return out


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [MTS] %(levelname)s %(message)s",
    )

    cfg = get_config()
    api_key = cfg.polygon_api_key
    if not api_key:
        logger.error("polygon_api_key not found in config.json")
        sys.exit(1)

    from prime_data.prime_db import init_db, log_ops_event

    init_db()

    log_ops_event("SCAN_START", "mts_scanner")

    scan_data = run_mts_scan(api_key)

    print(f"\nMTS Scan: {scan_data['signals_found']} signals "
          f"(T2={scan_data['tranche2_count']} T1={scan_data['tranche1_count']} "
          f"Watch={scan_data['watch_count']})")
    if scan_data.get("gs_ratio"):
        print(f"  Gold/Silver ratio: {scan_data['gs_ratio']:.1f}")
    for s in scan_data["signals"]:
        print(
            f"  {s['symbol']:<5} {s['tier']:<10} RSI={s['rsi']:5.1f}  "
            f"SMA%={s['pct_from_sma']:+5.1f}%  Vol={s['vol_surge']:.1f}x"
        )

    save_results(scan_data)

    log_ops_event(
        "SCAN_COMPLETE",
        "mts_scanner",
        detail=f"signals={scan_data['signals_found']}",
    )


if __name__ == "__main__":
    main()
