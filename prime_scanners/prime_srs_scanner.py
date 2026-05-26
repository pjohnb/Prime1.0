"""
PRIME v1.0 Sector Recovery Scanner (SRS)
Ported from v0.9 prime_srs_scanner.py (501 lines).

Detects macro mean-reversion cycles across GICS sectors by monitoring
sector ETFs for drawdown, stabilization, and recovery phases.

Each sector produces one of four signals per scan:
  DECLINING    -- sector is actively falling (avoid / short candidates)
  BOTTOMING    -- selling is drying up, range narrowing (watch list)
  RECOVERING   -- buying volume confirming, upside beginning (LONG candidates)
  STABLE       -- no significant drawdown or recovery pattern detected

Standalone: python prime_scanners/prime_srs_scanner.py
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"
API_TIMEOUT = 10
API_DELAY = 1.0

# GICS sector -> ETF ticker
SECTOR_ETFS = {
    "Technology":             "XLK",
    "Health Care":            "XLV",
    "Financials":             "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":       "XLP",
    "Energy":                 "XLE",
    "Industrials":            "XLI",
    "Materials":              "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
    "Communication Services": "XLC",
    "Broad Market":           "SPY",
}

# Phase detection thresholds
DRAWDOWN_DECLINING = -3.0
DRAWDOWN_BOTTOMING = -1.5
RECOVERY_GAIN = 1.5
STABILIZATION_RATIO = 0.65
RECOVERY_VOLUME_RATIO = 1.20

BARS_5D = 5
BARS_10D = 10
BARS_2D = 2

PHASE_ACTION = {
    "DECLINING":  "AVOID -- sector in active decline; suppress PSA signals in this sector",
    "BOTTOMING":  "WATCH -- selling exhausting; prepare entry, confirm before trading",
    "RECOVERING": "LONG CANDIDATES -- recovery confirmed; run PSA for top stock in sector",
    "STABLE":     "NEUTRAL -- no significant pattern; standard PSA rules apply",
    "UNKNOWN":    "UNKNOWN -- insufficient data",
}


def _polygon_get(endpoint: str, params: Dict, api_key: str) -> Optional[Dict]:
    params["apiKey"] = api_key
    try:
        r = requests.get(
            f"{POLYGON_BASE}{endpoint}",
            params=params,
            timeout=API_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json()
        logger.warning("Polygon %s -> HTTP %s: %s", endpoint, r.status_code, r.text[:200])
        return None
    except Exception as e:
        logger.warning("Polygon fetch error %s: %s", endpoint, e)
        return None


def fetch_daily_bars(symbol: str, lookback_days: int, api_key: str) -> List[Dict]:
    today = datetime.now().date()
    from_date = today - timedelta(days=lookback_days + 7)

    data = _polygon_get(
        f"/v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{today}",
        {"adjusted": "true", "sort": "asc", "limit": 30},
        api_key,
    )

    if not data or not data.get("results"):
        return []

    bars = []
    for r in data["results"]:
        bar_date = datetime.utcfromtimestamp(r["t"] / 1000).strftime("%Y-%m-%d")
        bars.append({
            "date": bar_date,
            "open": r.get("o", 0),
            "high": r.get("h", 0),
            "low": r.get("l", 0),
            "close": r.get("c", 0),
            "volume": r.get("v", 0),
        })
    return bars[-lookback_days:] if len(bars) >= lookback_days else bars


def detect_phase(bars: List[Dict]) -> Tuple[str, Dict]:
    """Analyze daily bars and return (phase_label, metrics_dict)."""
    if len(bars) < BARS_5D:
        return "UNKNOWN", {"reason": "Insufficient data"}

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]

    close_now = closes[-1]
    close_5d = closes[-BARS_5D] if len(closes) >= BARS_5D else closes[0]
    chg_5d_pct = ((close_now - close_5d) / close_5d * 100) if close_5d > 0 else 0.0

    close_2d = closes[-BARS_2D] if len(closes) >= BARS_2D else closes[0]
    chg_2d_pct = ((close_now - close_2d) / close_2d * 100) if close_2d > 0 else 0.0

    lookback_highs = highs[-BARS_10D:] if len(highs) >= BARS_10D else highs
    recent_high = max(lookback_highs)
    drawdown_pct = ((close_now - recent_high) / recent_high * 100) if recent_high > 0 else 0.0

    if len(bars) >= 7:
        recent_range = max(highs[-2:]) - min(lows[-2:])
        prior_range = max(highs[-7:-2]) - min(lows[-7:-2])
        range_ratio = (recent_range / prior_range) if prior_range > 0 else 1.0
    else:
        range_ratio = 1.0

    up_vol = down_vol = 0
    up_days = down_days = 0
    for i in range(1, min(BARS_5D + 1, len(bars))):
        if closes[-i] > closes[-i - 1]:
            up_vol += volumes[-i]
            up_days += 1
        else:
            down_vol += volumes[-i]
            down_days += 1

    avg_up_vol = up_vol / up_days if up_days > 0 else 0
    avg_down_vol = down_vol / down_days if down_days > 0 else 0
    vol_ratio = avg_up_vol / avg_down_vol if avg_down_vol > 0 else 1.0

    if chg_5d_pct <= DRAWDOWN_DECLINING:
        if chg_2d_pct >= RECOVERY_GAIN and vol_ratio >= RECOVERY_VOLUME_RATIO:
            phase = "RECOVERING"
        elif range_ratio <= STABILIZATION_RATIO:
            phase = "BOTTOMING"
        else:
            phase = "DECLINING"
    elif chg_5d_pct <= DRAWDOWN_BOTTOMING:
        if chg_2d_pct >= RECOVERY_GAIN:
            phase = "RECOVERING"
        elif range_ratio <= STABILIZATION_RATIO:
            phase = "BOTTOMING"
        else:
            phase = "DECLINING"
    else:
        if chg_2d_pct >= RECOVERY_GAIN and drawdown_pct <= -2.0:
            phase = "RECOVERING"
        else:
            phase = "STABLE"

    metrics = {
        "close": round(close_now, 2),
        "chg_5d_pct": round(chg_5d_pct, 2),
        "chg_2d_pct": round(chg_2d_pct, 2),
        "drawdown_pct": round(drawdown_pct, 2),
        "recent_high": round(recent_high, 2),
        "range_ratio": round(range_ratio, 3),
        "vol_ratio_up_dn": round(vol_ratio, 2),
        "bars_available": len(bars),
    }

    return phase, metrics


def run_srs_scan(api_key: str) -> Dict:
    """Scan all sector ETFs, detect phases, return full results dict."""
    scan_time = datetime.now()
    results = {}
    summary = {"DECLINING": [], "BOTTOMING": [], "RECOVERING": [], "STABLE": [], "UNKNOWN": []}

    logger.info("SRS SCAN -- %s", scan_time.strftime("%Y-%m-%d %H:%M ET"))

    for sector, etf in SECTOR_ETFS.items():
        bars = fetch_daily_bars(etf, lookback_days=BARS_10D, api_key=api_key)
        time.sleep(API_DELAY)

        if not bars:
            phase = "UNKNOWN"
            metrics = {"reason": "No data from Polygon"}
        else:
            phase, metrics = detect_phase(bars)

        action = PHASE_ACTION.get(phase, "")
        results[sector] = {
            "etf": etf,
            "phase": phase,
            "action": action,
            "metrics": metrics,
        }
        summary[phase].append(etf)

    n_declining = len(summary["DECLINING"])
    n_recovering = len(summary["RECOVERING"])
    n_bottoming = len(summary["BOTTOMING"])
    n_total = len(SECTOR_ETFS)

    if n_declining >= 5:
        regime = "BROAD_DECLINE"
        regime_note = f"{n_declining}/{n_total} sectors declining -- macro headwind"
    elif n_recovering >= 4:
        regime = "BROAD_RECOVERY"
        regime_note = f"{n_recovering}/{n_total} sectors recovering -- favorable for SRS"
    elif n_bottoming >= 3:
        regime = "STABILIZING"
        regime_note = f"{n_bottoming}/{n_total} sectors bottoming -- watch for confirmation"
    else:
        regime = "MIXED"
        regime_note = "No dominant sector trend -- standard rules apply"

    return {
        "scan_time": scan_time.isoformat(),
        "scanner": "prime_srs_scanner",
        "version": "1.0",
        "regime": regime,
        "regime_note": regime_note,
        "summary": {
            "declining": summary["DECLINING"],
            "bottoming": summary["BOTTOMING"],
            "recovering": summary["RECOVERING"],
            "stable": summary["STABLE"],
        },
        "sectors": results,
    }


def save_results(scan_data: Dict) -> Path:
    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"srs_scan_{ts}_ET.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=2)
    logger.info("Results saved: %s", out)
    return out


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [SRS] %(levelname)s %(message)s",
    )

    cfg = get_config()
    api_key = cfg.polygon_api_key
    if not api_key:
        logger.error("polygon_api_key not found in config.json")
        sys.exit(1)

    scan_data = run_srs_scan(api_key)

    print(f"\nSRS Scan: regime={scan_data['regime']}")
    for sector, data in scan_data["sectors"].items():
        m = data["metrics"]
        print(f"  {data['etf']:<5} ({sector:<24}) | {data['phase']:<12} | "
              f"5d {m.get('chg_5d_pct', 0):+5.1f}%")

    save_results(scan_data)


if __name__ == "__main__":
    main()
