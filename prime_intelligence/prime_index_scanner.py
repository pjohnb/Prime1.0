"""
PRIME v1.0 Index Trader Scanner (Sprint 16 Item 3).

A full index/ETF trading strategy. Evaluates 14 sector/broad-market ETFs on
trend (price vs 50/100/200-day SMA + golden/death cross), relative strength vs
SPY, and volume vs its 20-day average, then classifies each into one of:

    STRONG_LONG, WEAK_LONG, NEUTRAL, WEAK_SHORT, STRONG_SHORT

Tradeable (non-NEUTRAL) classifications are written to prime_signals with
strategy="IDX" and instrument_type="ETF". They surface automatically in the
Lovable UI Signals tab via the dynamic strategy filter (get_distinct_strategies)
-- no UI/bridge change needed.

Integrations:
  * DK nullifier: a symbol carrying an active DK NULLIFIER has its IDX signal
    written as status='SUPPRESSED' (consistent with apply_nullifier_suppression).
  * MATA routing: index trades route to a single account (default
    "Joint Brokerage", configurable via ops_config.json index_account). The
    routed account is recorded in the signal's factors for downstream MATA
    execution.

Design notes (Sprint 16 ambiguity resolution):
  * Per the work order the module lives in prime_intelligence/ (alongside the
    DK trader strategy), not prime_scanners/. ops_config.json idx.scanner_module
    is aligned to prime_intelligence.prime_index_scanner.
  * SHORT-direction signals are *classified and recorded* here; short-side
    execution/borrow/sizing is Sprint 17. IDX only generates signals.
  * run_index_scan() accepts an optional bars_by_symbol map so the scoring path
    is fully testable without network access.
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

from prime_analytics.prime_signals_db import init_signals_table, insert_signal_dedup

logger = logging.getLogger("prime_index_scanner")

# 14-instrument universe: SPY + broad indices + 11 sector SPDRs.
INDEX_UNIVERSE = [
    "SPY", "QQQ", "IWM",
    "XLK", "XLF", "XLE", "XLV", "XLI",
    "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU",
]
BENCHMARK = "SPY"

# Classification tiers (also used as the signal tier label).
STRONG_LONG = "STRONG_LONG"
WEAK_LONG = "WEAK_LONG"
NEUTRAL = "NEUTRAL"
WEAK_SHORT = "WEAK_SHORT"
STRONG_SHORT = "STRONG_SHORT"

# Scoring parameters.
SMA_FAST, SMA_MID, SMA_SLOW = 50, 100, 200
RS_LOOKBACK = 20            # trading days for relative-strength comparison vs SPY
RS_THRESHOLD = 1.0         # percent out/under-performance vs SPY to count
VOLUME_LOOKBACK = 20        # trading days for the average-volume baseline
VOLUME_CONFIRM = 1.2        # volume vs 20-day avg required to confirm conviction
LONG_SCORE = 2              # |trend_score| at/above which a direction is taken
STRONG_SCORE = 5            # |trend_score| at/above which (+ volume) it is STRONG

POLYGON_BASE = "https://api.polygon.io"
API_TIMEOUT = 15
DEFAULT_DAYS_BACK = 320     # ~ 220 trading days, enough for the 200-day SMA

DEFAULT_INDEX_ACCOUNT = "Joint Brokerage"


# ---------------------------------------------------------------------------
# Math helpers (pure, testable)
# ---------------------------------------------------------------------------

def compute_sma(closes: List[float], period: int) -> Optional[float]:
    """Simple moving average of the last `period` closes, or None if too few."""
    if not closes or len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


def _sma_at(closes: List[float], period: int, offset_from_end: int) -> Optional[float]:
    """SMA computed as of `offset_from_end` bars before the latest bar."""
    end = len(closes) - offset_from_end
    if end < period:
        return None
    window = closes[end - period:end]
    return sum(window) / period


def detect_sma_crossover(closes: List[float], fast: int = SMA_FAST,
                         slow: int = SMA_SLOW) -> Optional[str]:
    """Detect a fast/slow SMA crossover on the most recent bar.

    GOLDEN -> fast crossed above slow; DEATH -> fast crossed below slow;
    None -> no crossover on the latest bar (or insufficient data).
    """
    fast_now = _sma_at(closes, fast, 0)
    slow_now = _sma_at(closes, slow, 0)
    fast_prev = _sma_at(closes, fast, 1)
    slow_prev = _sma_at(closes, slow, 1)
    if None in (fast_now, slow_now, fast_prev, slow_prev):
        return None
    if fast_prev <= slow_prev and fast_now > slow_now:
        return "GOLDEN"
    if fast_prev >= slow_prev and fast_now < slow_now:
        return "DEATH"
    return None


def _return_pct(closes: List[float], lookback: int) -> Optional[float]:
    """Percent price change over the last `lookback` bars."""
    if len(closes) <= lookback:
        return None
    past = closes[-(lookback + 1)]
    if past <= 0:
        return None
    return (closes[-1] - past) / past * 100.0


def relative_strength(symbol_closes: List[float], spy_closes: List[float],
                      lookback: int = RS_LOOKBACK) -> Optional[float]:
    """Relative strength vs SPY: symbol return minus SPY return over lookback (%)."""
    sym_ret = _return_pct(symbol_closes, lookback)
    spy_ret = _return_pct(spy_closes, lookback)
    if sym_ret is None or spy_ret is None:
        return None
    return sym_ret - spy_ret


def compute_metrics(closes: List[float], volumes: List[float],
                    spy_closes: List[float]) -> Optional[Dict[str, Any]]:
    """Compute the full metric set for one instrument. None if insufficient bars."""
    if len(closes) < SMA_SLOW:
        return None
    price = closes[-1]
    sma50 = compute_sma(closes, SMA_FAST)
    sma100 = compute_sma(closes, SMA_MID)
    sma200 = compute_sma(closes, SMA_SLOW)
    crossover = detect_sma_crossover(closes)
    rs = relative_strength(closes, spy_closes)

    avg_vol = (sum(volumes[-VOLUME_LOOKBACK:]) / VOLUME_LOOKBACK
               if len(volumes) >= VOLUME_LOOKBACK and VOLUME_LOOKBACK > 0 else 0.0)
    cur_vol = volumes[-1] if volumes else 0.0
    volume_ratio = (cur_vol / avg_vol) if avg_vol > 0 else 0.0

    return {
        "price": round(price, 4),
        "sma50": round(sma50, 4) if sma50 is not None else None,
        "sma100": round(sma100, 4) if sma100 is not None else None,
        "sma200": round(sma200, 4) if sma200 is not None else None,
        "crossover": crossover,
        "rs_vs_spy": round(rs, 4) if rs is not None else None,
        "volume_ratio": round(volume_ratio, 4),
    }


def classify_index(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Map metrics to a classification, direction, and 0-100 score.

    trend_score (range -6..+6) sums six directional components:
      price vs SMA50 / SMA100 / SMA200, SMA50 vs SMA200 regime, golden/death
      cross, and relative strength vs SPY. Volume vs the 20-day average is the
      conviction gate that separates STRONG_ from WEAK_ classifications.
    """
    price = metrics.get("price")
    sma50 = metrics.get("sma50")
    sma100 = metrics.get("sma100")
    sma200 = metrics.get("sma200")
    crossover = metrics.get("crossover")
    rs = metrics.get("rs_vs_spy")
    volume_ratio = metrics.get("volume_ratio") or 0.0

    trend_score = 0
    if price is not None and sma50 is not None:
        trend_score += 1 if price > sma50 else -1
    if price is not None and sma100 is not None:
        trend_score += 1 if price > sma100 else -1
    if price is not None and sma200 is not None:
        trend_score += 1 if price > sma200 else -1
    if sma50 is not None and sma200 is not None:
        trend_score += 1 if sma50 > sma200 else -1
    if crossover == "GOLDEN":
        trend_score += 1
    elif crossover == "DEATH":
        trend_score -= 1
    if rs is not None:
        if rs > RS_THRESHOLD:
            trend_score += 1
        elif rs < -RS_THRESHOLD:
            trend_score -= 1

    volume_confirms = volume_ratio >= VOLUME_CONFIRM

    if trend_score >= LONG_SCORE:
        direction = "LONG"
        classification = (STRONG_LONG if trend_score >= STRONG_SCORE and volume_confirms
                          else WEAK_LONG)
    elif trend_score <= -LONG_SCORE:
        direction = "SHORT"
        classification = (STRONG_SHORT if trend_score <= -STRONG_SCORE and volume_confirms
                          else WEAK_SHORT)
    else:
        direction = "FLAT"
        classification = NEUTRAL

    # 0-100 score: magnitude of conviction (|trend_score| normalised to 6).
    score = round(min(abs(trend_score), 6) / 6.0 * 100.0, 1)
    return {
        "classification": classification,
        "direction": direction,
        "trend_score": trend_score,
        "score": score,
        "volume_confirms": volume_confirms,
    }


# ---------------------------------------------------------------------------
# Routing + config
# ---------------------------------------------------------------------------

def route_index_account(config_path: Optional[Path] = None) -> str:
    """MATA routing for index trades: single configurable account.

    Reads index_account from ops_config.json; defaults to "Joint Brokerage".
    """
    if config_path is None:
        config_path = PROJECT_ROOT / "ops_config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            acct = str(data.get("index_account", "") or "").strip()
            if acct:
                return acct
    except Exception:
        pass
    return DEFAULT_INDEX_ACCOUNT


# ---------------------------------------------------------------------------
# Data fetch (Polygon daily aggregates)
# ---------------------------------------------------------------------------

def _polygon_delay():
    """Sleep between Polygon API calls per ops_config rate-limit settings."""
    try:
        ops = PROJECT_ROOT / "ops_config.json"
        cfg = json.loads(ops.read_text())
        plan = cfg.get("polygon_plan", "free")
        delay_ms = 100 if plan == "paid" else int(cfg.get("polygon_rate_limit_delay_ms", 13000))
        time.sleep(delay_ms / 1000)
    except Exception:
        time.sleep(0.5)


def _polygon_get(endpoint: str, params: Dict, api_key: str) -> Optional[Dict]:
    params["apiKey"] = api_key
    try:
        r = requests.get(f"{POLYGON_BASE}{endpoint}", params=params, timeout=API_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        logger.warning("Polygon %s -> HTTP %s", endpoint, r.status_code)
        return None
    except Exception as e:
        logger.warning("Polygon %s failed: %s", endpoint, e)
        return None


def fetch_daily_bars(symbol: str, api_key: str,
                     days_back: int = DEFAULT_DAYS_BACK) -> Optional[List[Dict]]:
    """Fetch daily OHLCV bars for `symbol` over the last `days_back` calendar days."""
    today = datetime.now().date()
    from_date = today - timedelta(days=days_back)
    data = _polygon_get(
        f"/v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{today}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
        api_key,
    )
    if not data or not data.get("results"):
        return None
    return [{"close": r.get("c", 0), "volume": r.get("v", 0),
             "high": r.get("h", 0), "low": r.get("l", 0),
             "open": r.get("o", 0), "timestamp": r.get("t", 0)}
            for r in data["results"]]


# ---------------------------------------------------------------------------
# Scan orchestration
# ---------------------------------------------------------------------------

def evaluate_instrument(symbol: str, bars: List[Dict],
                        spy_closes: List[float]) -> Optional[Dict[str, Any]]:
    """Compute metrics + classification for one instrument. None if insufficient data."""
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    metrics = compute_metrics(closes, volumes, spy_closes)
    if metrics is None:
        return None
    verdict = classify_index(metrics)
    return {"symbol": symbol, "metrics": metrics, **verdict}


def run_index_scan(
    api_key: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    scan_ts: Optional[str] = None,
    db_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    bars_by_symbol: Optional[Dict[str, List[Dict]]] = None,
) -> Dict[str, Any]:
    """Scan the index universe, classify each instrument, write IDX signals.

    When bars_by_symbol is provided, it is used instead of fetching from Polygon
    (keeps the scoring path testable offline). Returns a summary dict.
    """
    init_signals_table(db_path)
    if symbols is None:
        symbols = list(INDEX_UNIVERSE)
    if scan_ts is None:
        scan_ts = datetime.utcnow().isoformat()

    # CIL-070: graceful degradation. When bars are fetched live (no injected
    # bars_by_symbol) but there is no Polygon key, return an empty (well-formed)
    # summary and log a WARNING rather than fetching against an empty key.
    if bars_by_symbol is None and not (api_key or "").strip():
        logger.warning("IDX: Polygon unavailable — skipping scan")
        return {
            "scan_ts": scan_ts, "account": route_index_account(config_path),
            "polygon_unavailable": True, "scanned": 0,
            "written": [], "suppressed": [], "neutral": [], "errors": [],
            "by_classification": {STRONG_LONG: [], WEAK_LONG: [], NEUTRAL: [],
                                  WEAK_SHORT: [], STRONG_SHORT: []},
        }

    # DK nullifier integration: a nullified symbol's IDX signal is suppressed.
    try:
        from prime_intelligence.prime_dk_trader import get_active_nullifiers
        nullifiers = get_active_nullifiers(db_path)
    except Exception as e:
        logger.debug("could not load DK nullifiers: %s", e)
        nullifiers = set()

    account = route_index_account(config_path)

    def _bars(sym: str) -> Optional[List[Dict]]:
        if bars_by_symbol is not None:
            return bars_by_symbol.get(sym)
        bars = fetch_daily_bars(sym, api_key)
        _polygon_delay()
        return bars

    # SPY benchmark closes (needed for relative strength of every instrument).
    spy_bars = _bars(BENCHMARK)
    spy_closes = [b["close"] for b in spy_bars] if spy_bars else []

    summary: Dict[str, Any] = {
        "scan_ts": scan_ts, "account": account, "scanned": 0,
        "written": [], "suppressed": [], "neutral": [], "errors": [],
        "by_classification": {STRONG_LONG: [], WEAK_LONG: [], NEUTRAL: [],
                              WEAK_SHORT: [], STRONG_SHORT: []},
    }

    for symbol in symbols:
        try:
            bars = spy_bars if symbol == BENCHMARK else _bars(symbol)
            if not bars:
                summary["errors"].append({"symbol": symbol, "error": "no_data"})
                continue
            summary["scanned"] += 1
            result = evaluate_instrument(symbol, bars, spy_closes)
            if result is None:
                summary["errors"].append({"symbol": symbol, "error": "insufficient_bars"})
                continue

            cls = result["classification"]
            summary["by_classification"][cls].append(symbol)

            if cls == NEUTRAL:
                summary["neutral"].append(symbol)
                continue

            suppressed = symbol in nullifiers
            status = "SUPPRESSED" if suppressed else "APPROVED"
            factors = {
                "classification": cls,
                "trend_score": result["trend_score"],
                "metrics": result["metrics"],
                "routed_account": account,
                "dk_suppressed": suppressed,
            }
            insert_signal_dedup(
                symbol=symbol,
                strategy="IDX",
                scan_ts=scan_ts,
                entry_price=result["metrics"]["price"],
                score=result["score"],
                sector="Index/ETF",
                tier=cls,
                status=status,
                direction=result["direction"],
                factors=json.dumps(factors),
                instrument_type="ETF",
                db_path=db_path,
            )
            if suppressed:
                summary["suppressed"].append(symbol)
            else:
                summary["written"].append(symbol)
        except Exception as e:
            logger.error("index scan error for %s: %s", symbol, e)
            summary["errors"].append({"symbol": symbol, "error": str(e)})

    logger.info("IDX scan: %d scanned, %d written, %d suppressed, %d neutral",
                summary["scanned"], len(summary["written"]),
                len(summary["suppressed"]), len(summary["neutral"]))
    return summary


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [IDX] %(levelname)s %(message)s")
    from prime_config.prime_config import get_config
    from prime_data.prime_db import init_db, log_ops_event

    cfg = get_config()
    api_key = cfg.polygon_api_key
    if not api_key:
        # CIL-070: graceful degradation — warn and exit 0 (no crash).
        logger.warning("IDX: Polygon unavailable — skipping scan")
        return

    init_db()
    log_ops_event("SCAN_START", "index_scanner", detail="universe={0}".format(len(INDEX_UNIVERSE)))
    summary = run_index_scan(api_key=api_key)
    log_ops_event("SCAN_COMPLETE", "index_scanner",
                  detail="written={0} suppressed={1} neutral={2}".format(
                      len(summary["written"]), len(summary["suppressed"]),
                      len(summary["neutral"])))
    print("IDX Scan: {0} scanned, {1} signals written, {2} suppressed, {3} neutral".format(
        summary["scanned"], len(summary["written"]),
        len(summary["suppressed"]), len(summary["neutral"])))
    for cls, syms in summary["by_classification"].items():
        if syms:
            print("  {0:<13} {1}".format(cls, ", ".join(syms)))


if __name__ == "__main__":
    main()
