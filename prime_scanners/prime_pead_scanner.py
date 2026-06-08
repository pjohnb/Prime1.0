"""
PRIME v1.0 Post-Earnings Announcement Drift Scanner (PEAD).
Ported from v0.9 prime_earnings_scanner.py (1,043 lines).

Detects stocks exhibiting post-earnings drift: a well-documented anomaly
where stocks continue to drift in the direction of an earnings surprise
for 60-90 days after the announcement.

Four-factor scoring model (0-100):
  EPS Surprise (40%)       -- magnitude of earnings beat/miss
  Price Momentum (25%)     -- confirming price reaction direction
  Analyst Coverage (20%)   -- fewer analysts = stronger drift
  Revenue Surprise (15%)   -- revenue confirmation amplifies signal

Standalone: python prime_scanners/prime_pead_scanner.py
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLYGON_BASE = "https://api.polygon.io"
FINNHUB_BASE = "https://finnhub.io/api/v1"

API_TIMEOUT = 10
FINNHUB_RATE_DELAY = 1.1  # 60 calls/min free tier
POLYGON_RATE_DELAY = 0.25

WEIGHT_EPS_SURPRISE = 40
WEIGHT_PRICE_MOMENTUM = 25
WEIGHT_ANALYST_COVERAGE = 20
WEIGHT_REVENUE_SURPRISE = 15

MIN_SIGNAL_SCORE = 50
MIN_SURPRISE_PCT = 1.0
EPS_SURPRISE_CAP_PCT = 200.0
MAX_LOOKBACK_DAYS = 5
DRIFT_WINDOW_DAYS = 20

# Default universe when no watchlist configured
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM",
    "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC", "XOM",
    "NFLX", "COST", "CRM", "AMD", "INTC", "CSCO", "PEP", "AVGO",
    "ADBE", "CMCSA", "TXN", "QCOM", "INTU", "AMAT", "AMGN", "ISRG",
    "LRCX", "MU", "ADI", "MRVL", "KLAC", "CDNS", "SNPS", "FTNT",
    "PANW", "CRWD", "ZS", "DDOG", "NET", "MDB", "SNOW",
]


# ---------------------------------------------------------------------------
# Finnhub cache -- avoids redundant API calls within a scan session
# ---------------------------------------------------------------------------

class _FinnhubCache:
    def __init__(self, cache_dir: Path):
        self._path = cache_dir / "finnhub_cache.json"
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists():
                with open(self._path, "r") as f:
                    self._data = json.load(f)
        except Exception:
            self._data = {}

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f)
        except Exception:
            pass

    def get(self, key: str, ttl_seconds: int) -> Optional[Any]:
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.time() - entry.get("ts", 0) > ttl_seconds:
            return None
        return entry.get("value")

    def set(self, key: str, value: Any):
        self._data[key] = {"ts": time.time(), "value": value}
        self._save()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _finnhub_get(
    endpoint: str,
    params: Dict,
    api_key: str,
    cache: Optional[_FinnhubCache] = None,
    cache_key: Optional[str] = None,
    cache_ttl: int = 3600,
    last_call: list = [0.0],
) -> Optional[Any]:
    if cache and cache_key:
        cached = cache.get(cache_key, cache_ttl)
        if cached is not None:
            return cached

    elapsed = time.time() - last_call[0]
    if elapsed < FINNHUB_RATE_DELAY:
        time.sleep(FINNHUB_RATE_DELAY - elapsed)
    last_call[0] = time.time()

    params["token"] = api_key
    try:
        r = requests.get(
            f"{FINNHUB_BASE}/{endpoint}", params=params, timeout=API_TIMEOUT
        )
        if r.status_code == 200:
            data = r.json()
            if cache and cache_key and data:
                cache.set(cache_key, data)
            return data
        if r.status_code == 429:
            logger.warning("Finnhub rate limited -- waiting 60s")
            time.sleep(60)
            return _finnhub_get(endpoint, params, api_key, cache, cache_key, cache_ttl)
        logger.warning("Finnhub %s -> HTTP %s", endpoint, r.status_code)
        return None
    except requests.RequestException as e:
        logger.error("Finnhub %s failed: %s", endpoint, e)
        return None


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


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_earnings_calendar(
    from_date: str, to_date: str, api_key: str, cache: _FinnhubCache
) -> List[Dict]:
    cache_key = f"earnings_calendar:{from_date}:{to_date}"
    data = _finnhub_get(
        "calendar/earnings",
        {"from": from_date, "to": to_date},
        api_key,
        cache=cache,
        cache_key=cache_key,
        cache_ttl=3600,
    )
    if data and "earningsCalendar" in data:
        return data["earningsCalendar"]
    return data if isinstance(data, list) else []


def fetch_earnings_history(
    symbol: str, api_key: str, cache: _FinnhubCache, limit: int = 8
) -> List[Dict]:
    cache_key = f"earnings_history:{symbol}:{limit}"
    data = _finnhub_get(
        "stock/earnings",
        {"symbol": symbol, "limit": limit},
        api_key,
        cache=cache,
        cache_key=cache_key,
        cache_ttl=3600,
    )
    return data if isinstance(data, list) else []


def fetch_analyst_count(
    symbol: str, api_key: str, cache: _FinnhubCache
) -> int:
    cache_key = f"recommendation:{symbol}"
    data = _finnhub_get(
        "stock/recommendation",
        {"symbol": symbol},
        api_key,
        cache=cache,
        cache_key=cache_key,
        cache_ttl=14400,
    )
    if not data or not isinstance(data, list) or len(data) == 0:
        return 0
    latest = data[0]
    return (
        latest.get("buy", 0)
        + latest.get("hold", 0)
        + latest.get("sell", 0)
        + latest.get("strongBuy", 0)
        + latest.get("strongSell", 0)
    )


def fetch_price_change(
    symbol: str, from_date: str, to_date: str, api_key: str
) -> Optional[Dict]:
    time.sleep(POLYGON_RATE_DELAY)
    data = _polygon_get(
        f"/v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{to_date}",
        {"adjusted": "true", "sort": "asc"},
        api_key,
    )
    if not data or not data.get("results"):
        return None
    results = data["results"]
    open_after = results[0]["o"]
    close_latest = results[-1]["c"]
    pct = ((close_latest - open_after) / open_after) * 100.0
    return {
        "open_after": open_after,
        "close_latest": close_latest,
        "pct_change": round(pct, 2),
        "days": len(results),
    }


def fetch_snapshot_price(symbol: str, api_key: str) -> float:
    data = _polygon_get(
        f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}",
        {},
        api_key,
    )
    if data and data.get("ticker"):
        day = data["ticker"].get("day", {})
        return day.get("c", 0.0) or day.get("o", 0.0)
    if data and data.get("results"):
        return data["results"].get("price", 0.0)
    return 0.0


# ---------------------------------------------------------------------------
# PEAD scoring engine
# ---------------------------------------------------------------------------

def score_eps_surprise(surprise_pct: float) -> float:
    abs_pct = abs(surprise_pct)
    if abs_pct < 1.0:
        return 0.0
    elif abs_pct < 3.0:
        return 20.0 + (abs_pct - 1.0) * 10.0
    elif abs_pct < 7.0:
        return 40.0 + (abs_pct - 3.0) * 7.5
    elif abs_pct < 15.0:
        return 70.0 + (abs_pct - 7.0) * 2.5
    else:
        return min(100.0, 90.0 + (abs_pct - 15.0) * 0.5)


def score_price_momentum(surprise_pct: float, price_change_pct: float) -> float:
    if surprise_pct == 0 or price_change_pct == 0:
        return 0.0
    same_direction = (surprise_pct > 0) == (price_change_pct > 0)
    abs_move = abs(price_change_pct)
    if same_direction:
        if abs_move < 1.0:
            return 30.0
        elif abs_move < 3.0:
            return 30.0 + (abs_move - 1.0) * 15.0
        elif abs_move < 5.0:
            return 60.0 + (abs_move - 3.0) * 10.0
        else:
            return min(100.0, 80.0 + (abs_move - 5.0) * 4.0)
    else:
        if abs_move < 1.0:
            return 20.0
        elif abs_move < 3.0:
            return 10.0
        else:
            return 0.0


def score_analyst_coverage(total_analysts: int) -> float:
    if total_analysts <= 0:
        return 80.0
    elif total_analysts <= 5:
        return 100.0
    elif total_analysts <= 10:
        return 70.0
    elif total_analysts <= 20:
        return 40.0
    else:
        return 20.0


def score_revenue_surprise(
    eps_surprise_pct: float,
    rev_actual: Optional[float],
    rev_estimate: Optional[float],
) -> float:
    if rev_actual is None or rev_estimate is None or rev_estimate == 0:
        return 50.0
    rev_surprise_pct = ((rev_actual - rev_estimate) / abs(rev_estimate)) * 100.0
    eps_beat = eps_surprise_pct > 0
    rev_beat = rev_surprise_pct > 0
    if eps_beat == rev_beat:
        return 100.0
    elif eps_beat and not rev_beat:
        return 50.0
    else:
        return 30.0


def classify_guidance_flag(
    surprise_pct: float,
    price_change_pct: float,
    guidance_direction: Optional[str] = None,
) -> str:
    """Classify earnings signal into one of six guidance_flag values.

    Uses explicit guidance_direction if available; otherwise derives from price
    action. A beat with a significant price drop (HPE pattern) signals a guidance
    cut that overshadows the reported earnings beat.

    Returns: BEAT_RAISE | BEAT_HOLD | BEAT_CUT | MISS_RAISE | MISS_CUT | UNKNOWN
    """
    if not guidance_direction and surprise_pct == 0:
        return "UNKNOWN"

    # Determine guidance direction
    if guidance_direction:
        direction = guidance_direction.upper()
        if direction not in ("RAISE", "HOLD", "CUT"):
            direction = "HOLD"
    else:
        # Price-action heuristic: significant adverse price reaction overrides headline EPS
        if surprise_pct > 0 and price_change_pct < -2.5:
            direction = "CUT"   # Beat but price fell hard: guidance cut pricing
        elif surprise_pct > 0 and price_change_pct > 2.0:
            direction = "RAISE"
        elif surprise_pct > 0:
            direction = "HOLD"
        elif surprise_pct < 0 and price_change_pct < -2.0:
            direction = "CUT"
        elif surprise_pct < 0 and price_change_pct > 1.5:
            direction = "RAISE"
        else:
            direction = "HOLD"

    eps_beat = surprise_pct > 0
    if eps_beat and direction == "RAISE":
        return "BEAT_RAISE"
    if eps_beat and direction == "HOLD":
        return "BEAT_HOLD"
    if eps_beat and direction == "CUT":
        return "BEAT_CUT"
    if not eps_beat and direction == "RAISE":
        return "MISS_RAISE"
    if not eps_beat and direction == "CUT":
        return "MISS_CUT"
    return "UNKNOWN"


def extract_finnhub_guidance(earnings_data: Dict) -> Tuple[Optional[str], bool]:
    """Derive guidance direction from Finnhub revenue data.

    Uses revenue actual vs estimate as a proxy for forward guidance: a meaningful
    revenue miss signals management guided down (CUT), a revenue beat signals
    guided up (RAISE), and within 2% either way is HOLD.

    Returns (guidance_direction, finnhub_available):
      - finnhub_available=True only when both revenue fields are present and
        the estimate is non-zero (i.e. Finnhub actually gave us the data).
      - finnhub_available=False falls back to the price-action heuristic in
        classify_guidance_flag().
    """
    rev_actual = earnings_data.get("revenueActual")
    rev_est = earnings_data.get("revenueEstimate")
    if rev_actual is None or rev_est is None or rev_est == 0:
        return None, False
    rev_surprise_pct = ((rev_actual - rev_est) / abs(rev_est)) * 100.0
    if rev_surprise_pct < -2.0:
        return "CUT", True
    elif rev_surprise_pct > 2.0:
        return "RAISE", True
    return "HOLD", True


def calculate_pead_signal(
    earnings_data: Dict,
    price_data: Optional[Dict],
    analyst_count: int,
) -> Dict[str, Any]:
    surprise_pct = earnings_data.get("surprisePercent", 0.0) or 0.0
    price_change = price_data["pct_change"] if price_data else 0.0

    f1 = score_eps_surprise(surprise_pct)
    f2 = score_price_momentum(surprise_pct, price_change) if price_data else 0.0
    f3 = score_analyst_coverage(analyst_count)
    f4 = score_revenue_surprise(
        surprise_pct,
        earnings_data.get("revenueActual"),
        earnings_data.get("revenueEstimate"),
    )

    total = (
        f1 * WEIGHT_EPS_SURPRISE / 100.0
        + f2 * WEIGHT_PRICE_MOMENTUM / 100.0
        + f3 * WEIGHT_ANALYST_COVERAGE / 100.0
        + f4 * WEIGHT_REVENUE_SURPRISE / 100.0
    )

    if surprise_pct > 0:
        direction = "LONG"
    elif surprise_pct < 0:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # Sprint 27 Item 6: use Finnhub revenue data for guidance when available;
    # fall back to price-action heuristic if revenue fields are missing.
    guidance_direction, finnhub_guidance_available = extract_finnhub_guidance(earnings_data)
    guidance_flag = classify_guidance_flag(surprise_pct, price_change, guidance_direction)

    return {
        "symbol": earnings_data.get("symbol", ""),
        "score": round(total, 1),
        "direction": direction,
        "guidance_flag": guidance_flag,
        "finnhub_guidance_available": finnhub_guidance_available,
        "factors": {
            "eps_surprise": {
                "score": round(f1, 1),
                "weight": WEIGHT_EPS_SURPRISE,
                "value": round(surprise_pct, 2),
            },
            "price_momentum": {
                "score": round(f2, 1),
                "weight": WEIGHT_PRICE_MOMENTUM,
                "value": price_change,
            },
            "analyst_coverage": {
                "score": round(f3, 1),
                "weight": WEIGHT_ANALYST_COVERAGE,
                "value": analyst_count,
            },
            "revenue_surprise": {
                "score": round(f4, 1),
                "weight": WEIGHT_REVENUE_SURPRISE,
                "value": earnings_data.get("revenueActual"),
            },
        },
        "earnings_date": earnings_data.get("date", ""),
        "hour": earnings_data.get("hour", ""),
        "eps_estimate": earnings_data.get("epsEstimate"),
        "eps_actual": earnings_data.get("epsActual"),
        "surprise_pct": round(surprise_pct, 2),
        "price_change_pct": price_change,
        "days_since_earnings": price_data["days"] if price_data else 0,
        "momentum_pending": (price_data["days"] if price_data else 0) <= 1,
    }


# ---------------------------------------------------------------------------
# Earnings data builder
# ---------------------------------------------------------------------------

def build_earnings_data(
    calendar_entry: Dict, history: List[Dict]
) -> Dict[str, Any]:
    symbol = calendar_entry.get("symbol", "")
    earnings_date = calendar_entry.get("date", "")

    data: Dict[str, Any] = {
        "symbol": symbol,
        "date": earnings_date,
        "hour": calendar_entry.get("hour", ""),
        "epsEstimate": calendar_entry.get("epsEstimate"),
        "epsActual": calendar_entry.get("epsActual"),
        "revenueEstimate": calendar_entry.get("revenueEstimate"),
        "revenueActual": calendar_entry.get("revenueActual"),
        "surprisePercent": None,
    }

    if data["epsActual"] is not None and data["epsEstimate"] is not None:
        if data["epsEstimate"] != 0:
            data["surprisePercent"] = (
                (data["epsActual"] - data["epsEstimate"]) / abs(data["epsEstimate"])
            ) * 100.0

    if data["surprisePercent"] is None and history:
        for h in history:
            h_period = h.get("period", "")
            if h_period and earnings_date:
                try:
                    h_dt = datetime.strptime(h_period, "%Y-%m-%d")
                    e_dt = datetime.strptime(earnings_date, "%Y-%m-%d")
                    if abs((h_dt - e_dt).days) <= 45:
                        data["epsActual"] = h.get("actual")
                        data["epsEstimate"] = h.get("estimate")
                        data["surprisePercent"] = h.get("surprisePercent")
                        break
                except ValueError:
                    continue

        if data["surprisePercent"] is None and history:
            latest = history[0]
            data["epsActual"] = latest.get("actual")
            data["epsEstimate"] = latest.get("estimate")
            data["surprisePercent"] = latest.get("surprisePercent")

    return data


# ---------------------------------------------------------------------------
# Main scan orchestrator
# ---------------------------------------------------------------------------

def run_pead_scan(
    polygon_api_key: str,
    finnhub_api_key: str,
    watchlist: Optional[List[str]] = None,
    lookback_days: int = MAX_LOOKBACK_DAYS,
    single_symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """Run PEAD earnings scan. Returns structured results dict."""
    cfg = get_config()
    cache = _FinnhubCache(cfg.scan_results_dir.parent / "cache")

    if watchlist is None:
        watchlist = DEFAULT_UNIVERSE

    scan_time = datetime.now()
    today_str = scan_time.strftime("%Y-%m-%d")
    from_date = (scan_time - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    logger.info(
        "PEAD SCAN -- %s  lookback=%dd  universe=%d symbols",
        scan_time.strftime("%Y-%m-%d %H:%M ET"),
        lookback_days,
        len(watchlist),
    )

    # Step 1: earnings calendar
    calendar = fetch_earnings_calendar(from_date, today_str, finnhub_api_key, cache)
    logger.info("Earnings calendar: %d entries", len(calendar))

    # Step 2: filter to watchlist
    watchlist_upper = {s.upper() for s in watchlist}
    if single_symbol:
        recent_earners = [
            e for e in calendar
            if e.get("symbol", "").upper() == single_symbol.upper()
        ]
    else:
        recent_earners = [
            e for e in calendar
            if e.get("symbol", "").upper() in watchlist_upper
        ]

    logger.info("Watchlist matches: %d stocks with recent earnings", len(recent_earners))

    # Step 3: enrich and score
    signals: List[Dict[str, Any]] = []
    data_errors: List[Dict[str, Any]] = []

    for entry in recent_earners:
        symbol = entry.get("symbol", "")
        earnings_date = entry.get("date", "")

        history = fetch_earnings_history(symbol, finnhub_api_key, cache)
        earnings_data = build_earnings_data(entry, history)

        surprise_pct = earnings_data.get("surprisePercent", 0)
        if surprise_pct is None or surprise_pct == 0:
            logger.debug("%s: skipped -- no surprise data", symbol)
            continue

        if abs(surprise_pct) < MIN_SURPRISE_PCT:
            logger.debug("%s: skipped -- surprise %.1f%% below threshold", symbol, surprise_pct)
            continue

        if abs(surprise_pct) > EPS_SURPRISE_CAP_PCT:
            logger.info("%s: skipped -- surprise %.1f%% exceeds cap (data error)", symbol, surprise_pct)
            data_errors.append({
                "symbol": symbol,
                "reason": "eps_surprise_cap",
                "surprise_pct": round(surprise_pct, 2),
            })
            continue

        analyst_count = fetch_analyst_count(symbol, finnhub_api_key, cache)

        price_data = None
        if polygon_api_key and earnings_date:
            next_day = (
                datetime.strptime(earnings_date, "%Y-%m-%d") + timedelta(days=1)
            ).strftime("%Y-%m-%d")
            price_data = fetch_price_change(symbol, next_day, today_str, polygon_api_key)

        signal = calculate_pead_signal(earnings_data, price_data, analyst_count)

        # Capture price_at_scan
        if polygon_api_key:
            snap_price = fetch_snapshot_price(symbol, polygon_api_key)
            if snap_price > 0:
                signal["price_at_scan"] = snap_price
        if "price_at_scan" not in signal or not signal.get("price_at_scan"):
            if price_data and price_data.get("close_latest", 0) > 0:
                signal["price_at_scan"] = price_data["close_latest"]
            else:
                signal["price_at_scan"] = 0.0

        signals.append(signal)
        logger.info(
            "%s: score=%.1f  dir=%s  eps=%+.1f%%  price=%+.1f%%  analysts=%d",
            symbol,
            signal["score"],
            signal["direction"],
            signal["surprise_pct"],
            signal["price_change_pct"],
            analyst_count,
        )

    # Step 4: rank
    signals.sort(key=lambda s: s["score"], reverse=True)
    actionable = [s for s in signals if s["score"] >= MIN_SIGNAL_SCORE]

    logger.info(
        "PEAD complete: %d signals, %d actionable (>= %d)",
        len(signals),
        len(actionable),
        MIN_SIGNAL_SCORE,
    )

    return {
        "scan_time": scan_time.isoformat(),
        "scanner": "prime_pead_scanner",
        "version": "1.0",
        "lookback_days": lookback_days,
        "universe_size": len(watchlist),
        "min_signal_score": MIN_SIGNAL_SCORE,
        "eps_surprise_cap_pct": EPS_SURPRISE_CAP_PCT,
        "weights": {
            "eps_surprise": WEIGHT_EPS_SURPRISE,
            "price_momentum": WEIGHT_PRICE_MOMENTUM,
            "analyst_coverage": WEIGHT_ANALYST_COVERAGE,
            "revenue_surprise": WEIGHT_REVENUE_SURPRISE,
        },
        "signals_found": len(signals),
        "actionable_count": len(actionable),
        "data_error_count": len(data_errors),
        "data_errors": data_errors,
        "signals": signals,
    }


def save_results(scan_data: Dict) -> Path:
    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"pead_scan_{ts}_ET.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=2, default=str)
    logger.info("Results saved: %s", out)
    return out


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="PRIME v1.0 PEAD Earnings Scanner")
    parser.add_argument(
        "--days",
        type=int,
        default=MAX_LOOKBACK_DAYS,
        help=f"Lookback days (default: {MAX_LOOKBACK_DAYS})",
    )
    parser.add_argument("--symbol", type=str, default=None, help="Scan single symbol")
    parser.add_argument(
        "--min-score",
        type=int,
        default=MIN_SIGNAL_SCORE,
        help=f"Min signal score (default: {MIN_SIGNAL_SCORE})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [PEAD] %(levelname)s %(message)s",
    )

    cfg = get_config()
    if not cfg.finnhub_api_key:
        logger.error("finnhub_api_key not found in config.json")
        sys.exit(1)

    from prime_data.prime_db import init_db, log_ops_event

    init_db()

    log_ops_event("SCAN_START", "pead_scanner", detail=f"lookback={args.days}d")

    scan_data = run_pead_scan(
        polygon_api_key=cfg.polygon_api_key,
        finnhub_api_key=cfg.finnhub_api_key,
        lookback_days=args.days,
        single_symbol=args.symbol,
    )

    actionable = [s for s in scan_data["signals"] if s["score"] >= args.min_score]
    print(f"\nPEAD Scan: {scan_data['signals_found']} signals, "
          f"{len(actionable)} actionable (>= {args.min_score})")

    for s in actionable:
        arrow = "^" if s["direction"] == "LONG" else "v"
        print(
            f"  {s['symbol']:<6} score={s['score']:5.1f}  "
            f"{arrow} {s['direction']:<5}  eps={s['surprise_pct']:+.1f}%  "
            f"price={s['price_change_pct']:+.1f}%"
        )

    save_results(scan_data)

    log_ops_event(
        "SCAN_COMPLETE",
        "pead_scanner",
        detail=f"signals={scan_data['signals_found']} actionable={len(actionable)}",
    )


if __name__ == "__main__":
    main()
