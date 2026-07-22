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
                err_body = resp.json()
                logger.error("Schwab order rejected HTTP %s: %s", resp.status_code, err_body)
                reason = (
                    err_body.get("message")
                    or err_body.get("error")
                    or str(err_body)
                ) or ""
            except Exception:
                try:
                    reason = resp.text[:500]
                except Exception:
                    pass
                logger.error("Schwab order rejected HTTP %s (non-JSON): %s", resp.status_code, reason)
            raise OrderGateError(
                "SCHWAB_REJECT",
                f"Schwab rejected order: HTTP {resp.status_code} — {reason}".strip(),
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


# ---------------------------------------------------------------------------
# WO-PRIME-ACTIVE-POSITION-MGMT-01 Part A: stop-order attachment
# ---------------------------------------------------------------------------

def _build_stop_order_raw(
    symbol: str, qty: int, instruction: str, stop_price: float
) -> dict:
    """Raw Schwab STOP order dict (GTC). Used for protective stop-loss attachment.

    Section 3.3: Schwab requires a two-step process — entry order first, then a
    separate stop order. The schwab-py builder library does not expose a simple
    equity stop helper, so we use the raw dict path that _place_order() already
    falls back to for unsupported order types.
    """
    return {
        "orderType": "STOP",
        "session":   "NORMAL",
        "duration":  "GOOD_TILL_CANCEL",
        "orderStrategyType": "SINGLE",
        "stopPrice": str(round(float(stop_price), 2)),
        "orderLegCollection": [{
            "instruction": instruction,  # "SELL" (LONG) or "BUY" (SHORT cover)
            "quantity":    int(qty),
            "instrument":  {"symbol": symbol, "assetType": "EQUITY"},
        }],
    }


def _build_trailing_stop_order_raw(
    symbol: str, qty: int, instruction: str, trail_pct: float
) -> dict:
    """Raw Schwab TRAILING_STOP order dict (GTC, percent-based).

    Schwab trails from the last price by trail_pct percent. The stop price
    adjusts automatically as price moves favorably; it never degrades.
    trail_pct is the percentage as a number (e.g. 3.0 for 3%).
    """
    return {
        "orderType":           "TRAILING_STOP",
        "session":             "NORMAL",
        "duration":            "GOOD_TILL_CANCEL",
        "orderStrategyType":   "SINGLE",
        "stopPriceLinkBasis":  "LAST",
        "stopPriceLinkType":   "PERCENT",
        "stopPriceOffset":     round(float(trail_pct), 2),
        "orderLegCollection": [{
            "instruction": instruction,
            "quantity":    int(qty),
            "instrument":  {"symbol": symbol, "assetType": "EQUITY"},
        }],
    }


def attach_stop_order(
    symbol: str,
    qty: int,
    direction: str,
    stop_price: float,
    account_hash: str,
    schwab_client,
    db_path: Optional[Path] = None,
    min_stop_price: Optional[float] = None,
    trail_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Submit a protective STOP order to Schwab as a guaranteed follow-up after entry.

    WO-PRIME-ACTIVE-POSITION-MGMT-01 Section 3.3: Schwab does not support
    bracket/OCO orders for equity entries, so the stop is a separate submission.
    This function bypasses the 6-gate submit_order() because those gates govern
    entry orders; this is a protective, pre-authorized follow-up order.

    LONG: SELL STOP at stop_price (price floor).
    SHORT: BUY STOP at stop_price (buy-to-cover trigger).

    min_stop_price (addendum — stop escalation guard): when provided, the proposed
    stop_price must not be worse than this floor (LONG: stop_price >= min_stop_price;
    SHORT: stop_price <= min_stop_price). Callers set this to the trailing-stop
    high-water level so an escalated stop can never be degraded by a re-attachment.

    Returns {order_id, status}. Raises OrderGateError on rejection or missing params.
    If stop attachment fails after a confirmed entry fill, the caller must escalate
    immediately to a Tier 2 alert (see prime_stop_monitor.py Part B).
    """
    symbol     = (symbol or "").upper().strip()
    direction  = (direction or "LONG").upper()
    instruction = "BUY" if direction == "SHORT" else "SELL"
    stop_price  = round(float(stop_price), 2)

    # Addendum to WO-PRIME-SCENARIO-EXECUTE-01: stop escalation guard.
    # Never submit a stop that degrades a trailing stop already at a better level.
    if min_stop_price is not None and float(min_stop_price) > 0:
        _floor = round(float(min_stop_price), 2)
        if direction == "LONG" and stop_price < _floor:
            raise OrderGateError(
                "STOP_ESCALATION",
                f"attach_stop_order: proposed stop {stop_price:.2f} < high-water floor "
                f"{_floor:.2f} for LONG {symbol} — would degrade escalated trailing stop",
            )
        elif direction == "SHORT" and stop_price > _floor:
            raise OrderGateError(
                "STOP_ESCALATION",
                f"attach_stop_order: proposed stop {stop_price:.2f} > high-water ceiling "
                f"{_floor:.2f} for SHORT {symbol} — would degrade escalated trailing stop",
            )

    _use_trailing = trail_pct is not None and float(trail_pct) > 0
    if not symbol or int(qty) <= 0 or not account_hash:
        raise OrderGateError(
            "STOP_PARAMS",
            f"attach_stop_order: invalid params — symbol={symbol!r} qty={qty} "
            f"account_hash={bool(account_hash)}",
        )
    if not _use_trailing and stop_price <= 0:
        raise OrderGateError(
            "STOP_PARAMS",
            f"attach_stop_order: stop_price required for fixed stop (got {stop_price})",
        )
    if schwab_client is None:
        raise OrderGateError("NO_CLIENT", "attach_stop_order: no Schwab client available")

    if _use_trailing:
        raw = _build_trailing_stop_order_raw(symbol, int(qty), instruction, float(trail_pct) * 100.0)
    else:
        raw = _build_stop_order_raw(symbol, int(qty), instruction, stop_price)
    try:
        resp = schwab_client.client.place_order(account_hash, raw)
    except Exception as exc:
        raise OrderGateError("SCHWAB_ERROR", f"attach_stop_order API error: {exc}") from exc

    if resp.status_code not in (200, 201):
        reason = ""
        try:
            err_body = resp.json()
            logger.error("Schwab stop order rejected HTTP %s: %s", resp.status_code, err_body)
            reason = (
                err_body.get("message")
                or err_body.get("error")
                or str(err_body)
            ) or ""
        except Exception:
            try:
                reason = resp.text[:500]
            except Exception:
                pass
            logger.error("Schwab stop order rejected HTTP %s (non-JSON): %s", resp.status_code, reason)
        raise OrderGateError(
            "SCHWAB_REJECT",
            f"Schwab rejected stop order: HTTP {resp.status_code} — {reason}".strip(),
        )

    import time as _time
    location = resp.headers.get("Location") or resp.headers.get("location") or ""
    stop_order_id = location.rstrip("/").split("/")[-1] if location else str(int(_time.time()))

    if _use_trailing:
        logger.info(
            "Trailing stop attached: %s %d %s trail=%.1f%% order_id=%s",
            symbol, qty, direction, float(trail_pct) * 100.0, stop_order_id,
        )
    else:
        logger.info(
            "Stop order attached: %s %d %s STOP=%.2f order_id=%s",
            symbol, qty, direction, stop_price, stop_order_id,
        )
    try:
        from prime_data.prime_db import log_ops_event
        _stop_detail = (
            f"direction={direction} qty={qty} trail_pct={float(trail_pct)*100:.1f}% order_id={stop_order_id}"
            if _use_trailing else
            f"direction={direction} qty={qty} stop_price={stop_price:.2f} order_id={stop_order_id}"
        )
        log_ops_event(
            event_type="STOP_ORDER_ATTACHED",
            component="prime_schwab_orders",
            symbol=symbol,
            detail=_stop_detail,
            severity="INFO",
            db_path=db_path,
        )
    except Exception:
        pass

    return {"order_id": stop_order_id, "status": "STOP_SUBMITTED"}


def has_open_stop_order(
    symbol: str,
    account_hash: str,
    schwab_client,
) -> bool:
    """Return True if an open STOP order already exists for symbol on this account.

    Addendum to WO-PRIME-SCENARIO-EXECUTE-01: pre-attachment guard prevents
    duplicate stops. Fail-open — returns False on any API error so a transient
    network blip never silently blocks stop attachment.
    """
    symbol = (symbol or "").upper().strip()
    try:
        resp = schwab_client.client.get_orders_for_account(account_hash)
        if resp.status_code != 200:
            return False
        for o in (resp.json() or []):
            if (o.get("status") or "").upper() not in (
                "AWAITING_PARENT_ORDER", "AWAITING_CONDITION",
                "PENDING_ACTIVATION", "QUEUED", "WORKING", "PENDING_REPLACE",
            ):
                continue
            if (o.get("orderType") or "").upper() not in ("STOP", "STOP_LIMIT", "TRAILING_STOP", "TRAILING_STOP_LIMIT"):
                continue
            for leg in (o.get("orderLegCollection") or []):
                if (leg.get("instrument", {}).get("symbol") or "").upper() == symbol:
                    logger.debug("has_open_stop_order: existing stop found for %s", symbol)
                    return True
    except Exception as exc:
        logger.debug("has_open_stop_order: check failed for %s: %s", symbol, exc)
    return False
