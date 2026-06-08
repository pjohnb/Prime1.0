"""
PRIME v1.0 API Server (UI-CONTRACT-001).

Flask app on port 5001. Stable REST endpoints for Lovable UI consumption.
No business logic -- all reads via prime_db.py and prime_signals_db.py.

Run: python prime_api/prime_api_server.py
"""

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask

from prime_api.prime_api_routes import api_bp

logger = logging.getLogger(__name__)

API_PORT = 5001


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    # CORS: the Lovable UI is served from a different origin (port 5002) and
    # fetches this API on 5001. Without these headers the browser blocks the
    # response, surfacing as "API: offline" in the UI even though the endpoint
    # itself returns 200. Server binds to 127.0.0.1, so a wildcard origin is
    # acceptable for local-only read access.
    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response

    @app.route("/<path:_path>", methods=["OPTIONS"])
    @app.route("/", methods=["OPTIONS"])
    def cors_preflight(_path=""):
        # Answer CORS preflight requests; headers are added by add_cors_headers.
        return ("", 204)

    @app.route("/")
    def index():
        return {"name": "PRIME API", "version": "1.0", "docs": "/api/v1/health"}, 200

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Sprint 16 Item 1: pre-flight check FIRST -- ensure ANTHROPIC_API_KEY is
    # available (env, else ops_config.json fallback) so /advisory/* returns live
    # Claude recommendations rather than graceful-degradation placeholders.
    from prime_startup import run_startup_checks
    run_startup_checks()

    # Sprint 26 Item 4: warn if Polygon API key is missing (IDX/SHORT scanners need it).
    try:
        from prime_config.prime_config import get_config
        _cfg = get_config()
        if not (_cfg.polygon_api_key or "").strip():
            logger.warning(
                "PRIME STARTUP: polygon_api_key is missing from config.json. "
                "IDX and SHORT scans will fail. Add polygon_api_key to config.json."
            )
    except Exception as _e:
        logger.warning("Polygon key check failed: %s", _e)

    app = create_app()
    logger.info("PRIME API server starting on port %d", API_PORT)
    # Sprint 23 Item 1: auto-sync Schwab positions on startup if connected.
    try:
        from prime_trading.prime_schwab_sync import sync_schwab_positions
        result = sync_schwab_positions()
        logger.info(
            "Schwab startup sync: imported=%d skipped=%d errors=%d",
            result.get("imported", 0), result.get("skipped", 0),
            len(result.get("errors", [])),
        )
    except Exception as e:
        logger.info("Schwab startup sync skipped: %s", e)
    # Sprint 24 Item 4: start stop monitor background thread.
    try:
        from prime_trading.prime_stop_monitor import start_monitor
        start_monitor()
    except Exception as e:
        logger.warning("Stop monitor startup failed: %s", e)
    # Sprint 25 Item 3: start APScheduler for internal scan schedule.
    try:
        from prime_api.prime_api_routes import init_scheduler
        init_scheduler()
    except Exception as e:
        logger.warning("APScheduler startup failed: %s", e)
    app.run(host="127.0.0.1", port=API_PORT, debug=False)
