"""
PRIME v1.0 API Route Definitions (UI-CONTRACT-001).

Read-only REST endpoints for Lovable UI consumption.
All reads delegate to prime_db.py -- zero direct SQL in this file.
"""

import hmac
import logging
from datetime import datetime
from functools import wraps
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
    """GET /api/v1/positions -- all OPEN positions from prime_trade_log."""
    from prime_data.prime_db import get_open_positions
    try:
        positions = get_open_positions()
        return jsonify({"positions": positions, "count": len(positions)}), 200
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
