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
    """GET /api/v1/health -- server status, DB connection, last scan."""
    from prime_data.prime_db import get_ops_events, table_exists
    status: Dict[str, Any] = {
        "status": "ok",
        "db_connected": False,
        "last_scan_event": None,
    }
    try:
        status["db_connected"] = table_exists("prime_trade_log")
        events = get_ops_events(limit=1)
        if events:
            status["last_scan_event"] = events[0].get("timestamp")
    except Exception as e:
        status["status"] = "degraded"
        status["error"] = str(e)

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
    """POST /api/v1/trades -- submit a PAPER trade from the Lovable UI.

    Body: {symbol, qty, strategy, direction, account, price}. Validates PAPER
    mode (rejects when trading_mode != PAPER), guards against an accidental
    duplicate submission within 60s, and writes to prime_trade_log via
    prime_db.py. Returns 201 with the new log_id.
    """
    from prime_config.prime_config import get_config
    from prime_data.prime_db import (
        get_open_by_symbol,
        insert_trade,
        TradeRecordError,
    )

    if (get_config().trading_mode or "PAPER").upper() != "PAPER":
        return jsonify({"error": "rejected: server is not in PAPER mode"}), 403

    payload = request.get_json(silent=True) or {}
    symbol = str(payload.get("symbol", "")).strip().upper()
    strategy = str(payload.get("strategy", "")).strip()
    direction = str(payload.get("direction", "")).strip().upper()
    account = str(payload.get("account", "")).strip() or None

    try:
        qty = int(payload.get("qty"))
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        return jsonify({"error": "qty must be an integer and price a number"}), 400

    if not symbol or not strategy:
        return jsonify({"error": "symbol and strategy are required"}), 400
    if direction not in ("LONG", "SHORT", "BUY", "SELL"):
        return jsonify({"error": "direction must be LONG/SHORT/BUY/SELL"}), 400
    if qty <= 0:
        return jsonify({"error": "qty must be positive"}), 400
    if price <= 0:
        return jsonify({"error": "price must be positive"}), 400

    direction = {"BUY": "LONG", "SELL": "SHORT"}.get(direction, direction)
    now = datetime.now()

    # Duplicate guard: identical OPEN order for the same symbol/strategy/
    # direction/qty submitted within the last 60 seconds.
    try:
        for t in get_open_by_symbol(symbol):
            if (t.get("strategy") == strategy
                    and (t.get("direction") or "").upper() == direction
                    and t.get("shares") == qty
                    and _is_recent(t.get("entry_time", ""), now)):
                return jsonify({"error": "duplicate trade within 60s"}), 409

        log_id = insert_trade(
            strategy=strategy,
            symbol=symbol,
            direction=direction,
            mode="PAPER",
            order_type="MARKET",
            shares=qty,
            entry_time=now.isoformat(),
            price_at_scan=price,
            entry_price=price,
            account=account,
            signal_source="UI",
            trade_source="PAPER",
        )
    except TradeRecordError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("create_trade error: %s", e)
        return jsonify({"error": str(e)}), 500

    return jsonify({"log_id": log_id, "status": "OPEN", "trade_source": "PAPER"}), 201


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
        result = close_trade_manual(log_id, exit_price, exit_reason,
                                    close_ts=datetime.now().isoformat())
    except Exception as e:
        logger.error("close_trade error: %s", e)
        return jsonify({"error": str(e)}), 500

    if result is None:
        return jsonify({"error": "unknown log_id"}), 404
    return jsonify(result), 200
