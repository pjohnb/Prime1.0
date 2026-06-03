"""
PRIME v1.0 Dark Pool / Off-Exchange Activity Scanner (CIL-PRIME-DK-001).

Evaluates dark pool and off-exchange activity for manipulation patterns.
Integrates into the Trade Factor Registry as nullifier conditions.

Reference: PRIME Trade Intelligence Paper v1.0, Section 5.

Five data source proxies:
  1. FINRA ATS volume baseline (weekly, ~2-week lag)
  2. Large off-price tape prints (block trades at mid or away from NBBO)
  3. Short volume data (FINRA daily)
  4. Bid-ask spread widening concurrent with unusual options flow
  5. Dark pool print direction vs. options flow direction

Three manipulation patterns:
  1. Price spike into UOA signal
  2. Call volume spike + price already extended
  3. Block print against options direction

Integration rules:
  - SUSPECT (1 flag) + ST duration = NULLIFIED
  - 2+ flags = hard NULLIFIED regardless of duration or score
  - Scanner failure = CLEAR with warning flag (never blocks entry)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DarkPoolEvaluation:
    """Result of dark pool analysis for a single signal."""
    symbol: str
    timestamp: str
    flags: List[str] = field(default_factory=list)
    flag_details: Dict[str, str] = field(default_factory=dict)
    flag_count: int = 0
    suspect: bool = False
    nullified: bool = False
    status: str = "CLEAR"
    rationale: str = ""
    warning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "flags": self.flags,
            "flag_details": self.flag_details,
            "flag_count": self.flag_count,
            "suspect": self.suspect,
            "nullified": self.nullified,
            "status": self.status,
            "rationale": self.rationale,
            "warning": self.warning,
        }


# ---------------------------------------------------------------------------
# Data source proxies
# ---------------------------------------------------------------------------

def _fetch_finra_ats_volume(symbol: str) -> Optional[Dict[str, Any]]:
    """Proxy 1: FINRA ATS weekly volume baseline.
    TODO: Integrate FINRA ATS data feed when available.
    Returns baseline dark pool participation rate for comparison.
    """
    return None


def _fetch_tape_prints(symbol: str) -> Optional[List[Dict[str, Any]]]:
    """Proxy 2: Large off-price tape prints (block trades at mid/away from NBBO).
    TODO: Integrate real-time tape print feed.
    Returns list of large block trades with price, size, and side.
    """
    return None


def _fetch_short_volume(symbol: str) -> Optional[Dict[str, Any]]:
    """Proxy 3: FINRA daily short volume.
    TODO: Integrate FINRA short volume data feed.
    Returns short volume ratio and comparison to baseline.
    """
    return None


def _fetch_spread_data(symbol: str) -> Optional[Dict[str, Any]]:
    """Proxy 4: Bid-ask spread widening concurrent with unusual options flow.
    TODO: Integrate real-time spread monitoring.
    Returns current spread, average spread, and widening factor.
    """
    return None


def _fetch_print_direction(symbol: str) -> Optional[Dict[str, Any]]:
    """Proxy 5: Dark pool print direction vs. options flow direction.
    TODO: Integrate tape direction analysis.
    Returns net direction of large prints vs. options signal direction.
    """
    return None


# ---------------------------------------------------------------------------
# Manipulation pattern detectors
# ---------------------------------------------------------------------------

def _check_price_spike_into_signal(
    symbol: str,
    signal: Dict[str, Any],
) -> Optional[str]:
    """Pattern 1: Price spike into UOA signal.

    If price moved >2% in the signal direction before the UOA signal appeared
    in the same session, this suggests potential manipulation -- driving price
    before going short, or covering a short before the visible signal fires.
    """
    price_at_scan = signal.get("price_at_scan", 0.0)
    session_open_price = signal.get("session_open_price", 0.0)
    direction = signal.get("direction", "LONG").upper()

    if not price_at_scan or not session_open_price or session_open_price <= 0:
        return None

    move_pct = ((price_at_scan - session_open_price) / session_open_price) * 100

    if direction == "LONG" and move_pct > 2.0:
        return (
            f"PRICE_SPIKE_INTO_SIGNAL: {symbol} up {move_pct:.1f}% from session "
            f"open before LONG signal -- possible pre-positioning"
        )
    elif direction == "SHORT" and move_pct < -2.0:
        return (
            f"PRICE_SPIKE_INTO_SIGNAL: {symbol} down {abs(move_pct):.1f}% from "
            f"session open before SHORT signal -- possible pre-positioning"
        )

    return None


def _check_call_volume_price_extended(
    symbol: str,
    signal: Dict[str, Any],
) -> Optional[str]:
    """Pattern 2: Call volume spike + price already extended.

    If a stock is already up 3-5% on the day when unusual call volume appears,
    the call buyer may have driven it up and is now positioning to exit into
    retail enthusiasm.
    """
    price_at_scan = signal.get("price_at_scan", 0.0)
    session_open_price = signal.get("session_open_price", 0.0)
    direction = signal.get("direction", "LONG").upper()
    strategy = signal.get("strategy", "").upper()

    if direction != "LONG" or strategy not in ("UOA", ""):
        return None

    if not price_at_scan or not session_open_price or session_open_price <= 0:
        return None

    day_move_pct = ((price_at_scan - session_open_price) / session_open_price) * 100

    if day_move_pct >= 3.0:
        return (
            f"CALL_VOLUME_PRICE_EXTENDED: {symbol} already up {day_move_pct:.1f}% "
            f"when unusual call volume appeared -- possible distribution setup"
        )

    return None


def _check_block_print_against_direction(
    symbol: str,
    signal: Dict[str, Any],
) -> Optional[str]:
    """Pattern 3: Block print against options direction.

    A large off-exchange sell print while call volume is spiking is inconsistent
    with a genuine bullish thesis and suggests institutional distribution.
    """
    block_prints = signal.get("block_prints", [])
    direction = signal.get("direction", "LONG").upper()

    if not block_prints:
        return None

    for print_data in block_prints:
        print_side = print_data.get("side", "").upper()
        print_size = print_data.get("size", 0)

        if direction == "LONG" and print_side == "SELL" and print_size >= 10000:
            return (
                f"BLOCK_PRINT_AGAINST_DIRECTION: Large sell block ({print_size:,} shares) "
                f"on {symbol} while LONG options flow detected -- possible screen for distribution"
            )
        elif direction == "SHORT" and print_side == "BUY" and print_size >= 10000:
            return (
                f"BLOCK_PRINT_AGAINST_DIRECTION: Large buy block ({print_size:,} shares) "
                f"on {symbol} while SHORT options flow detected -- possible covering"
            )

    return None


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

class DarkPoolScanner:
    """Evaluates dark pool activity for manipulation patterns on a given signal."""

    PATTERN_CHECKERS = [
        _check_price_spike_into_signal,
        _check_call_volume_price_extended,
        _check_block_print_against_direction,
    ]

    DATA_SOURCES = [
        ("finra_ats_volume", _fetch_finra_ats_volume),
        ("tape_prints", _fetch_tape_prints),
        ("short_volume", _fetch_short_volume),
        ("spread_data", _fetch_spread_data),
        ("print_direction", _fetch_print_direction),
    ]

    def evaluate(self, symbol: str, signal: Dict[str, Any]) -> DarkPoolEvaluation:
        """Run all dark pool checks on a signal. Returns DarkPoolEvaluation.

        Never raises -- scanner failure falls back to CLEAR with warning flag.
        """
        timestamp = datetime.utcnow().isoformat()
        evaluation = DarkPoolEvaluation(symbol=symbol, timestamp=timestamp)

        try:
            self._fetch_data_sources(symbol, signal, evaluation)
            self._run_pattern_checks(symbol, signal, evaluation)
            self._apply_integration_rules(signal, evaluation)
        except Exception as e:
            logger.warning("DK-001 scanner failure for %s: %s -- falling back to CLEAR", symbol, e)
            evaluation.status = "CLEAR"
            evaluation.warning = f"Scanner error: {e} -- defaulting to CLEAR"
            evaluation.rationale = "Dark pool evaluation failed; proceeding without constraint"

        return evaluation

    def _fetch_data_sources(
        self, symbol: str, signal: Dict[str, Any], evaluation: DarkPoolEvaluation,
    ) -> None:
        """Attempt to fetch all five data source proxies."""
        for source_name, fetcher in self.DATA_SOURCES:
            try:
                result = fetcher(symbol)
                if result is not None:
                    signal[f"_dp_{source_name}"] = result
            except Exception as e:
                logger.debug("DK-001 data source %s failed for %s: %s", source_name, symbol, e)

    def _run_pattern_checks(
        self, symbol: str, signal: Dict[str, Any], evaluation: DarkPoolEvaluation,
    ) -> None:
        """Run all three manipulation pattern detectors."""
        for checker in self.PATTERN_CHECKERS:
            try:
                flag = checker(symbol, signal)
                if flag:
                    flag_name = flag.split(":")[0]
                    evaluation.flags.append(flag_name)
                    evaluation.flag_details[flag_name] = flag
            except Exception as e:
                logger.debug("DK-001 pattern check failed for %s: %s", symbol, e)

        evaluation.flag_count = len(evaluation.flags)

    def _apply_integration_rules(
        self, signal: Dict[str, Any], evaluation: DarkPoolEvaluation,
    ) -> None:
        """Apply integration rules per TIP Section 5.3.

        - 1 flag: SUSPECT
        - SUSPECT + ST duration: NULLIFIED
        - 2+ flags: hard NULLIFIED regardless of duration or score
        """
        duration = signal.get("duration_class", "ST").upper()

        if evaluation.flag_count == 0:
            evaluation.status = "CLEAR"
            evaluation.suspect = False
            evaluation.nullified = False
            evaluation.rationale = "No dark pool manipulation patterns detected"
            return

        evaluation.suspect = True

        if evaluation.flag_count >= 2:
            evaluation.status = "NULLIFIED"
            evaluation.nullified = True
            evaluation.rationale = (
                f"HARD NULLIFIED: {evaluation.flag_count} dark pool flags detected "
                f"({', '.join(evaluation.flags)}) -- two or more flags constitute "
                f"a hard nullifier regardless of duration or score"
            )
            return

        # Single flag
        if duration == "ST":
            evaluation.status = "NULLIFIED"
            evaluation.nullified = True
            evaluation.rationale = (
                f"NULLIFIED: {evaluation.flags[0]} detected on ST-duration signal "
                f"-- single dark pool flag combined with short-term classification "
                f"is a nullifier per TIP Section 5.3"
            )
        else:
            evaluation.status = "SUSPECT"
            evaluation.nullified = False
            evaluation.rationale = (
                f"SUSPECT: {evaluation.flags[0]} detected but signal is "
                f"{duration}-duration -- flag displayed in Trade Management Panel "
                f"and incorporated into Claude advisory; entry not blocked"
            )


# ---------------------------------------------------------------------------
# DK Composite Scoring (CIL-PRIME-DK-001)
# ---------------------------------------------------------------------------

def score_dk_signal(symbol: str) -> Dict[str, Any]:
    """Combine three DK data sources into a composite score.

    Returns {dk_score: float 0-100, dk_status: str, detail: dict}.
    dk_status: CONFIRMING | NULLIFYING | NEUTRAL | UNAVAILABLE
    """
    from prime_intelligence.prime_dk_data import (
        get_finra_ats_volume,
        get_short_volume,
        get_tape_prints,
    )

    ats = get_finra_ats_volume(symbol)
    prints = get_tape_prints(symbol)
    short = get_short_volume(symbol)

    if ats is None and prints is None and short is None:
        return {
            "dk_score": None,
            "dk_status": "UNAVAILABLE",
            "detail": {"reason": "All three DK data sources unavailable"},
        }

    score = 0.0
    detail: Dict[str, Any] = {}

    # ATS component: ats_pct > 45% AND rising = +30
    if ats is not None:
        ats_pct = ats.get("ats_pct", 0)
        ats_rising = ats.get("ats_rising", False)
        detail["ats_pct"] = ats_pct
        detail["ats_rising"] = ats_rising
        if ats_pct > 45 and ats_rising:
            score += 30

    # Tape prints component: large block at mid within 5 days = +25 each (cap +50)
    if prints is not None:
        block_count = len(prints)
        detail["block_prints"] = block_count
        score += min(block_count * 25, 50)

    # Short volume: spike > 1.5x 20-day avg = NULLIFYING override
    if short is not None:
        short_pct = short.get("short_pct", 0)
        short_avg = short.get("short_avg_20d", short_pct)
        detail["short_pct"] = short_pct
        detail["short_avg_20d"] = short_avg
        if short_avg > 0 and short_pct > short_avg * 1.5:
            detail["short_spike"] = True
            return {
                "dk_score": 0.0,
                "dk_status": "NULLIFYING",
                "detail": {**detail, "reason": (
                    f"Short volume spike: {short_pct:.1f}% vs "
                    f"{short_avg:.1f}% 20d avg (>{1.5}x)"
                )},
            }

    score = min(score, 100.0)
    if score >= 50:
        dk_status = "CONFIRMING"
    elif score > 0:
        dk_status = "NEUTRAL"
    else:
        dk_status = "NEUTRAL"

    return {"dk_score": score, "dk_status": dk_status, "detail": detail}


# ---------------------------------------------------------------------------
# Matured DK print scoring (Sprint 16 Item 4)
# ---------------------------------------------------------------------------
#
# The legacy score_dk_signal() above combines FINRA ATS %, block-print count,
# and short-volume into a composite. Sprint 16 matures the DK *print* read with
# three additional factors computed directly from the dark-pool prints supplied
# by prime_data.prime_dk_feed.get_dk_prints():
#
#   volume_ratio   -- dark-pool print volume / total session volume. High ratio
#                     = heavy off-exchange participation.
#   price_proximity-- fraction of prints within PRICE_PROXIMITY_PCT of the
#                     reference (current) price. High = accumulation at price;
#                     low = prints away from price (absorption / distribution).
#   repeat_activity-- number of prints for the symbol this session. More prints
#                     = higher conviction.
#
# Verdict (thresholds documented in PRIME Documentation/DK_STRATEGY.md):
#   SIGNAL    -- print_score >= SIGNAL_PRINT_SCORE AND proximity high: genuine
#                accumulation near price with conviction.
#   NULLIFIER -- volume_ratio heavy but proximity low: large off-exchange volume
#                working against the visible price -- suppresses other signals.
#   None      -- neither (NEUTRAL).

PRICE_PROXIMITY_PCT = 0.5          # % of current price counted as "near"
SIGNAL_PRINT_SCORE = 50.0          # matured print_score required for SIGNAL
SIGNAL_MIN_PROXIMITY = 0.5         # fraction of prints near price for SIGNAL
NULLIFIER_VOL_RATIO = 0.5          # dark/total volume that flags NULLIFIER
NULLIFIER_MAX_PROXIMITY = 0.3      # prints scattered away from price


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def score_dk_prints(
    prints: List[Dict[str, Any]],
    reference_price: Optional[float] = None,
    total_volume: Optional[float] = None,
) -> Dict[str, Any]:
    """Score dark-pool prints on volume_ratio, price_proximity, repeat_activity.

    Pure function (no I/O). Returns:
        {repeat_activity, price_proximity, volume_ratio, print_score, verdict}
    verdict is "SIGNAL" | "NULLIFIER" | None.
    """
    prints = prints or []
    repeat_activity = len(prints)
    if repeat_activity == 0:
        return {"repeat_activity": 0, "price_proximity": 0.0,
                "volume_ratio": 0.0, "print_score": 0.0, "verdict": None}

    prices = [float(p.get("price", 0) or 0) for p in prints]
    volumes = [float(p.get("volume", 0) or 0) for p in prints]
    ref = reference_price if reference_price else _median(prices)

    # price_proximity: fraction of prints within PRICE_PROXIMITY_PCT of ref.
    if ref and ref > 0:
        near = sum(1 for px in prices
                   if abs(px - ref) / ref * 100.0 <= PRICE_PROXIMITY_PCT)
        price_proximity = near / repeat_activity
    else:
        price_proximity = 0.0

    # volume_ratio: dark-pool volume / total session volume (if known).
    dark_volume = sum(volumes)
    if total_volume and total_volume > 0:
        volume_ratio = min(dark_volume / total_volume, 1.0)
    else:
        # No session total available -> fall back to a per-print total field
        # if the feed supplied one, else 0 (no volume contribution).
        per_total = sum(float(p.get("total_volume", 0) or 0) for p in prints)
        volume_ratio = min(dark_volume / per_total, 1.0) if per_total > 0 else 0.0

    # Composite print_score 0-100 weighting all three factors.
    proximity_component = price_proximity * 40.0
    repeat_component = min(repeat_activity, 5) / 5.0 * 30.0
    volume_component = min(volume_ratio, NULLIFIER_VOL_RATIO) / NULLIFIER_VOL_RATIO * 30.0
    print_score = round(proximity_component + repeat_component + volume_component, 1)

    verdict: Optional[str] = None
    if print_score >= SIGNAL_PRINT_SCORE and price_proximity >= SIGNAL_MIN_PROXIMITY:
        verdict = "SIGNAL"
    elif volume_ratio >= NULLIFIER_VOL_RATIO and price_proximity < NULLIFIER_MAX_PROXIMITY:
        verdict = "NULLIFIER"

    return {
        "repeat_activity": repeat_activity,
        "price_proximity": round(price_proximity, 4),
        "volume_ratio": round(volume_ratio, 4),
        "print_score": print_score,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Convenience API for GUI consumption
# ---------------------------------------------------------------------------

_gui_scanner = DarkPoolScanner()


def get_nullifier_flags(symbol: str, signal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return nullifier status for a symbol in a GUI-friendly dict.

    Called by TradeManagementPanel to display traffic-light indicator.
    Integrates DK composite score when available.
    """
    dk = score_dk_signal(symbol)

    if signal is None:
        signal = {"symbol": symbol, "direction": "LONG", "duration_class": "MT"}
    evaluation = _gui_scanner.evaluate(symbol, signal)

    # DK status overrides pattern-based evaluation when NULLIFYING
    status = evaluation.status
    rationale = evaluation.rationale
    if dk["dk_status"] == "NULLIFYING":
        status = "NULLIFIED"
        rationale = dk["detail"].get("reason", "DK NULLIFYING override")
    elif dk["dk_status"] == "CONFIRMING" and status == "CLEAR":
        rationale = f"DK CONFIRMING (score={dk['dk_score']:.0f}); {rationale}"

    return {
        "status": status,
        "flags": evaluation.flags,
        "flag_count": evaluation.flag_count,
        "rationale": rationale,
        "nullified": status == "NULLIFIED",
        "suspect": evaluation.suspect,
        "warning": evaluation.warning,
        "dk_score": dk["dk_score"],
        "dk_status": dk["dk_status"],
        "dk_detail": dk["detail"],
    }
