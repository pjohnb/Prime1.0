"""
PRIME v1.0 Trade Factor Registry (CIL-PRIME-TF-001).

Evaluates trade factors for all strategies: UOA, PEAD, MMR, SRS, IDX.
Each strategy produces a five-category evaluation per TIP Section 2:
  1. Duration Classifiers (ST/MT/LT)
  2. Entry Modifiers (IMMEDIATE_FULL/IMMEDIATE_HALF/WAIT/SCALED)
  3. Exit Triggers (list of armed triggers)
  4. Nullifiers (CLEAR/SUSPECT/NULLIFIED via DK-001 integration)
  5. Trade Maintenance (flags for ongoing monitoring)

No GUI imports. Pure evaluation logic.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_intelligence.prime_dark_pool import DarkPoolEvaluation, DarkPoolScanner

logger = logging.getLogger(__name__)

_dark_pool_scanner = DarkPoolScanner()


@dataclass
class TradeFactorEvaluation:
    """Complete factor evaluation for a single signal."""
    strategy: str
    symbol: str
    timestamp: str = ""
    direction: str = "LONG"

    # Category 1: Duration Classifiers
    duration_class: str = "ST"
    duration_confidence: str = "MEDIUM"
    duration_rationale: str = ""

    # Category 2: Entry Modifiers
    entry_method: str = "IMMEDIATE_FULL"
    entry_trigger: str = ""
    entry_rationale: str = ""

    # Category 3: Exit Triggers
    exit_triggers: List[Dict[str, str]] = field(default_factory=list)

    # Category 4: Nullifiers
    nullifier_status: str = "CLEAR"
    nullifier_flags: List[str] = field(default_factory=list)
    nullifier_rationale: str = ""
    dark_pool_eval: Optional[Dict[str, Any]] = None

    # Category 5: Trade Maintenance
    maintenance_flags: List[str] = field(default_factory=list)

    # Score
    signal_score: float = 0.0
    normalized_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "direction": self.direction,
            "duration": {
                "class": self.duration_class,
                "confidence": self.duration_confidence,
                "rationale": self.duration_rationale,
            },
            "entry": {
                "method": self.entry_method,
                "trigger": self.entry_trigger,
                "rationale": self.entry_rationale,
            },
            "exit_triggers": self.exit_triggers,
            "nullifier": {
                "status": self.nullifier_status,
                "flags": self.nullifier_flags,
                "rationale": self.nullifier_rationale,
            },
            "dark_pool_eval": self.dark_pool_eval,
            "maintenance_flags": self.maintenance_flags,
            "signal_score": self.signal_score,
            "normalized_score": self.normalized_score,
        }


def _classify_duration(signal: Dict[str, Any], strategy: str) -> tuple:
    """Determine duration class based on strategy-specific heuristics."""
    dte = signal.get("weighted_dte", 0)

    if strategy == "UOA":
        if dte <= 10:
            return "ST", "HIGH", f"Weighted DTE {dte}d <= 10 -> short-term"
        elif dte <= 30:
            return "MT", "HIGH", f"Weighted DTE {dte}d in 11-30 range -> medium-term"
        else:
            return "LT", "HIGH", f"Weighted DTE {dte}d > 30 -> long-term institutional thesis"
    elif strategy == "PEAD":
        days_since = signal.get("days_since_earnings", 0)
        if days_since <= 3:
            return "ST", "HIGH", f"PEAD {days_since}d post-earnings -> immediate drift window"
        elif days_since <= 10:
            return "MT", "MEDIUM", f"PEAD {days_since}d post-earnings -> extended drift"
        else:
            return "LT", "LOW", f"PEAD {days_since}d post-earnings -> late drift, lower conviction"
    elif strategy == "MMR":
        return "MT", "MEDIUM", "Metals thesis: sector rotation timing is medium-term"
    elif strategy == "SRS":
        phase = signal.get("sector_phase", "STABLE")
        if phase == "RECOVERING":
            return "MT", "HIGH", "SRS recovery confirmed -> medium-term sector rotation play"
        elif phase == "BOTTOMING":
            return "LT", "LOW", "SRS bottoming -> early, lower confidence"
        else:
            return "ST", "LOW", f"SRS phase={phase} -> short-term only"
    elif strategy == "IDX":
        return "ST", "MEDIUM", "Index strategy: typically short-term momentum"
    else:
        return "MT", "LOW", f"Unknown strategy {strategy} -> default MT"


# CALC-TRADE_FACTORS_ML-3: UOA's STRONG_THRESHOLD (5.0x sizzle) lands at
# exactly 80 (the IMMEDIATE_FULL threshold below); WATCH_THRESHOLD (4.0x)
# lands at 64 (the SCALED/FULL band).
UOA_SIZZLE_NORMALIZE_SCALE = 16.0


def _normalize_score(strategy: str, score: float) -> float:
    """Normalize a strategy's raw score to a common 0-100 scale.

    Raw scanner score scales are wildly inconsistent: PSA's momentum_pct is
    an unbounded percentage observed up to ~1000; UOA's sizzle_index is an
    unbounded ratio. MTFA/IDX/PEAD/MMR/SRS scores are already effectively
    0-100 (or a small fixed tier ceiling), so they pass through unchanged.
    Without this, IMMEDIATE_HALF/SCALED were structurally unreachable for
    PEAD/MMR (always >=6-8x the old 0-10 thresholds) and IMMEDIATE_FULL was
    rare for IDX (small additive counter, max ~8.5).
    """
    if strategy == "PSA":
        normalized = score / 10.0
    elif strategy == "UOA":
        normalized = score * UOA_SIZZLE_NORMALIZE_SCALE
    else:
        normalized = score
    return max(0.0, min(normalized, 100.0))


def _determine_entry(signal: Dict[str, Any], duration: str, strategy: str) -> tuple:
    """Determine entry method based on signal characteristics."""
    score = signal.get("score", 0.0)
    normalized_score = _normalize_score(strategy, score)
    session = signal.get("session_type", "REGULAR")

    if session in ("PRE_MARKET", "AFTER_HOURS"):
        return ("WAIT", "market_open",
                f"Signal in {session} -- wait for regular session confirmation", normalized_score)

    if normalized_score >= 80.0:
        return ("IMMEDIATE_FULL", "",
                f"High conviction (score={score}, normalized={normalized_score:.1f}) -> full entry",
                normalized_score)
    elif normalized_score >= 60.0:
        if duration == "LT":
            return ("SCALED", "tranche_2_on_confirmation", (
                f"Score={score} (normalized={normalized_score:.1f}) on LT thesis -- "
                f"enter half, add on confirmation"
            ), normalized_score)
        return ("IMMEDIATE_FULL", "",
                f"Score={score} (normalized={normalized_score:.1f}) -> full entry",
                normalized_score)
    else:
        return ("IMMEDIATE_HALF", "",
                f"Moderate conviction (score={score}, normalized={normalized_score:.1f}) -> half position",
                normalized_score)


def _build_exit_triggers(signal: Dict[str, Any], strategy: str, duration: str) -> List[Dict[str, str]]:
    """Build the exit trigger list for the strategy."""
    triggers = []
    entry_price = signal.get("price_at_scan", 0.0)

    if entry_price > 0:
        stop_pct = {"ST": 2.0, "MT": 3.5, "LT": 5.0}.get(duration, 3.0)
        stop_price = round(entry_price * (1 - stop_pct / 100), 2)
        triggers.append({
            "type": "STOP_LOSS",
            "status": "ARMED",
            "value": str(stop_price),
            "description": f"{stop_pct}% stop at ${stop_price}",
        })

        target_pct = {"ST": 3.0, "MT": 6.0, "LT": 10.0}.get(duration, 5.0)
        target_price = round(entry_price * (1 + target_pct / 100), 2)
        triggers.append({
            "type": "PRICE_TARGET",
            "status": "ARMED",
            "value": str(target_price),
            "description": f"{target_pct}% target at ${target_price}",
        })

    time_stops = {"ST": "3 trading days", "MT": "10 trading days", "LT": "30 trading days"}
    triggers.append({
        "type": "TIME_STOP",
        "status": "ARMED",
        "value": time_stops.get(duration, "10 trading days"),
        "description": f"Duration-based time stop: {time_stops.get(duration, '10 trading days')}",
    })

    if strategy == "PEAD":
        triggers.append({
            "type": "DRIFT_DECAY",
            "status": "ARMED",
            "value": "drift_score < 3.0",
            "description": "PEAD drift score decay below threshold",
        })
    elif strategy == "SRS":
        triggers.append({
            "type": "REGIME_FLIP",
            "status": "ARMED",
            "value": "sector_phase != RECOVERING",
            "description": "SRS sector phase flips away from RECOVERING",
        })
    elif strategy == "MMR":
        triggers.append({
            "type": "RATIO_REVERSAL",
            "status": "ARMED",
            "value": "gold_silver_ratio_reversal",
            "description": "Gold/Silver ratio reverses against thesis",
        })
    elif strategy == "IDX":
        triggers.append({
            "type": "SMA_BREAK",
            "status": "ARMED",
            "value": "price < SMA_20",
            "description": "Index breaks below 20-day SMA",
        })

    return triggers


# CALC-TRADE_FACTORS_ML-2: TIP Section 2.3 nullifier rules for the factors
# that were unwired or dead code -- only the dark-pool nullifier was
# load-bearing before this fix.
_STATUS_RANK = {"CLEAR": 0, "SUSPECT": 1, "NULLIFIED": 2}


def _check_covered_call_nullifier(signal: Dict[str, Any]) -> tuple:
    """Covered-call: vol/OI < 1.5 + strikes clustered within 2% above price ->
    nullifier for ST, SUSPECT for LT (TIP Section 2.3). UOA's own
    detect_covered_call() already computes this exactly; it was just never
    read outside UOA's own CLI print. Not recomputed here -- reads the result
    UOA already attached to the signal dict as covered_call_eval."""
    cc = signal.get("covered_call_eval") or {}
    status = cc.get("status")
    if status in ("NULLIFIED", "SUSPECT"):
        return status, "COVERED_CALL", cc.get("rationale", "Covered-call pattern detected")
    return "CLEAR", None, None


def _check_contradictory_signal_nullifier(
    symbol: str, strategy: str, signal: Dict[str, Any], db_path: Optional[Path],
) -> tuple:
    """An active opposing-direction signal from another PRIME strategy on the
    same symbol is a nullifier (TIP Section 2.3), e.g. IDX LONG + UOA PUT."""
    direction = (signal.get("direction") or "LONG").upper()
    try:
        from prime_analytics.prime_signals_db import get_signals
        others = get_signals(symbol=symbol, db_path=db_path, limit=50)
    except Exception as e:
        logger.debug("Contradictory-signal check skipped for %s: %s", symbol, e)
        return "CLEAR", None, None

    for other in others:
        if (other.get("strategy") or "").upper() == strategy:
            continue
        other_direction = (other.get("direction") or "LONG").upper()
        if other_direction and other_direction != direction:
            return (
                "NULLIFIED", "CONTRADICTORY_SIGNAL",
                f"Opposing {other_direction} signal from {other.get('strategy')} "
                f"on {symbol} contradicts this {direction} thesis",
            )
    return "CLEAR", None, None


def _check_sector_regime_nullifier(signal: Dict[str, Any], db_path: Optional[Path]) -> tuple:
    """Confirmed broad-decline SRS regime + LONG signal overrides signal
    quality, except high-conviction signals (score > 75) (TIP Section 2.3).

    Reuses prime_srs_scanner's real regime infrastructure (get_broad_regime,
    bearish_regime_nullifier -- wired by CALC-SRS-2/3) rather than a
    signal['sector_regime'] key nothing in the codebase ever sets.
    """
    direction = (signal.get("direction") or "LONG").upper()
    if direction != "LONG":
        return "CLEAR", None, None

    score = signal.get("score", 0.0) or 0.0
    try:
        from prime_scanners.prime_srs_scanner import get_broad_regime, bearish_regime_nullifier
        regime = get_broad_regime(db_path)
    except Exception as e:
        logger.debug("Sector-regime nullifier check skipped: %s", e)
        return "CLEAR", None, None

    if not bearish_regime_nullifier(regime, score):
        return "CLEAR", None, None

    return (
        "NULLIFIED", "SECTOR_REGIME_BEARISH",
        f"Confirmed broad-decline sector regime ({regime}) overrides this LONG "
        f"signal (score={score} does not meet the high-conviction exception)",
    )


def _run_nullifier_check(
    symbol: str,
    signal: Dict[str, Any],
    duration: str,
    strategy: str = "",
    db_path: Optional[Path] = None,
) -> tuple:
    """Run all nullifier checks and return (status, flags, rationale, dp_eval_dict).

    Combines the dark-pool nullifier (unchanged -- load-bearing) with the
    covered-call, contradictory-signal, and sector-regime checks that were
    previously unwired or dead code (CALC-TRADE_FACTORS_ML-2). Overall status
    is the worst of the four (NULLIFIED > SUSPECT > CLEAR); flags/rationale
    are the union of whichever checks fired.
    """
    signal_with_duration = {**signal, "duration_class": duration}
    dp_eval = _dark_pool_scanner.evaluate(symbol, signal_with_duration)

    checks = [
        (dp_eval.status, dp_eval.flags, dp_eval.rationale),
        _check_covered_call_nullifier(signal),
        _check_contradictory_signal_nullifier(symbol, strategy, signal, db_path),
        _check_sector_regime_nullifier(signal, db_path),
    ]

    overall_status = "CLEAR"
    all_flags: List[str] = []
    rationales: List[str] = []
    for status, flags, rationale in checks:
        all_flags.extend(flags if isinstance(flags, list) else [flags] if flags else [])
        if rationale:
            rationales.append(rationale)
        if _STATUS_RANK[status] > _STATUS_RANK[overall_status]:
            overall_status = status

    combined_rationale = " | ".join(rationales) if rationales else dp_eval.rationale

    logger.info(
        "NULLIFIER check %s: status=%s flags=%s",
        symbol, overall_status, all_flags or "none",
    )

    return (
        overall_status,
        all_flags,
        combined_rationale,
        dp_eval.to_dict(),
    )


def _build_maintenance_flags(signal: Dict[str, Any], strategy: str) -> List[str]:
    """Build trade maintenance flags for ongoing monitoring."""
    flags = []

    earnings_days = signal.get("days_to_earnings", None)
    if earnings_days is not None and 0 < earnings_days <= 14:
        flags.append(f"Earnings in {earnings_days} days -- apply pre-earnings rules")

    sector_regime = signal.get("sector_regime", "")
    if sector_regime == "BEARISH":
        flags.append("Sector regime BEARISH -- monitor for regime confirmation")

    if signal.get("contradictory_signal"):
        flags.append("Contradictory signal from another PRIME strategy detected")

    if strategy == "MMR":
        flags.append("Monitor gold/silver ratio for directional changes")
    elif strategy == "SRS":
        flags.append("Monitor sector phase for recovery confirmation or reversal")
    elif strategy == "IDX":
        flags.append("Monitor VIX for regime shift affecting index thesis")

    return flags


# ---------------------------------------------------------------------------
# Public evaluation functions -- one per strategy
# ---------------------------------------------------------------------------

def evaluate_uoa(symbol: str, signal: Dict[str, Any], db_path: Optional[Path] = None) -> TradeFactorEvaluation:
    """Evaluate trade factors for a UOA signal."""
    return _evaluate("UOA", symbol, signal, db_path=db_path)


def evaluate_pead(symbol: str, signal: Dict[str, Any], db_path: Optional[Path] = None) -> TradeFactorEvaluation:
    """Evaluate trade factors for a PEAD signal."""
    return _evaluate("PEAD", symbol, signal, db_path=db_path)


def evaluate_mmr(symbol: str, signal: Dict[str, Any], db_path: Optional[Path] = None) -> TradeFactorEvaluation:
    """Evaluate trade factors for an MMR (Metals Mean-Reversion) signal."""
    return _evaluate("MMR", symbol, signal, db_path=db_path)


def evaluate_srs(symbol: str, signal: Dict[str, Any], db_path: Optional[Path] = None) -> TradeFactorEvaluation:
    """Evaluate trade factors for an SRS (Sector Recovery Strategy) signal."""
    return _evaluate("SRS", symbol, signal, db_path=db_path)


def evaluate_index(symbol: str, signal: Dict[str, Any], db_path: Optional[Path] = None) -> TradeFactorEvaluation:
    """Evaluate trade factors for an Index strategy signal."""
    return _evaluate("IDX", symbol, signal, db_path=db_path)


def _evaluate(
    strategy: str, symbol: str, signal: Dict[str, Any], db_path: Optional[Path] = None,
) -> TradeFactorEvaluation:
    """Core evaluation logic shared by all strategies."""
    timestamp = datetime.utcnow().isoformat()
    direction = signal.get("direction", "LONG")
    score = signal.get("score", 0.0)

    # CALC-TRADE_FACTORS_ML-1: make strategy routing visible in the server
    # log so a regression (a signal falling back into the generic branch)
    # is observable without inspecting trade_factors JSON.
    known_branch = strategy in ("UOA", "PEAD", "MMR", "SRS", "IDX")
    logger.info(
        "Trade factor routing: strategy=%s symbol=%s branch=%s",
        strategy, symbol, strategy if known_branch else "GENERIC(unknown strategy)",
    )

    dur_class, dur_conf, dur_rationale = _classify_duration(signal, strategy)
    entry_method, entry_trigger, entry_rationale, normalized_score = _determine_entry(
        signal, dur_class, strategy,
    )
    exit_triggers = _build_exit_triggers(signal, strategy, dur_class)
    null_status, null_flags, null_rationale, dp_eval = _run_nullifier_check(
        symbol, signal, dur_class, strategy=strategy, db_path=db_path,
    )
    maint_flags = _build_maintenance_flags(signal, strategy)

    return TradeFactorEvaluation(
        strategy=strategy,
        symbol=symbol,
        timestamp=timestamp,
        direction=direction,
        duration_class=dur_class,
        duration_confidence=dur_conf,
        duration_rationale=dur_rationale,
        entry_method=entry_method,
        entry_trigger=entry_trigger,
        entry_rationale=entry_rationale,
        exit_triggers=exit_triggers,
        nullifier_status=null_status,
        nullifier_flags=null_flags,
        nullifier_rationale=null_rationale,
        dark_pool_eval=dp_eval,
        maintenance_flags=maint_flags,
        signal_score=score,
        normalized_score=normalized_score,
    )
