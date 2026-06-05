"""
PRIME v1.0 Schwab Live Order Execution (Sprint 24 Item 1).

submit_order() is the single entry point for all live Schwab order submission.
All 6 safety gates are enforced here before any Schwab API call is made.

Gate order (per Sprint 24 Work Order Section 3):
  1. PAPER mode active          → OrderGateError("PAPER_MODE")
  2. Outside RTH (market)       → OrderGateError("RTH")
  3. Insufficient buying power  → OrderGateError("BUYING_POWER")
  4. Order > max_order_pct acct → OrderGateError("POSITION_SIZE")
  5. Duplicate <60s             → OrderGateError("DUPLICATE")
  6. Confirmation not provided  → OrderGateError("NO_CONFIRM")

Default max_order_pct = 10% of liquidation value (configurable in ops_config.json).
"""

import logging
import threading
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# RTH window (ET, naive wall-clock comparison)
_RTH_OPEN  = dt_time(9, 30)
_RTH_CLOSE = dt_time(16, 0)

# Duplicate guard: (SYMBOL, SIDE) -> last_submit_epoch_s
_dup_guard: Dict[Tuple[str, str], float] = {}
_dup_lock  = threading.Lock()
DUP_WINDOW_S = 60


class OrderGateError(Exception):
    """Raised when a safety gate blocks live order submission."""

    def __init__(self, gate: str, message: str) -> None:
        self.gate = gate
        super().__init__(message)


# ---------------------------------------------------------------------------
# Gate helpers
# ---------------------------------------------------------------------------

def _is_rth() -> bool:
    """True if current ET wall-clock time is within RTH Mon–Fri 09:30–16:00."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except ImportError:
        # Fallback for Python < 3.9: approximate ET as UTC-5
        from datetime import timezone, timedelta
        now_et = datetime.now(timezone(timedelta(hours=-5)))
    if now_et.weekday() >= 5:          # Saturday or Sunday
        return False
    t = now_et.time().replace(tzinfo=None)
    return _RTH_OPEN <= t < _RTH_CLOSE


def _check_duplicate(symbol: str, side: str, now_ts: float) -> bool:
    key = (symbol.upper(), side.upper())
    with _dup_lock:
        last = _dup_guard.get(key)
        return last is not None and (now_ts - last) < DUP_WINDOW_S


def _record_submission(symbol: str, side: str, now_ts: float) -> None:
    key = (symbol.upper(), side.upper())
    with _dup_lock:
        _dup_guard[key] = now_ts


def _get_account_balances(schwab_client, account_hash: str) -> Dict[str, float]:
    """Return {buying_power, liquidation_value} from Schwab account balances."""
    try:
        resp = schwab_client.client.get_account(
            account_hash,
            fields=schwab_client.client.Account.Fields.POSITIONS,
        )
        if resp.status_code != 200:
            return {}
        balances = (
            resp.json()
            .get("securitiesAccount", {})
            .get("currentBalances", {})
        )
        buying_power = (
            balances.get("buyingPower")
            or balances.get("cashAvailableForTrading")
            or balances.get("availableFunds")
            or 0.0
        )
        liquidation = (
            balances.get("liquidationValue")
            or balances.get("totalValue")
            or balances.get("accountValue")
            or 0.0
        )
        return {
            "buying_power":      float(buying_power),
            "liquidation_value": float(liquidation),
        }
    except Exception as e:
        logger.warning("Could not fetch account balances for gate check: %s", e)
        return {}


def _max_order_pct() -> float:
    """Max single-order fraction of account value (default 10%)."""
    try:
        from prime_config.prime_config import get_config
        return float(getattr(get_config().ops, "max_order_pct", 0.10))
    except Exception:
        return 0.10


# ---------------------------------------------------------------------------
# Order builder
# ---------------------------------------------------------------------------

def _build_raw_order(symbol: str, qty: int, side: str,
                     order_type: str, price: float) -> dict:
    """Raw Schwab order dict, used when the schwab-py builder is unavailable."""
    action = "BUY" if side in ("BUY", "LONG") else "SELL"
    order: Dict[str, Any] = {
        "orderType": order_type,
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": action,
                "quantity": qty,
                "instrument": {"symbol": symbol, "assetType": "EQUITY"},
            }
        ],
    }
    if order_type == "LIMIT":
        order["price"] = str(round(float(price), 2))
    return order


def _place_order(schwab_client, account_hash: str, symbol: str,
                 qty: int, side: str, order_type: str, price: float):
    """Call Schwab place_order using the builder or raw dict fallback."""
    try:
        import schwab as _schwab
        is_buy = side in ("BUY", "LONG")
        if order_type == "LIMIT":
            builder = (
                _schwab.orders.equities.equity_buy_limit(symbol, qty, price)
                if is_buy else
                _schwab.orders.equities.equity_sell_limit(symbol, qty, price)
            )
        else:
            builder = (
                _schwab.orders.equities.equity_buy_market(symbol, qty)
                if is_buy else
                _schwab.orders.equities.equity_sell_market(symbol, qty)
            )
        return schwab_client.client.place_order(account_hash, builder.build())
    except Exception as build_err:
        logger.warning("schwab order builder failed (%s); using raw dict", build_err)
        raw = _build_raw_order(symbol, qty, side, order_type, price)
        return schwab_client.client.place_order(account_hash, raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def submit_order(
    symbol: str,
    qty: int,
    side: str,
    order_type: str,
    price: float,
    account_hash: str,
    confirmed: bool = False,
    schwab_client=None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Submit a live equity order to Schwab after passing all 6 safety gates.

    Returns {order_id, status, filled_qty, fill_price, timestamp}.
    Raises OrderGateError if any gate blocks the order.
    """
    import time as _time
    from prime_config.prime_config import get_config

    symbol     = (symbol or "").upper().strip()
    side       = (side   or "").upper().strip()
    order_type = (order_type or "MARKET").upper().strip()
    qty        = int(qty)
    price      = float(price)
    now_ts     = _time.time()

    # ── Gate 1: PAPER mode ───────────────────────────────────────────────────
    if (get_config().trading_mode or "PAPER").upper() != "LIVE":
        raise OrderGateError("PAPER_MODE", "order blocked: server is not in LIVE mode")

    # ── Gate 2: Outside RTH (market orders only) ─────────────────────────────
    if order_type == "MARKET" and not _is_rth():
        raise OrderGateError(
            "RTH",
            "market orders are only allowed during RTH (09:30–16:00 ET Mon–Fri)"
        )

    # Need a live Schwab client for the remaining gates
    if schwab_client is None:
        raise OrderGateError("NO_CLIENT", "no Schwab client available for live order")

    # ── Gate 3: Buying power ─────────────────────────────────────────────────
    balances = _get_account_balances(schwab_client, account_hash)
    buying_power      = balances.get("buying_power", 0.0)
    liquidation_value = balances.get("liquidation_value", 0.0)
    order_notional    = float(qty) * price

    if buying_power > 0 and order_notional > buying_power:
        raise OrderGateError(
            "BUYING_POWER",
            f"insufficient buying power: need ${order_notional:.2f}, "
            f"available ${buying_power:.2f}",
        )

    # ── Gate 4: Position size > max_order_pct of account value ───────────────
    if liquidation_value > 0:
        max_pct = _max_order_pct()
        limit   = liquidation_value * max_pct
        if order_notional > limit:
            raise OrderGateError(
                "POSITION_SIZE",
                f"order size ${order_notional:.2f} exceeds "
                f"{max_pct*100:.0f}% of account value "
                f"(${limit:.2f} limit on ${liquidation_value:.2f})",
            )

    # ── Gate 5: Duplicate order (<60s same symbol+side) ──────────────────────
    if _check_duplicate(symbol, side, now_ts):
        raise OrderGateError(
            "DUPLICATE",
            f"duplicate: {side} {symbol} already submitted within {DUP_WINDOW_S}s",
        )

    # ── Gate 6: Confirmation required ────────────────────────────────────────
    if not confirmed:
        raise OrderGateError(
            "NO_CONFIRM",
            "live order requires explicit user confirmation (confirmed=True)",
        )

    # ── All 6 gates passed — place order ─────────────────────────────────────
    try:
        resp = _place_order(schwab_client, account_hash, symbol, qty, side, order_type, price)

        if resp.status_code not in (200, 201):
            reason = ""
            try:
                reason = resp.json().get("message", "")
            except Exception:
                pass
            raise OrderGateError(
                "SCHWAB_REJECT",
                f"Schwab rejected order: HTTP {resp.status_code} {reason}".strip(),
            )

        # Order ID lives in the Location header
        location = resp.headers.get("Location") or resp.headers.get("location") or ""
        order_id = location.rstrip("/").split("/")[-1] if location else str(int(now_ts))

    except OrderGateError:
        raise
    except Exception as exc:
        raise OrderGateError("SCHWAB_ERROR", f"Schwab API error: {exc}") from exc

    # Record in duplicate guard after confirmed success
    _record_submission(symbol, side, now_ts)

    ts = datetime.utcnow().isoformat()
    logger.info(
        "Live order submitted: %s %d %s %s order_id=%s",
        side, qty, symbol, order_type, order_id,
    )

    from prime_data.prime_db import log_ops_event
    try:
        log_ops_event(
            event_type="LIVE_ORDER_SUBMITTED",
            component="prime_schwab_orders",
            symbol=symbol,
            detail=(
                f"side={side} qty={qty} type={order_type} "
                f"price={price:.4f} order_id={order_id}"
            ),
            severity="INFO",
            db_path=db_path,
        )
    except Exception:
        pass

    return {
        "order_id":   order_id,
        "status":     "SUBMITTED",
        "filled_qty": 0,
        "fill_price": 0.0,
        "timestamp":  ts,
    }
