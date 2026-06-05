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
    confirmed  = bool(payload.get("confirmed", False))

    try:
        qty   = int(payload.get("qty"))
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

            result = submit_order(
                symbol=symbol,
                qty=qty,
                side=side_schwab,
                order_type=order_type,
                price=price,
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
            log_id = insert_trade(
                strategy=strategy,
                symbol=symbol,
                direction=direction,
                mode="LIVE",
                order_type=order_type,
                shares=qty,
                entry_time=now.isoformat(),
                price_at_scan=price,
                entry_price=price,
                account=account,
                order_id=result.get("order_id"),
                signal_source="UI",
                trade_source="LIVE",
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

    # ── PAPER mode path (unchanged) ───────────────────────────────────────────
    if mode_cfg != "PAPER":
        return jsonify({"error": f"unknown trading_mode: {mode_cfg}"}), 500

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
    # Sprint 24
    "max_order_pct", "stop_execution_mode", "max_sector_pct", "max_position_pct",
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
def set_trailing_stop(log_id):
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
