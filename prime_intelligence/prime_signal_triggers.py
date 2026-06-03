"""
PRIME v1.0 Signal Triggers (Sprint 18).

Shared predictive-trigger detectors that read recent UOA / PEAD signals from
prime_signals. Used by:
  * the PSA signal-led retrofit (Item 1): UOA_CALL / PEAD_BEAT long triggers;
  * the short scanner live feed (Item 2): UOA_PUT / PEAD_MISS short triggers.

Trigger data flows into prime_signals via the Sprint 14 scanner bridge:
  UOA  factors: {call_put_ratio (calls/puts), total_volume, group, source};
       direction LONG (call-dominant) or SHORT (put-dominant); score=sizzle.
  PEAD factors: {eps_surprise_pct, price_reaction_pct, days_since_earnings,
       earnings_date}; direction LONG/SHORT.

"Within last N sessions" is approximated as N trading sessions ~= a calendar
window (weekends covered); UOA defaults to 2 sessions, PEAD to 5. The window is
configurable per call.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prime_signal_triggers")

# UOA call/put surge orientation (call_put_ratio = calls / puts).
UOA_CALL_RATIO_MIN = 2.0      # calls >= 2x puts -> call surge
UOA_PUT_RATIO_MAX = 0.5       # calls <= 0.5x puts -> put surge

# Session -> calendar-day windows (cover intervening weekends).
UOA_SESSION_DAYS = 4          # ~2 trading sessions
PEAD_SESSION_DAYS = 8         # ~5 trading sessions


def _parse_ts(ts: Any) -> Optional[datetime]:
    """Parse a scan_ts ('YYYY-MM-DD HH:MM' or ISO). None if unparseable."""
    if not ts:
        return None
    s = str(ts).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _within_window(scan_ts: Any, ref_ts: Optional[datetime], window_days: int) -> bool:
    """True if scan_ts is within window_days before ref_ts (lenient on parse)."""
    ts = _parse_ts(scan_ts)
    if ts is None or ref_ts is None:
        return True  # be lenient: if we can't compare, don't exclude
    return (ref_ts - ts) <= timedelta(days=window_days) and ts <= ref_ts + timedelta(days=1)


def _factors(signal: Dict[str, Any]) -> Dict[str, Any]:
    raw = signal.get("factors")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def _recent(strategy: str, symbol: str, db_path: Optional[Path],
            ref_ts: Optional[datetime], window_days: int) -> List[Dict[str, Any]]:
    from prime_analytics.prime_signals_db import get_signals
    rows = get_signals(strategy=strategy, symbol=symbol, db_path=db_path, limit=50)
    return [r for r in rows if _within_window(r.get("scan_ts"), ref_ts, window_days)]


# ---------------------------------------------------------------------------
# Long triggers (PSA signal-led retrofit, Item 1)
# ---------------------------------------------------------------------------

def uoa_call_trigger(symbol: str, db_path: Optional[Path] = None,
                     ref_ts: Optional[datetime] = None,
                     window_days: int = UOA_SESSION_DAYS) -> bool:
    """A recent call-dominant UOA signal (call surge) exists for the symbol."""
    for s in _recent("UOA", symbol, db_path, ref_ts, window_days):
        direction = (s.get("direction") or "LONG").upper()
        cpr = _factors(s).get("call_put_ratio")
        if direction == "LONG" and (cpr is None or cpr >= UOA_CALL_RATIO_MIN):
            return True
    return False


def pead_long_trigger(symbol: str, db_path: Optional[Path] = None,
                      ref_ts: Optional[datetime] = None,
                      window_days: int = PEAD_SESSION_DAYS) -> bool:
    """A recent PEAD earnings-beat (eps_surprise > 0) long signal exists."""
    for s in _recent("PEAD", symbol, db_path, ref_ts, window_days):
        eps = _factors(s).get("eps_surprise_pct")
        direction = (s.get("direction") or "LONG").upper()
        if direction == "LONG" and (eps is None or eps > 0):
            return True
    return False


def psa_trigger_source(symbol: str, db_path: Optional[Path] = None,
                       ref_ts: Optional[datetime] = None) -> str:
    """Primary trigger for a PSA long: 'UOA_CALL' | 'PEAD_BEAT' | 'NONE'."""
    if uoa_call_trigger(symbol, db_path, ref_ts):
        return "UOA_CALL"
    if pead_long_trigger(symbol, db_path, ref_ts):
        return "PEAD_BEAT"
    return "NONE"


# ---------------------------------------------------------------------------
# Short triggers (short scanner live feed, Item 2)
# ---------------------------------------------------------------------------

def uoa_put_signal_present(symbol: str, db_path: Optional[Path] = None,
                           ref_ts: Optional[datetime] = None,
                           window_days: int = UOA_SESSION_DAYS) -> bool:
    """A recent put-dominant UOA signal (put surge) exists for the symbol."""
    for s in _recent("UOA", symbol, db_path, ref_ts, window_days):
        direction = (s.get("direction") or "").upper()
        cpr = _factors(s).get("call_put_ratio")
        if direction == "SHORT" or (cpr is not None and cpr <= UOA_PUT_RATIO_MAX):
            return True
    return False


def pead_miss_signal_present(symbol: str, db_path: Optional[Path] = None,
                             ref_ts: Optional[datetime] = None,
                             window_days: int = PEAD_SESSION_DAYS) -> bool:
    """A recent PEAD earnings-miss (eps_surprise < 0) signal exists for the symbol."""
    for s in _recent("PEAD", symbol, db_path, ref_ts, window_days):
        f = _factors(s)
        eps = f.get("eps_surprise_pct")
        direction = (s.get("direction") or "").upper()
        if (eps is not None and eps < 0) or f.get("guidance_cut") or direction == "SHORT":
            return True
    return False


def short_primary_triggers_from_signals(
    symbol: str, db_path: Optional[Path] = None,
    ref_ts: Optional[datetime] = None,
) -> List[str]:
    """Live short triggers from prime_signals: ['UOA_PUT'] / ['PEAD_MISS'] / both."""
    fired = []
    if uoa_put_signal_present(symbol, db_path, ref_ts):
        fired.append("UOA_PUT")
    if pead_miss_signal_present(symbol, db_path, ref_ts):
        fired.append("PEAD_MISS")
    return fired
