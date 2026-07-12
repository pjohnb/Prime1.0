"""
PRIME v1.0 Metals Mean-Reversion Scanner (MMR).
Ported from v0.9 prime_mts_scanner.py (renamed to MMR in WO-PRIME-MMR-RENAME-01).
SHORT expansion added in WO-PRIME-MMR-SHORT-01.

Mean-reversion strategy for precious metals ETFs and mining equities.
Two-phase staged entry: oversold screen (Phase 1) then momentum
confirmation (Phase 2). SHORT direction restricted to ETFs only.

LONG universe: SLV, GLD, GDX, GDXJ, NEM, WPM, AG, PAAS, HL, FR
SHORT universe: SLV, GLD, GDX, GDXJ (ETF-only; individual miners LONG only)

LONG tiers: TRANCHE_1, TRANCHE_2, WATCH
SHORT tiers: SHORT_TRANCHE_1, SHORT_TRANCHE_2

Data source: Schwab daily bars (primary), Polygon (fallback).
TradeStation is retired (Sprint 25). Scheduled at 12:45 ET alongside IDX.

Standalone: python prime_scanners/prime_mmr_scanner.py
"""

import csv
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

MMR_TARGETS = ["SLV", "GLD", "GDX", "GDXJ", "NEM", "WPM", "AG", "PAAS", "HL", "FR"]
MMR_SHORT_TARGETS = ["SLV", "GLD", "GDX", "GDXJ"]  # ETF-only; individual miners LONG only
GS_RATIO_SYMBOLS = ("GLD", "SLV")

MA_PERIOD = 20
RSI_PERIOD = 14
BARS_NEEDED = 60

OVERSOLD_THRESHOLD_PCT = -5.0
RSI_OVERSOLD = 35
OVERBOUGHT_THRESHOLD_PCT = 5.0
RSI_OVERBOUGHT = 65
VOL_SURGE_MULT = 1.5

GS_RATIO_HIGH = 80.0
GS_RATIO_NORMAL = 65.0

TIER_TRANCHE_2 = "TRANCHE_2"
TIER_TRANCHE_1 = "TRANCHE_1"
TIER_WATCH = "WATCH"
TIER_SHORT_TRANCHE_2 = "SHORT_TRANCHE_2"
TIER_SHORT_TRANCHE_1 = "SHORT_TRANCHE_1"

CSV_FIELDNAMES = [
    "symbol", "price", "pct_from_sma", "rsi", "vol_surge_mult",
    "tranche", "confidence", "direction", "scan_ts",
]


# ---------------------------------------------------------------------------
# Schwab daily bars (primary)
# ---------------------------------------------------------------------------

def _get_schwab_client():
    """Return a connected schwab-py Client, or None if unavailable."""
    try:
        import schwab
        cfg = get_config()
        ss = cfg.schwab_snapshot
        if not ss.schwab_token_path or not ss.schwab_app_key:
            return None
        return schwab.auth.client_from_token_file(
            token_path=ss.schwab_token_path,
            api_key=ss.schwab_app_key,
            app_secret=ss.schwab_app_secret,
        )
    except Exception as e:
        logger.debug("Schwab unavailable for MMR: %s", e)
        return None


def _fetch_daily_bars_schwab(symbol: str, lookback_days: int, client) -> List[Dict]:
    """Fetch daily OHLCV bars via Schwab price history API."""
    try:
        today = datetime.now()
        start = today - timedelta(days=lookback_days + 15)
        resp = client.get_price_history_every_day(
            symbol,
            start_datetime=start,
            end_datetime=today,
        )
        if resp.status_code != 200:
            logger.debug("%s: Schwab price history HTTP %s", symbol, resp.status_code)
            return []
        data = resp.json()
        bars = []
        for c in data.get("candles", []):
            ts_ms = c.get("datetime", 0)
            dt_str = datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
            bars.append({
                "date": dt_str,
                "open": c.get("open", 0),
                "high": c.get("high", 0),
                "low": c.get("low", 0),
                "close": c.get("close", 0),
                "volume": c.get("volume", 0),
            })
        return bars
    except Exception as e:
        logger.debug("%s: Schwab bars error: %s", symbol, e)
        return []


# ---------------------------------------------------------------------------
# Polygon helpers (fallback)
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


def _fetch_daily_bars_polygon(symbol: str, lookback_days: int, api_key: str) -> List[Dict]:
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


def fetch_daily_bars(symbol: str, lookback_days: int, api_key: str,
                     schwab_client=None) -> List[Dict]:
    """Fetch daily bars: Schwab primary, Polygon fallback."""
    if schwab_client is not None:
        bars = _fetch_daily_bars_schwab(symbol, lookback_days, schwab_client)
        if bars:
            return bars
        logger.debug("%s: Schwab bars empty, falling back to Polygon", symbol)
    if api_key:
        return _fetch_daily_bars_polygon(symbol, lookback_days, api_key)
    return []


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


def evaluate_signal_short(
    symbol: str,
    bars: List[Dict],
    gs_ratio: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Overbought reversal screen for ETF-only SHORT candidates.

    Phase 1: price extended above SMA20 AND RSI overbought AND volume surge.
    Phase 2: price declining (close < prev close) AND RSI falling.
    Tier: SHORT_TRANCHE_2 (phase2 confirmed) or SHORT_TRANCHE_1 (phase1 only).
    Only ETFs in MMR_SHORT_TARGETS should be passed; caller enforces this.
    """
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

    # Phase 1: overbought screen
    phase1_overbought = pct_from_sma >= OVERBOUGHT_THRESHOLD_PCT
    phase1_rsi = rsi >= RSI_OVERBOUGHT
    phase1_volume = vol_ratio >= VOL_SURGE_MULT
    phase1_met = phase1_overbought and phase1_rsi and phase1_volume

    if not phase1_met:
        return None

    # Phase 2: decline confirmation
    price_declining = len(closes) >= 2 and closes[-1] < closes[-2]
    rsi_prev = calc_rsi(closes[:-1], RSI_PERIOD) if len(closes) > RSI_PERIOD + 2 else None
    rsi_falling = rsi_prev is not None and rsi < rsi_prev
    phase2_met = price_declining and rsi_falling

    tier = TIER_SHORT_TRANCHE_2 if phase2_met else TIER_SHORT_TRANCHE_1
    confidence = "HIGH" if phase2_met else "MEDIUM"

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
        "direction": "SHORT",
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

def fetch_gs_ratio(api_key: str, schwab_client=None) -> Optional[float]:
    gld_bars = fetch_daily_bars("GLD", 5, api_key, schwab_client)
    slv_bars = fetch_daily_bars("SLV", 5, api_key, schwab_client)
    if schwab_client is None:
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

def run_mmr_scan(api_key: str) -> Dict[str, Any]:
    scan_time = datetime.now()

    logger.info("MMR SCAN -- %s", scan_time.strftime("%Y-%m-%d %H:%M ET"))

    schwab_client = _get_schwab_client()
    if schwab_client:
        logger.info("MMR: using Schwab daily bars (primary)")
    elif api_key:
        logger.info("MMR: Schwab unavailable, using Polygon fallback")
    else:
        logger.warning("MMR: no data source available -- 0 signals")

    gs_ratio = fetch_gs_ratio(api_key, schwab_client)
    if gs_ratio:
        logger.info("Gold/Silver ratio: %.1f", gs_ratio)

    signals: List[Dict[str, Any]] = []
    short_signals: List[Dict[str, Any]] = []
    all_results: Dict[str, Any] = {}
    bars_cache: Dict[str, List[Dict]] = {}

    # LONG pass — all 10 targets
    for symbol in MMR_TARGETS:
        bars = fetch_daily_bars(symbol, BARS_NEEDED, api_key, schwab_client)
        if schwab_client is None:
            time.sleep(API_DELAY)
        bars_cache[symbol] = bars

        if not bars:
            all_results[symbol] = {"status": "NO_DATA"}
            continue

        signal = evaluate_signal(symbol, bars, gs_ratio)
        if signal:
            signals.append(signal)
            all_results[symbol] = {"status": signal["tier"], "signal": signal}
        else:
            all_results[symbol] = {"status": "NO_SIGNAL"}

    # SHORT pass — ETF-only, reuse cached bars
    for symbol in MMR_SHORT_TARGETS:
        bars = bars_cache.get(symbol)
        if not bars:
            continue

        short_signal = evaluate_signal_short(symbol, bars, gs_ratio)
        if short_signal:
            short_signals.append(short_signal)
            existing = all_results.get(symbol, {})
            existing["short_signal"] = short_signal
            all_results[symbol] = existing

    signals.sort(key=lambda s: (
        s["tier"] == TIER_TRANCHE_2,
        s["tier"] == TIER_TRANCHE_1,
        -s["rsi"],
    ), reverse=True)

    short_signals.sort(key=lambda s: (
        s["tier"] == TIER_SHORT_TRANCHE_2,
        -s["rsi"],
    ), reverse=True)

    t2 = [s for s in signals if s["tier"] == TIER_TRANCHE_2]
    t1 = [s for s in signals if s["tier"] == TIER_TRANCHE_1]
    watch = [s for s in signals if s["tier"] == TIER_WATCH]
    st2 = [s for s in short_signals if s["tier"] == TIER_SHORT_TRANCHE_2]
    st1 = [s for s in short_signals if s["tier"] == TIER_SHORT_TRANCHE_1]

    total_signals = len(signals) + len(short_signals)

    logger.info(
        "MMR complete: %d signals (T2=%d T1=%d Watch=%d | ShortT2=%d ShortT1=%d)",
        total_signals, len(t2), len(t1), len(watch), len(st2), len(st1),
    )

    return {
        "scan_time": scan_time.isoformat(),
        "scanner": "prime_mmr_scanner",
        "version": "1.0",
        "targets": MMR_TARGETS,
        "short_targets": MMR_SHORT_TARGETS,
        "gs_ratio": round(gs_ratio, 2) if gs_ratio else None,
        "thresholds": {
            "oversold_pct": OVERSOLD_THRESHOLD_PCT,
            "rsi_oversold": RSI_OVERSOLD,
            "overbought_pct": OVERBOUGHT_THRESHOLD_PCT,
            "rsi_overbought": RSI_OVERBOUGHT,
            "vol_surge_mult": VOL_SURGE_MULT,
        },
        "signals_found": total_signals,
        "tranche2_count": len(t2),
        "tranche1_count": len(t1),
        "watch_count": len(watch),
        "short_tranche2_count": len(st2),
        "short_tranche1_count": len(st1),
        "signals": signals,
        "short_signals": short_signals,
        "results": all_results,
    }


def save_results(scan_data: Dict) -> Path:
    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"mmr_scan_{ts}_ET.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=2, default=str)
    logger.info("Results saved: %s", out)
    return out


def save_signals_csv(scan_data: Dict) -> Optional[Path]:
    """Write approved signals (all TRANCHE tiers, both LONG and SHORT) to CSV.

    The bridge reads mmr_signals_*.csv to ingest approved signals into
    prime_signals. File is only written when at least one signal exists.
    """
    all_signals = scan_data.get("signals", []) + scan_data.get("short_signals", [])
    approved = [
        s for s in all_signals
        if s.get("tier") in (
            TIER_TRANCHE_1, TIER_TRANCHE_2,
            TIER_SHORT_TRANCHE_1, TIER_SHORT_TRANCHE_2,
        )
    ]
    if not approved:
        return None

    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"mmr_signals_{ts}.csv"

    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for s in approved:
            writer.writerow({
                "symbol": s["symbol"],
                "price": s["price_at_scan"],
                "pct_from_sma": s["pct_from_sma"],
                "rsi": s["rsi"],
                "vol_surge_mult": s["vol_surge"],
                "tranche": s["tier"],
                "confidence": s["confidence"],
                "direction": s["direction"],
                "scan_ts": scan_data.get("scan_time", ""),
            })

    logger.info("Signals CSV saved: %s (%d rows)", out, len(approved))
    return out


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [MMR] %(levelname)s %(message)s",
    )

    cfg = get_config()
    api_key = cfg.polygon_api_key
    if not api_key:
        logger.error("polygon_api_key not found in config.json")
        sys.exit(1)

    from prime_data.prime_db import init_db, log_ops_event

    init_db()

    log_ops_event("SCAN_START", "mmr_scanner")

    scan_data = run_mmr_scan(api_key)

    print(f"\nMMR Scan: {scan_data['signals_found']} signals "
          f"(T2={scan_data['tranche2_count']} T1={scan_data['tranche1_count']} "
          f"Watch={scan_data['watch_count']} | "
          f"ShortT2={scan_data['short_tranche2_count']} "
          f"ShortT1={scan_data['short_tranche1_count']})")
    if scan_data.get("gs_ratio"):
        print(f"  Gold/Silver ratio: {scan_data['gs_ratio']:.1f}")
    for s in scan_data["signals"]:
        print(
            f"  {s['symbol']:<5} {s['tier']:<14} RSI={s['rsi']:5.1f}  "
            f"SMA%={s['pct_from_sma']:+5.1f}%  Vol={s['vol_surge']:.1f}x  LONG"
        )
    for s in scan_data["short_signals"]:
        print(
            f"  {s['symbol']:<5} {s['tier']:<14} RSI={s['rsi']:5.1f}  "
            f"SMA%={s['pct_from_sma']:+5.1f}%  Vol={s['vol_surge']:.1f}x  SHORT"
        )

    save_results(scan_data)
    save_signals_csv(scan_data)

    log_ops_event(
        "SCAN_COMPLETE",
        "mmr_scanner",
        detail=f"signals={scan_data['signals_found']}",
    )


if __name__ == "__main__":
    main()
