"""
PRIME v1.0 API Route Definitions (UI-CONTRACT-001).

Read-only REST endpoints for Lovable UI consumption.
All reads delegate to prime_db.py -- zero direct SQL in this file.
"""

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


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
