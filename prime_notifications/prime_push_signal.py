"""
PRIME v1.0 Per-Signal Push Notifications (Ops Sprint 2, Phase 2).

Each approved signal gets an individual alert with full factor evaluation
and Claude advisory. Integrates with prime_trade_factors and prime_claude_advisor.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="prime_push")


def build_signal_alert(
    signal: Dict[str, Any],
    factors: Dict[str, Any],
    advisory: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble per-signal alert with full factor evaluation and Claude advisory."""
    symbol = signal.get("symbol", "???")
    strategy = signal.get("strategy", "???")
    score = signal.get("score", 0.0)

    nullifier = factors.get("nullifier", {})
    duration = factors.get("duration", {})
    entry = factors.get("entry", {})
    exit_triggers = factors.get("exit_triggers", [])
    maintenance = factors.get("maintenance_flags", [])

    stop_advisory = ""
    for trigger in exit_triggers:
        if trigger.get("type") == "STOP_LOSS":
            stop_advisory = trigger.get("description", "")
            break

    advisory_text = advisory.get("risk_narrative", "Advisory unavailable")
    recommendation = advisory.get("recommendation", "MONITOR")

    alert = {
        "symbol": symbol,
        "strategy": strategy,
        "composite_score": score,
        "price_at_scan": signal.get("price_at_scan", 0.0),
        "direction": signal.get("direction", "LONG"),
        "duration": duration,
        "entry": entry,
        "nullifier": nullifier,
        "exit_triggers": exit_triggers,
        "maintenance_flags": maintenance,
        "advisory": {
            "recommendation": recommendation,
            "conviction": advisory.get("conviction", "LOW"),
            "narrative": advisory_text,
            "confidence_note": advisory.get("confidence_note", ""),
        },
        "stop_advisory": stop_advisory,
        "timestamp": datetime.utcnow().isoformat(),
    }

    return alert


def _format_signal_alert_text(alert: Dict[str, Any]) -> str:
    """Format a signal alert as plaintext for delivery."""
    lines = [
        f"PRIME Signal Alert -- {alert['symbol']} ({alert['strategy']})",
        f"Score: {alert['composite_score']:.1f}  |  Direction: {alert['direction']}",
        f"Entry Price: ${alert.get('price_at_scan', 0):.2f}",
        "",
        f"Duration: {alert.get('duration', {}).get('class', '--')} "
        f"({alert.get('duration', {}).get('confidence', '--')})",
        f"Entry: {alert.get('entry', {}).get('method', '--')}",
        f"Nullifier: {alert.get('nullifier', {}).get('status', 'CLEAR')}",
        "",
        "Exit Triggers:",
    ]

    for trigger in alert.get("exit_triggers", []):
        lines.append(f"  [{trigger.get('status', '')}] {trigger.get('type', '')}: "
                      f"{trigger.get('description', '')}")

    if alert.get("stop_advisory"):
        lines.append(f"\nStop Advisory: {alert['stop_advisory']}")

    adv = alert.get("advisory", {})
    lines.append(f"\nClaude Advisory: {adv.get('recommendation', '--')} "
                 f"[{adv.get('conviction', '--')}]")
    lines.append(adv.get("narrative", ""))

    if adv.get("confidence_note"):
        lines.append(f"\nWhat would change: {adv['confidence_note']}")

    lines.append(f"\nTimestamp: {alert.get('timestamp', '')}")
    return "\n".join(lines)


def _process_single_signal(signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process a single signal: evaluate factors, get advisory, build alert."""
    symbol = signal.get("symbol", "???")
    strategy = signal.get("strategy", "???")

    try:
        from prime_intelligence.prime_trade_factors import _evaluate
        factor_eval = _evaluate(strategy, symbol, signal)
        factors = factor_eval.to_dict()
    except Exception as e:
        logger.warning("Factor eval failed for %s: %s", symbol, e)
        factors = {}

    try:
        from prime_intelligence.prime_claude_advisor import generate_advisory
        advisory = generate_advisory(factors)
    except Exception as e:
        logger.warning("Claude advisory failed for %s: %s", symbol, e)
        advisory = {
            "recommendation": "MONITOR",
            "conviction": "LOW",
            "risk_narrative": "Advisory unavailable",
            "confidence_note": f"Advisory generation failed: {e}",
            "_fallback": True,
        }

    alert = build_signal_alert(signal, factors, advisory)
    return alert


def push_signal_alerts(approved_signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Process approved signals and send individual alerts.

    Uses thread pool to avoid blocking the scanner thread.
    Returns list of alerts that were successfully sent.
    """
    from prime_notifications.prime_notifier import send_signal_alert

    if not approved_signals:
        return []

    sent_alerts = []
    futures = {
        _executor.submit(_process_single_signal, sig): sig
        for sig in approved_signals
    }

    for future in as_completed(futures):
        sig = futures[future]
        symbol = sig.get("symbol", "???")
        try:
            alert = future.result(timeout=60)
            if alert:
                text = _format_signal_alert_text(alert)
                success = send_signal_alert(alert, text)
                if success:
                    sent_alerts.append(alert)
                    logger.info("Signal alert delivered for %s", symbol)
                else:
                    logger.warning("Signal alert delivery failed for %s", symbol)
        except Exception as e:
            logger.error("Signal alert processing failed for %s: %s", symbol, e)

    return sent_alerts
