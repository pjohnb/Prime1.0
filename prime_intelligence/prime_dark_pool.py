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
# Convenience API for GUI consumption
# ---------------------------------------------------------------------------

_gui_scanner = DarkPoolScanner()


def get_nullifier_flags(symbol: str, signal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return nullifier status for a symbol in a GUI-friendly dict.

    Called by TradeManagementPanel to display traffic-light indicator.
    If no signal context is available, evaluates with an empty signal dict.
    """
    if signal is None:
        signal = {"symbol": symbol, "direction": "LONG", "duration_class": "MT"}
    evaluation = _gui_scanner.evaluate(symbol, signal)
    return {
        "status": evaluation.status,
        "flags": evaluation.flags,
        "flag_count": evaluation.flag_count,
        "rationale": evaluation.rationale,
        "nullified": evaluation.nullified,
        "suspect": evaluation.suspect,
        "warning": evaluation.warning,
    }
