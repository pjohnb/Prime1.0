"""
PRIME v1.0 Position Monitor Engine (Sprint 32 Thread 1, PM-HEALTH-02).

A background daemon thread that, during regular trading hours, polls every OPEN
position and re-evaluates its original thesis against current conditions:

  * the latest dark-pool reading (dk_status) for the symbol, and
  * the most recent APPROVED signal from the position's originating scanner.

Each position is scored GREEN / AMBER / RED and upserted into prime_position_health.
A RED status logs a reversal ALERT to prime_ops_health exactly once per RED
detection, and — when position_monitor_action='AUTO_SELL' — fires a MATA sell.

The two cross-thread data helpers (get_open_positions_with_signal_context,
get_latest_signal_for_symbol) are defined by Sprint 32 Thread 3 in prime_db.py.
"""

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SIGNAL_FRESHNESS_HOURS = 24
_JOIN_TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# Pure thesis logic (no I/O — unit-testable in isolation)
# ---------------------------------------------------------------------------

def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """Best-effort parse of a scan_ts string into a naive datetime."""
    if not ts:
        return None
    raw = str(ts).strip().replace("Z", "")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
    return None


def _signal_fresh(sig_ts: Optional[str], now: datetime) -> bool:
    parsed = _parse_ts(sig_ts)
    if parsed is None:
        return False
    return timedelta(0) <= (now - parsed) <= timedelta(hours=SIGNAL_FRESHNESS_HOURS)


def compute_thesis_status(
    position_direction: Optional[str],
    signal_id: Any,
    dk_status: Optional[str],
    latest_signal: Optional[Dict[str, Any]],
    now: datetime,
) -> Tuple[str, str, Optional[str]]:
    """Classify a position's thesis as GREEN / AMBER / RED.

    Returns (thesis_status, reason, alert_type) where alert_type is one of
    'DK_REVERSAL', 'SIGNAL_REVERSAL' (RED only) or None.
    """
    pos_dir = (position_direction or "LONG").upper()
    dk = (dk_status or "NEUTRAL").upper()
    sig_dir = (latest_signal.get("direction") or "").upper() if latest_signal else None
    sig_ts = latest_signal.get("scan_ts") if latest_signal else None
    fresh = _signal_fresh(sig_ts, now)

    # 1. RED — dark-pool reversal (institutional flow now opposes the position).
    if pos_dir == "LONG" and dk == "NULLIFYING":
        return "RED", "DK NULLIFYING opposes LONG position", "DK_REVERSAL"
    if pos_dir == "SHORT" and dk == "CONFIRMING":
        return "RED", "DK CONFIRMING opposes SHORT position", "DK_REVERSAL"

    # 2. RED — originating scanner now signals the opposite direction.
    contrary = (pos_dir == "LONG" and sig_dir == "SHORT") or (
        pos_dir == "SHORT" and sig_dir == "LONG"
    )
    if latest_signal and contrary:
        return (
            "RED",
            f"latest originating signal {sig_dir} contrary to {pos_dir} position",
            "SIGNAL_REVERSAL",
        )

    # 3. AMBER — imported position with no originating signal to confirm against.
    if not signal_id:
        return "AMBER", "SCHWAB_IMPORT position has no originating signal", None

    # 4. AMBER — thesis unconfirmed: no originating-scanner signal within 24h.
    if not (latest_signal and fresh):
        return "AMBER", "no originating-scanner signal within 24h", None

    # 5. GREEN — DK supportive/neutral, signal matches and is fresh.
    dk_ok = (pos_dir == "LONG" and dk in ("CONFIRMING", "NEUTRAL")) or (
        pos_dir == "SHORT" and dk in ("NULLIFYING", "NEUTRAL")
    )
    if dk_ok and sig_dir == pos_dir:
        return "GREEN", "thesis confirmed by DK and fresh matching signal", None

    return "AMBER", "thesis unconfirmed", None


def load_position_health(db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """All prime_position_health rows keyed by str(log_id).

    Returns an empty dict when the table is absent or unreadable, so the
    /positions/health endpoint can fall back to UNKNOWN. Lives here (not in the
    routes module) to keep direct SQL out of the API layer.
    """
    from prime_data.prime_db import get_connection
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with get_connection(db_path) as conn:
            rows = conn.execute("SELECT * FROM prime_position_health").fetchall()
            for row in rows:
                out[str(row["log_id"])] = dict(row)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# PositionMonitor
# ---------------------------------------------------------------------------

class PositionMonitor:
    """Background poller that evaluates open-position theses during RTH."""

    def __init__(self, db_path: Optional[Path] = None, config: Any = None):
        self._db_path = db_path
        self._config = config           # OpsConfig-like; lazily resolved if None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- config accessors ----------------------------------------------------

    def _ops(self):
        if self._config is not None:
            return self._config
        from prime_config.prime_config import get_config
        return get_config().ops

    def _interval_seconds(self) -> int:
        return int(getattr(self._ops(), "position_monitor_interval_seconds", 300) or 300)

    def _action(self) -> str:
        return str(getattr(self._ops(), "position_monitor_action", "ALERT") or "ALERT").upper()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Launch the poll loop in a daemon thread. No-op if already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="PositionMonitor", daemon=True
        )
        self._thread.start()
        logger.info("PositionMonitor started (interval=%ds)", self._interval_seconds())

    def stop(self) -> None:
        """Signal the loop to exit and join the thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=_JOIN_TIMEOUT_S)
            self._thread = None

    # -- loop ----------------------------------------------------------------

    def _is_rth(self) -> bool:
        from prime_trading.prime_schwab_orders import _is_rth
        return _is_rth()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._is_rth():
                    self._poll()
            except Exception as e:                       # never let the thread die
                logger.warning("PositionMonitor poll cycle failed: %s", e)
            self._stop.wait(self._interval_seconds())

    def _poll(self, now: Optional[datetime] = None) -> int:
        """Evaluate every open position once. Returns the number evaluated."""
        from prime_data.prime_db import get_open_positions_with_signal_context
        now = now or datetime.now()
        positions = get_open_positions_with_signal_context(self._db_path)
        for pos in positions:
            try:
                self._evaluate_position(pos, now)
            except Exception as e:
                logger.warning(
                    "PositionMonitor: evaluation failed for %s: %s",
                    pos.get("symbol"), e,
                )
        return len(positions)

    # -- per-position evaluation --------------------------------------------

    def _latest_dk_status(self, symbol: str) -> str:
        """Most recent dk_status for the symbol across all scanners."""
        from prime_analytics.prime_signals_db import get_signals
        rows = get_signals(symbol=symbol, limit=1, db_path=self._db_path)
        if rows:
            return (rows[0].get("dk_status") or "NEUTRAL")
        return "NEUTRAL"

    def _latest_originating_signal(self, symbol: str, scanner: Optional[str]):
        if not scanner:
            return None
        from prime_data.prime_db import get_latest_signal_for_symbol
        return get_latest_signal_for_symbol(symbol, scanner, self._db_path)

    def _evaluate_position(self, pos: Dict[str, Any], now: datetime) -> str:
        symbol = pos.get("symbol")
        scanner = pos.get("scanner")
        pos_dir = pos.get("direction")
        signal_id = pos.get("signal_id")

        dk_status = self._latest_dk_status(symbol)
        latest_sig = self._latest_originating_signal(symbol, scanner)

        thesis, reason, alert_type = compute_thesis_status(
            pos_dir, signal_id, dk_status, latest_sig, now
        )
        sig_dir = (latest_sig.get("direction") if latest_sig else None)
        sig_ts = (latest_sig.get("scan_ts") if latest_sig else None)

        fired = self._upsert_health(
            pos, thesis, dk_status, sig_dir, sig_ts, now
        )
        if fired and thesis == "RED":
            self._fire_alert(pos, alert_type, reason)
        return thesis

    def _upsert_health(
        self, pos: Dict[str, Any], thesis: str, dk_status: str,
        sig_dir: Optional[str], sig_ts: Optional[str], now: datetime,
    ) -> bool:
        """Upsert the health row. Returns True when a *new* RED alert is due
        (RED now and not already alerted on a prior consecutive RED)."""
        from prime_data.prime_db import get_connection
        log_id = str(pos.get("log_id"))          # prime_trade_log.log_id is TEXT (UUID)
        symbol = pos.get("symbol")
        evaluated_at = now.isoformat()

        with get_connection(self._db_path) as conn:
            prev = conn.execute(
                "SELECT thesis_status, last_alerted_at FROM prime_position_health"
                " WHERE log_id=?",
                (log_id,),
            ).fetchone()
            prev_status = prev["thesis_status"] if prev else None
            prev_alerted = prev["last_alerted_at"] if prev else None

            alert_due = False
            last_alerted_at = prev_alerted
            if thesis == "RED":
                # Alert once per RED detection: skip only if the previous cycle
                # was already RED *and* had alerted.
                if not (prev_status == "RED" and prev_alerted):
                    alert_due = True
                    last_alerted_at = evaluated_at
            else:
                last_alerted_at = None        # reset so a future RED re-alerts

            conn.execute(
                """INSERT OR REPLACE INTO prime_position_health
                    (log_id, symbol, thesis_status, dk_status,
                     latest_signal_direction, latest_signal_ts,
                     evaluated_at, last_alerted_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (log_id, symbol, thesis, dk_status, sig_dir, sig_ts,
                 evaluated_at, last_alerted_at),
            )
            conn.commit()
        return alert_due

    # -- alerting / auto-sell ------------------------------------------------

    def _fire_alert(self, pos: Dict[str, Any], alert_type: Optional[str], reason: str) -> None:
        from prime_data.prime_db import log_ops_event
        symbol = pos.get("symbol")
        event_type = (
            "SIGNAL_REVERSAL_ALERT" if alert_type == "SIGNAL_REVERSAL"
            else "DK_REVERSAL_ALERT"
        )
        log_ops_event(
            event_type, "position_monitor", symbol=symbol,
            detail=reason, severity="WARN", db_path=self._db_path,
        )
        logger.warning("PositionMonitor RED %s for %s: %s", event_type, symbol, reason)
        if self._action() == "AUTO_SELL":
            self._fire_auto_sell(pos, alert_type)

    def _fire_auto_sell(self, pos: Dict[str, Any], alert_type: Optional[str]) -> None:
        from prime_data.prime_db import log_ops_event
        symbol = pos.get("symbol")
        try:
            self._post_mata_sell(symbol)
            sell_event = (
                "SIGNAL_REVERSAL_AUTO_SELL" if alert_type == "SIGNAL_REVERSAL"
                else "DK_REVERSAL_AUTO_SELL"
            )
            log_ops_event(
                sell_event, "position_monitor", symbol=symbol,
                detail=f"AUTO_SELL fired for {symbol} ({alert_type})",
                severity="WARN", db_path=self._db_path,
            )
        except Exception as e:
            logger.warning("PositionMonitor auto-sell failed for %s: %s", symbol, e)

    def _post_mata_sell(self, symbol: str) -> None:
        """POST the position's full holdings to the local /sell/mata endpoint."""
        import requests
        from prime_config.prime_config import get_config
        from prime_data.prime_db import get_open_by_symbol
        from prime_api.prime_api_server import API_PORT

        records = get_open_by_symbol(symbol, self._db_path)
        holdings: Dict[str, int] = {}
        for r in records:
            acc = r.get("account") or ""
            holdings[acc] = holdings.get(acc, 0) + int(r.get("shares") or 0)
        account_holdings = [
            {"account": acc, "account_hash": "", "shares": sh}
            for acc, sh in holdings.items() if sh > 0
        ]
        total_qty = sum(h["shares"] for h in account_holdings)
        if total_qty <= 0:
            logger.warning("PositionMonitor: no open shares to auto-sell for %s", symbol)
            return

        cfg = get_config()
        payload = {
            "symbol": symbol,
            "total_qty": total_qty,
            "order_type": "MARKET",
            "price": 0.0,
            "account_holdings": account_holdings,
            "confirmed": True,
        }
        requests.post(
            f"http://127.0.0.1:{API_PORT}/api/v1/sell/mata",
            json=payload,
            headers={"Authorization": f"Bearer {(cfg.api_token or '').strip()}"},
            timeout=15,
        )
