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
_CHECK_INTERVAL_S = 60

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
    """Return 'BREACH' if this position's stop is hit, else None."""
    direction   = (position.get("direction") or "LONG").upper()
    entry_price = float(position.get("entry_price") or position.get("price_at_scan") or 0.0)
    if entry_price <= 0 or current_price <= 0:
        return None

    trailing_pct = position.get("trailing_stop_pct")
    high_water   = position.get("trailing_stop_high_water")

    if trailing_pct is not None:
        hw = float(high_water) if high_water else entry_price
        stop = _trailing_stop_price(entry_price, float(trailing_pct), hw, direction)
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
# Monitor loop
# ---------------------------------------------------------------------------

def _monitor_loop(db_path: Optional[Path] = None) -> None:
    logger.info("Stop monitor started (interval=%ds)", _CHECK_INTERVAL_S)
    while not _stop_event.is_set():
        try:
            if _is_rth():
                _run_check_cycle(db_path)
        except Exception as e:
            logger.error("Stop monitor cycle error: %s", e)
        _stop_event.wait(timeout=_CHECK_INTERVAL_S)
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
            # Compute stop for logging
            t_pct  = pos.get("trailing_stop_pct")
            hw_val = pos.get("trailing_stop_high_water")
            dir_   = (pos.get("direction") or "LONG").upper()
            ep     = float(pos.get("entry_price") or pos.get("price_at_scan") or 0)
            if t_pct is not None:
                stop = _trailing_stop_price(ep, float(t_pct), float(hw_val) if hw_val else ep, dir_)
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
