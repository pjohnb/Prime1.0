"""
PSA Settings Optimizer — Diagnostic Scan + PCA Scoring + Threshold Back-Calculation.

WO-PRIME-PSA-CALIBRATION-02 Phase 2.

Entry points:
  run_psa_diagnostic_scan()   — full 503-symbol factor-matrix scan (gates suspended)
  compute_pca_scores()        — sklearn PCA → composite scores + factor weights
  compute_recommended_thresholds() — top-N min/max back-calc
  compute_gap_analysis()      — compare top-N against last live scan data
  load_diagnostic_results()   — read psa_diagnostic_latest.json
"""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DIAG_RESULT_FILENAME = "psa_diagnostic_latest.json"

FACTOR_COLS = [
    "momentum_pct",
    "volume_pct",
    "volatility_pct",
    "bc_drawdown",
    "cd_drawdown",
    "price",
    "daily_volume",
]

_FULL_DAY_BARS_5MIN = 78


# ---------------------------------------------------------------------------
# Diagnostic worker
# ---------------------------------------------------------------------------

def _diag_one(
    symbol: str,
    interval: str,
    total_bars: int,
    api_key: str,
    baseline_periods: int,
    long_periods: int,
    short_periods: int,
) -> tuple:
    """Fetch bars and compute the complete factor vector for one symbol.

    Gates are suspended: no Stage 0 or Stage 1 filter is applied.
    Returns (symbol, factor_dict | None).  None means insufficient data.
    """
    from prime_scanners.prime_psa_scanner import fetch_bars, analyze_symbol

    bars = fetch_bars(symbol, interval, total_bars + 5, api_key)
    if not bars:
        return symbol, None

    last_price = bars[-1]["close"]
    raw_vol = sum(b.get("volume", 0) for b in bars)
    daily_volume = raw_vol * _FULL_DAY_BARS_5MIN / len(bars)

    # Use permissive thresholds so analyze_symbol never gates on them.
    permissive = {"momentum": 0.0, "volume": 0.0, "volatility": 0.0}
    result = analyze_symbol(
        bars,
        baseline_periods,
        long_periods,
        short_periods,
        required_positive=1,   # minimal bar to avoid artificially failing
        thresholds=permissive,
        bc_max_drawdown=999.0,
        cd_max_drawdown=999.0,
    )

    # Skip symbols where analysis cannot produce factor values.
    if "momentum_pct" not in result:
        return symbol, None

    return symbol, {
        "symbol": symbol,
        "price": round(last_price, 4),
        "daily_volume": round(daily_volume, 0),
        "momentum_pct": round(result.get("momentum_pct", 0.0), 4),
        "volume_pct": round(result.get("volume_pct", 0.0), 4),
        "volatility_pct": round(result.get("volatility_pct", 0.0), 4),
        "bc_drawdown": round(result.get("trend_bc_drawdown", 0.0), 4),
        "cd_drawdown": round(result.get("trend_cd_drawdown", 0.0), 4),
    }


# ---------------------------------------------------------------------------
# Diagnostic scan orchestrator
# ---------------------------------------------------------------------------

def run_psa_diagnostic_scan(
    api_key: str,
    universe: Optional[List[str]] = None,
    workers: int = 10,
    scan_results_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run all symbols with gates suspended; build and save the factor matrix.

    The result is saved to psa_diagnostic_latest.json in scan_results_dir
    and also returned.
    """
    from prime_config.prime_config import get_config
    from prime_scanners.prime_psa_scanner import (
        DEFAULT_INTERVAL, DEFAULT_BASELINE_PERIODS, DEFAULT_LONG_PERIODS,
        DEFAULT_SHORT_PERIODS, resolve_psa_universe,
    )

    cfg = get_config()

    if universe is None:
        universe = resolve_psa_universe(
            mode=cfg.ops.psa_universe,
            custom=cfg.ops.psa_universe_custom,
            sector=cfg.ops.psa_universe_sector,
        )

    if scan_results_dir is None:
        scan_results_dir = cfg.scan_results_dir

    interval = DEFAULT_INTERVAL
    total_bars = DEFAULT_BASELINE_PERIODS + DEFAULT_LONG_PERIODS + DEFAULT_SHORT_PERIODS
    run_ts = datetime.utcnow().isoformat()

    logger.info("PSA DIAGNOSTIC SCAN start: %d symbols (gates suspended)", len(universe))

    factor_rows: List[Dict[str, Any]] = []
    skipped = 0

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="psa_diag") as pool:
        futures = {
            pool.submit(
                _diag_one,
                sym, interval, total_bars, api_key,
                DEFAULT_BASELINE_PERIODS, DEFAULT_LONG_PERIODS, DEFAULT_SHORT_PERIODS,
            ): sym
            for sym in universe
        }
        for fut in as_completed(futures):
            try:
                sym, data = fut.result()
            except Exception as exc:
                logger.debug("PSA diag worker error: %s", exc)
                skipped += 1
                continue
            if data is None:
                skipped += 1
            else:
                factor_rows.append(data)

    logger.info(
        "PSA DIAGNOSTIC SCAN complete: %d factor rows, %d skipped",
        len(factor_rows), skipped,
    )

    result = {
        "run_timestamp": run_ts,
        "universe_size": len(universe),
        "factor_rows": len(factor_rows),
        "skipped": skipped,
        "factors": factor_rows,
    }

    # Persist to scan_results_dir
    try:
        scan_results_dir.mkdir(parents=True, exist_ok=True)
        out = scan_results_dir / DIAG_RESULT_FILENAME
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        logger.info("Diagnostic results saved: %s", out)
    except Exception as e:
        logger.warning("Could not save diagnostic results: %s", e)

    return result


# ---------------------------------------------------------------------------
# PCA scoring
# ---------------------------------------------------------------------------

def compute_pca_scores(factor_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply sklearn PCA (n_components=1) to the factor matrix.

    Returns:
      weights  — dict of factor_name → PC1 loading (rounded to 4 dp)
      ranked   — list of factor rows annotated with pca_score, sorted desc
    """
    import numpy as np
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    if not factor_rows:
        return {"weights": {}, "ranked": []}

    X = np.array([[r.get(c, 0.0) for c in FACTOR_COLS] for r in factor_rows], dtype=float)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    pca = PCA(n_components=1, random_state=42)
    scores_2d = pca.fit_transform(X_scaled)
    raw_scores = scores_2d.flatten()

    loadings = pca.components_[0]
    weights = {col: round(float(loadings[i]), 4) for i, col in enumerate(FACTOR_COLS)}

    ranked = []
    for i, row in enumerate(factor_rows):
        ranked.append({**row, "pca_score": round(float(raw_scores[i]), 4)})
    ranked.sort(key=lambda r: r["pca_score"], reverse=True)

    return {"weights": weights, "ranked": ranked}


# ---------------------------------------------------------------------------
# Threshold back-calculation
# ---------------------------------------------------------------------------

def compute_recommended_thresholds(
    ranked: List[Dict[str, Any]],
    target_n: int,
) -> Dict[str, float]:
    """Derive Stage 0 and Stage 1 thresholds that admit the top-N symbols.

    Lower-bound thresholds (min_price, min_daily_volume, momentum, volume,
    volatility) are set to the minimum value in the top-N set — the least
    qualified symbol still passes.

    Upper-bound thresholds (max_price, bc_drawdown, cd_drawdown) are set to
    the maximum value in the top-N set — the most extreme symbol still passes.

    The operator can edit any value before applying.
    """
    top = ranked[:target_n]
    if not top:
        return {}

    prices = [r["price"] for r in top]
    volumes = [r["daily_volume"] for r in top]
    momentums = [r["momentum_pct"] for r in top]
    vol_pcts = [r["volume_pct"] for r in top]
    vlats = [r["volatility_pct"] for r in top]
    bc_dds = [r["bc_drawdown"] for r in top]
    cd_dds = [r["cd_drawdown"] for r in top]

    return {
        # Stage 0
        "psa_min_price": round(min(prices), 2),
        "psa_max_price": round(max(prices) * 1.1, 0),  # 10 % headroom above top-N max
        "psa_min_daily_volume": round(min(volumes), 0),
        # Stage 1 lower bounds
        "psa_stage1_momentum": round(min(momentums), 1),
        "psa_stage1_volume": round(min(vol_pcts), 1),
        "psa_stage1_volatility": round(min(vlats), 1),
        # Stage 1 upper bounds (drawdown = max allowed; set to worst in top-N)
        "psa_stage1_bc_drawdown": round(max(bc_dds), 2),
        "psa_stage1_cd_drawdown": round(max(cd_dds), 2),
    }


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------

def compute_gap_analysis(
    top_n_symbols: List[str],
    db_path: Optional[Path] = None,
    scan_results_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compare top-N diagnostic symbols against the most recent live PSA scan.

    For each symbol in top_n_symbols, classify as:
      'approved'                 — symbol appears in the last live scan signals
      'blocked_stage0'           — symbol is in psa_stage0_rejections (last run)
      'blocked_stage1_unknown'   — symbol is absent from both (Stage 1 reject or no scan data)
    """
    from prime_data.prime_db import get_connection, init_psa_stage0_rejections_table
    import glob

    # --- Stage 0 rejections from DB ---
    stage0_rejected: set = set()
    try:
        init_psa_stage0_rejections_table(db_path)
        with get_connection(db_path) as conn:
            last = conn.execute(
                "SELECT run_timestamp FROM psa_stage0_rejections "
                "ORDER BY run_timestamp DESC LIMIT 1"
            ).fetchone()
            if last:
                rows = conn.execute(
                    "SELECT symbol FROM psa_stage0_rejections WHERE run_timestamp=?",
                    (last[0],),
                ).fetchall()
                stage0_rejected = {r[0] for r in rows}
    except Exception as e:
        logger.debug("Gap analysis: stage0 DB query failed: %s", e)

    # --- Approved signals from last PSA scan JSON ---
    approved_symbols: set = set()
    if scan_results_dir:
        try:
            files = sorted(glob.glob(str(scan_results_dir / "psa_scan_*.json")))
            if files:
                data = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
                approved_symbols = {s["symbol"] for s in data.get("signals", [])}
        except Exception as e:
            logger.debug("Gap analysis: scan JSON read failed: %s", e)

    results = []
    for sym in top_n_symbols:
        if sym in approved_symbols:
            status = "approved"
        elif sym in stage0_rejected:
            status = "blocked_stage0"
        else:
            status = "blocked_stage1_unknown"
        results.append({"symbol": sym, "live_scan_status": status})

    blocked_s0 = sum(1 for r in results if r["live_scan_status"] == "blocked_stage0")
    blocked_s1 = sum(1 for r in results if r["live_scan_status"] == "blocked_stage1_unknown")
    approved_cnt = sum(1 for r in results if r["live_scan_status"] == "approved")

    return {
        "symbols": results,
        "blocked_stage0": blocked_s0,
        "blocked_stage1_unknown": blocked_s1,
        "currently_approved": approved_cnt,
    }


# ---------------------------------------------------------------------------
# Load persisted diagnostic results
# ---------------------------------------------------------------------------

def load_diagnostic_results(scan_results_dir: Optional[Path] = None) -> Optional[Dict]:
    """Load psa_diagnostic_latest.json, or return None if not found."""
    if scan_results_dir is None:
        from prime_config.prime_config import get_config
        scan_results_dir = get_config().scan_results_dir

    path = scan_results_dir / DIAG_RESULT_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not load diagnostic results: %s", e)
        return None
