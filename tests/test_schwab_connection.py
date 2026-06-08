"""Sprint 25 Item 2 — Schwab Connection API tests."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import prime_api.prime_api_routes as routes
    from prime_api.prime_api_server import create_app

    monkeypatch.setattr(routes, "_OPS_CONFIG_PATH", tmp_path / "ops_config.json")
    (tmp_path / "ops_config.json").write_text(json.dumps({
        "scan_schedule": {}, "notification_channels": "none", "health_check_interval": 900,
    }))

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "polygon_api_key": "test",
        "finnhub_api_key": "test",
        "tradestation": {},
        "schwab_snapshot": {"schwab_token_path": "", "schwab_app_key": "", "schwab_app_secret": ""},
        "execution": {},
        "risk_management": {},
        "api_token": "test-token",
        "trading_mode": "PAPER",
    }))
    monkeypatch.setattr(routes, "_PROJECT_ROOT_PATH", tmp_path)

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_schwab_status_disconnected(client, monkeypatch):
    """GET /schwab/status returns disconnected when SchwabClient raises."""
    with patch("prime_trading.prime_schwab.SchwabClient") as MockClient:
        MockClient.return_value.connect.side_effect = Exception("no token")
        resp = client.get("/api/v1/schwab/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["connected"] is False
    assert data["mode"] in ("PAPER", "LIVE")


def test_schwab_status_connected(client, monkeypatch):
    """GET /schwab/status returns connected with account list."""
    mock_sc = MagicMock()
    mock_sc.client.get_account_numbers.return_value = MagicMock(
        status_code=200,
        json=lambda: [{"accountNumber": "1234567890", "hashValue": "abc123"}],
    )
    with patch("prime_trading.prime_schwab.SchwabClient", return_value=mock_sc):
        resp = client.get("/api/v1/schwab/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["connected"] is True
    assert any(a["suffix"] == "7890" for a in data["accounts"])


def test_schwab_connect_ok(client, monkeypatch):
    """POST /schwab/connect returns connected=True on success."""
    mock_sc = MagicMock()
    mock_sc.client.get_account_numbers.return_value = MagicMock(
        status_code=200,
        json=lambda: [{"accountNumber": "1234567890", "hashValue": "abc123"}],
    )
    with patch("prime_trading.prime_schwab.SchwabClient", return_value=mock_sc):
        resp = client.post("/api/v1/schwab/connect")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["connected"] is True


def test_schwab_connect_auth_required(client):
    """POST /schwab/connect returns auth_required when token is expired."""
    with patch("prime_trading.prime_schwab.SchwabClient") as MockClient:
        MockClient.return_value.connect.side_effect = Exception("token expired")
        resp = client.post("/api/v1/schwab/connect")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["connected"] is False
    assert data["auth_required"] is True


def test_live_mode_requires_confirmed(client, tmp_path):
    """POST /schwab/mode to LIVE without confirmed=True returns 400."""
    resp = client.post(
        "/api/v1/schwab/mode",
        data=json.dumps({"mode": "LIVE", "confirmed": False}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "confirmed" in resp.get_json()["error"]


def test_live_mode_with_confirmed_persists(client, tmp_path, monkeypatch):
    """POST /schwab/mode LIVE with confirmed writes to config.json."""
    import prime_api.prime_api_routes as routes

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "polygon_api_key": "x", "finnhub_api_key": "x",
        "tradestation": {}, "schwab_snapshot": {}, "execution": {}, "risk_management": {},
        "api_token": "tok", "trading_mode": "PAPER",
    }))
    monkeypatch.setattr(routes, "_PROJECT_ROOT_PATH", tmp_path)

    with patch("prime_config.prime_config.reload_config"):
        resp = client.post(
            "/api/v1/schwab/mode",
            data=json.dumps({"mode": "LIVE", "confirmed": True}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    saved = json.loads(cfg_path.read_text())
    assert saved["trading_mode"] == "LIVE"


def test_paper_mode_switch(client, tmp_path, monkeypatch):
    """POST /schwab/mode PAPER does not require confirmation."""
    import prime_api.prime_api_routes as routes

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "polygon_api_key": "x", "finnhub_api_key": "x",
        "tradestation": {}, "schwab_snapshot": {}, "execution": {}, "risk_management": {},
        "api_token": "tok", "trading_mode": "LIVE",
    }))
    monkeypatch.setattr(routes, "_PROJECT_ROOT_PATH", tmp_path)

    with patch("prime_config.prime_config.reload_config"):
        resp = client.post(
            "/api/v1/schwab/mode",
            data=json.dumps({"mode": "PAPER", "confirmed": True}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    saved = json.loads(cfg_path.read_text())
    assert saved["trading_mode"] == "PAPER"


def test_schwab_balances_no_connection(client):
    """GET /schwab/balances degrades gracefully when Schwab is not connected."""
    with patch("prime_trading.prime_schwab.SchwabClient") as MockClient:
        MockClient.return_value.connect.side_effect = Exception("no token")
        resp = client.get("/api/v1/schwab/balances")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "balances" in data
    assert "error" in data
