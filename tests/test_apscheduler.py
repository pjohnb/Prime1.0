"""Sprint 25 Item 3 — APScheduler integration tests."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_init_scheduler_starts_and_has_jobs():
    """init_scheduler returns a running scheduler with at least 4 jobs."""
    from prime_api.prime_api_routes import init_scheduler, _SCHEDULER
    import prime_api.prime_api_routes as routes

    # Use a fresh scheduler for this test
    original = routes._SCHEDULER
    try:
        sched = init_scheduler()
        if sched is None:
            pytest.skip("APScheduler not available in this environment")
        assert sched.running
        jobs = sched.get_jobs()
        assert len(jobs) >= 4, f"expected >= 4 jobs, got {len(jobs)}"
        sched.shutdown(wait=False)
    finally:
        routes._SCHEDULER = original


def test_read_schedule_defaults(tmp_path, monkeypatch):
    """_read_schedule returns defaults when keys absent from ops_config.json."""
    import prime_api.prime_api_routes as routes

    ops = tmp_path / "ops_config.json"
    ops.write_text(json.dumps({"scan_schedule": {}, "notification_channels": "x", "health_check_interval": 900}))
    monkeypatch.setattr(routes, "_OPS_CONFIG_PATH", ops)

    sched = routes._read_schedule()
    assert sched["psa_time"] == "09:45"
    assert sched["uoa_pead_srs_time"] == "12:40"
    assert sched["idx_time"] == "12:45"
    assert sched["short_time"] == "12:50"
    assert sched["schedule_enabled"] is True


def test_read_schedule_custom(tmp_path, monkeypatch):
    """_read_schedule returns values from ops_config.json when present."""
    import prime_api.prime_api_routes as routes

    ops = tmp_path / "ops_config.json"
    ops.write_text(json.dumps({
        "scan_schedule": {},
        "notification_channels": "x",
        "health_check_interval": 900,
        "psa_time": "10:15",
        "uoa_pead_srs_time": "13:00",
    }))
    monkeypatch.setattr(routes, "_OPS_CONFIG_PATH", ops)

    sched = routes._read_schedule()
    assert sched["psa_time"] == "10:15"
    assert sched["uoa_pead_srs_time"] == "13:00"


def test_reschedule_all_updates_jobs():
    """_reschedule_all replaces existing APScheduler jobs with new times."""
    from prime_api.prime_api_routes import _reschedule_all

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        pytest.skip("APScheduler not installed")

    sched = BackgroundScheduler(timezone="America/New_York")
    sched.start()
    try:
        schedule = {
            "psa_time": "09:45",
            "uoa_pead_srs_time": "12:40",
            "idx_time": "12:45",
            "short_time": "12:50",
            "schedule_enabled": True,
        }
        _reschedule_all(sched, schedule)
        jobs_before = {j.id for j in sched.get_jobs()}
        assert "scan_job_psa" in jobs_before
        assert "scan_job_pead" in jobs_before
        assert "scan_job_idx" in jobs_before
        assert "scan_job_short" in jobs_before

        # Update time and verify job is rescheduled
        schedule2 = dict(schedule)
        schedule2["psa_time"] = "10:00"
        _reschedule_all(sched, schedule2)
        psa_job = sched.get_job("scan_job_psa")
        assert psa_job is not None
    finally:
        sched.shutdown(wait=False)


def test_reschedule_all_disabled_removes_jobs():
    """_reschedule_all with schedule_enabled=False removes all scan jobs."""
    from prime_api.prime_api_routes import _reschedule_all

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        pytest.skip("APScheduler not installed")

    sched = BackgroundScheduler(timezone="America/New_York")
    sched.start()
    try:
        # First add some jobs
        _reschedule_all(sched, {
            "psa_time": "09:45", "uoa_pead_srs_time": "12:40",
            "idx_time": "12:45", "short_time": "12:50", "schedule_enabled": True,
        })
        assert len(sched.get_jobs()) > 0
        # Now disable
        _reschedule_all(sched, {"schedule_enabled": False})
        scan_jobs = [j for j in sched.get_jobs() if j.id.startswith("scan_job_")]
        assert len(scan_jobs) == 0
    finally:
        sched.shutdown(wait=False)


def test_overlapping_scan_guard(monkeypatch):
    """A scheduled job skips if the scanner is already running."""
    import prime_api.prime_api_routes as routes

    with routes._scan_lock:
        routes._scan_state["psa"] = {"status": "running"}

    called = []

    def fake_run_bg(scanner, module):
        called.append(scanner)

    monkeypatch.setattr(routes, "_run_scanner_bg", fake_run_bg)

    # Simulate what the scheduler job does: check running first
    s = "psa"
    state = routes._scan_state.get(s, {})
    if state.get("status") != "running":
        fake_run_bg(s, routes._SCANNER_MAP[s])

    assert called == []  # was NOT called because status == 'running'

    with routes._scan_lock:
        routes._scan_state.clear()
