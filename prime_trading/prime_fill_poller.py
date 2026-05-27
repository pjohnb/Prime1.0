"""
PRIME v1.0 Fill Confirmation Poller (CIL-086).

Polls Schwab order status until FILLED or timeout, then updates
prime_trade_log with actual fill price and shares.
"""

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import get_connection, init_db, log_ops_event

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5
POLL_TIMEOUT = 120

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prime_fill")


def poll_fill(
    order_id: str,
    client,
    timeout_sec: int = POLL_TIMEOUT,
    poll_interval: int = POLL_INTERVAL,
) -> Optional[Dict[str, Any]]:
    """Poll Schwab order status until FILLED or timeout.

    Returns dict with fill_price and shares_filled, or None on timeout.
    """
    elapsed = 0

    while elapsed < timeout_sec:
        try:
            status = client.get_order_status(order_id)
        except Exception as e:
            logger.warning("Order status poll error for %s: %s", order_id, e)
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue

        if status is None:
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue

        order_status = status.get("status", "").upper()

        if order_status == "FILLED":
            fill_price = status.get("filledPrice", status.get("price", 0.0))
            shares_filled = status.get("filledQuantity", status.get("quantity", 0))
            logger.info("Order %s FILLED: price=%.2f shares=%d",
                        order_id, fill_price, shares_filled)
            return {
                "fill_price": float(fill_price),
                "shares_filled": int(shares_filled),
                "fill_time": datetime.utcnow().isoformat(),
            }

        if order_status in ("CANCELED", "REJECTED", "EXPIRED"):
            logger.warning("Order %s terminal status: %s", order_id, order_status)
            return None

        time.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning("Order %s poll timeout after %ds", order_id, timeout_sec)
    return None


def update_trade_on_fill(
    trade_id: str,
    fill_price: float,
    shares_filled: int,
    db_path: Optional[Path] = None,
) -> None:
    """Update prime_trade_log entry_price and shares with actual fill data."""
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE prime_trade_log SET
                entry_price=?, shares=?
            WHERE log_id=?""",
            (fill_price, shares_filled, trade_id),
        )
        conn.commit()

    log_ops_event(
        event_type="FILL_CONFIRMED",
        component="prime_fill_poller",
        detail=f"trade={trade_id} fill_price={fill_price} shares={shares_filled}",
        severity="INFO",
        db_path=db_path,
    )
    logger.info("Trade %s updated with fill: price=%.2f shares=%d",
                trade_id, fill_price, shares_filled)


def start_fill_watcher(
    order_id: str,
    trade_id: str,
    client,
    db_path: Optional[Path] = None,
) -> None:
    """Submit fill polling as a background task. Non-blocking."""

    def _watch():
        try:
            result = poll_fill(order_id, client)
            if result:
                update_trade_on_fill(
                    trade_id,
                    result["fill_price"],
                    result["shares_filled"],
                    db_path=db_path,
                )
            else:
                log_ops_event(
                    event_type="FILL_TIMEOUT",
                    component="prime_fill_poller",
                    detail=f"order={order_id} trade={trade_id} -- no fill confirmation",
                    severity="WARN",
                    db_path=db_path,
                )
        except Exception as e:
            logger.error("Fill watcher error for order %s: %s", order_id, e)
            try:
                log_ops_event(
                    event_type="FILL_WATCHER_ERROR",
                    component="prime_fill_poller",
                    detail=f"order={order_id} error={e}",
                    severity="ERROR",
                    db_path=db_path,
                )
            except Exception:
                pass

    _executor.submit(_watch)
    logger.info("Fill watcher started for order %s (trade %s)", order_id, trade_id)
