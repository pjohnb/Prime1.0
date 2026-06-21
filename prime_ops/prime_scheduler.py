"""
PRIME v1.0 Scheduler -- Windows Task Scheduler registration for all scanners.

Reads scan_schedule from ops_config.json and registers/updates Windows
Task Scheduler jobs via schtasks.exe. Each scanner gets one scheduled
task per run time.

Usage:
  python prime_ops/prime_scheduler.py --register   Create/update all scheduled tasks
  python prime_ops/prime_scheduler.py --status     Show current schedule status
  python prime_ops/prime_scheduler.py --remove     Remove all PRIME scheduled tasks
"""

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config

logger = logging.getLogger(__name__)

TASK_PREFIX = "PRIME_"
PYTHON_EXE = sys.executable


# ---------------------------------------------------------------------------
# Task definition builder
# ---------------------------------------------------------------------------

def build_task_definitions() -> List[Dict[str, Any]]:
    cfg = get_config()
    schedule = cfg.ops.scan_schedule

    if not isinstance(schedule, dict):
        logger.error("scan_schedule is not configured (still TBD)")
        return []

    tasks = []
    for scanner_name, sched in schedule.items():
        if not isinstance(sched, dict):
            continue

        times = sched.get("times_et", [])
        module = sched.get("scanner_module", "")
        days = sched.get("days", "weekdays")

        if not times or not module:
            logger.warning("Scanner %s: missing times_et or scanner_module", scanner_name)
            continue

        module_path = PROJECT_ROOT / module.replace(".", "/")
        script = str(module_path) + ".py"

        if not Path(script).exists():
            logger.warning("Scanner script not found: %s", script)
            continue

        for run_time in times:
            task_name = f"{TASK_PREFIX}{scanner_name}_{run_time.replace(':', '')}"
            tasks.append({
                "task_name": task_name,
                "scanner": scanner_name,
                "script": script,
                "time_et": run_time,
                "days": days,
                "module": module,
            })

    return tasks


# ---------------------------------------------------------------------------
# schtasks.exe wrapper
# ---------------------------------------------------------------------------

def _run_schtasks(args: List[str]) -> tuple:
    cmd = ["schtasks.exe"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "schtasks.exe not found"
    except subprocess.TimeoutExpired:
        return -1, "", "schtasks timed out"


def register_task(task: Dict[str, Any]) -> bool:
    task_name = task["task_name"]
    script = task["script"]
    run_time = task["time_et"]
    days = task["days"]

    if days == "weekdays":
        day_arg = "MON,TUE,WED,THU,FRI"
    else:
        day_arg = "*"

    command = f'"{PYTHON_EXE}" "{script}"'

    rc, out, err = _run_schtasks([
        "/Create",
        "/TN", task_name,
        "/TR", command,
        "/SC", "WEEKLY",
        "/D", day_arg,
        "/ST", run_time,
        "/F",
    ])

    if rc == 0:
        logger.info("Registered: %s at %s ET (%s)", task_name, run_time, days)
        return True
    else:
        logger.error("Failed to register %s: %s", task_name, err.strip())
        return False


def remove_task(task_name: str) -> bool:
    rc, _, err = _run_schtasks(["/Delete", "/TN", task_name, "/F"])
    if rc == 0:
        logger.info("Removed: %s", task_name)
        return True
    else:
        logger.warning("Could not remove %s: %s", task_name, err.strip())
        return False


def query_tasks() -> List[Dict[str, str]]:
    rc, out, _ = _run_schtasks([
        "/Query", "/FO", "CSV", "/NH",
    ])
    if rc != 0:
        return []

    prime_tasks = []
    for line in out.strip().split("\n"):
        if TASK_PREFIX not in line:
            continue
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 3:
            prime_tasks.append({
                "name": parts[0].strip('"'),
                "next_run": parts[1].strip('"') if len(parts) > 1 else "",
                "status": parts[2].strip('"') if len(parts) > 2 else "",
            })
    return prime_tasks


# ---------------------------------------------------------------------------
# Post-scan notification hook
# ---------------------------------------------------------------------------

def post_scan_notify(scanner_name: str, scan_data: Dict[str, Any]) -> None:
    """Called by scanners after a run to trigger digest + per-signal alerts."""
    try:
        from prime_data.prime_db import get_open_positions
        from prime_notifications.prime_digest import assemble_digest
        from prime_notifications.prime_notifier import send_digest
        from prime_notifications.prime_push_signal import push_signal_alerts

        signals = scan_data.get("signals", [])
        open_positions = get_open_positions()

        digest, text = assemble_digest(scanner_name, signals, open_positions)
        send_digest(digest, text)

        approved = [s for s in signals if s.get("score", 0) > 0]
        if approved:
            # Sprint 16 Item 2: toggle-aware selection. With use_ai_ranker=True
            # and an overflow (approvals > Max Trades) this routes through the
            # AI signal ranker; otherwise deterministic score-sort. The chosen
            # path is logged to prime_ops_health for every run.
            from prime_ai.prime_signal_ranker import select_for_execution
            approved = select_for_execution(
                approved, open_positions=open_positions, scanner=scanner_name)
            push_signal_alerts(approved)

            # Sprint 31 / CIL-042: capture one ML training row per APPROVED
            # signal. Best-effort -- a capture failure must never block or
            # delay the scan pipeline, so each call is individually guarded.
            from prime_ml.prime_ml_capture_v2 import capture_ml_event
            for sig in approved:
                try:
                    sig.setdefault("scanner", scanner_name)
                    capture_ml_event(sig, db_path=None)
                except Exception as e:
                    logger.error("ML capture failed for %s: %s",
                                 sig.get("symbol", "?"), e)

        logger.info("Post-scan notifications sent for %s (%d signals)", scanner_name, len(signals))
    except Exception as e:
        logger.error("Post-scan notification failed for %s: %s", scanner_name, e)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_register():
    tasks = build_task_definitions()
    if not tasks:
        print("No tasks to register. Check ops_config.json scan_schedule.")
        return

    print(f"\nRegistering {len(tasks)} scheduled tasks...\n")
    success = 0
    for task in tasks:
        ok = register_task(task)
        if ok:
            success += 1
            print(f"  [OK] {task['task_name']}: {task['scanner']} at {task['time_et']} ET")
        else:
            print(f"  [FAIL] {task['task_name']}")

    print(f"\n{success}/{len(tasks)} tasks registered successfully.")


def cmd_status():
    tasks = build_task_definitions()
    registered = query_tasks()
    registered_names = {t["name"] for t in registered}

    print(f"\nPRIME Scanner Schedule ({len(tasks)} defined):\n")
    print(f"  {'Scanner':<10} {'Time ET':<10} {'Task Name':<30} {'Registered':<12}")
    print(f"  {'-'*10} {'-'*10} {'-'*30} {'-'*12}")

    for task in tasks:
        is_reg = "YES" if task["task_name"] in registered_names else "NO"
        print(f"  {task['scanner']:<10} {task['time_et']:<10} {task['task_name']:<30} {is_reg:<12}")

    if registered:
        print(f"\n  Active PRIME tasks in Windows Task Scheduler:")
        for t in registered:
            print(f"    {t['name']:<35} next={t['next_run']:<20} status={t['status']}")

    unregistered = [t for t in tasks if t["task_name"] not in registered_names]
    if unregistered:
        print(f"\n  {len(unregistered)} task(s) NOT registered. Run --register to create them.")


def cmd_remove():
    registered = query_tasks()
    if not registered:
        print("No PRIME tasks found in Task Scheduler.")
        return

    print(f"\nRemoving {len(registered)} PRIME tasks...\n")
    for t in registered:
        ok = remove_task(t["name"])
        print(f"  {'[OK]' if ok else '[FAIL]'} {t['name']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="PRIME v1.0 Scheduler")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", action="store_true", help="Create/update all scheduled tasks")
    group.add_argument("--status", action="store_true", help="Show schedule status")
    group.add_argument("--remove", action="store_true", help="Remove all PRIME tasks")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [SCHEDULER] %(levelname)s %(message)s",
    )

    if args.register:
        cmd_register()
    elif args.status:
        cmd_status()
    elif args.remove:
        cmd_remove()


if __name__ == "__main__":
    main()
