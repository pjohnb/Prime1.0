"""
PRIME v1.0 Prime Segment Analysis Scanner (PSA).
Ported from v0.9 prime_psa_runner.py + prime_parallel_analyzer.py.

General momentum/volatility scanner using the A-B-C-D ratio framework.
Segments historical bars into baseline (A-B) and current (C-D) windows,
compares momentum, volume, and volatility ratios, then checks for trend
consistency, pattern signals, and consecutive positive bars.

Runs 4x daily on configurable schedule against S&P 500 or custom universe.

Standalone: python prime_scanners/prime_psa_scanner.py
"""

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
API_TIMEOUT = 10
FETCH_WORKERS = 10
ANALYSIS_WORKERS = 8

# A-B-C-D defaults (overridable via config)
DEFAULT_BASELINE_PERIODS = 22
DEFAULT_LONG_PERIODS = 9
DEFAULT_SHORT_PERIODS = 3
DEFAULT_INTERVAL = "5min"
DEFAULT_REQUIRED_POSITIVE = 2

# Threshold defaults
DEFAULT_MOMENTUM_THRESHOLD = 55.0
DEFAULT_VOLUME_THRESHOLD = 50.0
DEFAULT_VOLATILITY_THRESHOLD = 50.0
DEFAULT_BC_MAX_DRAWDOWN = 3.0
DEFAULT_CD_MAX_DRAWDOWN = 3.0

# Stage 0 defaults
DEFAULT_MIN_PRICE = 5.0
DEFAULT_MAX_PRICE = 500.0
DEFAULT_MIN_DAILY_VOLUME = 500_000

# Anomaly caps
MAX_REASONABLE_MOMENTUM = 1000.0
MAX_REASONABLE_VOLUME = 10000.0
MAX_REASONABLE_VOLATILITY = 2000.0

# Pattern detection
BREAKOUT_LOOKBACK = 10
HIGHER_HIGHS_BARS = 4
VOLUME_EXPANSION_MULT = 1.20

# Default universe (S&P top 50 for fast scans — used as fallback only)
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM",
    "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC", "XOM",
    "NFLX", "COST", "CRM", "AMD", "INTC", "CSCO", "PEP", "AVGO",
    "ADBE", "CMCSA", "TXN", "QCOM", "INTU", "AMAT", "AMGN", "ISRG",
    "LRCX", "MU", "ADI", "MRVL", "KLAC", "CDNS", "SNPS", "FTNT",
    "PANW", "CRWD", "ZS", "DDOG", "NET", "MDB", "SNOW", "NOW",
]

_MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META"]
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def resolve_psa_universe(mode: str = "sp500",
                         custom: str = "",
                         sector: str = "XLK") -> List[str]:
    """Resolve a universe mode string into a flat ticker list.

    mode values:
      sp500         — full S&P 500 from data/sp500_constituents.json
      mag7          — AAPL MSFT GOOGL AMZN NVDA TSLA META
      sp500_ex_mag7 — S&P 500 minus Mag7
      russell2000   — small-cap list from data/russell2000_constituents.json
      all_sectors   — union of all sector ETF constituents
      sector        — single sector ETF constituents (uses `sector` param)
      custom        — comma-separated tickers from `custom` param
    Falls back to DEFAULT_UNIVERSE on any file-load error.
    """
    if mode == "mag7":
        return list(_MAG7)

    if mode == "custom":
        symbols = [s.strip().upper() for s in custom.split(",") if s.strip()]
        return symbols if symbols else list(DEFAULT_UNIVERSE)

    try:
        if mode == "sp500":
            return json.loads((_DATA_DIR / "sp500_constituents.json").read_text())

        if mode == "sp500_ex_mag7":
            sp500 = json.loads((_DATA_DIR / "sp500_constituents.json").read_text())
            mag7_set = set(_MAG7)
            return [s for s in sp500 if s not in mag7_set]

        if mode == "russell2000":
            return json.loads((_DATA_DIR / "russell2000_constituents.json").read_text())

        if mode in ("all_sectors", "sector"):
            data: dict = json.loads((_DATA_DIR / "sectors_constituents.json").read_text())
            if mode == "sector":
                return data.get(sector, list(DEFAULT_UNIVERSE))
            seen: set = set()
            result: List[str] = []
            for syms in data.values():
                for s in syms:
                    if s not in seen:
                        seen.add(s)
                        result.append(s)
            return result

    except Exception as e:
        logger.warning("PSA: universe data unavailable for mode=%s: %s — using DEFAULT_UNIVERSE", mode, e)

    return list(DEFAULT_UNIVERSE)

INTERVAL_MINUTES = {
    "1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60,
}


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


def fetch_bars(
    symbol: str, interval: str, total_bars: int, api_key: str
) -> Optional[List[Dict]]:
    multiplier, timespan = _parse_interval(interval)
    days_back = _bars_to_days(total_bars, interval) + 5

    today = datetime.now().date()
    from_date = today - timedelta(days=days_back)

    data = _polygon_get(
        f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{from_date}/{today}",
        # desc = newest bars first; reversed below to restore chronological order.
        # asc + limit would return the OLDEST bars (pre-market from days ago),
        # causing stale A-B-C-D analysis and severe volume underestimation.
        {"adjusted": "true", "sort": "desc", "limit": total_bars + 20},
        api_key,
    )
    if not data or not data.get("results"):
        return None

    bars = []
    for r in data["results"]:
        bars.append({
            "open": r.get("o", 0),
            "high": r.get("h", 0),
            "low": r.get("l", 0),
            "close": r.get("c", 0),
            "volume": r.get("v", 0),
            "timestamp": r.get("t", 0),
        })

    bars.reverse()  # restore chronological (oldest → newest) after desc fetch
    return bars[-total_bars:] if len(bars) >= total_bars else bars


def fetch_snapshot(symbol: str, api_key: str) -> Optional[Dict]:
    data = _polygon_get(
        f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}",
        {},
        api_key,
    )
    if not data or not data.get("ticker"):
        return None
    t = data["ticker"]
    day = t.get("day", {})
    return {
        "price": day.get("c") or day.get("o") or t.get("lastTrade", {}).get("p", 0),
        "volume": day.get("v", 0),
    }


def _parse_interval(interval: str) -> Tuple[int, str]:
    if interval.endswith("min"):
        return int(interval.replace("min", "")), "minute"
    elif interval.endswith("hour"):
        return int(interval.replace("hour", "")), "hour"
    return 5, "minute"


def _bars_to_days(total_bars: int, interval: str) -> int:
    mins = INTERVAL_MINUTES.get(interval, 5)
    bars_per_day = 390 // mins
    if bars_per_day <= 0:
        bars_per_day = 1
    return (total_bars // bars_per_day) + 3


# ---------------------------------------------------------------------------
# A-B-C-D analysis core
# ---------------------------------------------------------------------------

def analyze_symbol(
    bars: List[Dict],
    baseline_periods: int,
    long_periods: int,
    short_periods: int,
    required_positive: int,
    thresholds: Dict[str, float],
    bc_max_drawdown: float,
    cd_max_drawdown: float,
) -> Dict[str, Any]:
    total_needed = baseline_periods + long_periods + short_periods
    if len(bars) < total_needed:
        return {"approved": False, "reason": "insufficient_bars"}

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    highs = [b["high"] for b in bars]

    # Segment boundaries (chronological: oldest first)
    ab_end = baseline_periods
    bc_end = ab_end + long_periods
    cd_end = bc_end + short_periods

    ab_closes = closes[:ab_end]
    bc_closes = closes[ab_end:bc_end]
    cd_closes = closes[bc_end:cd_end]

    ab_volumes = volumes[:ab_end]
    cd_volumes = volumes[bc_end:cd_end]

    # -- Momentum --
    ab_returns = _pct_changes(ab_closes)
    cd_returns = _pct_changes(cd_closes)

    ab_avg = _safe_mean(ab_returns)
    cd_avg = _safe_mean(cd_returns)

    if cd_avg <= 0:
        momentum_pct = 0.0
    elif ab_avg == 0:
        momentum_pct = 100.0
    else:
        momentum_pct = abs((cd_avg / ab_avg) * 100.0)

    # -- Volume --
    ab_avg_vol = _safe_mean(ab_volumes)
    cd_avg_vol = _safe_mean(cd_volumes)
    volume_pct = (cd_avg_vol / ab_avg_vol * 100.0) if ab_avg_vol > 0 else 0.0

    # -- Volatility --
    ab_std = _safe_std(_pct_changes(ab_closes))
    cd_std = _safe_std(_pct_changes(cd_closes))
    volatility_pct = (cd_std / ab_std * 100.0) if ab_std > 0 else 0.0

    # -- Anomaly check --
    if momentum_pct > MAX_REASONABLE_MOMENTUM:
        return {"approved": False, "reason": f"anomalous_momentum ({momentum_pct:.0f}%)"}
    if volume_pct > MAX_REASONABLE_VOLUME:
        return {"approved": False, "reason": f"anomalous_volume ({volume_pct:.0f}%)"}
    if volatility_pct > MAX_REASONABLE_VOLATILITY:
        return {"approved": False, "reason": f"anomalous_volatility ({volatility_pct:.0f}%)"}

    # -- B-D direction gate --
    bd_closes = closes[ab_end:]
    if len(bd_closes) >= 2 and bd_closes[-1] <= bd_closes[0]:
        return {
            "approved": False,
            "reason": "bd_direction_negative",
            "momentum_pct": round(momentum_pct, 1),
            "volume_pct": round(volume_pct, 1),
            "volatility_pct": round(volatility_pct, 1),
        }

    # -- Trend (max drawdown) --
    bc_dd = _max_drawdown(bc_closes)
    cd_dd = _max_drawdown(cd_closes)
    trend_approved = bc_dd <= bc_max_drawdown and cd_dd <= cd_max_drawdown

    # -- Consecutive positives --
    all_returns = _pct_changes(closes)
    if len(all_returns) >= required_positive:
        last_n = all_returns[-required_positive:]
        consecutive_met = all(r > 0 for r in last_n)
    else:
        consecutive_met = False

    # -- Pattern detection --
    patterns = _detect_patterns(bars, baseline_periods)

    # -- Threshold gates --
    min_mom = thresholds.get("momentum", DEFAULT_MOMENTUM_THRESHOLD)
    min_vol = thresholds.get("volume", DEFAULT_VOLUME_THRESHOLD)
    min_vlat = thresholds.get("volatility", DEFAULT_VOLATILITY_THRESHOLD)

    approved = (
        momentum_pct >= min_mom
        and volume_pct >= min_vol
        and volatility_pct >= min_vlat
        and trend_approved
        and consecutive_met
    )

    rejection_reasons = []
    if not approved:
        if momentum_pct < min_mom:
            rejection_reasons.append(f"momentum {momentum_pct:.1f}% < {min_mom}")
        if volume_pct < min_vol:
            rejection_reasons.append(f"volume {volume_pct:.1f}% < {min_vol}")
        if volatility_pct < min_vlat:
            rejection_reasons.append(f"volatility {volatility_pct:.1f}% < {min_vlat}")
        if not trend_approved:
            rejection_reasons.append(f"drawdown bc={bc_dd:.1f}% cd={cd_dd:.1f}%")
        if not consecutive_met:
            rejection_reasons.append("consecutive_positives not met")

    return {
        "approved": approved,
        "momentum_pct": round(momentum_pct, 1),
        "volume_pct": round(volume_pct, 1),
        "volatility_pct": round(volatility_pct, 1),
        "trend_bc_drawdown": round(bc_dd, 2),
        "trend_cd_drawdown": round(cd_dd, 2),
        "trend_approved": trend_approved,
        "consecutive_positives": consecutive_met,
        "patterns": patterns,
        "rejection_reasons": rejection_reasons,
    }


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

def _detect_patterns(bars: List[Dict], baseline_periods: int) -> List[str]:
    patterns = []
    if len(bars) < 5:
        return patterns

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    volumes = [b["volume"] for b in bars]

    # Breakout: current close > max of prior N highs
    lookback = min(BREAKOUT_LOOKBACK, len(bars) - 1)
    prior_highs = highs[-(lookback + 1):-1]
    if prior_highs and closes[-1] > max(prior_highs):
        if volumes[-1] > _safe_mean(volumes[-(lookback + 1):-1]):
            patterns.append("breakout")

    # Higher highs: 2 of last 3 comparisons increasing
    check = min(HIGHER_HIGHS_BARS, len(bars))
    recent_highs = highs[-check:]
    if len(recent_highs) >= 3:
        up_count = sum(
            1 for i in range(1, len(recent_highs))
            if recent_highs[i] > recent_highs[i - 1]
        )
        if up_count >= len(recent_highs) - 2:
            patterns.append("higher_highs")

    # Volume expansion: current > 120% of baseline average
    if len(volumes) > baseline_periods:
        baseline_avg = _safe_mean(volumes[:baseline_periods])
        if baseline_avg > 0 and volumes[-1] > baseline_avg * VOLUME_EXPANSION_MULT:
            patterns.append("volume_expansion")

    return patterns


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _pct_changes(values: List[float]) -> List[float]:
    if len(values) < 2:
        return []
    return [(values[i] - values[i - 1]) / values[i - 1]
            for i in range(1, len(values)) if values[i - 1] != 0]


def _safe_mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _safe_std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _safe_mean(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance ** 0.5


def _max_drawdown(closes: List[float]) -> float:
    if len(closes) < 2:
        return 0.0
    worst = 0.0
    for i in range(1, len(closes)):
        peak = max(closes[:i])
        if closes[i] < peak and peak > 0:
            dd = ((peak - closes[i]) / peak) * 100.0
            worst = max(worst, dd)
    return worst


# ---------------------------------------------------------------------------
# Stage 0 filtering
# ---------------------------------------------------------------------------

def stage0_filter(
    symbol: str,
    snapshot: Dict,
    min_price: float,
    max_price: float,
    min_volume: float,
) -> Optional[str]:
    price = snapshot.get("price", 0)
    vol = snapshot.get("volume", 0)
    if price < min_price:
        return f"price {price} < {min_price}"
    if price > max_price:
        return f"price {price} > {max_price}"
    if vol < min_volume:
        return f"volume {vol} < {min_volume}"
    return None


# ---------------------------------------------------------------------------
# Main scan orchestrator
# ---------------------------------------------------------------------------

def _read_use_signal_led_psa(config_path: Optional[Path] = None) -> bool:
    """Read use_signal_led_psa from ops_config.json at runtime. Default True."""
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "ops_config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            return bool(data.get("use_signal_led_psa", True))
    except Exception:
        pass
    return True


def apply_signal_led_psa(scan_result: Dict[str, Any],
                         db_path: Optional[Path] = None,
                         config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Annotate PSA technically-approved signals with a signal-led verdict.

    Sprint 18 Item 1. For each technically-approved candidate, require a primary
    predictive trigger (UOA call surge or PEAD earnings beat in prime_signals)
    before APPROVED; technically-strong candidates without a trigger are WATCH
    (visible, not auto-approved). Adds trigger_source ('UOA_CALL'|'PEAD_BEAT'|
    'NONE') and approval_status ('APPROVED'|'WATCH') to each signal. When
    use_signal_led_psa is false, reverts to legacy technical-only (all APPROVED).

    Sprint 20 Item 2: after signal-led gating, apply DK three-state modifier.
    CONFIRMING -> WATCH promoted to STRONG; APPROVED gains dk_confirming flag.
    NULLIFYING -> signal SUPPRESSED (suppression_reason='DK_NULLIFYING').
    NEUTRAL -> pass through. dk_status and dk_conviction stamped on every signal.
    """
    from prime_intelligence.prime_signal_triggers import psa_trigger_source

    use_signal_led = _read_use_signal_led_psa(config_path)
    ref_ts = _parse_scan_time(scan_result.get("scan_time"))
    approved = watch = 0
    for sig in scan_result.get("signals", []):
        if use_signal_led:
            ts = psa_trigger_source(sig.get("symbol"), db_path=db_path, ref_ts=ref_ts)
        else:
            ts = "NONE"
        sig["trigger_source"] = ts
        if not use_signal_led or ts != "NONE":
            sig["approval_status"] = "APPROVED"
            approved += 1
        else:
            sig["approval_status"] = "WATCH"
            watch += 1
    scan_result["signal_led"] = use_signal_led
    scan_result["approved_count"] = approved
    scan_result["watch_count"] = watch
    # Sprint 20 Item 2: DK three-state modifier applied after signal-led gating.
    return _apply_dk_modifier_psa(scan_result, db_path)


def _apply_dk_modifier_psa(scan_result: Dict[str, Any],
                            db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Sprint 20 Item 2: Apply DK three-state modifier to PSA signals.

    Per the Sprint 20 DK reference table (long-signal effects):
      CONFIRMING  -> WATCH promoted to STRONG; if already APPROVED, add
                     dk_confirming=True flag for AI ranker weighting.
      NEUTRAL     -> signal passes through unchanged.
      NULLIFYING  -> signal suppressed: approval_status='SUPPRESSED',
                     suppression_reason='DK_NULLIFYING'.
    dk_status and dk_conviction are stamped on every signal dict (graceful
    degradation: missing DK data defaults to NEUTRAL).
    """
    try:
        from prime_intelligence.prime_dk_trader import get_dk_status
    except Exception:
        return scan_result  # DK layer unavailable; skip modifier silently

    confirming_cnt = nullified_cnt = watch_upgraded_cnt = 0
    for sig in scan_result.get("signals", []):
        symbol = (sig.get("symbol") or "").upper()
        try:
            verdict = get_dk_status(symbol, db_path)
        except Exception:
            verdict = {"dk_status": "NEUTRAL", "dk_conviction": None}
        dk_st = verdict.get("dk_status") or "NEUTRAL"
        dk_conv = verdict.get("dk_conviction")
        sig["dk_status"] = dk_st
        sig["dk_conviction"] = dk_conv

        if dk_st == "CONFIRMING":
            confirming_cnt += 1
            cur = sig.get("approval_status", "WATCH")
            if cur == "WATCH":
                sig["approval_status"] = "STRONG"
                watch_upgraded_cnt += 1
            else:
                sig["dk_confirming"] = True
        elif dk_st == "NULLIFYING":
            sig["approval_status"] = "SUPPRESSED"
            sig["suppression_reason"] = "DK_NULLIFYING"
            nullified_cnt += 1

    scan_result["dk_confirming_count"] = confirming_cnt
    scan_result["dk_nullified_count"] = nullified_cnt
    scan_result["dk_watch_upgraded"] = watch_upgraded_cnt
    return scan_result


def _parse_scan_time(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def run_psa_scan(
    api_key: str,
    universe: Optional[List[str]] = None,
    thresholds: Optional[Dict[str, float]] = None,
    baseline_periods: int = DEFAULT_BASELINE_PERIODS,
    long_periods: int = DEFAULT_LONG_PERIODS,
    short_periods: int = DEFAULT_SHORT_PERIODS,
    interval: str = DEFAULT_INTERVAL,
    required_positive: int = DEFAULT_REQUIRED_POSITIVE,
    bc_max_drawdown: float = DEFAULT_BC_MAX_DRAWDOWN,
    cd_max_drawdown: float = DEFAULT_CD_MAX_DRAWDOWN,
    min_price: float = DEFAULT_MIN_PRICE,
    max_price: float = DEFAULT_MAX_PRICE,
    min_daily_volume: float = DEFAULT_MIN_DAILY_VOLUME,
    db_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    scan_time = datetime.now()

    # CIL-070: graceful degradation. Without a Polygon key there is no data
    # source, so log a WARNING and return an empty (but well-formed) result
    # rather than fetching against an empty key or crashing the caller.
    if not (api_key or "").strip():
        logger.warning("PSA: Polygon unavailable — skipping scan")
        return {
            "scan_time": scan_time.isoformat(),
            "scanner": "prime_psa_scanner",
            "version": "1.0",
            "polygon_unavailable": True,
            "analyzed": 0,
            "signals_found": 0,
            "stage0_rejected": 0,
            "stage1_rejected": 0,
            "fetch_failures": 0,
            "signals": [],
            "stage0_rejections": [],
        }

    if universe is None:
        try:
            cfg = get_config()
            universe = resolve_psa_universe(
                mode=cfg.ops.psa_universe,
                custom=cfg.ops.psa_universe_custom,
                sector=cfg.ops.psa_universe_sector,
            )
        except Exception:
            universe = list(DEFAULT_UNIVERSE)
    if thresholds is None:
        thresholds = {
            "momentum": DEFAULT_MOMENTUM_THRESHOLD,
            "volume": DEFAULT_VOLUME_THRESHOLD,
            "volatility": DEFAULT_VOLATILITY_THRESHOLD,
        }

    total_bars = baseline_periods + long_periods + short_periods

    logger.info(
        "PSA SCAN -- %s  universe=%d  bars=%d (%s)  thresholds m=%.0f v=%.0f vl=%.0f",
        scan_time.strftime("%Y-%m-%d %H:%M ET"),
        len(universe),
        total_bars,
        interval,
        thresholds.get("momentum", 0),
        thresholds.get("volume", 0),
        thresholds.get("volatility", 0),
    )

    signals: List[Dict[str, Any]] = []
    stage0_rejected = 0
    stage0_rejections: List[Dict[str, Any]] = []
    stage1_rejected = 0
    analyzed = 0
    fetch_failures = 0

    for symbol in universe:
        time.sleep(0.15)
        bars = fetch_bars(symbol, interval, total_bars + 5, api_key)
        if not bars:
            fetch_failures += 1
            continue

        last_price = bars[-1]["close"] if bars else 0
        # Normalize bar-window volume to a full-day equivalent before comparing
        # against the daily volume threshold. A complete RTH session is 78 × 5-min
        # bars; the PSA window is 39 bars (~half day), so a raw sum would under-count
        # by ~2x. Dividing by len(bars) and multiplying by 78 gives the equivalent
        # daily pace regardless of how many bars the window actually contains.
        _FULL_DAY_BARS_5MIN = 78
        if bars:
            _raw_vol = sum(b.get("volume", 0) for b in bars)
            last_vol = _raw_vol * _FULL_DAY_BARS_5MIN / len(bars)
        else:
            last_vol = 0
        s0_reason = stage0_filter(symbol, {"price": last_price, "volume": last_vol},
                                   min_price, max_price, min_daily_volume)
        if s0_reason:
            logger.debug("Stage0 rejected %s: %s (price=%.2f vol=%.0f norm, raw=%.0f over %d bars)",
                         symbol, s0_reason, last_price, last_vol, _raw_vol if bars else 0, len(bars))
            stage0_rejected += 1
            stage0_rejections.append({"symbol": symbol, "reason": s0_reason,
                                      "scan_ts": scan_time.isoformat()})
            continue

        analyzed += 1
        result = analyze_symbol(
            bars, baseline_periods, long_periods, short_periods,
            required_positive, thresholds, bc_max_drawdown, cd_max_drawdown,
        )

        if not result["approved"]:
            stage1_rejected += 1
            continue

        price_at_scan = bars[-1]["close"] if bars else 0.0

        signals.append({
            "symbol": symbol,
            "price_at_scan": round(price_at_scan, 2),
            "direction": "LONG",
            "score": round(result["momentum_pct"], 1),
            "momentum_pct": result["momentum_pct"],
            "volume_pct": result["volume_pct"],
            "volatility_pct": result["volatility_pct"],
            "trend_bc_drawdown": result["trend_bc_drawdown"],
            "trend_cd_drawdown": result["trend_cd_drawdown"],
            "patterns": result["patterns"],
        })

    signals.sort(key=lambda s: s["score"], reverse=True)

    logger.info(
        "PSA complete: analyzed=%d approved=%d stage0_rejected=%d stage1_rejected=%d fetch_fail=%d",
        analyzed, len(signals), stage0_rejected, stage1_rejected, fetch_failures,
    )
    logger.info("APPROVED: %d stocks", len(signals))

    # Persist Stage0 rejections to prime_signals
    for rej in stage0_rejections:
        try:
            from prime_data.prime_db import write_stage0_rejection
            write_stage0_rejection(rej["symbol"], rej["reason"], rej["scan_ts"],
                                   strategy="PSA")
        except Exception as e:
            logger.debug("Stage0 rejection write failed for %s: %s", rej["symbol"], e)

    result = {
        "scan_time": scan_time.isoformat(),
        "scanner": "prime_psa_scanner",
        "version": "1.0",
        "interval": interval,
        "universe_size": len(universe),
        "total_bars": total_bars,
        "thresholds": thresholds,
        "analyzed": analyzed,
        "signals_found": len(signals),
        "stage0_rejected": stage0_rejected,
        "stage1_rejected": stage1_rejected,
        "fetch_failures": fetch_failures,
        "signals": signals,
        "stage0_rejections": stage0_rejections,
    }
    # Sprint 18 Item 1: signal-led approval gating (UOA call / PEAD beat trigger
    # required to reach APPROVED; technical-only -> WATCH). Toggle via ops_config.
    return apply_signal_led_psa(result, db_path=db_path, config_path=config_path)


def save_results(scan_data: Dict) -> Path:
    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"psa_scan_{ts}_ET.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=2, default=str)
    logger.info("Results saved: %s", out)
    return out


def main():
    import argparse

    parser = argparse.ArgumentParser(description="PRIME v1.0 PSA Momentum Scanner")
    # Stage 1 thresholds — None means "read from ops_config"
    parser.add_argument("--momentum", type=float, default=None)
    parser.add_argument("--volume", type=float, default=None)
    parser.add_argument("--volatility", type=float, default=None)
    parser.add_argument("--interval", type=str, default=DEFAULT_INTERVAL)
    # Stage 0 thresholds — None means "read from ops_config"
    parser.add_argument("--min-price", type=float, default=None, dest="min_price")
    parser.add_argument("--max-price", type=float, default=None, dest="max_price")
    parser.add_argument("--min-volume", type=float, default=None, dest="min_volume")
    # Per-role drawdown: 'primary' (strict 3%) or 'confirmation' (loose 5%)
    parser.add_argument("--role", type=str, default="primary",
                        choices=["primary", "confirmation"])
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [PSA] %(levelname)s %(message)s",
    )

    cfg = get_config()
    api_key = cfg.polygon_api_key
    if not api_key:
        # CIL-070: graceful degradation — warn and exit 0 (no crash) so the
        # scheduler/subprocess records a clean skip rather than an error.
        logger.warning("PSA: Polygon unavailable — skipping scan")
        return

    from prime_data.prime_db import init_db, log_ops_event

    init_db()

    log_ops_event("SCAN_START", "psa_scanner", detail=f"interval={args.interval}")

    universe = resolve_psa_universe(
        mode=cfg.ops.psa_universe,
        custom=cfg.ops.psa_universe_custom,
        sector=cfg.ops.psa_universe_sector,
    )

    # Stage 1 thresholds: CLI args override ops_config values
    momentum_t = args.momentum if args.momentum is not None else cfg.ops.psa_stage1_momentum
    volume_t = args.volume if args.volume is not None else cfg.ops.psa_stage1_volume
    volatility_t = args.volatility if args.volatility is not None else cfg.ops.psa_stage1_volatility

    # Stage 0 thresholds: CLI args override ops_config values
    min_price = args.min_price if args.min_price is not None else cfg.ops.psa_min_price
    max_price = args.max_price if args.max_price is not None else cfg.ops.psa_max_price
    min_vol = args.min_volume if args.min_volume is not None else cfg.ops.psa_min_daily_volume

    # Per-role drawdown: confirmation mode uses looser BC/CD drawdown tolerance
    if args.role == "confirmation":
        bc_dd = cfg.ops.psa_confirmation_bc_drawdown
        cd_dd = cfg.ops.psa_confirmation_cd_drawdown
    else:
        bc_dd = cfg.ops.psa_stage1_bc_drawdown
        cd_dd = cfg.ops.psa_stage1_cd_drawdown

    scan_data = run_psa_scan(
        api_key=api_key,
        universe=universe,
        thresholds={"momentum": momentum_t, "volume": volume_t, "volatility": volatility_t},
        interval=args.interval,
        min_price=min_price,
        max_price=max_price,
        min_daily_volume=min_vol,
        bc_max_drawdown=bc_dd,
        cd_max_drawdown=cd_dd,
    )

    print(f"\nPSA Scan: analyzed={scan_data['analyzed']} "
          f"approved={scan_data['signals_found']} "
          f"rejected={scan_data['stage1_rejected']}")
    print(f"APPROVED: {scan_data['signals_found']} stocks")
    for s in scan_data["signals"]:
        pats = ",".join(s["patterns"]) if s["patterns"] else "-"
        print(
            f"  {s['symbol']:<6} mom={s['momentum_pct']:5.1f}%  "
            f"vol={s['volume_pct']:5.1f}%  vlat={s['volatility_pct']:5.1f}%  "
            f"pat={pats}"
        )

    save_results(scan_data)

    log_ops_event(
        "SCAN_COMPLETE",
        "psa_scanner",
        detail=f"signals={scan_data['signals_found']}",
    )


if __name__ == "__main__":
    main()
