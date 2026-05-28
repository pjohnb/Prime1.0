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

    @app.route("/")
    def index():
        return {"name": "PRIME API", "version": "1.0", "docs": "/api/v1/health"}, 200

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    logger.info("PRIME API server starting on port %d", API_PORT)
    app.run(host="127.0.0.1", port=API_PORT, debug=False)
