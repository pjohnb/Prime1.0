"""
PRIME v1.0 Index Strategy Scanner (CIL-PRIME-IDX-001).

Scans SPY, QQQ, IWM for unusual options activity and momentum signals
on index instruments. Standalone, headless, runnable from command line.

Standalone: python prime_scanners/prime_index_scanner.py
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config

logger = logging.getLogger(__name__)

INDEX_TARGETS = ["SPY", "QQQ", "IWM"]

POLYGON_BASE = "https://api.polygon.io"
API_TIMEOUT = 10


def _polygon_delay():
    """Sleep between Polygon API calls per ops_config rate-limit settings."""
    try:
        from prime_config.prime_config import get_config
        import json
        from pathlib import Path
        ops = Path(__file__).resolve().parent.parent / "ops_config.json"
        cfg = json.loads(ops.read_text())
        plan = cfg.get("polygon_plan", "free")
        delay_ms = 100 if plan == "paid" else int(cfg.get("polygon_rate_limit_delay_ms", 13000))
        time.sleep(delay_ms / 1000)
    except Exception:
        time.sleep(0.5)  # safe default


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
        logger.warning("Polygon %s -> HTTP %s", endpoint, r.status_code)
        return None
    except Exception as e:
        logger.warning("Polygon error %s: %s", endpoint, e)
        return None


def fetch_snapshot(symbol: str, api_key: str) -> Optional[Dict]:
    """Fetch current-day snapshot for an index ETF."""
    data = _polygon_get(
        f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}",
        {},
        api_key,
    )
    if data and data.get("ticker"):
        return data["ticker"]
    return None


def fetch_options_flow(symbol: str, api_key: str) -> Optional[Dict]:
    """Fetch options activity summary for an index ETF.
    Returns call/put volumes and sizzle-like metrics.
    """
    data = _polygon_get(
        f"/v3/snapshot/options/{symbol}",
        {"limit": 250},
        api_key,
    )
    if not data or not data.get("results"):
        return None

    call_vol = put_vol = 0
    call_oi = put_oi = 0

    for opt in data["results"]:
        details = opt.get("details", {})
        day = opt.get("day", {})
        oi = opt.get("open_interest", 0)
        vol = day.get("volume", 0)

        if details.get("contract_type") == "call":
            call_vol += vol
            call_oi += oi
        elif details.get("contract_type") == "put":
            put_vol += vol
            put_oi += oi

    total_vol = call_vol + put_vol
    put_call_ratio = (put_vol / call_vol) if call_vol > 0 else 0.0

    return {
        "call_volume": call_vol,
        "put_volume": put_vol,
        "total_volume": total_vol,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "put_call_ratio": round(put_call_ratio, 3),
    }


def evaluate_index_signal(
    symbol: str,
    snapshot: Dict,
    options: Optional[Dict],
) -> Dict[str, Any]:
    """Evaluate an index instrument for signal generation."""
    day = snapshot.get("day", {})
    prev = snapshot.get("prevDay", {})

    close = day.get("c", 0) or day.get("l", 0)
    prev_close = prev.get("c", 0)
    day_change_pct = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
    volume = day.get("v", 0)
    avg_volume = prev.get("v", 0)
    volume_ratio = (volume / avg_volume) if avg_volume > 0 else 1.0

    score = 0.0
    direction = "LONG"
    factors = []

    if options:
        pcr = options.get("put_call_ratio", 0.5)
        if pcr > 1.5:
            direction = "SHORT"
            score += 3.0
            factors.append(f"High put/call ratio {pcr:.2f} -> bearish index signal")
        elif pcr < 0.4:
            direction = "LONG"
            score += 2.0
            factors.append(f"Low put/call ratio {pcr:.2f} -> bullish index signal")

        total_vol = options.get("total_volume", 0)
        if total_vol > 500000:
            score += 2.0
            factors.append(f"Heavy options volume {total_vol:,}")

    if abs(day_change_pct) > 1.0:
        score += 1.5
        factors.append(f"Index move {day_change_pct:+.1f}% today")
        if day_change_pct < -1.0 and direction == "SHORT":
            score += 1.0

    if volume_ratio > 1.5:
        score += 1.0
        factors.append(f"Volume {volume_ratio:.1f}x average")

    signal_generated = score >= 5.0

    return {
        "symbol": symbol,
        "price_at_scan": round(close, 2),
        "session_open_price": round(day.get("o", 0), 2),
        "day_change_pct": round(day_change_pct, 2),
        "volume": volume,
        "volume_ratio": round(volume_ratio, 2),
        "direction": direction,
        "score": round(score, 1),
        "signal": signal_generated,
        "factors": factors,
        "options": options or {},
        "strategy": "IDX",
    }


def run_index_scan(api_key: str) -> Dict[str, Any]:
    """Scan all index targets and return results."""
    scan_time = datetime.now()
    signals = []
    all_results = {}

    logger.info("INDEX SCAN -- %s", scan_time.strftime("%Y-%m-%d %H:%M ET"))

    for symbol in INDEX_TARGETS:
        snapshot = fetch_snapshot(symbol, api_key)
        if not snapshot:
            logger.warning("%s: no snapshot data", symbol)
            all_results[symbol] = {"error": "No snapshot data"}
            continue

        options = fetch_options_flow(symbol, api_key)
        _polygon_delay()

        result = evaluate_index_signal(symbol, snapshot, options)
        all_results[symbol] = result

        if result["signal"]:
            signals.append(result)
            logger.info(
                "%s: SIGNAL %s score=%.1f dir=%s",
                symbol, symbol, result["score"], result["direction"],
            )
        else:
            logger.info(
                "%s: no signal (score=%.1f, threshold=5.0)",
                symbol, result["score"],
            )

    return {
        "scan_time": scan_time.isoformat(),
        "scanner": "prime_index_scanner",
        "version": "1.0",
        "targets": INDEX_TARGETS,
        "signals": signals,
        "results": all_results,
    }


def save_results(scan_data: Dict) -> Path:
    cfg = get_config()
    out_dir = cfg.scan_results_dir
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"idx_scan_{ts}_ET.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(scan_data, f, indent=2)
    logger.info("Results saved: %s", out)
    return out


# ---------------------------------------------------------------------------
# Index UOA Pipeline (CIL-PRIME-IDX-001 Sprint 10)
# ---------------------------------------------------------------------------

def run_index_uoa_scan(
    market_data: Optional[List[Dict]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run index UOA pipeline: scan -> nullifier checks -> write to prime_signals."""
    from prime_scanners.prime_index_uoa import scan_index_uoa

    result = {
        "scanned": 0,
        "approved": [],
        "nullified": [],
        "errors": [],
        "timestamp": datetime.now().isoformat(),
    }

    raw_signals = scan_index_uoa(market_data)
    result["scanned"] = len(raw_signals) if market_data else 0

    for signal in raw_signals:
        symbol = signal["symbol"]
        direction = signal["direction"]

        # SRS regime nullifier
        srs_null = _check_srs_regime(direction)
        if srs_null:
            result["nullified"].append({"symbol": symbol, "reason": srs_null})
            continue

        # DK nullifier
        dk_null = _check_dk_status(symbol)
        if dk_null:
            result["nullified"].append({"symbol": symbol, "reason": dk_null})
            continue

        try:
            _write_index_signal(signal, db_path)
            result["approved"].append({
                "symbol": symbol, "direction": direction,
                "tier": signal["tier"], "score": signal["score"],
            })
        except Exception as e:
            result["errors"].append({"symbol": symbol, "error": str(e)})

    logger.info("Index UOA: %d raw, %d approved, %d nullified",
                len(raw_signals), len(result["approved"]), len(result["nullified"]))
    return result


def _check_srs_regime(direction: str) -> Optional[str]:
    """BROAD_DECLINE suppresses LONG; BROAD_RALLY suppresses SHORT."""
    try:
        from prime_scanners.prime_srs_scanner import get_broad_regime
        regime = get_broad_regime()
        if regime == "BROAD_DECLINE" and direction == "LONG":
            return "SRS BROAD_DECLINE suppresses LONG index signal"
        if regime == "BROAD_RALLY" and direction == "SHORT":
            return "SRS BROAD_RALLY suppresses SHORT index signal"
    except (ImportError, AttributeError):
        pass
    return None


def _check_dk_status(symbol: str) -> Optional[str]:
    try:
        from prime_intelligence.prime_dark_pool import score_dk_signal
        dk = score_dk_signal(symbol)
        if dk["dk_status"] == "NULLIFYING":
            return f"DK NULLIFYING: {dk['detail'].get('reason', 'dark pool')}"
    except Exception:
        pass
    return None


def _write_index_signal(signal: Dict[str, Any], db_path: Optional[Path] = None) -> None:
    from prime_analytics.prime_signals_db import init_signals_table, insert_signal
    from prime_intelligence.prime_portfolio_factor import sector_map

    init_signals_table(db_path)
    insert_signal(
        symbol=signal["symbol"],
        strategy="UOA_INDEX",
        scan_ts=datetime.now().isoformat(),
        entry_price=signal.get("price_at_scan", 0),
        score=signal.get("score", 0),
        sector=sector_map(signal["symbol"]),
        tier=signal.get("tier", ""),
        direction=signal.get("direction", "LONG"),
        factors=json.dumps(signal.get("factors", {})),
        db_path=db_path,
    )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [IDX] %(levelname)s %(message)s",
    )

    cfg = get_config()
    api_key = cfg.polygon_api_key
    if not api_key:
        logger.error("polygon_api_key not found in config.json")
        sys.exit(1)

    scan_data = run_index_scan(api_key)

    print(f"\nIndex Scan: {len(scan_data['signals'])} signals generated")
    for sym, data in scan_data["results"].items():
        if isinstance(data, dict) and "score" in data:
            sig = "[SIGNAL]" if data.get("signal") else ""
            print(f"  {sym}: {data['direction']} score={data['score']:.1f} "
                  f"day={data.get('day_change_pct', 0):+.1f}% {sig}")

    save_results(scan_data)


if __name__ == "__main__":
    main()
