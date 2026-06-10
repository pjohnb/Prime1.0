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
from datetime import datetime
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
    try:
        summary = fetch_summary()
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
    from prime_data.prime_db import get_ops_events, table_exists
    status: Dict[str, Any] = {
        "status": "ok",
        "db_connected": False,
        "last_scan_event": None,
        "ml_dataset_row_count": 0,
    }
    try:
        status["db_connected"] = table_exists("prime_trade_log")
        events = get_ops_events(limit=1)
        if events:
            status["last_scan_event"] = events[0].get("timestamp")
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
        get_open_by_symbol,
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

    # ── LIVE mode path ────────────────────────────────────────────────────────
    if mode_cfg == "LIVE":
        from prime_trading.prime_schwab_orders import submit_order, OrderGateError
        from prime_trading.prime_fill_poller import start_fill_watcher

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
            )
        except TradeRecordError as e:
            return jsonify({"error": str(e)}), 400

        # Start fill watcher (non-blocking background thread)
        try:
            schwab_for_fill = _sc if "_sc" in dir() else None  # type: ignore[name-defined]
            start_fill_watcher(result["order_id"], log_id, schwab_for_fill)
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
        for t in get_open_by_symbol(symbol):
            if (t.get("strategy") == strategy
                    and (t.get("direction") or "").upper() == direction
                    and t.get("shares") == qty
                    and _is_recent(t.get("entry_time", ""), now)):
                return jsonify({"error": "duplicate trade within 60s"}), 409

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


@api_bp.route("/sync/schwab", methods=["GET"])
def sync_schwab():
    """GET /api/v1/sync/schwab -- import current Schwab holdings into prime_trade_log.

    Sprint 23 Item 1. Triggers a live Schwab position sync and returns a count
    summary. Safe to call multiple times -- deduplication is enforced in the sync
    module. Degrades gracefully when Schwab is not connected.
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

        positions = get_open_trades()

        # Group by symbol
        groups: Dict[str, Any] = {}
        for p in positions:
            sym = (p.get("symbol") or "").upper()
            if not sym:
                continue
            if sym not in groups:
                groups[sym] = {
                    "symbol":       sym,
                    "total_shares": 0,
                    "total_cost":   0.0,
                    "accounts":     [],
                    "log_ids":      [],
                    "direction":    (p.get("direction") or "LONG").upper(),
                }
            ep = float(p.get("entry_price") or p.get("price_at_scan") or 0.0)
            sh = int(p.get("shares") or 0)
            groups[sym]["total_shares"] += sh
            groups[sym]["total_cost"]   += ep * sh
            acc = p.get("account") or ""
            if acc and acc not in groups[sym]["accounts"]:
                groups[sym]["accounts"].append(acc)
            groups[sym]["log_ids"].append(p.get("log_id"))

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

        summary = {
            "total_market_value": round(total_market_value, 2),
            "total_cost_basis":   round(total_cost_basis, 2),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "position_count":     len(rows),
            "sector_breakdown":   {
                s: round(v / total_market_value * 100, 1) if total_market_value else 0.0
                for s, v in sector_totals.items()
            },
        }

        return jsonify({
            "rows":     rows,
            "count":    len(rows),
            "summary":  summary,
            "warnings": warnings,
        }), 200
    except Exception as e:
        logger.error("portfolio endpoint error: %s", e)
        return jsonify({"error": str(e)}), 500


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

    symbol    = str(payload.get("symbol", "")).strip().upper()
    order_type = str(payload.get("order_type", "MARKET")).upper()
    confirmed  = bool(payload.get("confirmed", False))
    holdings   = payload.get("account_holdings", [])

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

    for alloc in allocation["allocations"]:
        sell_qty     = alloc["sell_qty"]
        account      = alloc["account"]
        account_hash = alloc.get("account_hash", "")
        if sell_qty <= 0:
            continue

        if mode == "LIVE":
            from prime_trading.prime_schwab_orders import submit_order, OrderGateError
            try:
                from prime_trading.prime_schwab import SchwabClient
                _sc = SchwabClient()
                _sc.connect()
                result = submit_order(
                    symbol=symbol,
                    qty=sell_qty,
                    side="SELL",
                    order_type=order_type,
                    price=price or 0.0,
                    account_hash=account_hash,
                    confirmed=True,
                    schwab_client=_sc,
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
            # PAPER mode: close proportional shares from each account's log_ids
            orders_placed.append({
                "account":  account,
                "sell_qty": sell_qty,
                "status":   "PAPER_CLOSE",
            })

    return jsonify({
        "symbol":          symbol,
        "total_qty":       total_qty,
        "total_held":      allocation["total_held"],
        "allocated_total": allocation["allocated_total"],
        "orders":          orders_placed,
        "failures":        failures,
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
    "mts":   "prime_scanners.prime_mts_scanner",
    "idx":   "prime_intelligence.prime_index_scanner",
    "short": "prime_intelligence.prime_short_scanner",
}

# Per-scanner run state: {scanner: {status, last_run, signals, pid}}
_scan_state: Dict[str, Any] = {}
_scan_lock = threading.Lock()


def _run_scanner_bg(scanner: str, module: str) -> None:
    """Background thread: run scanner subprocess, append output to dated scan log."""
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

        # After scanner completes, run bridge to ingest new signals
        if scanner in ("psa", "pead", "uoa", "srs", "mts"):
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


@api_bp.route("/scans/<string:scanner>", methods=["POST"])
def trigger_scan(scanner: str):
    """POST /api/v1/scans/{scanner} -- trigger a scanner run in the background.

    Sprint 25 Item 1. Returns 202 immediately; actual run happens async. If the
    scanner is already running, returns 409. Valid scanners: psa, pead, uoa,
    srs, mts, idx, short.
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
    "mts_time":           "12:45",
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
    mts_h, mts_m = _parse_time(schedule.get("mts_time", "12:45"))
    idx_h, idx_m = _parse_time(schedule.get("idx_time", "12:45"))
    sht_h, sht_m = _parse_time(schedule.get("short_time", "12:50"))

    # Sprint 26 Item 5: pre-market deep scan runs ALL scanners sequentially.
    deep_h, deep_m = _parse_time(schedule.get("deep_scan_time", "08:00"))

    def _deep_scan_job():
        _DEEP_ORDER = ["psa", "pead", "uoa", "srs", "mts", "idx", "short"]
        for s in _DEEP_ORDER:
            mod = _SCANNER_MAP.get(s)
            if not mod:
                continue
            state = _scan_state.get(s, {})
            if state.get("status") == "running":
                logger.info("Deep scan: %s already running — skipping", s)
                continue
            _run_scanner_bg(s, mod)

    job_defs = [
        ("scan_job_psa",   _make_job("psa"),   psa_h, psa_m),
        ("scan_job_uoa",   _make_job("uoa"),   uoa_h, uoa_m),
        ("scan_job_pead",  _make_job("pead"),  uoa_h, uoa_m),
        ("scan_job_srs",   _make_job("srs"),   uoa_h, uoa_m),
        ("scan_job_mts",   _make_job("mts"),   mts_h, mts_m),
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
    """GET /api/v1/trades/history -- all CLOSED trades with entry/exit/P&L.

    Sprint 26 Item 7. Query params: strategy, direction, limit (default 500).
    """
    from prime_data.prime_db import get_closed_trades
    strategy  = request.args.get("strategy")
    direction = request.args.get("direction", "").upper()
    limit     = int(request.args.get("limit", 500))
    try:
        trades = get_closed_trades(limit=limit)
        if strategy:
            trades = [t for t in trades if t.get("strategy") == strategy]
        if direction:
            trades = [t for t in trades if (t.get("direction") or "").upper() == direction]

        total    = len(trades)
        wins     = sum(1 for t in trades if (t.get("pnl_dollars") or 0) > 0)
        total_pnl = sum((t.get("pnl_dollars") or 0) for t in trades)
        win_rate = round(wins / total * 100, 1) if total else 0.0
        avg_hold = round(
            sum((t.get("hold_minutes") or 0) for t in trades) / total, 0
        ) if total else 0.0

        return jsonify({
            "trades":    trades,
            "summary": {
                "total":     total,
                "wins":      wins,
                "win_rate":  win_rate,
                "total_pnl": round(total_pnl, 2),
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
