"""
PRIME v1.0 MTFA Scanner — Multi-Timeframe Analysis.

Analyzes price trend alignment across three timeframes for each symbol in the
PSA universe:
  - Intraday  (5-min bars, current session)
  - Weekly    (daily bars, last 5 sessions)
  - Annual    (daily bars, last 252 sessions)

Score = aligned timeframes × (100/3), rounded to one decimal.
  3 aligned → 100.0 (STRONG)
  2 aligned →  66.7 (WEAK)
  ≤1 aligned → 33.3 / 0.0 (WATCH)

Also flags proximity to significant highs/lows:
  within 2% of 52-week high/low, within 1% of session high/low.

Uses the same universe and Polygon API as PSA.

Standalone: python prime_scanners/prime_mtfa_scanner.py
"""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config
from prime_data.prime_bar_cache import write_daily_bars, write_intraday_bars

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"
API_TIMEOUT = 10

INTRADAY_BARS = 78   # full RTH session: 6.5 h × 12 bars/h
WEEKLY_BARS = 5
ANNUAL_BARS = 252

SCORE_ALL_ALIGNED = 100.0
SCORE_TWO_ALIGNED = round(200.0 / 3, 1)   # 66.7
SCORE_ONE_ALIGNED = round(100.0 / 3, 1)   # 33.3
SCORE_NONE = 0.0

# Minimum percentage slope per bar to call a trend directional (not FLAT)
TREND_SLOPE_THRESHOLD = 0.0001   # 0.01%

DEFAULT_MIN_PRICE = 5.0
DEFAULT_MIN_DAILY_VOLUME = 500_000

NEAR_52W_PCT = 0.02     # within 2% of 52-week high/low
NEAR_SESSION_PCT = 0.01  # within 1% of session high/low


# ---------------------------------------------------------------------------
# Polygon helpers
# ---------------------------------------------------------------------------

def _polygon_get(endpoint: str, params: Dict, api_key: str) -> Optional[Dict]:
    params["apiKey"] = api_key
    try:
        r = requests.get(
            f"{POLYGON_BASE}{endpoint}", params=params, timeout=API_TIMEOUT
        )
        if r.status_code == 200:
            return r.json()
        logger.debug("Polygon %s -> HTTP %s", endpoint, r.status_code)
    except Exception as e:
        logger.debug("Polygon %s error: %s", endpoint, e)
    return None


def fetch_daily_bars(symbol: str, days: int, api_key: str) -> List[Dict]:
    """Fetch the last `days` daily bars.  Returns most-recent `days` rows.

    Each bar includes a 'bar_date' key (YYYY-MM-DD) for bar_cache writes.
    """
    # Buffer for weekends + holidays: request 1.5× the desired window.
    buffer = int(days * 1.5)
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=buffer)).strftime("%Y-%m-%d")
    endpoint = f"/v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{to_date}"
    data = _polygon_get(endpoint, {
        "adjusted": "true",
        "sort": "asc",
        "limit": buffer,
    }, api_key)
    if not data:
        return []
    raw = data.get("results") or []
    bars = [
        {
            "bar_date": datetime.utcfromtimestamp(b["t"] / 1000).strftime("%Y-%m-%d"),
            "open": b["o"], "high": b["h"], "low": b["l"],
            "close": b["c"], "volume": b["v"],
        }
        for b in raw
        if all(k in b for k in ("t", "o", "h", "l", "c", "v"))
    ]
    return bars[-days:] if len(bars) >= days else bars


def fetch_intraday_bars(symbol: str, api_key: str) -> List[Dict]:
    """Fetch 5-min bars over a 2-session rolling window (yesterday + today).

    Returning up to ~156 bars gives PSA's 39-bar minimum even on early-morning
    runs before today's session has accumulated enough bars.  Each bar includes
    a 'timestamp' key (ms epoch) for bar_cache writes.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    endpoint = f"/v2/aggs/ticker/{symbol}/range/5/minute/{yesterday}/{today}"
    data = _polygon_get(endpoint, {
        "adjusted": "true",
        "sort": "asc",
        "limit": INTRADAY_BARS * 2 + 20,
    }, api_key)
    if not data:
        return []
    raw = data.get("results") or []
    return [
        {
            "timestamp": b["t"],
            "open": b["o"], "high": b["h"], "low": b["l"],
            "close": b["c"], "volume": b["v"],
        }
        for b in raw
        if all(k in b for k in ("t", "o", "h", "l", "c", "v"))
    ]


# ---------------------------------------------------------------------------
# Trend + scoring
# ---------------------------------------------------------------------------

def _trend(closes: List[float]) -> str:
    """Return 'UP', 'DOWN', or 'FLAT' from a list of close prices."""
    n = len(closes)
    if n < 2:
        return "FLAT"
    base = closes[0] if closes[0] != 0 else 1.0
    # Normalised percentage move over the window, per-bar slope
    slope = (closes[-1] / base - 1.0) / (n - 1)
    if slope > TREND_SLOPE_THRESHOLD:
        return "UP"
    if slope < -TREND_SLOPE_THRESHOLD:
        return "DOWN"
    return "FLAT"


def analyze_symbol(
    daily_bars: List[Dict],
    intraday_bars: List[Dict],
) -> Optional[Dict]:
    """Compute MTFA score and high/low flags.  Returns None if data is thin."""
    if not daily_bars or not intraday_bars:
        return None

    current_price = intraday_bars[-1]["close"]

    annual_closes = [b["close"] for b in daily_bars]
    weekly_closes = [b["close"] for b in daily_bars[-WEEKLY_BARS:]]
    intraday_closes = [b["close"] for b in intraday_bars]

    annual_trend = _trend(annual_closes)
    weekly_trend = _trend(weekly_closes)
    intraday_trend = _trend(intraday_closes)

    up_count = sum(1 for t in (intraday_trend, weekly_trend, annual_trend) if t == "UP")
    down_count = sum(1 for t in (intraday_trend, weekly_trend, annual_trend) if t == "DOWN")
    aligned_count = max(up_count, down_count)

    direction = "LONG" if up_count >= down_count else "SHORT"

    if aligned_count == 3:
        score = SCORE_ALL_ALIGNED
        tier = "STRONG"
    elif aligned_count == 2:
        score = SCORE_TWO_ALIGNED
        tier = "WEAK"
    elif aligned_count == 1:
        score = SCORE_ONE_ALIGNED
        tier = "WATCH"
    else:
        score = SCORE_NONE
        tier = "WATCH"

    # 52-week high/low proximity
    hw52 = max(b["high"] for b in daily_bars)
    lw52 = min(b["low"] for b in daily_bars)
    near_52w_high = current_price >= hw52 * (1.0 - NEAR_52W_PCT)
    near_52w_low = current_price <= lw52 * (1.0 + NEAR_52W_PCT)

    # Session high/low proximity
    session_high = max(b["high"] for b in intraday_bars)
    session_low = min(b["low"] for b in intraday_bars)
    near_session_high = current_price >= session_high * (1.0 - NEAR_SESSION_PCT)
    near_session_low = current_price <= session_low * (1.0 + NEAR_SESSION_PCT)

    return {
        "score": score,
        "tier": tier,
        "direction": direction,
        "entry_price": round(current_price, 2),
        "aligned_count": aligned_count,
        "intraday_trend": intraday_trend,
        "weekly_trend": weekly_trend,
        "annual_trend": annual_trend,
        "near_52w_high": near_52w_high,
        "near_52w_low": near_52w_low,
        "near_session_high": near_session_high,
        "near_session_low": near_session_low,
    }


# ---------------------------------------------------------------------------
# Per-symbol worker (called from ThreadPoolExecutor)
# ---------------------------------------------------------------------------

_STAGE0  = "stage0"
_FETCH   = "fetch"
_SIGNAL  = "signal"
_NOSIG   = "nosig"


def _scan_one(
    symbol: str,
    api_key: str,
    min_price: float,
    min_daily_volume: float,
    scan_ts: str,
) -> Tuple[str, Optional[Dict[str, Any]], str]:
    """Fetch + analyse one symbol. Returns (symbol, signal_dict_or_None, outcome).

    outcome values: 'stage0' | 'fetch' | 'nosig' | 'signal'
    """
    daily_bars = fetch_daily_bars(symbol, ANNUAL_BARS, api_key)
    if not daily_bars:
        return symbol, None, _FETCH

    last_close = daily_bars[-1]["close"]
    last_volume = daily_bars[-1]["volume"]
    if last_close < min_price or last_volume < min_daily_volume:
        logger.debug("MTFA stage0 rejected %s: price=%.2f vol=%.0f",
                     symbol, last_close, last_volume)
        return symbol, None, _STAGE0

    intraday_bars = fetch_intraday_bars(symbol, api_key)
    if not intraday_bars:
        return symbol, None, _FETCH

    # Cache writes — fire-and-forget; failure must not abort the scan.
    try:
        write_daily_bars(symbol, daily_bars)
    except Exception as exc:
        logger.warning("MTFA cache write_daily_bars(%s): %s", symbol, exc)
    try:
        write_intraday_bars(symbol, intraday_bars)
    except Exception as exc:
        logger.warning("MTFA cache write_intraday_bars(%s): %s", symbol, exc)

    result = analyze_symbol(daily_bars, intraday_bars)
    if result is None:
        return symbol, None, _NOSIG

    return symbol, {"symbol": symbol, "scan_ts": scan_ts, **result}, _SIGNAL


# ---------------------------------------------------------------------------
# Main scan orchestrator
# ---------------------------------------------------------------------------

def run_mtfa_scan(
    api_key: str,
    universe: Optional[List[str]] = None,
    min_price: float = DEFAULT_MIN_PRICE,
    min_daily_volume: float = DEFAULT_MIN_DAILY_VOLUME,
) -> Dict[str, Any]:
    scan_time = datetime.now()

    if not (api_key or "").strip():
        logger.warning("MTFA: Polygon unavailable — skipping scan")
        return {
            "scan_time": scan_time.isoformat(),
            "scanner": "prime_mtfa_scanner",
            "version": "1.0",
            "polygon_unavailable": True,
            "universe_size": 0,
            "analyzed": 0,
            "signals_found": 0,
            "stage0_rejected": 0,
            "fetch_failures": 0,
            "signals": [],
        }

    if universe is None:
        from prime_scanners.prime_psa_scanner import resolve_psa_universe
        cfg = get_config()
        universe = resolve_psa_universe(
            mode=cfg.ops.psa_universe,
            custom=cfg.ops.psa_universe_custom,
            sector=cfg.ops.psa_universe_sector,
        )

    logger.info(
        "MTFA SCAN -- %s  universe=%d",
        scan_time.strftime("%Y-%m-%d %H:%M ET"),
        len(universe),
    )

    signals: List[Dict[str, Any]] = []
    fetch_failures = 0
    stage0_rejected = 0
    analyzed = 0
    scan_ts = scan_time.isoformat()

    try:
        workers = get_config().ops.mtfa_workers
    except Exception:
        workers = 10

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mtfa") as pool:
        futures = {
            pool.submit(_scan_one, sym, api_key, min_price, min_daily_volume, scan_ts): sym
            for sym in universe
        }
        for fut in as_completed(futures):
            try:
                _sym, sig, outcome = fut.result()
            except Exception as exc:
                logger.warning("MTFA worker error: %s", exc)
                fetch_failures += 1
                continue

            if outcome == _FETCH:
                fetch_failures += 1
            elif outcome == _STAGE0:
                stage0_rejected += 1
            else:
                analyzed += 1
                if sig is not None:
                    signals.append(sig)

    signals.sort(key=lambda s: s["score"], reverse=True)

    logger.info(
        "MTFA complete: analyzed=%d signals=%d stage0_rejected=%d fetch_failures=%d",
        analyzed, len(signals), stage0_rejected, fetch_failures,
    )
    logger.info("MTFA signals found: %d", len(signals))

    return {
        "scan_time": scan_time.isoformat(),
        "scanner": "prime_mtfa_scanner",
        "version": "1.0",
        "universe_size": len(universe),
        "analyzed": analyzed,
        "signals_found": len(signals),
        "stage0_rejected": stage0_rejected,
        "fetch_failures": fetch_failures,
        "signals": signals,
    }


def save_results(scan_data: Dict) -> Path:
    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"mtfa_scan_{ts}_ET.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=2, default=str)
    logger.info("Results saved: %s", out)
    return out


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [MTFA] %(levelname)s %(message)s",
    )

    cfg = get_config()
    api_key = cfg.polygon_api_key
    if not api_key:
        logger.warning("MTFA: Polygon unavailable — skipping scan")
        return

    from prime_data.prime_db import init_db, log_ops_event
    init_db()
    log_ops_event("SCAN_START", "mtfa_scanner")

    universe = None  # resolved inside run_mtfa_scan from cfg.ops.psa_universe
    scan_data = run_mtfa_scan(api_key=api_key, universe=universe)

    print(
        f"\nMTFA Scan: analyzed={scan_data['analyzed']} "
        f"signals={scan_data['signals_found']} "
        f"stage0_rejected={scan_data.get('stage0_rejected', 0)}"
    )

    if scan_data["signals"]:
        print("\nMTFA SIGNALS:")
        for sig in scan_data["signals"]:
            flags = []
            if sig.get("near_52w_high"):
                flags.append("52W_HI")
            if sig.get("near_52w_low"):
                flags.append("52W_LO")
            if sig.get("near_session_high"):
                flags.append("SESS_HI")
            if sig.get("near_session_low"):
                flags.append("SESS_LO")
            flag_str = f" [{','.join(flags)}]" if flags else ""
            print(
                f"  {sig['symbol']:6s} score={sig['score']:5.1f} "
                f"tier={sig['tier']:6s} dir={sig['direction']} "
                f"[I={sig['intraday_trend']} W={sig['weekly_trend']} "
                f"A={sig['annual_trend']}]{flag_str}"
            )

    out = save_results(scan_data)
    print(f"\nResults: {out}")

    log_ops_event("SCAN_COMPLETE", "mtfa_scanner",
                  detail=f"signals={scan_data['signals_found']}")


if __name__ == "__main__":
    main()
