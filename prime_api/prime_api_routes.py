"""
PRIME v1.0 API Route Definitions (UI-CONTRACT-001).

Read-only REST endpoints for Lovable UI consumption.
All reads delegate to prime_db.py -- zero direct SQL in this file.
"""

import hmac
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

_LOCALHOST = {"127.0.0.1", "::1", "localhost"}


def require_local_token(view):
    """Auth guard for write endpoints (Sprint 14 Item 2).

    Enforces: (1) request originates from localhost; (2) a non-empty bearer
    token in the Authorization header matches config.api_token (constant-time
    compare). The token lives in config.json, which is never committed.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        from prime_config.prime_config import get_config

        if request.remote_addr not in _LOCALHOST:
            return jsonify({"error": "forbidden: localhost only"}), 403

        expected = (get_config().api_token or "").strip()
        header = request.headers.get("Authorization", "")
        provided = header[7:].strip() if header.startswith("Bearer ") else ""
        if not expected or not provided or not hmac.compare_digest(expected, provided):
            return jsonify({"error": "unauthorized: invalid or missing token"}), 401

        return view(*args, **kwargs)

    return wrapper


@api_bp.route("/positions", methods=["GET"])
def get_positions():
    """GET /api/v1/positions -- OPEN positions with live P&L / stop / hold time.

    Each position is enriched (Sprint 16 Item 5) with unrealized P&L, a stop
    alert badge (GREEN/AMBER/RED), the computed stop price, and a human-readable
    hold time + time-stop flag. Current price uses a live Schwab quote when
    available, else the last known price.
    """
    from prime_data.prime_db import get_open_positions
    from prime_api.prime_positions import enrich_position
    try:
        positions = get_open_positions()
        now = datetime.now()
        enriched = [enrich_position(p, current_price=None, now=now) for p in positions]
        return jsonify({"positions": enriched, "count": len(enriched)}), 200
    except Exception as e:
        logger.error("positions endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


def _health_current_prices(symbols: list) -> Dict[str, float]:
    """Best-effort batch of live Schwab last/mark prices, keyed by upper symbol."""
    prices: Dict[str, float] = {}
    if not symbols:
        return prices
    try:
        from prime_trading.prime_schwab import SchwabClient
        sc = SchwabClient()
        sc.connect()
        for sym, q in (sc.get_quotes(symbols) or {}).items():
            price = q.get("quote", {}).get("lastPrice") or q.get("quote", {}).get("mark") or 0.0
            if price:
                prices[sym.upper()] = float(price)
    except Exception:
        pass
    return prices


def _days_held(entry_time: Any, now: datetime) -> int:
    """Whole calendar days between entry_time and now (0 if unparseable)."""
    if not entry_time:
        return 0
    raw = str(entry_time).strip().replace("Z", "")
    for parse in (datetime.fromisoformat,):
        try:
            entered = parse(raw)
            return max(0, (now - entered.replace(tzinfo=None)).days)
        except ValueError:
            break
    try:
        entered = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        return max(0, (now - entered).days)
    except ValueError:
        return 0


@api_bp.route("/positions/health", methods=["GET"])
def get_positions_health():
    """GET /api/v1/positions/health -- per-position thesis health (PM-HEALTH-03).

    Serves prime_position_health (written by the PositionMonitor), overlaid on
    the current OPEN positions. When the health table is empty (monitor hasn't
    run yet) every position comes back thesis_status='UNKNOWN' with red_count=0.
    current_price/pnl_pct use a best-effort live Schwab quote, falling back to
    the entry price from prime_trade_log.
    """
    from prime_data.prime_db import get_open_positions_with_signal_context
    from prime_trading.prime_position_monitor import load_position_health
    try:
        now = datetime.utcnow()
        db_positions = get_open_positions_with_signal_context()
        health_by_log = load_position_health()

        symbols = sorted({(p.get("symbol") or "").upper()
                          for p in db_positions if p.get("symbol")})
        current_prices = _health_current_prices(symbols)

        positions = []
        red_count = amber_count = 0
        for p in db_positions:
            log_id = p.get("log_id")
            symbol = (p.get("symbol") or "").upper()
            direction = (p.get("direction") or "LONG").upper()
            entry_price = float(p.get("entry_price") or 0.0)
            cur_price = current_prices.get(symbol, entry_price)
            if entry_price:
                if direction == "SHORT":
                    pnl_pct = (entry_price - cur_price) / entry_price * 100.0
                else:
                    pnl_pct = (cur_price - entry_price) / entry_price * 100.0
            else:
                pnl_pct = 0.0

            h = health_by_log.get(str(log_id))
            if h:
                thesis = h.get("thesis_status") or "UNKNOWN"
                dk_status = h.get("dk_status")
                latest_dir = h.get("latest_signal_direction")
                latest_ts = h.get("latest_signal_ts")
                evaluated_at = h.get("evaluated_at")
            else:
                thesis = "UNKNOWN"
                dk_status = p.get("dk_status")
                latest_dir = latest_ts = evaluated_at = None

            if thesis == "RED":
                red_count += 1
            elif thesis == "AMBER":
                amber_count += 1

            raw_stop = p.get("stop_price")
            positions.append({
                "log_id": log_id,
                "symbol": symbol,
                "direction": direction,
                "entry_price": round(entry_price, 4),
                "current_price": round(cur_price, 4),
                "pnl_pct": round(pnl_pct, 2),
                "days_held": _days_held(p.get("entry_time"), now),
                "dk_status": dk_status,
                "scanner": p.get("scanner"),
                "latest_signal_direction": latest_dir,
                "latest_signal_ts": latest_ts,
                "thesis_status": thesis,
                "evaluated_at": evaluated_at,
                "stop_price": round(float(raw_stop), 4) if raw_stop is not None else None,
            })

        return jsonify({
            "positions": positions,
            "red_count": red_count,
            "amber_count": amber_count,
            "as_of": now.isoformat(),
        }), 200
    except Exception as e:
        logger.error("positions/health endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/signals", methods=["GET"])
def get_signals():
    """GET /api/v1/signals -- recent prime_signals (filterable)."""
    from prime_analytics.prime_signals_db import get_signals as fetch_signals
    strategy = request.args.get("strategy")
    instrument_type = request.args.get("instrument_type")
    try:
        kwargs = {"limit": 200}
        if strategy:
            kwargs["strategy"] = strategy
        signals = fetch_signals(**kwargs)
        if instrument_type:
            signals = [s for s in signals if s.get("instrument_type") == instrument_type]
        return jsonify({"signals": signals, "count": len(signals)}), 200
    except Exception as e:
        logger.error("signals endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/signals/<string:signal_id>/dismiss", methods=["POST"])
@require_local_token
def dismiss_signal_endpoint(signal_id):
    """POST /api/v1/signals/{signal_id}/dismiss -- soft-delete a pending signal.

    CIL-075. Sets prime_signals.status='DISMISSED' (a soft delete that preserves
    the row for ML training data). Returns 404 if the signal does not exist, 409
    if it was already dismissed.
    """
    from prime_analytics.prime_signals_db import dismiss_signal

    if not signal_id:
        return jsonify({"error": "signal_id is required"}), 400

    try:
        result = dismiss_signal(signal_id)
    except Exception as e:
        logger.error("dismiss_signal error: %s", e)
        return jsonify({"error": str(e)}), 500

    if result == "NOT_FOUND":
        return jsonify({"error": "unknown signal_id"}), 404
    if result == "ALREADY_DISMISSED":
        return jsonify({"error": "signal already dismissed"}), 409
    return jsonify({"signal_id": signal_id, "status": "DISMISSED"}), 200


def _is_rth() -> bool:
    """True if the current ET wall-clock time falls within RTH (09:30–16:00 Mon–Fri)."""
    import zoneinfo
    from datetime import timezone as _tz
    try:
        et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: UTC-4 (EDT) if zoneinfo unavailable
        et = datetime.now(_tz(timedelta(hours=-4)))
    if et.weekday() >= 5:
        return False
    mins = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= mins <= 16 * 60


@api_bp.route("/signals/<string:signal_id>/execute", methods=["POST"])
@require_local_token
def execute_signal_endpoint(signal_id):
    """POST /api/v1/signals/{signal_id}/execute -- buy an APPROVED signal via MATA.

    CIL-NEW-06. Body: {order_type, limit_price (optional), confirmed: true}.
    Returns: {orders_placed, allocated_total, signal_id}.
    Writes each filled trade to prime_trade_log with signal_id linkage.
    Updates signal status to EXECUTED on success.
    PAPER mode: routes through PAPER path (no Schwab call).
    After-hours: MARKET orders outside RTH are rejected with a 400 so the UI
    can prompt for a limit price.
    """
    from prime_analytics.prime_signals_db import get_signal_by_id, update_signal_status
    from prime_config.prime_config import get_config
    from prime_data.prime_db import insert_trade, TradeRecordError

    if not signal_id:
        return jsonify({"error": "signal_id is required"}), 400

    payload = request.get_json(silent=True) or {}
    order_type = str(payload.get("order_type", "MARKET")).strip().upper()
    if order_type not in ("MARKET", "LIMIT"):
        order_type = "MARKET"
    confirmed = bool(payload.get("confirmed", False))
    limit_price_raw = payload.get("limit_price")

    # CIL-NEW-08: staged entry params
    staged_entry_on    = bool(payload.get("staged_entry", False))
    stage_count        = int(payload.get("stage_count", 2))
    stage_trigger      = str(payload.get("stage_trigger", "TIME")).strip().upper()
    stage_interval_min = int(payload.get("stage_interval_min", 30))
    if stage_count not in (2, 3):
        stage_count = 2
    if stage_trigger not in ("TIME", "DK_CONFIRM"):
        stage_trigger = "TIME"

    # WO-PRIME-BUY-DIALOG-QUANTITY-01: user-supplied share qty overrides auto-compute.
    user_qty = int(payload.get("qty", 0)) if payload.get("qty") is not None else 0
    if user_qty < 0:
        user_qty = 0

    # WO-PRIME-SCENARIO-EXECUTE-01: optional per-execution overrides (all backward-compatible).
    target_account  = (payload.get("target_account") or "").strip()  # 4-char suffix; routes to this account only
    direction_param = (payload.get("direction") or "LONG").strip().upper()
    if direction_param not in ("LONG", "SHORT"):
        direction_param = "LONG"
    stop_type_param = (payload.get("stop_type") or "").strip().upper()
    if stop_type_param not in ("FIXED", "TRAILING"):
        stop_type_param = ""  # empty = no stop attachment requested
    stop_pct_raw  = payload.get("stop_pct")
    trail_pct_raw = payload.get("trailing_stop_pct")

    if not confirmed:
        return jsonify({"error": "confirmed is required to execute a signal"}), 400

    sig = get_signal_by_id(signal_id)
    if sig is None:
        return jsonify({"error": "unknown signal_id"}), 404
    if (sig.get("status") or "").upper() not in ("APPROVED", "STRONG", "WATCH"):
        return jsonify({"error": f"signal status is '{sig.get('status')}' — only APPROVED signals can be executed"}), 409

    symbol = (sig.get("symbol") or "").upper()
    strategy = sig.get("strategy") or "SIGNAL"
    entry_price_scan = float(sig.get("entry_price") or 0.0)

    # After-hours guard: MARKET orders require RTH.
    if order_type == "MARKET" and not _is_rth():
        return jsonify({
            "error": "after_hours",
            "message": "After-hours session — LIMIT order required. Enter a limit price.",
        }), 400

    # Resolve limit price.
    limit_price_val: float | None = None
    if order_type == "LIMIT":
        try:
            limit_price_val = float(limit_price_raw) if limit_price_raw is not None else None
        except (TypeError, ValueError):
            limit_price_val = None
        if not limit_price_val or limit_price_val <= 0:
            return jsonify({"error": "limit_price is required for LIMIT orders"}), 400

    cfg = get_config()
    mode_cfg = (cfg.trading_mode or "PAPER").upper()
    now = datetime.now()

    # ── Fetch live quote ───────────────────────────────────────────────────────
    live_price = 0.0
    schwab_client = None
    try:
        from prime_trading.prime_schwab import SchwabClient
        schwab_client = SchwabClient()
        schwab_client.connect()
        quotes = schwab_client.get_quotes([symbol])
        data = quotes.get(symbol) or quotes.get(symbol.upper()) or {}
        price = (
            data.get("quote", {}).get("lastPrice")
            or data.get("quote", {}).get("mark")
            or 0.0
        )
        live_price = float(price) if price else 0.0
    except Exception as e:
        logger.debug("execute_signal: live quote failed for %s: %s", symbol, e)

    execution_price = limit_price_val if order_type == "LIMIT" and limit_price_val else live_price
    if execution_price <= 0:
        execution_price = entry_price_scan
    if execution_price <= 0:
        return jsonify({"error": "could not determine a valid execution price — Schwab may be offline"}), 400

    # ── Load MATA accounts and compute per-account allocation ─────────────────
    max_order_pct = float(getattr(cfg.ops, "max_order_pct", 0.1))
    orders_placed = []
    total_allocated = 0

    # WO-PRIME-SCENARIO-EXECUTE-01: pre-compute stop price when caller requests it.
    _exec_stop_price = 0.0
    _exec_trail_pct  = None
    if stop_type_param:
        try:
            _sops: dict = {}
            try:
                import json as _jmod
                with open(_OPS_CONFIG_PATH, "r", encoding="utf-8") as _sf:
                    _sops = _jmod.load(_sf)
            except Exception:
                pass
            _def_sp = float(_sops.get("default_stop_loss_pct", 3.0))
            _sp_pct = float(stop_pct_raw) if stop_pct_raw is not None else _def_sp
            _exec_stop_price = round(
                execution_price * (1 + _sp_pct / 100.0) if direction_param == "SHORT"
                else execution_price * (1 - _sp_pct / 100.0), 4,
            )
            if stop_type_param == "TRAILING" and trail_pct_raw is not None:
                _exec_trail_pct = float(trail_pct_raw)
        except (TypeError, ValueError):
            pass

    try:
        from prime_trading.prime_mata import load_accounts
        mata_accounts = load_accounts()
    except Exception:
        mata_accounts = []

    # CIL-NEW-13: resolve which accounts to route to based on active mata_profile.
    _live_profile = (getattr(cfg.ops, "mata_profile", "") or "").strip().lower()
    _live_profile_all = not _live_profile or _live_profile == "all"

    if mode_cfg == "LIVE" and schwab_client is not None:
        # Fetch live buying power per account.
        try:
            acct_numbers_resp = schwab_client.client.get_account_numbers()
            if acct_numbers_resp.status_code == 200:
                for acct in acct_numbers_resp.json():
                    suffix = (acct.get("accountNumber") or "")[-4:]
                    hash_val = acct.get("hashValue", "")
                    # Check if this account is in MATA profile; default weight=1.
                    mata_entry = next(
                        (a for a in mata_accounts if str(a.get("name", "")).endswith(suffix)), None
                    )
                    if mata_accounts and mata_entry is None:
                        continue  # account not in MATA profile — skip
                    # CIL-NEW-13: single-account profile — skip non-matching accounts.
                    if not _live_profile_all and mata_entry:
                        if mata_entry.get("name", "").strip().lower() != _live_profile:
                            continue
                    # WO-PRIME-SCENARIO-EXECUTE-01: target_account overrides profile when set.
                    if target_account and not suffix.endswith(target_account):
                        continue
                    try:
                        bp_resp = schwab_client.client.get_account(hash_val)
                        if bp_resp.status_code == 200:
                            cb = bp_resp.json().get("securitiesAccount", {}).get("currentBalances", {})
                            buying_power = float(cb.get("buyingPower") or cb.get("availableFunds") or 0.0)
                        else:
                            buying_power = 0.0
                    except Exception:
                        buying_power = 0.0
                    shares = user_qty if user_qty > 0 else int(buying_power * max_order_pct / execution_price)
                    if shares <= 0:
                        continue
                    try:
                        from prime_trading.prime_schwab_orders import submit_order, OrderGateError
                        result = submit_order(
                            symbol=symbol,
                            qty=shares,
                            side="BUY",
                            order_type=order_type,
                            price=execution_price,
                            account_hash=hash_val,
                            confirmed=True,
                            schwab_client=schwab_client,
                        )
                        log_id = insert_trade(
                            strategy=strategy,
                            symbol=symbol,
                            direction=direction_param,
                            mode="LIVE",
                            order_type=order_type,
                            shares=shares,
                            entry_time=now.isoformat(),
                            price_at_scan=entry_price_scan or execution_price,
                            entry_price=execution_price,
                            account=suffix,
                            order_id=result.get("order_id"),
                            signal_source="SIGNAL_EXECUTE",
                            trade_source="LIVE",
                            limit_price=limit_price_val,
                            signal_id=signal_id,
                            stage_number=1 if staged_entry_on else None,
                            stage_total=stage_count if staged_entry_on else None,
                            stop_price=_exec_stop_price if _exec_stop_price > 0 else None,
                            stop_type=stop_type_param if stop_type_param else None,
                        )
                        orders_placed.append({
                            "account": suffix,
                            "shares": shares,
                            "order_id": result.get("order_id"),
                            "log_id": log_id,
                            "status": "SUBMITTED",
                        })
                        total_allocated += shares
                        # WO-PRIME-SCENARIO-EXECUTE-01: attach protective stop after fill.
                        if _exec_stop_price > 0:
                            try:
                                from prime_trading.prime_schwab_orders import (
                                    attach_stop_order, has_open_stop_order,
                                )
                                if has_open_stop_order(symbol, hash_val, schwab_client):
                                    logger.info(
                                        "execute_signal: stop already on Schwab for %s/%s — skipping",
                                        symbol, suffix,
                                    )
                                else:
                                    attach_stop_order(
                                        symbol=symbol, qty=shares,
                                        direction=direction_param,
                                        stop_price=_exec_stop_price,
                                        account_hash=hash_val,
                                        schwab_client=schwab_client,
                                    )
                                    if stop_type_param == "TRAILING" and _exec_trail_pct is not None and log_id:
                                        from prime_data.prime_db import update_trailing_stop
                                        update_trailing_stop(log_id, _exec_trail_pct)
                            except Exception as _stop_err:
                                logger.error(
                                    "execute_signal: stop attachment failed for %s/%s: %s",
                                    symbol, suffix, _stop_err,
                                )
                    except Exception as order_err:
                        logger.error("execute_signal LIVE order failed for %s: %s", suffix, order_err)
                        orders_placed.append({
                            "account": suffix,
                            "shares": shares,
                            "status": "FAILED",
                            "error": str(order_err),
                        })
        except Exception as e:
            logger.error("execute_signal: account iteration error: %s", e)

    else:
        # PAPER mode: simulate across MATA accounts (or one synthetic account).
        # CIL-NEW-13: filter by active mata_profile when not 'all'.
        _exec_profile = (getattr(cfg.ops, "mata_profile", "") or "").strip().lower()
        if mata_accounts and _exec_profile and _exec_profile != "all":
            _filtered = [a for a in mata_accounts if a.get("name", "").strip().lower() == _exec_profile]
            paper_accounts = _filtered if _filtered else mata_accounts
        else:
            paper_accounts = mata_accounts if mata_accounts else [{"name": "PAPER", "buying_power": 100000}]
        # WO-PRIME-SCENARIO-EXECUTE-01: target_account filters to a single account.
        if target_account:
            _ta_filtered = [a for a in paper_accounts if str(a.get("name", "")).endswith(target_account)]
            if _ta_filtered:
                paper_accounts = _ta_filtered
        for acct in paper_accounts:
            bp = float(acct.get("buying_power", 100000) or 100000)
            shares = user_qty if user_qty > 0 else int(bp * max_order_pct / execution_price)
            if shares <= 0:
                continue
            acct_name = str(acct.get("name", "PAPER"))
            try:
                log_id = insert_trade(
                    strategy=strategy,
                    symbol=symbol,
                    direction=direction_param,
                    mode="PAPER",
                    order_type=order_type,
                    shares=shares,
                    entry_time=now.isoformat(),
                    price_at_scan=entry_price_scan or execution_price,
                    entry_price=execution_price,
                    account=acct_name,
                    signal_source="SIGNAL_EXECUTE",
                    trade_source="PAPER",
                    limit_price=limit_price_val,
                    signal_id=signal_id,
                    stage_number=1 if staged_entry_on else None,
                    stage_total=stage_count if staged_entry_on else None,
                    stop_price=_exec_stop_price if _exec_stop_price > 0 else None,
                    stop_type=stop_type_param if stop_type_param else None,
                )
                if stop_type_param == "TRAILING" and _exec_trail_pct is not None and log_id:
                    from prime_data.prime_db import update_trailing_stop
                    update_trailing_stop(log_id, _exec_trail_pct)
                orders_placed.append({
                    "account": acct_name,
                    "shares": shares,
                    "log_id": log_id,
                    "status": "PAPER_SIMULATED",
                })
                total_allocated += shares
            except TradeRecordError as e:
                orders_placed.append({"account": acct_name, "shares": shares, "status": "FAILED", "error": str(e)})

    if total_allocated > 0:
        try:
            update_signal_status(signal_id, "EXECUTED")
        except Exception as e:
            logger.warning("execute_signal: could not update signal status: %s", e)

        # CIL-NEW-08: register and schedule follow-on stages if staged entry is on.
        if staged_entry_on and stage_count > 1:
            try:
                from prime_trading.prime_staged_entry import StagedEntry, register
                paper_accounts_list = []
                live_accounts_list = []
                if mode_cfg == "PAPER":
                    _exec_profile = (getattr(cfg.ops, "mata_profile", "") or "").strip().lower()
                    from prime_trading.prime_mata import load_accounts
                    _all_accts = load_accounts()
                    if _all_accts and _exec_profile and _exec_profile != "all":
                        _filtered = [a for a in _all_accts if a.get("name", "").strip().lower() == _exec_profile]
                        paper_accounts_list = _filtered if _filtered else _all_accts
                    else:
                        paper_accounts_list = _all_accts or [{"name": "PAPER", "buying_power": 100000}]
                staged = StagedEntry(
                    signal_id=signal_id,
                    symbol=symbol,
                    strategy=strategy,
                    stage_count=stage_count,
                    stage_trigger=stage_trigger,
                    stage_interval_min=stage_interval_min,
                    completed_stages=1,
                    paper_accounts=paper_accounts_list,
                    live_accounts=live_accounts_list,
                    execution_price=execution_price,
                    order_type=order_type,
                    mode=mode_cfg,
                    max_order_pct=max_order_pct,
                    entry_price_scan=entry_price_scan or execution_price,
                )
                register(staged)

                if stage_trigger == "TIME":
                    _schedule_staged_entry_time_job(signal_id, stage_interval_min)
            except Exception as e:
                logger.error("execute_signal: staged entry registration failed: %s", e)

    return jsonify({
        "signal_id":       signal_id,
        "symbol":          symbol,
        "orders_placed":   orders_placed,
        "allocated_total": total_allocated,
        "execution_price": execution_price,
        "mode":            mode_cfg,
        "staged_entry":    staged_entry_on,
        "stage_count":     stage_count if staged_entry_on else None,
        "stage_trigger":   stage_trigger if staged_entry_on else None,
        "stop_price":      _exec_stop_price if _exec_stop_price > 0 else None,
        "stop_type":       stop_type_param if stop_type_param else None,
    }), 200 if orders_placed else 400


def _schedule_staged_entry_time_job(signal_id: str, interval_min: int) -> None:
    """Register an APScheduler one-shot job to fire the next staged tranche."""
    from datetime import timedelta
    from apscheduler.triggers.date import DateTrigger

    if _SCHEDULER is None or not _SCHEDULER.running:
        return

    fire_time = datetime.now() + timedelta(minutes=interval_min)
    job_id = f"staged_{signal_id}"

    def _fire():
        from prime_trading.prime_staged_entry import get_pending, execute_next_stage
        entry = get_pending(signal_id)
        if entry is None:
            return
        result = execute_next_stage(entry)
        # If more stages remain and trigger is TIME, re-schedule.
        entry_after = get_pending(signal_id)
        if entry_after is not None and entry_after.stage_trigger == "TIME":
            _schedule_staged_entry_time_job(signal_id, entry_after.stage_interval_min)
        logger.info("staged_entry: TIME job fired for %s — result: %s", signal_id, result)

    try:
        _SCHEDULER.add_job(
            _fire,
            trigger=DateTrigger(run_date=fire_time),
            id=job_id,
            replace_existing=True,
        )
        logger.info(
            "staged_entry: TIME job scheduled for signal %s in %d min at %s",
            signal_id, interval_min, fire_time.strftime("%H:%M"),
        )
    except Exception as e:
        logger.error("staged_entry: could not schedule TIME job: %s", e)


@api_bp.route("/advisory/positions", methods=["GET"])
def get_position_advisory():
    """GET /api/v1/advisory/positions -- Claude HOLD/TRIM/EXIT per open position.

    Degrades gracefully: when the API is unavailable each entry comes back with
    recommendation 'UNAVAILABLE' rather than erroring.
    """
    from prime_ai.prime_position_advisor import advise_positions
    try:
        advisories = advise_positions()
        return jsonify({"advisories": advisories, "count": len(advisories)}), 200
    except Exception as e:
        logger.error("position advisory error: %s", e)
        return jsonify({"advisories": [], "count": 0, "error": str(e)}), 200


@api_bp.route("/advisory/briefing", methods=["GET"])
def get_advisory_briefing():
    """GET /api/v1/advisory/briefing -- one-call AI portfolio briefing (Item 4)."""
    from prime_ai.prime_briefing import generate_briefing
    try:
        return jsonify(generate_briefing()), 200
    except Exception as e:
        logger.error("advisory briefing error: %s", e)
        return jsonify({"headline": "AI briefing unavailable",
                        "recommended_actions": [], "error": str(e)}), 200


_SCAN_EXPLAIN_PROMPTS = {
    "mmr": (
        "You are analyzing a Metals Mean-Reversion (MMR) scan run. "
        "MMR uses RSI(14) to identify oversold bounce conditions on precious metals instruments. "
        "Explain in plain English why this run produced {signal_count} signal(s). "
        "Reference the RSI/SMA/pct_from_sma metrics visible in the log. "
        "If 0 signals: explain what conditions were not met. "
        "Be specific — mention actual metric values from the log if present. "
        "150–300 words."
    ),
    "uoa": (
        "You are analyzing an Unusual Options Activity (UOA) scan run. "
        "UOA detects elevated options volume via sizzle index and DTE filters. "
        "Explain in plain English why this run produced {signal_count} signal(s). "
        "Reference sizzle index minimums, DTE windows, and volume ratios from the log. "
        "If 0 signals: explain what thresholds were not reached. "
        "150–300 words."
    ),
    "psa": (
        "You are analyzing a Price-and-Signal Analyzer (PSA) scan run. "
        "PSA uses volume surge, momentum scores, and trend filters. "
        "Explain in plain English why this run produced {signal_count} signal(s). "
        "Reference volume thresholds and momentum scores from the log. "
        "If 0 signals: explain which filter eliminated candidates. "
        "150–300 words."
    ),
    "short": (
        "You are analyzing a Short-Selling scan run. "
        "SHORT identifies deteriorating stocks using borrow rate checks and put volume surges. "
        "Explain in plain English why this run produced {signal_count} signal(s). "
        "Reference borrow availability, put/call ratios, and downtrend signals from the log. "
        "If 0 signals: explain what conditions were not met. "
        "150–300 words."
    ),
}

_SCAN_EXPLAIN_GENERIC = (
    "You are analyzing a PRIME scanner ({scanner}) run. "
    "Explain in plain English why this run produced {signal_count} signal(s). "
    "Reference any metric values, thresholds, or rejection reasons visible in the log. "
    "If 0 signals: speculate on what may have prevented signals based on the log. "
    "150–300 words."
)


@api_bp.route("/advisory/scan-explain", methods=["POST"])
def scan_explain():
    """POST /api/v1/advisory/scan-explain -- AI explanation of a scanner run.

    UI-AskPrime-01. Body: {scanner, run_ts, signal_count, log_excerpt, rejection_summary}.
    Returns {explanation: string}.
    """
    payload = request.get_json(silent=True) or {}
    scanner = (payload.get("scanner") or "").lower()
    signal_count = payload.get("signal_count", 0)
    log_excerpt = payload.get("log_excerpt", "")
    rejection_summary = payload.get("rejection_summary", "")

    template = _SCAN_EXPLAIN_PROMPTS.get(scanner, _SCAN_EXPLAIN_GENERIC)
    system_prompt = template.format(scanner=scanner, signal_count=signal_count)
    user_msg = (
        f"Scanner: {scanner.upper()}\n"
        f"Run time: {payload.get('run_ts', 'unknown')}\n"
        f"Signals produced: {signal_count}\n\n"
        f"LOG EXCERPT (last 50 lines):\n{log_excerpt}\n\n"
        f"REJECTION SUMMARY:\n{rejection_summary}"
    )

    api_key = ""
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            from prime_config.prime_config import get_config
            api_key = get_config().ops.anthropic_api_key or ""
    except Exception:
        pass

    if not api_key:
        return jsonify({"explanation": "Advisory unavailable — check Anthropic API key in Settings."}), 200

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        explanation = response.content[0].text.strip()
        return jsonify({"explanation": explanation}), 200
    except Exception as e:
        logger.warning("scan-explain Claude call failed: %s", e)
        return jsonify({"explanation": "Advisory unavailable — check Anthropic API key in Settings."}), 200


# ── PORT-03: advisory/rebalance cache ────────────────────────────────────────
_rebalance_cache: Dict[str, Any] = {}


@api_bp.route("/advisory/rebalance", methods=["POST"])
def advisory_rebalance():
    """POST /api/v1/advisory/rebalance -- ML-17 AI Rebalance Advisor.

    PORT-03. Calls Claude with current portfolio and ops thresholds.
    Returns ranked suggestions [{symbol, action, rationale, urgency}].
    Caches last successful response and returns it with stale indicator when API unavailable.
    """
    global _rebalance_cache
    try:
        from prime_data.prime_db import get_open_trades
        from prime_config.prime_config import get_config
        from prime_intelligence.prime_rebalance_advisor import get_rebalance_advice

        cfg = get_config()
        ops = cfg.ops
        positions = get_open_trades()
        max_position_pct = float(getattr(ops, "max_position_pct", 0.15))
        max_sector_pct = float(getattr(ops, "max_sector_pct", 0.30))

        # Build sector summary from portfolio_factor
        from prime_intelligence.prime_portfolio_factor import sector_map
        sector_totals: Dict[str, float] = {}
        portfolio_value = 0.0
        for p in positions:
            mv = float(p.get("entry_price") or 0) * int(p.get("shares") or 0)
            portfolio_value += mv
            sec = sector_map(p.get("symbol") or "")
            sector_totals[sec] = sector_totals.get(sec, 0.0) + mv

        sector_summary = {
            s: round(v / portfolio_value * 100, 1) if portfolio_value else 0.0
            for s, v in sector_totals.items()
        }

        api_key = ""
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                api_key = ops.anthropic_api_key or ""
        except Exception:
            pass

        result = get_rebalance_advice(
            positions=positions,
            portfolio_value=portfolio_value,
            sector_summary=sector_summary,
            max_position_pct=max_position_pct,
            max_sector_pct=max_sector_pct,
            api_key=api_key,
        )

        if not result.get("_fallback"):
            _rebalance_cache = {"data": result, "timestamp": result.get("timestamp", "")}

        return jsonify(result), 200

    except Exception as e:
        logger.error("advisory rebalance error: %s", e)
        if _rebalance_cache:
            cached = dict(_rebalance_cache["data"])
            cached["_stale"] = True
            cached["_stale_from"] = _rebalance_cache["timestamp"]
            return jsonify(cached), 200
        return jsonify({"error": str(e), "suggestions": []}), 500


@api_bp.route("/scenarios", methods=["GET"])
def get_scenarios_endpoint():
    """GET /api/v1/scenarios -- detected convergence scenarios (WO-PRIME-SCENARIOS-01).

    Query params:
      direction=LONG|SHORT
      type_num=1..9
      active_only=false  (default: true, only active scenarios)
      limit=N            (default: 100)
    """
    from prime_scenarios.prime_scenarios_db import get_scenarios, init_scenarios_table
    try:
        init_scenarios_table()  # idempotent — ensures table exists before first query
        direction = request.args.get("direction")
        type_num = request.args.get("type_num")
        active_only = request.args.get("active_only", "true").lower() != "false"
        limit = int(request.args.get("limit", 100))
        scenarios = get_scenarios(
            limit=limit,
            active_only=active_only,
            direction=direction or None,
            type_num=type_num or None,
        )
        return jsonify({"scenarios": scenarios, "count": len(scenarios)}), 200
    except Exception as e:
        logger.error("scenarios endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/scenarios/detect", methods=["POST"])
@require_local_token
def detect_scenarios_endpoint():
    """POST /api/v1/scenarios/detect -- run scenario detection against current signals.

    Reads all APPROVED signals from prime_signals, runs the convergence engine,
    persists new scenario records. Engine failure returns an error response but
    does not affect individual signal records.
    Body: {} (no parameters required; uses all current APPROVED signals)
    """
    from prime_analytics.prime_signals_db import get_signals as fetch_signals
    from prime_scenarios.prime_scenario_engine import run_detection
    try:
        signals = fetch_signals(limit=1000)
        approved = [s for s in signals if s.get("status") == "APPROVED"]
        result = run_detection(approved)
        return jsonify(result), 200
    except Exception as e:
        logger.error("detect_scenarios endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/strategies", methods=["GET"])
def get_strategies():
    """GET /api/v1/strategies -- distinct strategies for the UI filter (Item 3)."""
    from prime_analytics.prime_signals_db import get_distinct_strategies
    try:
        strategies = get_distinct_strategies()
        return jsonify({"strategies": strategies, "count": len(strategies)}), 200
    except Exception as e:
        logger.error("strategies endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/tiers", methods=["GET"])
def get_tiers():
    """GET /api/v1/tiers -- distinct tier values for the UI filter (SIG-01).

    Populated dynamically from prime_signals so every tier present in the data
    (e.g. WEAK-LONG, TRANCHE_1) is selectable, regardless of when introduced.
    """
    from prime_analytics.prime_signals_db import get_distinct_tiers
    try:
        tiers = get_distinct_tiers()
        return jsonify({"tiers": tiers, "count": len(tiers)}), 200
    except Exception as e:
        logger.error("tiers endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/analytics/summary", methods=["GET"])
def get_analytics_summary():
    """GET /api/v1/analytics/summary -- Overview tab data."""
    from prime_analytics.prime_signals_db import get_analytics_summary as fetch_summary
    from prime_analytics.prime_signals_db import get_strategy_approval_rates
    try:
        summary = fetch_summary()
        # CIL-NEW-09: merge approval_rate into each strategy entry.
        rate_map = {r["strategy"]: r["approval_rate"] for r in get_strategy_approval_rates(days=7)}
        for s in summary.get("strategies", []):
            s["approval_rate"] = rate_map.get(s["strategy"], 0.0)
        return jsonify(summary), 200
    except Exception as e:
        logger.error("analytics summary error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/analytics/by-strategy", methods=["GET"])
def get_analytics_by_strategy():
    """GET /api/v1/analytics/by-strategy -- By Strategy tab data."""
    from prime_analytics.prime_signals_db import get_analytics_summary as fetch_summary
    strategy = request.args.get("strategy")
    try:
        summary = fetch_summary(strategy=strategy)
        return jsonify(summary), 200
    except Exception as e:
        logger.error("by-strategy error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/analytics/pnl-history", methods=["GET"])
def get_pnl_history():
    """GET /api/v1/analytics/pnl-history -- daily realized P&L for last 7 days.

    Sprint 22 Item 3: feeds the Dashboard P&L sparkline. Returns up to 7 date
    buckets (YYYY-MM-DD) with total realized P&L from closed prime_trade_log rows.
    """
    from prime_data.prime_db import get_pnl_history
    try:
        history = get_pnl_history(days=7)
        return jsonify({"history": history}), 200
    except Exception as e:
        logger.error("pnl-history error: %s", e)
        return jsonify({"history": [], "error": str(e)}), 200


@api_bp.route("/analytics/effectiveness", methods=["GET"])
def get_analytics_effectiveness():
    """GET /api/v1/analytics/effectiveness -- strategy performance over CLOSED trades.

    CIL-063 (Sprint 31 Thread 3): groups CLOSED prime_trade_log rows by strategy
    with win rate, avg P&L %, avg hold, and best/worst trade %. Strategies with
    fewer than 5 closed trades are flagged insufficient_data with null metrics.
    """
    from prime_data.prime_db import _get_effectiveness_stats
    try:
        return jsonify(_get_effectiveness_stats()), 200
    except Exception as e:
        logger.error("analytics/effectiveness error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/instrument/<string:symbol>", methods=["GET"])
def get_instrument(symbol):
    """GET /api/v1/instrument/{symbol} -- Instrument detail stub.

    Sprint 22 Item 5 (UII Data Model): returns 501 Not Implemented.
    Full implementation deferred to v1.2 (UII Instrument Detail page).
    Schema will include: quote, options chain summary, DK history,
    signal history, PEAD context, sector/industry metadata.
    """
    return jsonify({
        "status": "not_implemented",
        "symbol": symbol.upper(),
        "message": "Instrument detail endpoint is reserved for v1.2. "
                   "See PRIME_UII_DataModel_v1_2_2026-06-04.docx for the planned schema.",
        "planned_fields": [
            "quote", "dk_status", "dk_history_7d", "signal_history",
            "pead_context", "uoa_recent", "sector", "industry",
            "options_chain_summary", "borrow_rate",
        ],
    }), 501


@api_bp.route("/health", methods=["GET"])
def health_check():
    """GET /api/v1/health -- server status, DB connection, last scan, ML row count."""
    from prime_data.prime_db import (
        get_ops_events,
        table_exists,
        check_closed_trade_completeness,
    )
    status: Dict[str, Any] = {
        "status": "ok",
        "db_connected": False,
        "last_scan_event": None,
        "ml_dataset_row_count": 0,
        "incomplete_exits": 0,
    }
    try:
        status["db_connected"] = table_exists("prime_trade_log")
        events = get_ops_events(limit=1)
        if events:
            status["last_scan_event"] = events[0].get("timestamp")
        # CIL-074: surface CLOSED trades missing required exit fields.
        status["incomplete_exits"] = len(check_closed_trade_completeness())
    except Exception as e:
        status["status"] = "degraded"
        status["error"] = str(e)
    try:
        from prime_data.prime_ml_dataset import get_row_count
        status["ml_dataset_row_count"] = get_row_count()
    except Exception:
        pass

    code = 200 if status["status"] == "ok" else 503
    return jsonify(status), code


def _is_recent(entry_time: str, now: datetime, window_s: int = 60) -> bool:
    """True if entry_time parses to within window_s seconds of now."""
    try:
        ts = datetime.fromisoformat(entry_time)
    except (TypeError, ValueError):
        return False
    return abs((now - ts).total_seconds()) <= window_s


@api_bp.route("/trades", methods=["POST"])
@require_local_token
def create_trade():
    """POST /api/v1/trades -- submit a trade (PAPER or LIVE) from the Lovable UI.

    PAPER mode: validates inputs + duplicate guard + inserts into prime_trade_log.
    LIVE mode  : enforces all 6 safety gates via submit_order(), then inserts
                 with trade_source='LIVE', starts fill watcher. Requires
                 confirmed=true in the request body (gate 6).
    """
    from prime_config.prime_config import get_config
    from prime_data.prime_db import (
        _recent_open_trade_exists,
        insert_trade,
        TradeRecordError,
    )

    cfg       = get_config()
    mode_cfg  = (cfg.trading_mode or "PAPER").upper()

    payload   = request.get_json(silent=True) or {}
    symbol    = str(payload.get("symbol", "")).strip().upper()
    strategy  = str(payload.get("strategy", "")).strip()
    direction = str(payload.get("direction", "")).strip().upper()
    account   = str(payload.get("account", "")).strip() or None
    order_type = str(payload.get("order_type", "MARKET")).strip().upper()
    if order_type not in ("MARKET", "LIMIT"):
        order_type = "MARKET"
    confirmed  = bool(payload.get("confirmed", False))
    signal_id_val = str(payload.get("signal_id") or "").strip() or None

    try:
        qty   = int(payload.get("qty"))
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        return jsonify({"error": "qty must be an integer and price a number"}), 400

    # Sprint 27 Item 3: resolve limit_price (LIMIT orders fill at this price in PAPER)
    limit_price_val = None
    if order_type == "LIMIT":
        lp_raw = payload.get("limit_price")
        try:
            limit_price_val = float(lp_raw) if lp_raw is not None else price
        except (TypeError, ValueError):
            limit_price_val = price

    if not symbol or not strategy:
        return jsonify({"error": "symbol and strategy are required"}), 400
    if direction not in ("LONG", "SHORT", "BUY", "SELL"):
        return jsonify({"error": "direction must be LONG/SHORT/BUY/SELL"}), 400
    if qty <= 0:
        return jsonify({"error": "qty must be positive"}), 400
    if price <= 0:
        return jsonify({"error": "price must be positive"}), 400

    direction  = {"BUY": "LONG", "SELL": "SHORT"}.get(direction, direction)
    side_schwab = "BUY" if direction == "LONG" else "SELL"
    now = datetime.now()

    # CIL-095 (Sprint 30 Thread 3): double-execute guard. Reject a second
    # submission for the same symbol+strategy while an OPEN position from the
    # last 60s already exists (rapid double-click / submit-handler race).
    # Placed before the LIVE/PAPER branch so both modes are protected.
    if _recent_open_trade_exists(symbol, strategy, window_seconds=60):
        return jsonify({
            "error": "Duplicate trade submission detected — this position is already open."
        }), 409

    # ── LIVE mode path ────────────────────────────────────────────────────────
    if mode_cfg == "LIVE":
        from prime_trading.prime_schwab_orders import submit_order, OrderGateError
        from prime_trading.prime_fill_poller import start_fill_watcher

        # WO-PRIME-ACTIVE-POSITION-MGMT-01 Part A: read stop/target from payload;
        # apply config defaults when not provided. Every position must carry a
        # fixed stop from the moment of entry.
        try:
            import json as _json
            with open(_OPS_CONFIG_PATH, "r", encoding="utf-8") as _f:
                _ops_cfg = _json.load(_f)
        except Exception:
            _ops_cfg = {}
        _def_stop_pct   = float(_ops_cfg.get("default_stop_loss_pct", 3.0))   # percentage
        _def_trail_pct  = float(_ops_cfg.get("default_trailing_stop_pct", 3.0)) / 100.0  # decimal

        live_stop_type = (payload.get("stop_type") or "FIXED").upper()
        if live_stop_type not in ("FIXED", "TRAILING"):
            live_stop_type = "FIXED"

        live_stop_price  = None
        live_target_price = None
        live_trail_pct   = None
        try:
            if live_stop_type == "TRAILING":
                raw_tp = payload.get("trailing_stop_pct")
                live_trail_pct = float(raw_tp) if raw_tp is not None else _def_trail_pct
                # Fixed floor at default_stop_loss_pct even when trailing is active
                sp_pct = float(payload.get("stop_pct") or _def_stop_pct)
                live_stop_price = round(
                    price * (1 + sp_pct / 100.0) if direction == "SHORT"
                    else price * (1 - sp_pct / 100.0), 4
                )
            else:
                sp_pct = float(payload.get("stop_pct") or _def_stop_pct)
                live_stop_price = round(
                    price * (1 + sp_pct / 100.0) if direction == "SHORT"
                    else price * (1 - sp_pct / 100.0), 4
                )
            if payload.get("target_pct") is not None:
                tp = float(payload["target_pct"])
                live_target_price = round(
                    price * (1 - tp / 100.0) if direction == "SHORT"
                    else price * (1 + tp / 100.0), 4
                )
        except (TypeError, ValueError):
            sp_pct = _def_stop_pct
            live_stop_price = round(
                price * (1 + sp_pct / 100.0) if direction == "SHORT"
                else price * (1 - sp_pct / 100.0), 4
            )

        account_hash = account or ""
        try:
            # Resolve account_hash: if the caller passed a short suffix, look up
            # the full hash via SchwabClient. Fallback: use as-is.
            try:
                from prime_trading.prime_schwab import SchwabClient
                _sc = SchwabClient()
                _sc.connect()
                acct_resp = _sc.client.get_account_numbers()
                if acct_resp.status_code == 200:
                    for a in acct_resp.json():
                        if (a.get("accountNumber", "").endswith(account or "")
                                or a.get("hashValue") == account):
                            account_hash = a["hashValue"]
                            break
                    if not account_hash:
                        account_hash = acct_resp.json()[0]["hashValue"]
            except Exception:
                pass

            # Sprint 27 Item 3: LIVE LIMIT orders use limit_price as the order price
            live_price = limit_price_val if order_type == "LIMIT" and limit_price_val else price
            result = submit_order(
                symbol=symbol,
                qty=qty,
                side=side_schwab,
                order_type=order_type,
                price=live_price,
                account_hash=account_hash,
                confirmed=confirmed,
                schwab_client=_sc if "_sc" in dir() else None,
            )
        except OrderGateError as gate_err:
            gate_map = {
                "PAPER_MODE":    403,
                "RTH":           400,
                "BUYING_POWER":  400,
                "POSITION_SIZE": 400,
                "DUPLICATE":     409,
                "NO_CONFIRM":    400,
                "SCHWAB_REJECT": 400,
                "SCHWAB_ERROR":  502,
                "NO_CLIENT":     503,
            }
            status = gate_map.get(gate_err.gate, 400)
            return jsonify({"error": str(gate_err), "gate": gate_err.gate}), status
        except Exception as e:
            logger.error("live create_trade error: %s", e)
            return jsonify({"error": str(e)}), 500

        try:
            live_fill = limit_price_val if order_type == "LIMIT" and limit_price_val else price
            log_id = insert_trade(
                strategy=strategy,
                symbol=symbol,
                direction=direction,
                mode="LIVE",
                order_type=order_type,
                shares=qty,
                entry_time=now.isoformat(),
                price_at_scan=price,
                entry_price=live_fill,
                account=account,
                order_id=result.get("order_id"),
                signal_source="UI",
                trade_source="LIVE",
                limit_price=limit_price_val,
                stop_price=live_stop_price,
                target_price=live_target_price,
                stop_type=live_stop_type,
                signal_id=signal_id_val,
            )
            # Wire trailing stop pct if TRAILING mode
            if live_stop_type == "TRAILING" and live_trail_pct is not None and log_id:
                from prime_data.prime_db import update_trailing_stop
                update_trailing_stop(log_id, live_trail_pct)
        except TradeRecordError as e:
            return jsonify({"error": str(e)}), 400

        # Start fill watcher (non-blocking background thread)
        schwab_for_fill = _sc if "_sc" in dir() else None  # type: ignore[name-defined]
        try:
            start_fill_watcher(result["order_id"], log_id, schwab_for_fill)
        except Exception:
            pass

        # WO-PRIME-ACTIVE-POSITION-MGMT-01 Part A: attach protective STOP order.
        # Two-step required (Section 3.3) — entry order already submitted above.
        # On failure: log Tier 2 alert; do not block trade response.
        if live_stop_price and live_stop_price > 0:
            try:
                from prime_trading.prime_schwab_orders import attach_stop_order
                attach_stop_order(
                    symbol=symbol,
                    qty=qty,
                    direction=direction,
                    stop_price=live_stop_price,
                    account_hash=account_hash,
                    schwab_client=schwab_for_fill,
                    db_path=None,
                )
            except Exception as _stop_err:
                logger.error(
                    "STOP_ATTACH_FAILED: %s log_id=%s — %s",
                    symbol, log_id, _stop_err,
                )
                try:
                    from prime_data.prime_db import log_ops_event
                    log_ops_event(
                        event_type="NO_STOP_VIOLATION",
                        component="prime_api_routes",
                        symbol=symbol,
                        detail=(
                            f"log_id={log_id} stop_attach_failed=True "
                            f"reason={_stop_err}"
                        ),
                        severity="CRITICAL",
                    )
                except Exception:
                    pass

        return jsonify({
            "log_id":    log_id,
            "order_id":  result.get("order_id"),
            "status":    "SUBMITTED",
            "trade_source": "LIVE",
        }), 201

    # ── PAPER mode path ───────────────────────────────────────────────────────
    if mode_cfg != "PAPER":
        return jsonify({"error": f"unknown trading_mode: {mode_cfg}"}), 500

    # Sprint 26 Item 2: read optional stop/target/time fields from payload.
    # Sprint 27 Item 2: stop_type (FIXED/TRAILING) + trailing_stop_pct.
    stop_pct_raw    = payload.get("stop_pct")
    target_pct_raw  = payload.get("target_pct")
    time_stop_days  = payload.get("time_stop_days")
    stop_type_val   = (payload.get("stop_type") or "FIXED").upper()
    if stop_type_val not in ("FIXED", "TRAILING"):
        stop_type_val = "FIXED"

    stop_price_val    = None
    target_price_val  = None
    time_stop_min_val = None
    trailing_pct_val  = None
    try:
        if stop_type_val == "TRAILING":
            raw_tpct = payload.get("trailing_stop_pct")
            trailing_pct_val = float(raw_tpct) if raw_tpct is not None else 0.05
        elif stop_pct_raw is not None:
            sp = float(stop_pct_raw)  # e.g. 5.0 means 5%
            stop_price_val = round(
                price * (1 + sp / 100.0) if direction == "SHORT" else price * (1 - sp / 100.0), 4
            )
        if target_pct_raw is not None:
            tp = float(target_pct_raw)
            target_price_val = round(
                price * (1 - tp / 100.0) if direction == "SHORT" else price * (1 + tp / 100.0), 4
            )
        if time_stop_days is not None:
            time_stop_min_val = int(float(time_stop_days) * 480)  # 8 trading hours/day
    except (TypeError, ValueError):
        pass

    try:
        # Sprint 27 Item 3: PAPER LIMIT fills immediately at limit_price
        paper_fill = limit_price_val if order_type == "LIMIT" and limit_price_val else price

        log_id = insert_trade(
            strategy=strategy,
            symbol=symbol,
            direction=direction,
            mode="PAPER",
            order_type=order_type,
            shares=qty,
            entry_time=now.isoformat(),
            price_at_scan=price,
            entry_price=paper_fill,
            account=account,
            signal_source="UI",
            trade_source="PAPER",
            stop_price=stop_price_val,
            target_price=target_price_val,
            time_stop_minutes=time_stop_min_val,
            stop_type=stop_type_val,
            limit_price=limit_price_val,
            signal_id=signal_id_val,
        )

        # For TRAILING stop: wire trailing_stop_pct to the new trade
        if stop_type_val == "TRAILING" and trailing_pct_val is not None and log_id:
            from prime_data.prime_db import update_trailing_stop
            update_trailing_stop(log_id, trailing_pct_val)

    except TradeRecordError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("create_trade error: %s", e)
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "log_id": log_id, "status": "OPEN", "trade_source": "PAPER",
        "stop_price": stop_price_val, "target_price": target_price_val,
        "stop_type": stop_type_val, "order_type": order_type,
    }), 201


@api_bp.route("/sync/schwab", methods=["GET", "POST"])
def sync_schwab():
    """GET|POST /api/v1/sync/schwab -- import current Schwab holdings into prime_trade_log.

    Sprint 23 Item 1. Triggers a live Schwab position sync and returns a count
    summary. Safe to call multiple times -- deduplication is enforced in the sync
    module. Degrades gracefully when Schwab is not connected.
    CIL-NEW-15: POST variant added so the Sync Now button can call it without
    ambiguity (GET is kept for backwards compatibility with refreshPortfolio).
    """
    try:
        from prime_trading.prime_schwab_sync import sync_schwab_positions
        result = sync_schwab_positions()
        return jsonify(result), 200
    except Exception as e:
        logger.error("schwab sync error: %s", e)
        return jsonify({"imported": 0, "skipped": 0, "errors": [str(e)]}), 200


_OPS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "ops_config.json"

_SETTINGS_FIELDS = [
    # CIL-NEW-13/14: mata_profile now accepts 'all' as a valid value.
    "max_trades", "mata_profile", "analysis_mode", "use_ai_ranker",
    "long_stop_loss_pct", "short_stop_loss_pct", "short_size_multiplier",
    "time_stop_minutes", "short_time_stop_minutes", "use_signal_led_psa",
    "strategy_thresholds",
    # Sprint 24
    "max_order_pct", "stop_execution_mode", "max_sector_pct", "max_position_pct",
    # Sprint 26 Item 8
    "stop_monitor_interval_seconds",
    # Sprint 26 Item 6
    "monthly_ai_budget",
    # Sprint 27 Item 5: MATA account distribution editor
    "mata_accounts",
    # Sprint 28 Item 4: Polygon rate limiting
    "polygon_plan", "polygon_rate_limit_delay_ms",
    # Sprint 30 PM-04: automated exit management
    "exit_gain_trigger_pct", "exit_trail_pct",
    "exit_day_count_max", "exit_day_count_action",
]


@api_bp.route("/settings", methods=["GET"])
def get_settings():
    """GET /api/v1/settings -- return current UI-editable ops_config.json values.

    Sprint 23 Item 2.
    """
    try:
        with open(_OPS_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        payload = {k: raw.get(k) for k in _SETTINGS_FIELDS if k in raw}
        return jsonify(payload), 200
    except Exception as e:
        logger.error("get_settings error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/settings", methods=["POST"])
def post_settings():
    """POST /api/v1/settings -- partial-update UI-editable fields in ops_config.json.

    Sprint 23 Item 2. Writes changes to ops_config.json and reloads the config
    singleton so updated values take effect on the next scan without a restart.
    Returns the full updated settings payload.
    """
    payload = request.get_json(silent=True) or {}
    try:
        with open(_OPS_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)

        for key in _SETTINGS_FIELDS:
            if key in payload:
                raw[key] = payload[key]

        with open(_OPS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)

        from prime_config.prime_config import reload_config
        reload_config()

        updated = {k: raw.get(k) for k in _SETTINGS_FIELDS if k in raw}
        return jsonify(updated), 200
    except Exception as e:
        logger.error("post_settings error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/trades/<string:log_id>", methods=["DELETE"])
@require_local_token
def delete_trade_endpoint(log_id):
    """DELETE /api/v1/trades/{log_id} -- hard-delete a manual PAPER trade.

    Sprint 23 Item 4. Blocked in LIVE mode (403). Only removes records where
    trade_source != 'SCHWAB_IMPORT' -- never deletes imported Schwab positions.
    """
    from prime_config.prime_config import get_config
    from prime_data.prime_db import get_trade, delete_trade

    if (get_config().trading_mode or "PAPER").upper() != "PAPER":
        return jsonify({"error": "forbidden: delete is blocked in LIVE mode"}), 403

    if not log_id:
        return jsonify({"error": "log_id is required"}), 400

    trade = get_trade(log_id)
    if not trade:
        return jsonify({"error": "unknown log_id"}), 404

    if (trade.get("trade_source") or "").upper() == "SCHWAB_IMPORT":
        return jsonify({"error": "forbidden: cannot delete Schwab-imported positions"}), 403

    if (trade.get("status") or "").upper() != "OPEN":
        return jsonify({"error": "only OPEN trades can be deleted"}), 409

    try:
        deleted = delete_trade(log_id)
    except Exception as e:
        logger.error("delete_trade error: %s", e)
        return jsonify({"error": str(e)}), 500

    if not deleted:
        return jsonify({"error": "trade not found or already closed"}), 404
    return jsonify({"deleted": log_id, "status": "ok"}), 200


def _shutdown_servers() -> None:
    """Deferred shutdown: wait for response to flush, then kill UI server and self."""
    time.sleep(0.5)
    _kill_port(5002)
    time.sleep(0.1)
    os._exit(0)


def _kill_port(port: int) -> None:
    """Kill the process listening on the given port (cross-platform)."""
    try:
        if sys.platform == "win32":
            import subprocess
            result = subprocess.run(
                f"netstat -ano | findstr :{port}",
                shell=True, capture_output=True, text=True,
            )
            for line in result.stdout.strip().splitlines():
                if "LISTENING" in line:
                    pid = int(line.split()[-1])
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True,
                    )
                    break
        else:
            import subprocess, signal as _signal
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True,
            )
            for pid_str in result.stdout.strip().splitlines():
                try:
                    os.kill(int(pid_str), _signal.SIGTERM)
                except Exception:
                    pass
    except Exception as e:
        logger.warning("_kill_port(%d) error: %s", port, e)


@api_bp.route("/shutdown", methods=["POST"])
def shutdown_servers():
    """POST /api/v1/shutdown -- gracefully stop the API and UI Flask servers.

    Sprint 23 Item 4. Returns immediately; shutdown fires 500ms later so the
    browser receives the response. Tkinter GUI (prime_gui_app.py) is not affected.
    """
    t = threading.Thread(target=_shutdown_servers, daemon=True)
    t.start()
    return jsonify({"status": "shutting_down", "message": "PRIME servers stopping"}), 200


@api_bp.route("/trades/close", methods=["POST"])
@require_local_token
def close_trade_endpoint():
    """POST /api/v1/trades/close -- close an open PAPER position (Sprint 16 Item 5).

    Body: {log_id, exit_price, exit_reason}. Requires the bearer token, enforces
    PAPER mode, validates the inputs, and updates prime_trade_log via prime_db's
    close_trade_manual() (direction-aware realized P&L + hold_minutes). Returns
    200 with the realized P&L, 404 if the log_id is unknown.
    """
    from prime_config.prime_config import get_config
    from prime_data.prime_db import close_trade_manual

    if (get_config().trading_mode or "PAPER").upper() != "PAPER":
        return jsonify({"error": "rejected: server is not in PAPER mode"}), 403

    payload = request.get_json(silent=True) or {}
    log_id = str(payload.get("log_id", "")).strip()
    exit_reason = str(payload.get("exit_reason", "")).strip() or "MANUAL"

    if not log_id:
        return jsonify({"error": "log_id is required"}), 400
    try:
        exit_price = float(payload.get("exit_price"))
    except (TypeError, ValueError):
        return jsonify({"error": "exit_price must be a number"}), 400
    if exit_price <= 0:
        return jsonify({"error": "exit_price must be positive"}), 400

    try:
        # TZ-01: store close timestamp as UTC (tz.js converts to ET for display).
        result = close_trade_manual(log_id, exit_price, exit_reason,
                                    close_ts=datetime.utcnow().isoformat())
    except Exception as e:
        logger.error("close_trade error: %s", e)
        return jsonify({"error": str(e)}), 500

    if result is None:
        return jsonify({"error": "unknown log_id"}), 404
    return jsonify(result), 200


# ============================================================================
# Sprint 24 endpoints
# ============================================================================

@api_bp.route("/orders/<string:order_id>", methods=["GET"])
def get_order_status(order_id):
    """GET /api/v1/orders/{order_id} -- poll Schwab order status.

    Sprint 24 Item 1. Returns {order_id, status, filled_qty, fill_price}.
    Degrades gracefully when Schwab is not connected.
    """
    try:
        from prime_trading.prime_schwab import SchwabClient
        client = SchwabClient()
        client.connect()
        raw = client.get_order_status(order_id)
        if raw is None:
            return jsonify({"error": "order not found"}), 404
        status = (raw.get("status") or "UNKNOWN").upper()
        filled_qty   = int(raw.get("filledQuantity") or raw.get("quantity") or 0)
        fill_price   = float(raw.get("filledPrice") or raw.get("price") or 0.0)
        return jsonify({
            "order_id":   order_id,
            "status":     status,
            "filled_qty": filled_qty,
            "fill_price": fill_price,
        }), 200
    except Exception as e:
        logger.error("get_order_status error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/portfolio", methods=["GET"])
def get_portfolio():
    """GET /api/v1/portfolio -- consolidated holdings across all Schwab accounts.

    Sprint 24 Item 2. Groups OPEN prime_trade_log records by symbol,
    aggregates shares and weighted average entry price, attaches current
    price (from Schwab quotes when available), computes unrealized P&L,
    and flags risk warnings (sector concentration, position size limit).
    """
    from prime_data.prime_db import get_open_trades
    try:
        import json as _json
        from prime_config.prime_config import get_config
        cfg = get_config()
        ops_cfg = cfg.ops

        # CIL-NEW-13: read active MATA profile to drive flat vs. grouped view.
        _raw_profile = getattr(ops_cfg, "mata_profile", None)
        mata_profile = (str(_raw_profile) if _raw_profile and not callable(_raw_profile) else "").strip().lower()
        profile_all = not mata_profile or mata_profile == "all"

        positions = get_open_trades()

        # CIL-NEW-13: single-account profile — filter to positions for that account.
        if not profile_all and mata_profile:
            positions = [
                p for p in positions
                if str(p.get("account") or "").strip().lower() == mata_profile
            ]

        # Group by symbol
        groups: Dict[str, Any] = {}
        for p in positions:
            sym = (p.get("symbol") or "").upper()
            if not sym:
                continue
            if sym not in groups:
                groups[sym] = {
                    "symbol":        sym,
                    "total_shares":  0,
                    "total_cost":    0.0,
                    "accounts":      [],
                    "log_ids":       [],
                    "stop_prices":   [],
                    "direction":     (p.get("direction") or "LONG").upper(),
                    "per_account":   {},
                    "stage_numbers": [],   # CIL-NEW-08: track tranche counts
                    "stage_totals":  [],
                }
            ep = float(p.get("entry_price") or p.get("price_at_scan") or 0.0)
            sh = int(p.get("shares") or 0)
            groups[sym]["total_shares"] += sh
            groups[sym]["total_cost"]   += ep * sh
            acc = p.get("account") or ""
            if acc and acc not in groups[sym]["accounts"]:
                groups[sym]["accounts"].append(acc)
            groups[sym]["log_ids"].append(p.get("log_id"))
            # CIL-NEW-08: capture staged entry indicators.
            sn = p.get("stage_number")
            st = p.get("stage_total")
            if sn is not None:
                groups[sym]["stage_numbers"].append(int(sn))
            if st is not None:
                groups[sym]["stage_totals"].append(int(st))
            sp = p.get("stop_price")
            if sp is not None:
                groups[sym]["stop_prices"].append(float(sp))
            # CIL-NEW-13: track per-account breakdown for grouped view.
            acct_key = acc or "_unknown"
            if acct_key not in groups[sym]["per_account"]:
                groups[sym]["per_account"][acct_key] = {
                    "shares": 0, "cost": 0.0, "log_ids": [], "stop_prices": [],
                }
            groups[sym]["per_account"][acct_key]["shares"] += sh
            groups[sym]["per_account"][acct_key]["cost"]   += ep * sh
            groups[sym]["per_account"][acct_key]["log_ids"].append(p.get("log_id"))
            if sp is not None:
                groups[sym]["per_account"][acct_key]["stop_prices"].append(float(sp))

        # Fetch current prices from Schwab (best-effort)
        symbols = list(groups.keys())
        current_prices: Dict[str, float] = {}
        try:
            from prime_trading.prime_schwab import SchwabClient
            sc = SchwabClient()
            sc.connect()
            quotes = sc.get_quotes(symbols)
            for sym, q in quotes.items():
                price = (
                    q.get("quote", {}).get("lastPrice")
                    or q.get("quote", {}).get("mark")
                    or 0.0
                )
                if price:
                    current_prices[sym.upper()] = float(price)
        except Exception:
            pass

        # Build rows + compute portfolio totals
        rows = []
        total_market_value = 0.0
        total_cost_basis   = 0.0
        total_unrealized   = 0.0

        for sym, g in groups.items():
            shares = g["total_shares"]
            avg_entry = g["total_cost"] / shares if shares else 0.0
            cur_price = current_prices.get(sym, avg_entry)
            market_val = cur_price * shares
            direction  = g["direction"]
            if direction == "SHORT":
                pnl = (avg_entry - cur_price) * shares
            else:
                pnl = (cur_price - avg_entry) * shares
            pnl_pct = (pnl / g["total_cost"] * 100.0) if g["total_cost"] else 0.0

            # DK status (best-effort)
            dk_status = "NEUTRAL"
            try:
                from prime_intelligence.prime_dk_trader import get_dk_status
                dk_status = get_dk_status(sym).get("dk_status", "NEUTRAL")
            except Exception:
                pass

            stop_prices = g["stop_prices"]
            stop_price = min(stop_prices) if stop_prices else None

            # CIL-NEW-13: per-account breakdown for grouped view.
            per_account_rows = []
            if profile_all:
                for acct_name, acct_data in g["per_account"].items():
                    acct_shares = acct_data["shares"]
                    acct_cost   = acct_data["cost"]
                    acct_avg    = acct_cost / acct_shares if acct_shares else 0.0
                    acct_mval   = cur_price * acct_shares
                    if direction == "SHORT":
                        acct_pnl = (acct_avg - cur_price) * acct_shares
                    else:
                        acct_pnl = (cur_price - acct_avg) * acct_shares
                    acct_pnl_pct = (acct_pnl / acct_cost * 100.0) if acct_cost else 0.0
                    acct_stops = acct_data["stop_prices"]
                    per_account_rows.append({
                        "account":           acct_name,
                        "shares":            acct_shares,
                        "avg_entry_price":   round(acct_avg, 4),
                        "current_price":     round(cur_price, 4),
                        "market_value":      round(acct_mval, 2),
                        "unrealized_pnl":    round(acct_pnl, 2),
                        "unrealized_pnl_pct": round(acct_pnl_pct, 2),
                        "log_ids":           acct_data["log_ids"],
                        "stop_price":        round(min(acct_stops), 4) if acct_stops else None,
                    })

            # CIL-NEW-08: compute staged entry indicator.
            _stage_nums = g["stage_numbers"]
            _stage_tots = g["stage_totals"]
            stage_info = None
            if _stage_nums and _stage_tots:
                _max_done  = max(_stage_nums)
                _max_total = max(_stage_tots)
                if _max_done < _max_total:
                    stage_info = {"done": _max_done, "total": _max_total}

            row = {
                "symbol":            sym,
                "total_shares":      shares,
                "avg_entry_price":   round(avg_entry, 4),
                "current_price":     round(cur_price, 4),
                "total_cost":        round(g["total_cost"], 2),
                "market_value":      round(market_val, 2),
                "unrealized_pnl":    round(pnl, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "accounts":          g["accounts"],
                "direction":         direction,
                "dk_status":         dk_status,
                "log_ids":           g["log_ids"],
                "stop_price":        round(stop_price, 4) if stop_price is not None else None,
                "per_account_rows":  per_account_rows,
                "stage_info":        stage_info,
            }
            rows.append(row)
            total_market_value += market_val
            total_cost_basis   += g["total_cost"]
            total_unrealized   += pnl

        # Sort by market value descending (default)
        rows.sort(key=lambda r: r["market_value"], reverse=True)

        # Risk warnings (Item 5)
        warnings = []
        max_pos_pct = float(getattr(ops_cfg, "max_position_pct", 0.15))
        max_sec_pct = float(getattr(ops_cfg, "max_sector_pct", 0.30))

        if total_market_value > 0:
            for row in rows:
                pos_pct = row["market_value"] / total_market_value
                if pos_pct > max_pos_pct:
                    row["position_warning"] = True
                    warnings.append({
                        "type":    "POSITION_SIZE",
                        "symbol":  row["symbol"],
                        "pct":     round(pos_pct * 100, 1),
                        "limit_pct": round(max_pos_pct * 100, 1),
                    })

        # Sector concentration (best-effort using prime_intelligence sector_map)
        sector_totals: Dict[str, float] = {}
        try:
            from prime_intelligence.prime_portfolio_factor import sector_map
            for row in rows:
                sec = sector_map(row["symbol"])
                sector_totals[sec] = sector_totals.get(sec, 0.0) + row["market_value"]
        except Exception:
            pass

        sector_warnings = []
        if total_market_value > 0:
            for sec, val in sector_totals.items():
                sec_pct = val / total_market_value
                if sec_pct > max_sec_pct:
                    sector_warnings.append({
                        "type":      "SECTOR_CONCENTRATION",
                        "sector":    sec,
                        "pct":       round(sec_pct * 100, 1),
                        "limit_pct": round(max_sec_pct * 100, 1),
                    })
        warnings.extend(sector_warnings)

        # Cash Available: sum Cash & Sweep Vehicle across all Schwab accounts (PORT-03)
        cash_available = None
        try:
            from prime_trading.prime_schwab_sync import get_schwab_cash_total
            cash_available = get_schwab_cash_total()
        except Exception:
            pass

        summary = {
            "total_market_value": round(total_market_value, 2),
            "total_cost_basis":   round(total_cost_basis, 2),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "position_count":     len(rows),
            "cash_available":     cash_available,
            "sector_breakdown":   {
                s: round(v / total_market_value * 100, 1) if total_market_value else 0.0
                for s, v in sector_totals.items()
                if v > 0
            },
        }

        return jsonify({
            "rows":         rows,
            "count":        len(rows),
            "summary":      summary,
            "warnings":     warnings,
            "profile_mode": "all" if profile_all else mata_profile,
        }), 200
    except Exception as e:
        logger.error("portfolio endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


def _resolve_account_hash(suffix: str, schwab_client) -> str:
    """Resolve a 4-digit account suffix to its Schwab account hash (Sprint 30 PM-03).

    Mirrors the resolution block in the /trades route. Returns the hash for the
    given suffix, or an empty string if it cannot be resolved. Never raises.
    """
    suffix = str(suffix or "")
    try:
        acct_resp = schwab_client.client.get_account_numbers()
        if acct_resp.status_code == 200:
            for a in acct_resp.json():
                if a.get("accountNumber", "").endswith(suffix) or a.get("hashValue") == suffix:
                    return a["hashValue"]
    except Exception as e:  # noqa: BLE001
        logger.debug("account hash resolution failed for %s: %s", suffix, e)
    return ""


def _mata_live_quote_price(symbol: str, schwab_client=None) -> float:
    """Fetch a live last/mark price for the symbol (Sprint 30 PM-02). 0.0 on failure."""
    try:
        if schwab_client is None:
            from prime_trading.prime_schwab import SchwabClient
            schwab_client = SchwabClient()
            schwab_client.connect()
        quotes = schwab_client.get_quotes([symbol])
        data = quotes.get(symbol) or quotes.get(symbol.upper()) or {}
        price = (
            data.get("quote", {}).get("lastPrice")
            or data.get("quote", {}).get("mark")
            or data.get("regularMarketLastPrice")
            or 0.0
        )
        return float(price) if price else 0.0
    except Exception as e:  # noqa: BLE001
        logger.debug("mata_sell live quote failed for %s: %s", symbol, e)
        return 0.0


def _match_open_record(records, account, consumed):
    """Find an unconsumed OPEN trade-log record matching an account (Sprint 30 PM-02).

    Prefers an exact/suffix account match; falls back to any record with no
    account set. Returns the record dict or None.
    """
    acct = str(account or "")
    for r in records:
        if r["log_id"] in consumed:
            continue
        ra = str(r.get("account") or "")
        if ra and acct and (ra == acct or ra.endswith(acct) or acct.endswith(ra)):
            return r
    for r in records:
        if r["log_id"] in consumed:
            continue
        if not r.get("account"):
            return r
    return None


@api_bp.route("/sell/mata", methods=["POST"])
@require_local_token
def mata_sell():
    """POST /api/v1/sell/mata -- proportional sell across accounts (MATA).

    Sprint 24 Item 3. Body: {symbol, total_qty (or pct), order_type, price,
    account_holdings: [{account, account_hash, shares}], confirmed}.
    In PAPER mode: closes positions in prime_trade_log proportionally.
    In LIVE mode:  submits per-account SELL orders via submit_order().
    """
    from prime_config.prime_config import get_config
    from prime_trading.prime_mata_sell import calculate_sell_allocation, pct_to_shares

    cfg   = get_config()
    mode  = (cfg.trading_mode or "PAPER").upper()
    payload = request.get_json(silent=True) or {}

    symbol     = str(payload.get("symbol", "")).strip().upper()
    direction  = (payload.get("direction") or "LONG").strip().upper()
    order_type = str(payload.get("order_type", "MARKET")).upper()
    confirmed  = bool(payload.get("confirmed", False))
    holdings   = payload.get("account_holdings", [])
    # SHORT positions are covered with a BUY order; LONG positions use SELL.
    broker_side = "BUY" if direction == "SHORT" else "SELL"

    try:
        price = float(payload.get("price", 0))
    except (TypeError, ValueError):
        price = 0.0

    # Resolve qty — accept pct shortcut ("50%")
    qty_raw = payload.get("total_qty", 0)
    if isinstance(qty_raw, str) and qty_raw.strip().endswith("%"):
        pct_val = float(qty_raw.strip().rstrip("%"))
        total_held = sum(int(h.get("shares", 0)) for h in holdings)
        total_qty  = pct_to_shares(pct_val, total_held)
    else:
        try:
            total_qty = int(qty_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "total_qty must be an integer or percentage string"}), 400

    if not symbol:
        return jsonify({"error": "symbol is required"}), 400
    if total_qty <= 0:
        return jsonify({"error": "total_qty must be positive"}), 400
    if not holdings:
        return jsonify({"error": "account_holdings is required"}), 400
    if not confirmed:
        return jsonify({"error": "confirmed is required for MATA sell"}), 400

    allocation = calculate_sell_allocation(symbol, total_qty, holdings)
    orders_placed = []
    failures      = []

    # Sprint 30 PM-03: in LIVE mode connect once and reuse the client for both
    # account-hash resolution and order submission. PAPER mode never touches Schwab.
    schwab_client = None
    if mode == "LIVE":
        try:
            from prime_trading.prime_schwab import SchwabClient
            schwab_client = SchwabClient()
            schwab_client.connect()
        except Exception as e:
            logger.error("mata_sell: could not connect Schwab client: %s", e)

    for alloc in allocation["allocations"]:
        sell_qty     = alloc["sell_qty"]
        account      = alloc["account"]
        if sell_qty <= 0:
            continue

        if mode == "LIVE":
            from prime_trading.prime_schwab_orders import submit_order, OrderGateError
            # Sprint 30 PM-03: resolve the account suffix to a hash before routing.
            account_hash = ""
            if schwab_client is not None:
                account_hash = _resolve_account_hash(account, schwab_client)
            if not account_hash:
                account_hash = alloc.get("account_hash", "")
            if not account_hash:
                logger.error(
                    "mata_sell: account_hash resolution failed for %s — skipping allocation",
                    account,
                )
                failures.append({
                    "account": account,
                    "error":   "account_hash resolution failed",
                })
                continue
            try:
                result = submit_order(
                    symbol=symbol,
                    qty=sell_qty,
                    side=broker_side,
                    order_type=order_type,
                    price=price or 0.0,
                    account_hash=account_hash,
                    confirmed=True,
                    schwab_client=schwab_client,
                )
                orders_placed.append({
                    "account":  account,
                    "sell_qty": sell_qty,
                    "order_id": result.get("order_id"),
                    "status":   "SUBMITTED",
                })
            except OrderGateError as gate_err:
                failures.append({
                    "account": account,
                    "error":   str(gate_err),
                    "gate":    gate_err.gate,
                })
            except Exception as e:
                failures.append({"account": account, "error": str(e)})
        else:
            # PAPER mode: no Schwab order — the trade-log close below records the exit.
            orders_placed.append({
                "account":  account,
                "sell_qty": sell_qty,
                "status":   "PAPER_CLOSE",
            })

    # ── Sprint 30 PM-02: write exit_reason=MANUAL to prime_trade_log ──────────
    # Close matching OPEN records for each sold account. In LIVE the exit price
    # is a live quote at confirm time; in PAPER it's the price passed by the UI
    # (current price from the portfolio row). Missing records log a WARNING and
    # never block the response.
    closed_logs = []
    try:
        from prime_data.prime_db import (
            get_open_by_symbol, close_trade_manual, log_ops_event,
        )

        exit_price = price
        if mode == "LIVE":
            live_px = _mata_live_quote_price(symbol, schwab_client)
            if live_px > 0:
                exit_price = live_px

        # CIL-086: map each account to its submitted LIVE order id so the fill
        # watcher can confirm the actual exit fill against the right log row.
        order_by_account = {
            o["account"]: o.get("order_id")
            for o in orders_placed if o.get("order_id")
        }

        if exit_price and exit_price > 0:
            open_recs = get_open_by_symbol(symbol)
            consumed = set()
            now_iso = datetime.now().isoformat()
            sold_accounts = [
                a["account"] for a in allocation["allocations"] if a["sell_qty"] > 0
            ]
            for acct in sold_accounts:
                # Safety invariant: in LIVE mode, never write CLOSED without a
                # confirmed broker order_id. If the broker call failed/was rejected
                # for this account, leave the local record OPEN and report the
                # failure via the `failures` list already populated above.
                if mode == "LIVE" and acct not in order_by_account:
                    logger.warning(
                        "mata_sell: skipping DB close for %s acct=%s — no confirmed order_id",
                        symbol, acct,
                    )
                    continue
                match = _match_open_record(open_recs, acct, consumed)
                if match is None:
                    log_ops_event(
                        event_type="MATA_SELL_NO_LOG",
                        component="prime_api_routes",
                        symbol=symbol,
                        detail=f"account={acct}: no OPEN prime_trade_log record to close",
                        severity="WARN",
                    )
                    continue
                consumed.add(match["log_id"])
                summary = close_trade_manual(
                    match["log_id"], float(exit_price),
                    exit_reason="MANUAL", close_ts=now_iso,
                )
                if summary:
                    closed_logs.append(summary)

                # CIL-086: LIVE only — start a fill watcher so the actual broker
                # fill overwrites the live-quote exit price/P&L when it lands.
                # PAPER mode has no broker order and skips this entirely.
                if mode == "LIVE" and schwab_client is not None:
                    order_id = order_by_account.get(acct)
                    if order_id:
                        try:
                            from prime_trading.prime_fill_poller import start_fill_watcher
                            start_fill_watcher(
                                order_id, match["log_id"], schwab_client, side=broker_side,
                            )
                        except Exception as fw_err:
                            logger.warning(
                                "mata_sell: fill watcher start failed for %s: %s",
                                acct, fw_err,
                            )
    except Exception as e:
        logger.error("mata_sell trade-log close error: %s", e)

    # BUG-PRIME-MATA-SELL-SILENT-NOOP-01: in LIVE mode return 422 when every
    # allocation failed — no broker order was submitted and the frontend must
    # never display a plain success confirmation in that case.
    if mode == "LIVE" and not orders_placed and failures:
        return jsonify({
            "symbol":          symbol,
            "total_qty":       total_qty,
            "total_held":      allocation["total_held"],
            "allocated_total": allocation["allocated_total"],
            "orders":          [],
            "failures":        failures,
            "closed_logs":     [],
            "error": (
                f"No broker orders submitted — {len(failures)} allocation(s) failed: "
                + "; ".join(f.get("error", "unknown") for f in failures)
            ),
        }), 422

    return jsonify({
        "symbol":          symbol,
        "total_qty":       total_qty,
        "total_held":      allocation["total_held"],
        "allocated_total": allocation["allocated_total"],
        "orders":          orders_placed,
        "failures":        failures,
        "closed_logs":     closed_logs,
    }), 200


@api_bp.route("/stop-alerts", methods=["GET"])
def get_stop_alerts():
    """GET /api/v1/stop-alerts -- active stop-breach alerts for Lovable UI topbar.

    Sprint 24 Item 4. Returns list of breach records. UI polls every 30s.
    """
    try:
        from prime_trading.prime_stop_monitor import get_active_alerts
        alerts = get_active_alerts()
        return jsonify({"alerts": alerts, "count": len(alerts)}), 200
    except Exception as e:
        logger.error("stop_alerts error: %s", e)
        return jsonify({"alerts": [], "count": 0}), 200


@api_bp.route("/stop-alerts/<string:log_id>", methods=["DELETE"])
@require_local_token
def clear_stop_alert(log_id):
    """DELETE /api/v1/stop-alerts/{log_id} -- dismiss a stop alert.

    Sprint 24 Item 4. Called by the UI when the user acknowledges a breach.
    """
    try:
        from prime_trading.prime_stop_monitor import clear_alert
        clear_alert(log_id)
        return jsonify({"cleared": log_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/positions/<string:log_id>/stop", methods=["PUT"])
@require_local_token
def update_position_stop(log_id):
    """PUT /api/v1/positions/{log_id}/stop -- update stop_price on an OPEN trade.

    CIL-NEW-05. Validates that stop_price is a positive number.
    Warns (but does not block) if stop is above current price for LONG positions
    or below current price for SHORT positions.
    Logs the change to prime_ops_health.
    """
    from prime_data.prime_db import update_stop_price, _STOP_NOT_FOUND, log_ops_event, get_open_trades
    try:
        data = request.get_json(silent=True) or {}
        raw = data.get("stop_price")
        if raw is None:
            return jsonify({"error": "stop_price is required"}), 400
        try:
            new_stop = float(raw)
        except (TypeError, ValueError):
            return jsonify({"error": "stop_price must be a number"}), 400
        if new_stop <= 0:
            return jsonify({"error": "stop_price must be positive"}), 400

        old_stop = update_stop_price(log_id, new_stop)
        if old_stop is _STOP_NOT_FOUND:
            return jsonify({"error": "position not found or not OPEN"}), 404

        # Determine symbol and direction for warning + logging
        symbol = log_id
        warning = None
        try:
            trades = get_open_trades()
            trade = next((t for t in trades if str(t.get("log_id")) == str(log_id)), None)
            if trade:
                symbol = trade.get("symbol", log_id)
                direction = (trade.get("direction") or "LONG").upper()
                cur_price = float(trade.get("current_price") or trade.get("entry_price") or 0.0)
                if direction == "LONG" and cur_price > 0 and new_stop > cur_price:
                    warning = f"Stop ${new_stop:.2f} is above current price ${cur_price:.2f} for LONG position"
                elif direction == "SHORT" and cur_price > 0 and new_stop < cur_price:
                    warning = f"Stop ${new_stop:.2f} is below current price ${cur_price:.2f} for SHORT position"
        except Exception:
            pass

        old_str = f"{old_stop:.2f}" if old_stop is not None else "none"
        log_ops_event(
            event_type="STOP_PRICE_UPDATED",
            component="portfolio_ui",
            symbol=symbol,
            detail=f"{symbol} stop updated from {old_str} to {new_stop:.2f}",
        )

        result = {"log_id": log_id, "symbol": symbol, "stop_price": new_stop, "old_stop": old_stop}
        if warning:
            result["warning"] = warning
        return jsonify(result), 200
    except Exception as e:
        logger.error("update_position_stop error: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/portfolio/rebalance", methods=["POST"])
def portfolio_rebalance():
    """POST /api/v1/portfolio/rebalance -- ML-17 AI rebalance suggestions.

    Sprint 24 Item 5. Calls Claude with current portfolio weights and open
    signals. Returns ranked trim suggestions. Never auto-executes.
    """
    try:
        from prime_data.prime_db import get_open_positions
        from prime_api.prime_positions import enrich_position
        from prime_intelligence.prime_rebalance_advisor import (
            build_portfolio_snapshot,
            get_ai_rebalance_suggestions,
        )

        positions = get_open_positions()
        enriched  = [enrich_position(p) for p in positions]
        snapshot  = build_portfolio_snapshot(enriched)

        api_key = ""
        try:
            import os
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                from prime_config.prime_config import get_config
                api_key = get_config().ops.anthropic_api_key or ""
        except Exception:
            pass

        suggestions = get_ai_rebalance_suggestions(snapshot, api_key=api_key)
        return jsonify(suggestions), 200
    except Exception as e:
        logger.error("portfolio rebalance error: %s", e)
        return jsonify({"error": str(e), "suggestions": []}), 500


@api_bp.route("/trades/<string:log_id>/trailing-stop", methods=["POST"])
@require_local_token
def set_trailing_stop(log_id):  # noqa: E302 (Sprint 24 Item 4)
    """POST /api/v1/trades/{log_id}/trailing-stop -- set or clear trailing stop.

    Sprint 24 Item 4. Body: {trailing_stop_pct: float | null}.
    """
    from prime_data.prime_db import update_trailing_stop
    payload = request.get_json(silent=True) or {}
    pct_raw = payload.get("trailing_stop_pct")
    pct = None if pct_raw is None else float(pct_raw)
    updated = update_trailing_stop(log_id, pct)
    if not updated:
        return jsonify({"error": "unknown or closed log_id"}), 404
    return jsonify({"log_id": log_id, "trailing_stop_pct": pct}), 200


# ============================================================================
# Sprint 25 — Scan Control (Item 1)
# ============================================================================

import subprocess as _subprocess

_PROJECT_ROOT_PATH = Path(__file__).resolve().parent.parent
_LOGS_DIR = _PROJECT_ROOT_PATH / "logs"
_SCAN_LOG = _LOGS_DIR / "scan_log.txt"


def _get_scan_log_path() -> Path:
    """Return today's dated scan log path (rolling daily rotation)."""
    today = datetime.now().strftime("%Y-%m-%d")
    return _LOGS_DIR / f"scan_log_{today}.txt"


def _prune_old_scan_logs(keep_days: int = 7) -> None:
    """Delete scan_log_*.txt files older than keep_days."""
    try:
        cutoff = datetime.now().timestamp() - keep_days * 86400
        for f in _LOGS_DIR.glob("scan_log_*.txt"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass

_SCANNER_MAP: Dict[str, str] = {
    "psa":   "prime_scanners.prime_psa_scanner",
    "pead":  "prime_scanners.prime_pead_scanner",
    "uoa":   "prime_scanners.prime_uoa_scanner",
    "srs":   "prime_scanners.prime_srs_scanner",
    "mmr":   "prime_scanners.prime_mmr_scanner",
    "idx":   "prime_intelligence.prime_index_scanner",
    "short": "prime_intelligence.prime_short_scanner",
}

# Per-scanner run state: {scanner: {status, last_run, signals, pid}}
_scan_state: Dict[str, Any] = {}
_scan_lock = threading.Lock()


def _run_scanner_bg(scanner: str, module: str, *, skip_bridge: bool = False) -> None:
    """Background thread: run scanner subprocess, append output to dated scan log.

    skip_bridge — when True the per-scanner signal-bridge call is suppressed.
    Set by the parallel deep-scan coordinator, which runs its own bridge passes.
    """
    import sys as _sys
    import os as _os
    _LOGS_DIR.mkdir(exist_ok=True)
    _prune_old_scan_logs()
    scan_log = _get_scan_log_path()
    # TZ-01: human-readable local time for the raw log line; UTC for the
    # `last_run` field that the UI converts to ET via tz.js.
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_ts = datetime.utcnow().isoformat()
    with _scan_lock:
        _scan_state[scanner] = {"status": "running", "last_run": run_ts, "signals": None, "pid": None}

    log_line = f"\n--- {ts} START {scanner.upper()} ---\n"
    # Sprint 26 Item 4: pass PYTHONPATH explicitly so APScheduler subprocesses
    # find the project packages the same way a direct `python -m` call does.
    _env = dict(_os.environ)
    _env["PYTHONPATH"] = str(_PROJECT_ROOT_PATH)
    # CIL-049: force UTF-8 for the scanner subprocess's own stdout. On Windows the
    # child otherwise inherits cp1252 and raises UnicodeEncodeError when a scan
    # prints non-ASCII (special chars in symbols/notes/API fields), crashing the
    # runner silently. The parent already reads/writes the scan log as UTF-8 with
    # errors='replace'; this closes the gap on the producing side.
    _env["PYTHONIOENCODING"] = "utf-8"

    try:
        with open(scan_log, "a", encoding="utf-8") as lf:
            lf.write(log_line)
        proc = _subprocess.Popen(
            [_sys.executable, "-m", module],
            cwd=str(_PROJECT_ROOT_PATH),
            stdout=_subprocess.PIPE,
            stderr=_subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env,
        )
        with _scan_lock:
            _scan_state[scanner]["pid"] = proc.pid

        output_lines = []
        with open(scan_log, "a", encoding="utf-8") as lf:
            for line in proc.stdout:
                lf.write(line)
                output_lines.append(line)
        proc.wait()

        # After scanner completes, run bridge to ingest new signals.
        # Suppressed in parallel batch mode (skip_bridge=True); coordinator
        # runs two consolidated bridge passes instead.
        if not skip_bridge and scanner in ("psa", "pead", "uoa", "srs", "mmr"):
            bridge_proc = _subprocess.run(
                [_sys.executable, "-m", "prime_bridge.prime_signal_bridge", "--ingest-latest"],
                cwd=str(_PROJECT_ROOT_PATH),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_env,
            )
            with open(scan_log, "a", encoding="utf-8") as lf:
                lf.write(bridge_proc.stdout or "")
                if bridge_proc.stderr:
                    lf.write(bridge_proc.stderr)

        # Count new signals from bridge output
        signals = 0
        for line in output_lines:
            if "new signals" in line.lower() or "signals found" in line.lower():
                import re as _re
                m = _re.search(r"(\d+)\s+(?:new\s+)?signals", line, _re.IGNORECASE)
                if m:
                    signals = int(m.group(1))

        finish_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "complete" if proc.returncode == 0 else "error"
        with _scan_lock:
            # TZ-01: store last_run as UTC (display-converted to ET); log line stays local.
            _scan_state[scanner].update({"status": status,
                                         "last_run": datetime.utcnow().isoformat(),
                                         "signals": signals})
        with open(scan_log, "a", encoding="utf-8") as lf:
            lf.write(f"--- {finish_ts} END {scanner.upper()} rc={proc.returncode} ---\n")

    except Exception as exc:
        logger.error("scan runner %s error: %s", scanner, exc)
        with _scan_lock:
            _scan_state[scanner].update({"status": "error", "error": str(exc)})


def _run_bridge(label: str) -> None:
    """Run signal bridge and append labelled output to the scan log.

    Extracted as module-level so the parallel coordinator can call it between
    stages and tests can patch it directly.
    """
    import sys as _sys
    import os as _os
    _env = dict(_os.environ)
    _env["PYTHONPATH"] = str(_PROJECT_ROOT_PATH)
    _env["PYTHONIOENCODING"] = "utf-8"
    scan_log = _get_scan_log_path()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        bp = _subprocess.run(
            [_sys.executable, "-m", "prime_bridge.prime_signal_bridge", "--ingest-latest"],
            cwd=str(_PROJECT_ROOT_PATH),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env,
        )
        with open(scan_log, "a", encoding="utf-8") as lf:
            lf.write(f"--- {ts} BRIDGE-{label} ---\n")
            lf.write(bp.stdout or "")
            if bp.stderr:
                lf.write(bp.stderr)
    except Exception as exc:
        logger.error("Bridge pass %s error: %s", label, exc)


def _run_parallel_deep_scan() -> None:
    """WO-PRIME-PARALLEL-SCANS-01: concurrent deep-scan coordinator.

    Execution model:
      Stage 1 (concurrent) — IDX, UOA, MMR, PEAD, SRS, API-semaphore gated.
      Bridge pass 1        — fires as soon as UOA + PEAD complete; gates PSA
                             signal-led upgrade (PSA reads prime_signals for
                             UOA triggers, which only exist after the bridge).
      Stage 2              — PSA submitted to the same pool once UOA+PEAD
                             threads are free.
      Bridge pass 2        — consolidates PSA + SRS + all remaining output.
      Short scanner        — after bridge pass 2 (needs UOA/PEAD/PSA in DB).

    Per-API semaphores prevent concurrent Polygon or Schwab subprocesses from
    exceeding API rate limits.  Polygon concurrency = 1 on free plan, 3 on
    paid.  A failed scanner does not block remaining scanners (AC6).
    """
    import concurrent.futures as _cf

    # --- API semaphores -------------------------------------------------------
    try:
        ops = json.loads(open(_OPS_CONFIG_PATH, encoding="utf-8").read())
        polygon_plan = ops.get("polygon_plan", "free")
    except Exception:
        polygon_plan = "free"

    polygon_sem = threading.Semaphore(1 if polygon_plan == "free" else 3)
    schwab_sem = threading.Semaphore(3)

    _SCANNER_API_CLASS: Dict[str, str] = {
        "psa":   "polygon",
        "pead":  "polygon",
        "uoa":   "schwab",
        "srs":   "polygon",
        "mmr":   "schwab",
        "idx":   "polygon",
        "short": "schwab",
    }

    uoa_done: threading.Event = threading.Event()
    pead_done: threading.Event = threading.Event()

    def _guarded_run(scanner: str) -> None:
        """Acquire API semaphore, run scanner without per-scanner bridge, release."""
        mod = _SCANNER_MAP.get(scanner)
        if not mod:
            return
        with _scan_lock:
            if _scan_state.get(scanner, {}).get("status") == "running":
                logger.info("Parallel deep scan: %s already running — skipping", scanner)
                if scanner == "uoa":
                    uoa_done.set()
                elif scanner == "pead":
                    pead_done.set()
                return
        api = _SCANNER_API_CLASS.get(scanner, "polygon")
        sem = polygon_sem if api == "polygon" else schwab_sem
        sem.acquire()
        try:
            _run_scanner_bg(scanner, mod, skip_bridge=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("Parallel deep scan: %s failed: %s", scanner, exc)
        finally:
            sem.release()
            if scanner == "uoa":
                uoa_done.set()
            elif scanner == "pead":
                pead_done.set()

    # Stage 1: five scanners concurrent, PSA submitted after bridge pass 1.
    # max_workers = Stage-1 count + 1 so PSA always has a free slot.
    stage1 = ["idx", "uoa", "mmr", "pead", "srs"]
    with _cf.ThreadPoolExecutor(max_workers=len(stage1) + 1,
                                thread_name_prefix="deepscan") as pool:
        for s in stage1:
            pool.submit(_guarded_run, s)

        # Block *this* thread (not a pool worker) until UOA completes, then
        # run bridge pass 1 and submit PSA.  PSA's signal-led gate requires
        # only UOA signals in prime_signals; PEAD is supplementary and runs
        # concurrently — its signals reach bridge pass 2.  Previously gated on
        # both UOA+PEAD, but PEAD contends for polygon_sem with IDX/SRS (free
        # plan cap=1) and can block the coordinator indefinitely, preventing
        # PSA and SHORT from firing at all.
        uoa_done.wait()
        _run_bridge("1")
        pool.submit(_guarded_run, "psa")
    # ThreadPoolExecutor.__exit__ calls shutdown(wait=True) — all work done here.

    # Bridge pass 2: consolidate PSA + SRS + IDX output into prime_signals.
    _run_bridge("2")

    # Short scanner: reads UOA/PEAD/PSA signals from DB after bridge pass 2.
    short_mod = _SCANNER_MAP.get("short")
    if short_mod:
        with _scan_lock:
            if _scan_state.get("short", {}).get("status") != "running":
                _run_scanner_bg("short", short_mod, skip_bridge=False)


@api_bp.route("/scans/all", methods=["POST"])
def trigger_all_scans():
    """POST /api/v1/scans/all -- run full parallel deep scan coordinator.

    Mirrors the APScheduler _deep_scan_job(). Returns 202 immediately;
    coordinator runs in a daemon thread with the same stage ordering and
    bridge passes as the scheduled pre-market deep scan.
    """
    with _scan_lock:
        running = [s for s, st in _scan_state.items() if st.get("status") == "running"]
    if running:
        return jsonify({"error": "scan already running", "running": running}), 409
    t = threading.Thread(target=_run_parallel_deep_scan, daemon=True)
    t.name = "manual-deep-scan"
    t.start()
    return jsonify({"status": "started"}), 202


@api_bp.route("/scans/<string:scanner>", methods=["POST"])
def trigger_scan(scanner: str):
    """POST /api/v1/scans/{scanner} -- trigger a scanner run in the background.

    Sprint 25 Item 1. Returns 202 immediately; actual run happens async. If the
    scanner is already running, returns 409. Valid scanners: psa, pead, uoa,
    srs, mmr, idx, short.
    """
    scanner = scanner.lower()
    if scanner not in _SCANNER_MAP:
        return jsonify({"error": f"unknown scanner: {scanner}",
                        "valid": list(_SCANNER_MAP.keys())}), 400

    with _scan_lock:
        state = _scan_state.get(scanner, {})
        if state.get("status") == "running":
            return jsonify({"error": "scanner already running", "scanner": scanner}), 409

    module = _SCANNER_MAP[scanner]
    t = threading.Thread(target=_run_scanner_bg, args=(scanner, module), daemon=True)
    t.start()
    started = datetime.now().isoformat()
    return jsonify({"scanner": scanner, "started": started, "status": "started"}), 202


@api_bp.route("/scans/status", methods=["GET"])
def get_scan_status():
    """GET /api/v1/scans/status -- last run info per scanner.

    Sprint 25 Item 1. Returns list of {scanner, last_run, status, signals}.
    Merges in-process state with ops_events for scanners not yet triggered via API.
    """
    from prime_data.prime_db import get_ops_events

    # Fetch last SCAN_COMPLETE event per scanner from ops_events as a baseline.
    ops_baseline: Dict[str, str] = {}
    try:
        events = get_ops_events(limit=200)
        for ev in events:
            comp = ev.get("component", "")
            etype = ev.get("event_type", "")
            ts = ev.get("timestamp", "")
            if etype == "SCAN_COMPLETE" and comp and ts:
                name = comp.replace("_scanner", "").replace("prime_", "")
                if name not in ops_baseline:
                    ops_baseline[name] = ts
    except Exception:
        pass

    rows = []
    for scanner in _SCANNER_MAP:
        with _scan_lock:
            state = dict(_scan_state.get(scanner, {}))
        last_run = state.get("last_run") or ops_baseline.get(scanner, "--")
        rows.append({
            "scanner":  scanner.upper(),
            "last_run": last_run,
            "status":   state.get("status", "idle"),
            "signals":  state.get("signals"),
        })

    return jsonify({"scanners": rows, "count": len(rows)}), 200


@api_bp.route("/scans/log", methods=["GET"])
def get_scan_log():
    """GET /api/v1/scans/log -- last 50 lines of the scan log file.

    Sprint 25 Item 1. Returns plain text. UI polls every 5s during active scan.
    """
    n = int(request.args.get("lines", 50))
    # Sprint 26 Item 8: read from dated log (rolling rotation).
    date_param = request.args.get("date")  # YYYY-MM-DD, or today if omitted
    try:
        if date_param:
            log_path = _LOGS_DIR / f"scan_log_{date_param}.txt"
        else:
            log_path = _get_scan_log_path()
            # Fall back to legacy scan_log.txt if dated file doesn't exist yet.
            if not log_path.exists() and _SCAN_LOG.exists():
                log_path = _SCAN_LOG
        if not log_path.exists():
            return jsonify({"lines": [], "path": str(log_path), "date": date_param or "today"}), 200
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-n:] if len(all_lines) > n else all_lines
        return jsonify({
            "lines": [l.rstrip("\n") for l in tail],
            "total": len(all_lines),
            "date": date_param or datetime.now().strftime("%Y-%m-%d"),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e), "lines": []}), 500


# ============================================================================
# Sprint 25 — Schwab Connection (Item 2)
# ============================================================================

@api_bp.route("/schwab/status", methods=["GET"])
def get_schwab_status():
    """GET /api/v1/schwab/status -- Schwab connection status + token age + mode.

    Sprint 25 Item 2.
    """
    from prime_config.prime_config import get_config
    cfg = get_config()
    token_path = Path(cfg.schwab_snapshot.schwab_token_path) if cfg.schwab_snapshot.schwab_token_path else None
    token_age_hours = None
    token_warning = False

    if token_path and token_path.exists():
        try:
            import time as _time
            age_s = _time.time() - token_path.stat().st_mtime
            token_age_hours = round(age_s / 3600, 1)
            token_warning = token_age_hours > 23
        except Exception:
            pass

    connected = False
    accounts: list = []
    try:
        from prime_trading.prime_schwab import SchwabClient
        sc = SchwabClient()
        sc.connect()
        connected = True
        resp = sc.client.get_account_numbers()
        if resp and resp.status_code == 200:
            for a in resp.json():
                accounts.append({
                    "suffix": a.get("accountNumber", "")[-4:],
                    "hash":   a.get("hashValue", ""),
                })
    except Exception as e:
        logger.debug("schwab/status connect check: %s", e)

    mode = (cfg.trading_mode or "PAPER").upper()
    return jsonify({
        "connected":        connected,
        "mode":             mode,
        "accounts":         accounts,
        "token_age_hours":  token_age_hours,
        "token_warning":    token_warning,
        "token_path":       str(token_path) if token_path else None,
    }), 200


@api_bp.route("/schwab/connect", methods=["POST"])
def schwab_connect():
    """POST /api/v1/schwab/connect -- attempt a Schwab connection.

    Sprint 25 Item 2. Returns {connected, error, auth_required}.
    auth_required=true means the token is expired and schwab_auth_v2.py must be run.
    """
    try:
        from prime_trading.prime_schwab import SchwabClient
        sc = SchwabClient()
        sc.connect()
        resp = sc.client.get_account_numbers()
        accounts = []
        if resp and resp.status_code == 200:
            for a in resp.json():
                accounts.append(a.get("accountNumber", "")[-4:])
        return jsonify({"connected": True, "accounts": accounts}), 200
    except Exception as e:
        err = str(e)
        auth_req = "token" in err.lower() or "auth" in err.lower() or "expired" in err.lower()
        return jsonify({
            "connected":     False,
            "error":         err,
            "auth_required": auth_req,
            "auth_command":  "python schwab_auth_v2.py" if auth_req else None,
        }), 200


@api_bp.route("/schwab/balances", methods=["GET"])
def get_schwab_balances():
    """GET /api/v1/schwab/balances -- buying power per account.

    Sprint 25 Item 2.
    """
    try:
        from prime_trading.prime_schwab import SchwabClient
        sc = SchwabClient()
        sc.connect()
        resp = sc.client.get_account_numbers()
        if not resp or resp.status_code != 200:
            return jsonify({"balances": [], "error": "could not list accounts"}), 200

        balances = []
        for a in resp.json():
            suffix = a.get("accountNumber", "")[-4:]
            account_hash = a.get("hashValue", "")
            buying_power = None
            try:
                acct_resp = sc.client.get_account(account_hash, fields=["positions"])
                if acct_resp and acct_resp.status_code == 200:
                    acct_data = acct_resp.json()
                    acct_info = acct_data.get("securitiesAccount", acct_data)
                    cb = acct_info.get("currentBalances", {})
                    buying_power = cb.get("buyingPower") or cb.get("availableFunds") or cb.get("liquidationValue")
            except Exception:
                pass
            balances.append({
                "suffix":       suffix,
                "account_hash": account_hash,
                "buying_power": buying_power,
            })
        return jsonify({"balances": balances}), 200
    except Exception as e:
        logger.error("schwab/balances error: %s", e)
        return jsonify({"balances": [], "error": str(e)}), 200


@api_bp.route("/schwab/mode", methods=["POST"])
def set_schwab_mode():
    """POST /api/v1/schwab/mode -- switch PAPER/LIVE mode.

    Sprint 25 Item 2. Body: {mode: "PAPER"|"LIVE", confirmed: true}.
    Writes to config.json. Requires confirmed=true for LIVE.
    """
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "")).strip().upper()
    confirmed = bool(payload.get("confirmed", False))

    if mode not in ("PAPER", "LIVE"):
        return jsonify({"error": "mode must be PAPER or LIVE"}), 400
    if mode == "LIVE" and not confirmed:
        return jsonify({"error": "confirmed is required to switch to LIVE mode"}), 400

    config_path = _PROJECT_ROOT_PATH / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        raw["trading_mode"] = mode
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
        from prime_config.prime_config import reload_config
        reload_config()
        return jsonify({"mode": mode, "status": "ok"}), 200
    except Exception as e:
        logger.error("set_schwab_mode error: %s", e)
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Sprint 25 — Scan Schedule / APScheduler (Item 3)
# ============================================================================

# _SCHEDULER is initialised by prime_api_server.py at startup.
_SCHEDULER: Any = None
_SCAN_SCHEDULE_DEFAULTS = {
    "psa_time":           "09:45",
    "uoa_pead_srs_time":  "12:40",
    "mmr_time":           "12:45",
    "idx_time":           "12:45",
    "short_time":         "12:50",
    "schedule_enabled":   True,
    # Sprint 26 Item 5: pre-market deep scan (all scanners sequentially)
    "deep_scan_time":     "08:00",
    "deep_scan_enabled":  True,
}
_SCHEDULE_FIELDS = list(_SCAN_SCHEDULE_DEFAULTS.keys())


def _schedule_key(scanner: str) -> str:
    return f"scan_job_{scanner}"


def _reschedule_all(scheduler, schedule: Dict[str, Any]) -> None:
    """Apply schedule dict to the running APScheduler instance."""
    from apscheduler.triggers.cron import CronTrigger

    def _make_job(s: str):
        def _job():
            state = _scan_state.get(s, {})
            if state.get("status") == "running":
                logger.info("APScheduler: %s already running — skipping", s)
                return
            module = _SCANNER_MAP.get(s)
            if module:
                _run_scanner_bg(s, module)
        _job.__name__ = f"_scheduled_{s}"
        return _job

    if not schedule.get("schedule_enabled", True):
        for key in list(scheduler.get_jobs()):
            if key.id.startswith("scan_job_"):
                scheduler.remove_job(key.id)
        return

    def _parse_time(t: str):
        parts = t.split(":")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

    psa_h, psa_m = _parse_time(schedule.get("psa_time", "09:45"))
    uoa_h, uoa_m = _parse_time(schedule.get("uoa_pead_srs_time", "12:40"))
    mmr_h, mmr_m = _parse_time(schedule.get("mmr_time", "12:45"))
    idx_h, idx_m = _parse_time(schedule.get("idx_time", "12:45"))
    sht_h, sht_m = _parse_time(schedule.get("short_time", "12:50"))

    # Sprint 26 Item 5: pre-market deep scan runs ALL scanners sequentially.
    deep_h, deep_m = _parse_time(schedule.get("deep_scan_time", "08:00"))

    def _deep_scan_job():
        _run_parallel_deep_scan()

    job_defs = [
        ("scan_job_psa",   _make_job("psa"),   psa_h, psa_m),
        ("scan_job_uoa",   _make_job("uoa"),   uoa_h, uoa_m),
        ("scan_job_pead",  _make_job("pead"),  uoa_h, uoa_m),
        ("scan_job_srs",   _make_job("srs"),   uoa_h, uoa_m),
        ("scan_job_mmr",   _make_job("mmr"),   mmr_h, mmr_m),
        ("scan_job_idx",   _make_job("idx"),   idx_h, idx_m),
        ("scan_job_short", _make_job("short"), sht_h, sht_m),
    ]

    for job_id, fn, hour, minute in job_defs:
        trigger = CronTrigger(
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
            timezone="America/New_York",
        )
        if scheduler.get_job(job_id):
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            scheduler.add_job(fn, trigger=trigger, id=job_id, replace_existing=True)

    # Deep scan job
    deep_trigger = CronTrigger(
        day_of_week="mon-fri",
        hour=deep_h,
        minute=deep_m,
        timezone="America/New_York",
    )
    if schedule.get("deep_scan_enabled", True):
        if scheduler.get_job("scan_job_deep"):
            scheduler.reschedule_job("scan_job_deep", trigger=deep_trigger)
        else:
            scheduler.add_job(_deep_scan_job, trigger=deep_trigger,
                              id="scan_job_deep", replace_existing=True)
    else:
        if scheduler.get_job("scan_job_deep"):
            scheduler.remove_job("scan_job_deep")


def _read_schedule() -> Dict[str, Any]:
    """Read scan schedule settings from ops_config.json."""
    try:
        with open(_OPS_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = dict(_SCAN_SCHEDULE_DEFAULTS)
        for k in _SCHEDULE_FIELDS:
            if k in raw:
                result[k] = raw[k]
        return result
    except Exception:
        return dict(_SCAN_SCHEDULE_DEFAULTS)


@api_bp.route("/scans/schedule", methods=["GET"])
def get_scan_schedule():
    """GET /api/v1/scans/schedule -- current APScheduler scan schedule.

    Sprint 25 Item 3.
    """
    schedule = _read_schedule()

    # Attach next-run times from APScheduler
    next_runs: Dict[str, str] = {}
    if _SCHEDULER:
        try:
            for job in _SCHEDULER.get_jobs():
                jid = job.id
                if jid.startswith("scan_job_") and job.next_run_time:
                    scanner = jid.replace("scan_job_", "")
                    nr = job.next_run_time.strftime("%H:%M ET")
                    next_runs[scanner] = nr
        except Exception:
            pass

    return jsonify({"schedule": schedule, "next_runs": next_runs}), 200


@api_bp.route("/scans/schedule", methods=["POST"])
def post_scan_schedule():
    """POST /api/v1/scans/schedule -- update scan schedule and reschedule APScheduler jobs.

    Sprint 25 Item 3. Writes to ops_config.json; takes effect immediately without restart.
    """
    payload = request.get_json(silent=True) or {}
    try:
        with open(_OPS_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)

        for key in _SCHEDULE_FIELDS:
            if key in payload:
                raw[key] = payload[key]

        with open(_OPS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)

        new_schedule = _read_schedule()
        if _SCHEDULER and _SCHEDULER.running:
            _reschedule_all(_SCHEDULER, new_schedule)

        return jsonify({"schedule": new_schedule, "rescheduled": _SCHEDULER is not None}), 200
    except Exception as e:
        logger.error("post_scan_schedule error: %s", e)
        return jsonify({"error": str(e)}), 500


def init_scheduler() -> Any:
    """Create and start the APScheduler BackgroundScheduler.

    Called from prime_api_server.py at startup. Returns the scheduler instance,
    which is also stored as the module-level _SCHEDULER so route handlers can
    reschedule jobs live without a restart.
    """
    global _SCHEDULER
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone="America/New_York")
        schedule = _read_schedule()
        _reschedule_all(scheduler, schedule)
        scheduler.start()
        _SCHEDULER = scheduler
        logger.info("APScheduler started — %d scan jobs scheduled", len(scheduler.get_jobs()))
        return scheduler
    except Exception as e:
        logger.warning("APScheduler init failed: %s", e)
        return None


@api_bp.route("/scans/log/files", methods=["GET"])
def get_scan_log_files():
    """GET /api/v1/scans/log/files -- list available dated scan log files (last 7 days)."""
    try:
        files = sorted(
            [f.stem.replace("scan_log_", "") for f in _LOGS_DIR.glob("scan_log_*.txt")],
            reverse=True,
        )[:7]
        return jsonify({"dates": files}), 200
    except Exception as e:
        return jsonify({"dates": [], "error": str(e)}), 200


# ============================================================================
# Sprint 26 — New Endpoints
# ============================================================================

@api_bp.route("/positions/prices", methods=["GET"])
def get_position_prices():
    """GET /api/v1/positions/prices -- current quotes for all open position symbols.

    Sprint 26 Item 3. Returns {symbol: current_price}. Graceful fallback to
    last known price (entry_price) when Schwab is unavailable.
    """
    from prime_data.prime_db import get_open_positions
    try:
        positions = get_open_positions()
        symbols = list({(p.get("symbol") or "").upper() for p in positions if p.get("symbol")})
        prices: Dict[str, float] = {}

        # Try Schwab live quotes.
        try:
            from prime_trading.prime_schwab import SchwabClient
            client = SchwabClient()
            client.connect()
            quotes = client.get_quotes(symbols)
            for sym, data in quotes.items():
                price = (
                    data.get("quote", {}).get("lastPrice")
                    or data.get("quote", {}).get("mark")
                    or data.get("regularMarketLastPrice")
                    or 0.0
                )
                if price:
                    prices[sym.upper()] = float(price)
        except Exception as e:
            logger.debug("Live quote fetch failed: %s — using fallback prices", e)

        # Fill gaps with last known price from DB.
        for p in positions:
            sym = (p.get("symbol") or "").upper()
            if sym and sym not in prices:
                prices[sym] = float(p.get("entry_price") or p.get("price_at_scan") or 0.0)

        return jsonify({
            "prices": prices,
            "count": len(prices),
            "ts": datetime.now().isoformat(),
        }), 200
    except Exception as e:
        logger.error("positions/prices error: %s", e)
        return jsonify({"prices": {}, "count": 0, "error": str(e)}), 500


@api_bp.route("/trades/history", methods=["GET"])
def get_trade_history():
    """GET /api/v1/trades/history -- CLOSED and/or OPEN trades with entry/exit/P&L.

    Sprint 26 Item 7. Sprint 29 H-01/H-02/H-03.
    Query params: strategy, direction, limit (default 500),
                  from_date (ISO date), to_date (ISO date),
                  status (all|open|closed, default all).
    """
    from prime_data.prime_db import get_closed_trades, get_open_trades
    strategy  = request.args.get("strategy")
    direction = request.args.get("direction", "").upper()
    limit     = int(request.args.get("limit", 500))
    from_date = request.args.get("from_date")        # e.g. "2026-06-01"
    to_date   = request.args.get("to_date")          # e.g. "2026-06-19"
    status    = request.args.get("status", "all").lower()  # all | open | closed

    def _apply_filters(rows, time_field):
        if strategy:
            rows = [t for t in rows if t.get("strategy") == strategy]
        if direction:
            rows = [t for t in rows if (t.get("direction") or "").upper() == direction]
        if from_date:
            rows = [t for t in rows if (t.get(time_field) or "") >= from_date]
        if to_date:
            rows = [t for t in rows if (t.get(time_field) or "") <= to_date + "T23:59:59"]
        return rows

    try:
        closed_trades: list = []
        if status in ("all", "closed"):
            closed_trades = get_closed_trades(limit=limit)
            closed_trades = _apply_filters(closed_trades, "exit_time")

        open_trades: list = []
        if status in ("all", "open"):
            open_trades = get_open_trades()
            open_trades = _apply_filters(open_trades, "entry_time")
            from datetime import timezone
            now_utc = datetime.now(timezone.utc)
            for t in open_trades:
                try:
                    et = t.get("entry_time") or ""
                    if et:
                        entry_dt = datetime.fromisoformat(et.replace("Z", "+00:00"))
                        if entry_dt.tzinfo is None:
                            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                        t["hold_minutes"] = int((now_utc - entry_dt).total_seconds() / 60)
                    else:
                        t["hold_minutes"] = None
                except Exception:
                    t["hold_minutes"] = None
                t["pnl_dollars"] = None
                t["pnl_pct"] = None

        # OPEN rows sort before CLOSED rows of the same date.
        trades = open_trades + closed_trades

        # Summary statistics cover only CLOSED trades.
        total     = len(closed_trades)
        wins      = sum(1 for t in closed_trades if (t.get("pnl_dollars") or 0) > 0)
        total_pnl = sum((t.get("pnl_dollars") or 0) for t in closed_trades)
        win_rate  = round(wins / total * 100, 1) if total else 0.0
        avg_hold  = round(
            sum((t.get("hold_minutes") or 0) for t in closed_trades) / total, 0
        ) if total else 0.0

        return jsonify({
            "trades": trades,
            "summary": {
                "total":            total,
                "wins":             wins,
                "win_rate":         win_rate,
                "total_pnl":        round(total_pnl, 2),
                "avg_hold_minutes": avg_hold,
            },
        }), 200
    except Exception as e:
        logger.error("trades/history error: %s", e)
        return jsonify({"trades": [], "summary": {}, "error": str(e)}), 500


@api_bp.route("/ml/dataset", methods=["GET"])
def get_ml_dataset():
    """GET /api/v1/ml/dataset -- ML training rows (signal features + trade outcomes).

    Sprint 26 Item 5. Also writes CSV to data/ml_training_dataset.csv on each call.
    """
    try:
        from prime_data.prime_ml_dataset import get_training_rows, export_csv
        rows = get_training_rows()
        try:
            csv_path = export_csv(rows)
            csv_written = str(csv_path)
        except Exception:
            csv_written = None
        return jsonify({
            "rows":       rows,
            "count":      len(rows),
            "csv_path":   csv_written,
        }), 200
    except Exception as e:
        logger.error("ml/dataset error: %s", e)
        return jsonify({"rows": [], "count": 0, "error": str(e)}), 500


@api_bp.route("/ai/usage", methods=["GET"])
def get_ai_usage():
    """GET /api/v1/ai/usage -- aggregated AI cost stats.

    Sprint 26 Item 6. Returns today/week/month/total cost, by_feature breakdown,
    recent call log, and budget alert status.
    """
    try:
        from prime_ai.prime_ai_usage import get_usage_stats
        stats = get_usage_stats()

        # Budget alert from ops_config.json
        budget_alert = None
        try:
            with open(_OPS_CONFIG_PATH, "r", encoding="utf-8") as f:
                _ops = json.load(f)
            budget = float(_ops.get("monthly_ai_budget", 10.0))
            month_cost = stats.get("month_cost", 0.0)
            if budget > 0:
                pct = month_cost / budget
                if pct >= 1.0:
                    budget_alert = {"level": "RED",   "message": f"Monthly AI budget exceeded (${month_cost:.2f}/${budget:.2f})"}
                elif pct >= 0.8:
                    budget_alert = {"level": "AMBER", "message": f"Monthly AI budget at {round(pct*100)}% (${month_cost:.2f}/${budget:.2f})"}
        except Exception:
            pass

        stats["budget_alert"] = budget_alert
        return jsonify(stats), 200
    except Exception as e:
        logger.error("ai/usage error: %s", e)
        return jsonify({"error": str(e)}), 500
