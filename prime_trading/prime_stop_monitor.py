"""
PRIME v1.0 Stop Monitor — Automated Stop Execution + Trailing Stops (Sprint 24 Item 4).

Runs as a background thread in prime_api_server.py. Every 60 seconds during RTH,
checks all OPEN positions for stop breaches:

  LONG:  breach when current_price <= entry_price * (1 - long_stop_loss_pct)
         (or <= trailing_stop_price when trailing_stop_pct is set)
  SHORT: breach when current_price >= entry_price * (1 + short_stop_loss_pct)
         (or >= trailing_stop_price for short trailing)

stop_execution_mode (from ops_config.json):
  ALERT  → writes a breach record to _stop_alerts dict; Lovable topbar polls it
  AUTO   → submits MARKET SELL via submit_order() immediately (no confirmation)

Trailing stop: trailing_stop_pct is per-trade (nullable). When set:
  LONG  → stop_price moves UP with price (high-water mark); never moves down
  SHORT → stop_price moves DOWN with price (low-water mark); never moves up
trailing_stop_high_water is updated in prime_trade_log on each cycle.
"""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CHECK_INTERVAL_S = 60  # default; overridden at runtime by ops_config.json


def _get_check_interval() -> int:
    """Read stop_monitor_interval_seconds from ops_config.json; fall back to 60."""
    try:
        import json
        path = _PROJECT_ROOT / "ops_config.json"
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return int(raw.get("stop_monitor_interval_seconds", _CHECK_INTERVAL_S))
    except Exception:
        return _CHECK_INTERVAL_S

# Active stop alerts: {log_id: {symbol, direction, breach_price, stop_price, ts}}
_stop_alerts: Dict[str, Dict[str, Any]] = {}
_alerts_lock = threading.Lock()

_monitor_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

def get_active_alerts() -> List[Dict[str, Any]]:
    """Return snapshot of currently active stop-breach alerts."""
    with _alerts_lock:
        return list(_stop_alerts.values())


def clear_alert(log_id: str) -> None:
    with _alerts_lock:
        _stop_alerts.pop(log_id, None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_ops_config() -> Dict[str, Any]:
    import json
    path = _PROJECT_ROOT / "ops_config.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _is_rth() -> bool:
    from prime_trading.prime_schwab_orders import _is_rth as _check
    return _check()


def _current_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch live quotes for symbols via Schwab or return {} on failure."""
    if not symbols:
        return {}
    try:
        from prime_trading.prime_schwab import SchwabClient
        client = SchwabClient()
        client.connect()
        quotes = client.get_quotes(symbols)
        out: Dict[str, float] = {}
        for sym, data in quotes.items():
            price = (
                data.get("quote", {}).get("lastPrice")
                or data.get("quote", {}).get("mark")
                or data.get("regularMarketLastPrice")
                or 0.0
            )
            if price:
                out[sym.upper()] = float(price)
        return out
    except Exception as e:
        logger.debug("Price fetch for stop monitor failed: %s", e)
        return {}


def _trailing_stop_price(
    entry_price: float,
    trailing_pct: float,
    high_water: float,
    direction: str,
) -> float:
    """Compute current trailing stop price given high_water mark."""
    if direction.upper() == "SHORT":
        # Short: stop moves DOWN with price (low-water); stop = low_water * (1 + pct)
        return round(high_water * (1 + trailing_pct), 4)
    else:
        # Long: stop moves UP with price (high-water); stop = high_water * (1 - pct)
        return round(high_water * (1 - trailing_pct), 4)


def _update_high_water(
    log_id: str,
    current_price: float,
    direction: str,
    existing_hw: Optional[float],
    entry_price: float,
    db_path: Optional[Path] = None,
) -> float:
    """Update trailing_stop_high_water in DB; return new value."""
    if direction.upper() == "SHORT":
        # Short: track the lowest price seen (low-water = most favorable = lowest)
        new_hw = min(current_price, existing_hw) if existing_hw else current_price
    else:
        # Long: track the highest price seen
        new_hw = max(current_price, existing_hw) if existing_hw else current_price

    # Only write if changed
    if new_hw != existing_hw:
        try:
            from prime_data.prime_db import get_connection
            with get_connection(db_path) as conn:
                conn.execute(
                    "UPDATE prime_trade_log SET trailing_stop_high_water=? WHERE log_id=?",
                    (new_hw, log_id),
                )
                conn.commit()
        except Exception as e:
            logger.warning("Could not update trailing_stop_high_water for %s: %s", log_id, e)
    return new_hw


def _check_position(
    position: Dict[str, Any],
    current_price: float,
    ops: Dict[str, Any],
) -> Optional[str]:
    """Return 'BREACH' if this position's stop is hit, else None.

    Sprint 26 Item 2: use stored stop_price when set; fall back to computed.
    """
    direction   = (position.get("direction") or "LONG").upper()
    entry_price = float(position.get("entry_price") or position.get("price_at_scan") or 0.0)
    if entry_price <= 0 or current_price <= 0:
        return None

    trailing_pct = position.get("trailing_stop_pct")
    high_water   = position.get("trailing_stop_high_water")

    if trailing_pct is not None:
        hw = float(high_water) if high_water else entry_price
        stop = _trailing_stop_price(entry_price, float(trailing_pct), hw, direction)
    elif position.get("stop_price") and float(position["stop_price"]) > 0:
        # Sprint 26 Item 2: prefer stored stop_price over recalculating each cycle.
        stop = float(position["stop_price"])
    else:
        if direction == "SHORT":
            pct = float(ops.get("short_stop_loss_pct", 0.05))
            stop = entry_price * (1 + pct)
        else:
            pct = float(ops.get("long_stop_loss_pct", 0.05))
            stop = entry_price * (1 - pct)

    if direction == "SHORT" and current_price >= stop:
        return "BREACH"
    if direction == "LONG" and current_price <= stop:
        return "BREACH"
    return None


def _fire_alert(position: Dict[str, Any], current_price: float, stop_price: float) -> None:
    log_id = position["log_id"]
    ts = datetime.utcnow().isoformat()
    alert = {
        "log_id":        log_id,
        "symbol":        position.get("symbol", ""),
        "direction":     position.get("direction", "LONG"),
        "current_price": current_price,
        "stop_price":    stop_price,
        "entry_price":   float(position.get("entry_price") or 0),
        "ts":            ts,
    }
    with _alerts_lock:
        _stop_alerts[log_id] = alert
    logger.warning(
        "STOP ALERT: %s %s current=%.4f stop=%.4f",
        position.get("symbol"), position.get("direction"), current_price, stop_price,
    )


def _fire_auto_sell(position: Dict[str, Any], current_price: float, db_path: Optional[Path] = None) -> None:
    """Submit automatic MARKET SELL for a breached position (AUTO mode)."""
    from prime_config.prime_config import get_config
    cfg = get_config()
    if (cfg.trading_mode or "PAPER").upper() != "LIVE":
        logger.info(
            "Stop AUTO mode: PAPER mode active — skipping live sell for %s",
            position.get("symbol"),
        )
        _fire_alert(position, current_price, current_price)
        return

    try:
        from prime_trading.prime_schwab import SchwabClient
        from prime_trading.prime_schwab_orders import submit_order, OrderGateError
        client = SchwabClient()
        client.connect()
        account_hash = position.get("account") or client.account_hash or ""
        result = submit_order(
            symbol=position.get("symbol", ""),
            qty=int(position.get("shares", 0)),
            side="SELL",
            order_type="MARKET",
            price=current_price,
            account_hash=account_hash,
            confirmed=True,   # AUTO mode is pre-confirmed via Settings toggle
            schwab_client=client,
            db_path=db_path,
        )
        logger.info(
            "AUTO stop sell submitted: %s order_id=%s",
            position.get("symbol"), result.get("order_id"),
        )
        from prime_data.prime_db import log_ops_event
        log_ops_event(
            event_type="AUTO_STOP_SELL",
            component="prime_stop_monitor",
            symbol=position.get("symbol"),
            detail=(
                f"log_id={position['log_id']} "
                f"price={current_price:.4f} "
                f"order_id={result.get('order_id')}"
            ),
            severity="WARN",
            db_path=db_path,
        )
    except Exception as e:
        logger.error("AUTO stop sell failed for %s: %s", position.get("symbol"), e)
        _fire_alert(position, current_price, current_price)


# ---------------------------------------------------------------------------
# Sprint 30 PM-04 — Automated exits: trailing stop (gain-triggered) + day count
# ---------------------------------------------------------------------------

def _utc_day_start() -> str:
    """ISO timestamp for 00:00 UTC today — used as the 'once per day' boundary."""
    now = datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _fire_exit_sell(
    position: Dict[str, Any],
    current_price: float,
    exit_reason: str,
    db_path: Optional[Path] = None,
) -> None:
    """Fire an automated MATA exit sell and close the trade log record (PM-04).

    LIVE  → submit a MARKET SELL via submit_order(), then close the log.
    PAPER → close the log only (no Schwab order).
    In both modes the prime_trade_log record is closed with the given
    exit_reason and an event is written to prime_ops_health.
    """
    from prime_config.prime_config import get_config
    from prime_data.prime_db import close_trade_manual, log_ops_event

    log_id = position.get("log_id")
    symbol = position.get("symbol", "")
    mode   = (get_config().trading_mode or "PAPER").upper()

    order_id = None
    if mode == "LIVE":
        try:
            from prime_trading.prime_schwab import SchwabClient
            from prime_trading.prime_schwab_orders import submit_order
            client = SchwabClient()
            client.connect()
            account_hash = position.get("account") or client.account_hash or ""
            result = submit_order(
                symbol=symbol,
                qty=int(position.get("shares", 0)),
                side="SELL",
                order_type="MARKET",
                price=current_price,
                account_hash=account_hash,
                confirmed=True,   # automated exits are pre-confirmed
                schwab_client=client,
                db_path=db_path,
            )
            order_id = result.get("order_id")
        except Exception as e:  # noqa: BLE001 — still close the log so we don't loop
            logger.error("Automated exit (%s) sell failed for %s: %s", exit_reason, symbol, e)

    try:
        close_trade_manual(log_id, current_price, exit_reason=exit_reason, db_path=db_path)
    except Exception as e:  # noqa: BLE001
        logger.error("Could not close trade log %s on %s exit: %s", log_id, exit_reason, e)

    log_ops_event(
        event_type=exit_reason,
        component="prime_stop_monitor",
        symbol=(symbol or "").upper(),
        detail=f"log_id={log_id} price={current_price:.4f} order_id={order_id}",
        severity="WARN",
        db_path=db_path,
    )
    logger.warning(
        "AUTOMATED EXIT %s: %s price=%.4f order_id=%s",
        exit_reason, symbol, current_price, order_id,
    )


def _check_trailing_stop(
    position: Dict[str, Any],
    current_price: float,
    ops: Dict[str, Any],
    db_path: Optional[Path] = None,
) -> bool:
    """Gain-triggered trailing stop (CIL-097). Returns True if an exit fired.

    Arms when price rises to entry*(1 + exit_gain_trigger_pct/100); thereafter
    trails the rolling peak and fires when price falls to peak*(1 - exit_trail_pct/100).
    LONG positions only — shorts use the per-trade trailing_stop_pct mechanism.
    """
    direction = (position.get("direction") or "LONG").upper()
    if direction != "LONG":
        return False

    entry = float(position.get("entry_price") or position.get("price_at_scan") or 0.0)
    if entry <= 0 or current_price <= 0:
        return False

    trigger_pct = float(ops.get("exit_gain_trigger_pct", 3.0))
    trail_pct   = float(ops.get("exit_trail_pct", 1.5))
    active      = bool(position.get("trailing_stop_active"))
    log_id      = position.get("log_id")

    if not active:
        if current_price >= entry * (1 + trigger_pct / 100.0):
            from prime_data.prime_db import set_trailing_stop_active
            set_trailing_stop_active(log_id, True, current_price, db_path=db_path)
            logger.info(
                "Trailing stop ARMED for %s at %.4f (entry %.4f, trigger %.2f%%)",
                position.get("symbol"), current_price, entry, trigger_pct,
            )
        return False

    peak = position.get("trailing_stop_peak")
    peak_val = float(peak) if peak else entry

    # New high watermark — raise the peak, no exit.
    if current_price > peak_val:
        from prime_data.prime_db import update_trailing_stop_peak
        update_trailing_stop_peak(log_id, current_price, db_path=db_path)
        return False

    # Trail breach — fire the exit.
    if current_price <= peak_val * (1 - trail_pct / 100.0):
        _fire_exit_sell(position, current_price, "TRAILING_STOP", db_path=db_path)
        return True

    return False


def _check_day_count(
    position: Dict[str, Any],
    current_price: Optional[float],
    ops: Dict[str, Any],
    db_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Day-count exit (CIL-097). Returns True if it alerted or sold.

    When a position has been held >= exit_day_count_max calendar days, either log
    a DAY_COUNT_ALERT (ALERT, once per day) or fire a MATA sell (AUTO_SELL).
    """
    now = now or datetime.now()
    try:
        entry_dt = datetime.fromisoformat(str(position.get("entry_time")))
    except (TypeError, ValueError):
        return False

    hold_days = (now.date() - entry_dt.date()).days
    max_days  = int(ops.get("exit_day_count_max", 3))
    if hold_days < max_days:
        return False

    action = (ops.get("exit_day_count_action") or "ALERT").upper()
    symbol = (position.get("symbol") or "").upper()
    log_id = position.get("log_id")
    from prime_data.prime_db import _recent_trade_exists, log_ops_event

    if action == "AUTO_SELL":
        if current_price is None or current_price <= 0:
            return False
        # Fire at most once per day for this symbol.
        if _recent_trade_exists(symbol, "DAY_COUNT_AUTO", _utc_day_start(), db_path=db_path):
            return False
        _fire_exit_sell(position, current_price, "DAY_COUNT_AUTO", db_path=db_path)
        return True

    # ALERT (default): warn once per day.
    if _recent_trade_exists(symbol, "DAY_COUNT_ALERT", _utc_day_start(), db_path=db_path):
        return False
    log_ops_event(
        event_type="DAY_COUNT_ALERT",
        component="prime_stop_monitor",
        symbol=symbol,
        detail=f"log_id={log_id} hold_days={hold_days} max={max_days}",
        severity="WARN",
        db_path=db_path,
    )
    logger.warning("DAY_COUNT_ALERT: %s held %d days (max %d)", symbol, hold_days, max_days)
    return True


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------

def _monitor_loop(db_path: Optional[Path] = None) -> None:
    interval = _get_check_interval()
    logger.info("Stop monitor started (interval=%ds)", interval)
    while not _stop_event.is_set():
        try:
            if _is_rth():
                _run_check_cycle(db_path)
        except Exception as e:
            logger.error("Stop monitor cycle error: %s", e)
        # Re-read interval each cycle so Settings changes take effect without restart.
        interval = _get_check_interval()
        _stop_event.wait(timeout=interval)
    logger.info("Stop monitor stopped")


def _run_check_cycle(db_path: Optional[Path] = None) -> None:
    from prime_data.prime_db import get_open_trades
    ops = _read_ops_config()
    mode = (ops.get("stop_execution_mode") or "ALERT").upper()

    positions = get_open_trades(db_path=db_path)
    if not positions:
        return

    symbols = list({p.get("symbol", "").upper() for p in positions if p.get("symbol")})
    prices = _current_prices(symbols)
    if not prices:
        logger.debug("Stop monitor: no live prices available")
        return

    for pos in positions:
        sym   = (pos.get("symbol") or "").upper()
        price = prices.get(sym)
        if not price:
            continue

        # Sprint 30 PM-04: automated exits (gain-triggered trailing stop + day
        # count). These run only inside RTH because the whole cycle is gated by
        # _is_rth() in _monitor_loop. If either fires an exit, skip the legacy
        # stop-breach check for this (now-closed) position.
        if _check_trailing_stop(pos, price, ops, db_path):
            continue
        if _check_day_count(pos, price, ops, db_path):
            continue

        # Update trailing high-water mark
        trailing_pct = pos.get("trailing_stop_pct")
        if trailing_pct is not None:
            direction  = (pos.get("direction") or "LONG").upper()
            entry      = float(pos.get("entry_price") or pos.get("price_at_scan") or 0)
            hw         = pos.get("trailing_stop_high_water")
            new_hw     = _update_high_water(
                pos["log_id"], price, direction,
                float(hw) if hw else None,
                entry, db_path,
            )
            # Refresh for breach check
            pos = dict(pos)
            pos["trailing_stop_high_water"] = new_hw

        breach = _check_position(pos, price, ops)
        if breach:
            # Compute stop for alert logging — mirror _check_position logic.
            t_pct  = pos.get("trailing_stop_pct")
            hw_val = pos.get("trailing_stop_high_water")
            dir_   = (pos.get("direction") or "LONG").upper()
            ep     = float(pos.get("entry_price") or pos.get("price_at_scan") or 0)
            if t_pct is not None:
                stop = _trailing_stop_price(ep, float(t_pct), float(hw_val) if hw_val else ep, dir_)
            elif pos.get("stop_price") and float(pos["stop_price"]) > 0:
                stop = float(pos["stop_price"])
            else:
                if dir_ == "SHORT":
                    stop = ep * (1 + float(ops.get("short_stop_loss_pct", 0.05)))
                else:
                    stop = ep * (1 - float(ops.get("long_stop_loss_pct", 0.05)))

            if mode == "AUTO":
                _fire_auto_sell(pos, price, db_path)
            else:
                _fire_alert(pos, price, stop)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def start_monitor(db_path: Optional[Path] = None) -> None:
    """Start the stop monitor background thread (idempotent)."""
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return
    _stop_event.clear()
    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=(db_path,),
        daemon=True,
        name="prime_stop_monitor",
    )
    _monitor_thread.start()
    logger.info("Stop monitor thread started")


def stop_monitor() -> None:
    """Signal the monitor to stop (for graceful shutdown / tests)."""
    _stop_event.set()
