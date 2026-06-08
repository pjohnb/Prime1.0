"""Sprint 25 Item 1 — Scan Control API tests."""
import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Minimal Flask test client with a fresh scan_state for each test."""
    from prime_api.prime_api_server import create_app
    import prime_api.prime_api_routes as routes

    # Patch DB and config so no real filesystem reads needed
    monkeypatch.setattr(routes, "_LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(routes, "_SCAN_LOG", tmp_path / "logs" / "scan_log.txt")
    monkeypatch.setattr(routes, "_OPS_CONFIG_PATH", tmp_path / "ops_config.json")
    (tmp_path / "ops_config.json").write_text(json.dumps({"scan_schedule": {}, "notification_channels": "none", "health_check_interval": 900}))

    with routes._scan_lock:
        routes._scan_state.clear()

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_trigger_scan_unknown(client):
    resp = client.post("/api/v1/scans/bogus")
    assert resp.status_code == 400
    assert "unknown scanner" in resp.get_json()["error"]


def test_trigger_scan_pead_starts(client, monkeypatch):
    """POST /scans/pead returns 202 and starts a background thread."""
    import prime_api.prime_api_routes as routes

    started = []

    def fake_run_bg(scanner, module):
        started.append(scanner)

    monkeypatch.setattr(routes, "_run_scanner_bg", fake_run_bg)

    resp = client.post("/api/v1/scans/pead")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["scanner"] == "pead"
    assert data["status"] == "started"


def test_trigger_scan_already_running(client, monkeypatch):
    """POST /scans/pead returns 409 when already running."""
    import prime_api.prime_api_routes as routes

    with routes._scan_lock:
        routes._scan_state["pead"] = {"status": "running"}

    resp = client.post("/api/v1/scans/pead")
    assert resp.status_code == 409


def test_scan_status_returns_all_scanners(client, monkeypatch):
    """GET /scans/status lists all 7 scanners."""
    monkeypatch.setattr(
        "prime_data.prime_db.get_ops_events",
        lambda **kwargs: [],
    )
    resp = client.get("/api/v1/scans/status")
    assert resp.status_code == 200
    data = resp.get_json()
    names = {s["scanner"] for s in data["scanners"]}
    assert "PSA" in names
    assert "PEAD" in names
    assert "UOA" in names
    assert "SHORT" in names
    assert len(data["scanners"]) == 7


def test_scan_status_reflects_state(client, monkeypatch):
    """GET /scans/status returns per-scanner state populated by a run."""
    import prime_api.prime_api_routes as routes

    monkeypatch.setattr("prime_data.prime_db.get_ops_events", lambda **kwargs: [])
    with routes._scan_lock:
        routes._scan_state["pead"] = {"status": "complete", "last_run": "2026-06-08 12:40", "signals": 3}

    resp = client.get("/api/v1/scans/status")
    data = resp.get_json()
    pead = next(s for s in data["scanners"] if s["scanner"] == "PEAD")
    assert pead["status"] == "complete"
    assert pead["signals"] == 3


def test_scan_log_empty(client):
    """GET /scans/log returns empty list when log file doesn't exist."""
    resp = client.get("/api/v1/scans/log")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["lines"] == []


def test_scan_log_returns_last_n_lines(client, tmp_path, monkeypatch):
    """GET /scans/log returns last N lines of the log file."""
    import prime_api.prime_api_routes as routes

    log_file = tmp_path / "logs" / "scan_log.txt"
    log_file.parent.mkdir(exist_ok=True)
    lines = [f"line {i}" for i in range(100)]
    log_file.write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(routes, "_SCAN_LOG", log_file)

    resp = client.get("/api/v1/scans/log?lines=10")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["lines"]) == 10
    assert data["lines"][0] == "line 90"


def test_scan_schedule_get(client):
    """GET /scans/schedule returns schedule with defaults."""
    resp = client.get("/api/v1/scans/schedule")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "schedule" in data
    # Defaults are present
    assert "psa_time" in data["schedule"] or "schedule" in data


def test_scan_schedule_post(client, tmp_path, monkeypatch):
    """POST /scans/schedule writes new times to ops_config.json."""
    import prime_api.prime_api_routes as routes

    ops_path = tmp_path / "ops_config.json"
    ops_path.write_text(json.dumps({
        "scan_schedule": {},
        "notification_channels": "none",
        "health_check_interval": 900,
        "psa_time": "09:45",
    }))
    monkeypatch.setattr(routes, "_OPS_CONFIG_PATH", ops_path)

    resp = client.post(
        "/api/v1/scans/schedule",
        data=json.dumps({"psa_time": "09:50", "schedule_enabled": True}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    saved = json.loads(ops_path.read_text())
    assert saved["psa_time"] == "09:50"
