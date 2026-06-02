"""
PRIME v1.0 Lovable UI Server (UI-LOVABLE-001).

Minimal Flask app on port 5002 serving static UI files.
All data fetched from Flask API on port 5001 -- no direct DB access.
"""

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, Response, send_from_directory

logger = logging.getLogger(__name__)

UI_PORT = 5002
UI_DIR = Path(__file__).resolve().parent

# API base the browser calls for reads and the write path (Item 2).
API_BASE = "http://localhost:5001/api/v1"


def create_ui_app() -> Flask:
    app = Flask(__name__, static_folder=str(UI_DIR), static_url_path="")

    @app.route("/")
    def index():
        return send_from_directory(str(UI_DIR), "index.html")

    @app.route("/ui-config.js")
    def ui_config():
        """Expose the API base + write-path bearer token to the browser.

        The token lives server-side in config.json (never committed) and is
        delivered to the localhost-only UI at runtime, keeping it out of the
        repo and the static files.
        """
        token = ""
        try:
            from prime_config.prime_config import get_config
            token = get_config().api_token or ""
        except Exception as e:  # config missing/invalid -> writes simply fail auth
            logger.warning("ui-config: could not load api_token: %s", e)
        js = (
            "window.PRIME_CONFIG = {{ apiBase: {base!r}, apiToken: {token!r} }};"
            .format(base=API_BASE, token=token)
        )
        return Response(js, mimetype="application/javascript")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(str(UI_DIR), filename)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_ui_app()
    logger.info("PRIME Lovable UI serving on port %d", UI_PORT)
    app.run(host="127.0.0.1", port=UI_PORT, debug=False)
