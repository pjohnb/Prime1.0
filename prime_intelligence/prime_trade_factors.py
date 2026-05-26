"""
PRIME v1.0 Trade Factor Registry (CIL-PRIME-TF-001).

Evaluates trade factors for all strategies: UOA, PEAD, MTS, SRS, IDX.
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
    elif strategy == "MTS":
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


def _determine_entry(signal: Dict[str, Any], duration: str) -> tuple:
    """Determine entry method based on signal characteristics."""
    score = signal.get("score", 0.0)
    session = signal.get("session_type", "REGULAR")

    if session in ("PRE_MARKET", "AFTER_HOURS"):
        return "WAIT", "market_open", f"Signal in {session} -- wait for regular session confirmation"

    if score >= 8.0:
        return "IMMEDIATE_FULL", "", f"High conviction (score={score}) -> full entry"
    elif score >= 6.0:
        if duration == "LT":
            return "SCALED", "tranche_2_on_confirmation", (
                f"Score={score} on LT thesis -> enter half, add on confirmation"
            )
        return "IMMEDIATE_FULL", "", f"Score={score} -> full entry"
    else:
        return "IMMEDIATE_HALF", "", f"Moderate conviction (score={score}) -> half position"


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
    elif strategy == "MTS":
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


def _run_nullifier_check(
    symbol: str,
    signal: Dict[str, Any],
    duration: str,
) -> tuple:
    """Run dark pool nullifier check and return (status, flags, rationale, dp_eval_dict)."""
    signal_with_duration = {**signal, "duration_class": duration}
    dp_eval = _dark_pool_scanner.evaluate(symbol, signal_with_duration)

    return (
        dp_eval.status,
        dp_eval.flags,
        dp_eval.rationale,
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

    if strategy == "MTS":
        flags.append("Monitor gold/silver ratio for directional changes")
    elif strategy == "SRS":
        flags.append("Monitor sector phase for recovery confirmation or reversal")
    elif strategy == "IDX":
        flags.append("Monitor VIX for regime shift affecting index thesis")

    return flags


# ---------------------------------------------------------------------------
# Public evaluation functions -- one per strategy
# ---------------------------------------------------------------------------

def evaluate_uoa(symbol: str, signal: Dict[str, Any]) -> TradeFactorEvaluation:
    """Evaluate trade factors for a UOA signal."""
    return _evaluate("UOA", symbol, signal)


def evaluate_pead(symbol: str, signal: Dict[str, Any]) -> TradeFactorEvaluation:
    """Evaluate trade factors for a PEAD signal."""
    return _evaluate("PEAD", symbol, signal)


def evaluate_mts(symbol: str, signal: Dict[str, Any]) -> TradeFactorEvaluation:
    """Evaluate trade factors for an MTS (Metals Trading Strategy) signal."""
    return _evaluate("MTS", symbol, signal)


def evaluate_srs(symbol: str, signal: Dict[str, Any]) -> TradeFactorEvaluation:
    """Evaluate trade factors for an SRS (Sector Recovery Strategy) signal."""
    return _evaluate("SRS", symbol, signal)


def evaluate_index(symbol: str, signal: Dict[str, Any]) -> TradeFactorEvaluation:
    """Evaluate trade factors for an Index strategy signal."""
    return _evaluate("IDX", symbol, signal)


def _evaluate(strategy: str, symbol: str, signal: Dict[str, Any]) -> TradeFactorEvaluation:
    """Core evaluation logic shared by all strategies."""
    timestamp = datetime.utcnow().isoformat()
    direction = signal.get("direction", "LONG")
    score = signal.get("score", 0.0)

    dur_class, dur_conf, dur_rationale = _classify_duration(signal, strategy)
    entry_method, entry_trigger, entry_rationale = _determine_entry(signal, dur_class)
    exit_triggers = _build_exit_triggers(signal, strategy, dur_class)
    null_status, null_flags, null_rationale, dp_eval = _run_nullifier_check(
        symbol, signal, dur_class,
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
    )
