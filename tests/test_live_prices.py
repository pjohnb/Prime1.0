"""Sprint 26 Item 3: /positions/prices endpoint and live price polling logic."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path):
    """Minimal Flask test client for prime_api_routes."""
    db = tmp_path / "test.db"
    from prime_data.prime_db import init_db
    init_db(db_path=db)

    from prime_api.prime_api_routes import api_bp
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPositionsPricesEndpoint:

    def test_prices_endpoint_exists(self, client):
        """GET /positions/prices must return 200 with a prices dict."""
        with patch("prime_data.prime_db.get_open_positions", return_value=[]):
            resp = client.get("/api/v1/positions/prices")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "prices" in data

    def test_prices_empty_when_no_positions(self, client):
        """When no open positions exist, prices should be an empty dict."""
        with patch("prime_data.prime_db.get_open_positions", return_value=[]):
            resp = client.get("/api/v1/positions/prices")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["prices"] == {}

    def test_prices_endpoint_falls_back_to_entry_price(self, client):
        """When Schwab is unavailable, falls back to entry_price from DB row."""
        fake_positions = [
            {"symbol": "META", "entry_price": 300.0, "direction": "LONG"},
        ]
        with patch("prime_data.prime_db.get_open_positions",
                   return_value=fake_positions), \
             patch("prime_trading.prime_schwab.SchwabClient",
                   side_effect=Exception("offline")):
            resp = client.get("/api/v1/positions/prices")
        assert resp.status_code == 200
        data = resp.get_json()
        prices = data.get("prices", {})
        # Fallback: entry_price should appear for the symbol
        assert "META" in prices
        assert prices["META"] == pytest.approx(300.0)

    def test_prices_response_contains_count(self, client):
        """Response must include a count field matching len(prices)."""
        fake_positions = [
            {"symbol": "AAPL", "entry_price": 150.0, "direction": "LONG"},
            {"symbol": "TSLA", "entry_price": 200.0, "direction": "LONG"},
        ]
        with patch("prime_data.prime_db.get_open_positions",
                   return_value=fake_positions), \
             patch("prime_trading.prime_schwab.SchwabClient",
                   side_effect=Exception("offline")):
            resp = client.get("/api/v1/positions/prices")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("count") == len(data.get("prices", {}))
