"""PRIME v1.0 ML signal-event capture (Sprint 31 / CIL-041-031, CIL-042).

Captures one labelled training row per APPROVED signal into the
prime_ml_dataset table. Capture fields are written at scan time; the outcome
fields (P&L, hold time, exit reason) are filled in later by
prime_data.prime_db.update_ml_outcome() when the originating trade closes.
This is the foundational training-data pipeline for Auto Execution Phase 1.

Design notes
------------
* signal_id is the table PK and the join key back to prime_trade_log. The
  signal dict is expected to carry 'signal_id' (populated by the signal
  persistence layer -- Sprint 31 Thread 2). When it is absent we derive a
  deterministic id from (scanner, symbol, scan_ts) so each APPROVED signal
  still gets its own row and re-running the same scan stays idempotent
  (INSERT OR REPLACE on a stable key). The deterministic fallback will not
  match a trade row, so its outcome simply stays NULL -- acceptable, since a
  signal with no signal_id cannot be linked to a trade anyway.
* capture_ml_event() never raises. Capture is best-effort instrumentation and
  must never block or break the scan pipeline.
* The table DDL lives in prime_data.prime_db (init_db); the column order here
  must stay in sync with it.
"""

import logging
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from prime_analytics.prime_signals_db import make_signal_id
from prime_data.prime_db import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class PrimeMLEvent:
    """One captured signal event plus (eventually) its trade outcome.

    Field order matches the prime_ml_dataset table column order in
    prime_data.prime_db._PRIME_ML_DATASET_SCHEMA.
    """

    # --- capture fields (written at scan time) ---
    signal_id: str
    scanner: Optional[str] = None
    symbol: Optional[str] = None
    direction: Optional[str] = None
    score: Optional[float] = None
    tier: Optional[str] = None
    dk_status: Optional[str] = None
    dk_conviction: Optional[float] = None
    entry_price: Optional[float] = None
    price_at_scan: Optional[float] = None
    sizzle_index: Optional[float] = None      # UOA
    dnow_score: Optional[float] = None        # UOA D-NOW numeric direction (CIL-039)
    rsi: Optional[float] = None               # MTS
    pct_from_sma: Optional[float] = None      # MTS
    eps_surprise: Optional[float] = None      # PEAD
    guidance_flag: Optional[str] = None       # PEAD
    borrow_rate: Optional[float] = None       # SHORT
    market_regime: Optional[str] = None
    capture_ts: Optional[str] = None

    # --- outcome fields (filled in on trade close) ---
    exit_price: Optional[float] = None
    pnl_dollars: Optional[float] = None
    pnl_pct: Optional[float] = None
    hold_minutes: Optional[int] = None
    exit_reason: Optional[str] = None
    outcome_captured_at: Optional[str] = None


# Column names in table order, derived once from the dataclass so the INSERT
# and the table DDL cannot drift apart in their ordering.
ML_COLUMNS = [f.name for f in fields(PrimeMLEvent)]


# ---------------------------------------------------------------------------
# Market regime detection (CIL-042)
# ---------------------------------------------------------------------------

# Per-calendar-day cache: at most one Schwab price-history call per day.
_REGIME_CACHE: Dict[str, str] = {}
_REGIME_SYMBOL = "SPY"
_REGIME_SMA_PERIOD = 50
_REGIME_BEAR_FACTOR = 0.97


def _fetch_spy_closes(schwab_client) -> list:
    """Return SPY daily closes (oldest first) via the Schwab price-history API.

    Mirrors the bar-fetch shape used by prime_mts_scanner. Returns [] on any
    problem -- the caller treats an empty/short series as 'UNKNOWN'.
    """
    today = datetime.now()
    start = today - timedelta(days=_REGIME_SMA_PERIOD + 30)
    resp = schwab_client.get_price_history_every_day(
        _REGIME_SYMBOL, start_datetime=start, end_datetime=today
    )
    if getattr(resp, "status_code", 200) != 200:
        return []
    data = resp.json()
    return [c.get("close", 0) for c in data.get("candles", []) if c.get("close")]


def _get_market_regime(schwab_client=None) -> str:
    """Classify the broad-market regime from SPY vs its 50-day SMA.

    BULL if SPY close > SMA50; BEAR if SPY close < SMA50 * 0.97; else NEUTRAL.
    Returns 'UNKNOWN' when SPY data is unavailable. The result is cached per
    calendar day (module-level dict) so only one Schwab call happens per day;
    pass an explicit schwab_client to reuse an authenticated session.
    """
    day = datetime.now().strftime("%Y-%m-%d")
    if day in _REGIME_CACHE:
        return _REGIME_CACHE[day]

    regime = "UNKNOWN"
    try:
        client = schwab_client
        if client is None:
            from prime_trading.prime_schwab import SchwabClient
            client = SchwabClient()
        closes = _fetch_spy_closes(client)
        if len(closes) >= _REGIME_SMA_PERIOD:
            sma = sum(closes[-_REGIME_SMA_PERIOD:]) / _REGIME_SMA_PERIOD
            last = closes[-1]
            if last > sma:
                regime = "BULL"
            elif last < sma * _REGIME_BEAR_FACTOR:
                regime = "BEAR"
            else:
                regime = "NEUTRAL"
    except Exception as e:  # noqa: BLE001 - regime is best-effort
        logger.debug("Market regime unavailable: %s", e)
        regime = "UNKNOWN"

    _REGIME_CACHE[day] = regime
    return regime


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _resolve_signal_id(signal: Dict[str, Any], scanner: Optional[str],
                       symbol: Optional[str], capture_ts: str) -> str:
    """Return the signal's id, or a deterministic fallback when it is absent.

    A NULL/empty signal_id would collapse every capture row onto one PK under
    INSERT OR REPLACE, so we derive a stable id from the natural key instead.
    """
    sid = signal.get("signal_id")
    if sid:
        return str(sid)
    scan_ts = signal.get("scan_ts") or capture_ts
    return make_signal_id(scanner or "UNKNOWN", symbol or "UNKNOWN", scan_ts)


def build_ml_event(signal: Dict[str, Any],
                   market_regime: Optional[str] = None) -> PrimeMLEvent:
    """Project a scanner signal dict onto a PrimeMLEvent (capture fields only).

    Scanner-specific fields are read with .get() so a UOA signal (sizzle_index)
    and a PEAD signal (eps_surprise) both map cleanly, each leaving the other's
    fields NULL. market_regime defaults to a (cached) _get_market_regime() call
    when not supplied or already on the signal.
    """
    capture_ts = datetime.utcnow().isoformat()
    scanner = signal.get("scanner") or signal.get("strategy")
    symbol = signal.get("symbol")
    if market_regime is None:
        market_regime = signal.get("market_regime") or _get_market_regime()

    return PrimeMLEvent(
        signal_id=_resolve_signal_id(signal, scanner, symbol, capture_ts),
        scanner=scanner,
        symbol=symbol,
        direction=signal.get("direction"),
        score=signal.get("score"),
        tier=signal.get("tier"),
        dk_status=signal.get("dk_status"),
        dk_conviction=signal.get("dk_conviction"),
        entry_price=signal.get("entry_price"),
        price_at_scan=signal.get("price_at_scan"),
        sizzle_index=signal.get("sizzle_index"),
        dnow_score=signal.get("dnow_score"),
        rsi=signal.get("rsi"),
        pct_from_sma=signal.get("pct_from_sma"),
        eps_surprise=signal.get("eps_surprise"),
        guidance_flag=signal.get("guidance_flag"),
        borrow_rate=signal.get("borrow_rate"),
        market_regime=market_regime,
        capture_ts=capture_ts,
    )


def capture_ml_event(signal: Dict[str, Any],
                     db_path: Optional[Path] = None) -> Optional[str]:
    """Capture one APPROVED signal into prime_ml_dataset. Never raises.

    Writes via INSERT OR REPLACE keyed on signal_id, so re-capturing the same
    signal updates in place rather than duplicating. Returns the signal_id on
    success, or None if anything went wrong (logged at WARN).
    """
    try:
        event = build_ml_event(signal)
        values = [getattr(event, col) for col in ML_COLUMNS]
        placeholders = ",".join("?" for _ in ML_COLUMNS)
        columns = ",".join(ML_COLUMNS)
        with get_connection(db_path) as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO prime_ml_dataset ({columns}) "
                f"VALUES ({placeholders})",
                values,
            )
            conn.commit()
        return event.signal_id
    except Exception as e:  # noqa: BLE001 - capture must never break the scan
        symbol = signal.get("symbol", "?") if isinstance(signal, dict) else "?"
        logger.warning("capture_ml_event failed for %s: %s", symbol, e)
        return None
