"""
sprint13_position_review.py
PRIME v1.0 — Sprint 13 — Item 1: Open Position Review (MSFT + TJX)
==================================================================
Ported from v0.9 (C:\\Dev\\PRIME\\sprint13_position_review.py).

Operational runner that scans OPEN positions for MSFT and TJX, applies the
current strategy stop/exit rules, and reports a close/hold decision for each.

WHAT THIS DOES (read-only by default):
  1. Queries prime_trade_log for OPEN positions matching MSFT or TJX
  2. For each open position, evaluates current exit/stop criteria
  3. Prints a decision row: SYMBOL | mata_batch_id | entry_price | current | decision
  4. With --execute, calls prime_data.prime_db.close_trade() to close any
     position whose exit criteria are met. Also writes a SPRINT13_CLOSE
     ops event for audit.

Quotes are fetched via prime_trading.prime_schwab.SchwabClient. If no Schwab
token cache exists, quote fetch is skipped and every row falls through to
"no current quote -- hold" (so dry-run never triggers a browser OAuth flow).

USAGE:
    python sprint13_position_review.py                # dry-run report only
    python sprint13_position_review.py --execute      # actually close where criteria met

ITEM REF: Sprint 13 -- Item 1
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config  # noqa: E402
from prime_data.prime_db import (  # noqa: E402
    close_trade,
    get_open_trades,
    log_ops_event,
)

SYMBOLS = ["MSFT", "TJX"]
SPRINT_TAG = "Sprint13 Item 1 Position Review"


def _fetch_quotes(symbols):
    """Return {SYMBOL: last_price_float}. Empty dict on any failure or
    when no Schwab token cache is present (avoids triggering browser OAuth)."""
    try:
        cfg = get_config()
        token_path = cfg.schwab_snapshot.schwab_token_path
        if not token_path or not Path(token_path).exists():
            return {}
        from prime_trading.prime_schwab import SchwabClient
        c = SchwabClient()
        c.connect()
        qd = c.get_quotes(symbols) or {}
        out = {}
        for sym, info in qd.items():
            q = info.get("quote", {}) if isinstance(info, dict) else {}
            price = q.get("lastPrice") or q.get("mark") or q.get("bidPrice")
            if price:
                out[sym.upper()] = float(price)
        return out
    except Exception as e:
        print(f"  (quote fetch failed: {e} -- rows will fall through to HOLD)")
        return {}


def _evaluate_exit(symbol, entry_price, current_price, hold_minutes, direction="LONG"):
    """
    Apply Sprint 13 exit/stop logic, direction-aware. Returns
    (should_close, trigger, reason).

    LONG rules:
      - stop_loss   : current_price <= entry_price * 0.95  (-5% stop)
      - time_stop   : hold_minutes >= 5 trading days (~1950 min)
      - take_profit : current_price >= entry_price * 1.10  (+10% target)

    SHORT rules (Sprint 17 Item 2e -- inverse): stop fires when price RISES +5%
    above entry; take-profit when price falls; same time stop. Delegated to
    prime_position_sizer.evaluate_short_exit so the short stop logic lives in one
    tested place.
    """
    if (direction or "LONG").upper() == "SHORT":
        from prime_intelligence.prime_position_sizer import evaluate_short_exit
        return evaluate_short_exit(entry_price, current_price, hold_minutes)

    if current_price is None or entry_price is None or entry_price <= 0:
        return False, None, "no current quote -- hold"

    pnl_pct = (current_price - entry_price) / entry_price
    if pnl_pct <= -0.05:
        return True, "stop_loss", f"pnl={pnl_pct:+.2%} hit -5% stop"
    if pnl_pct >= 0.10:
        return True, "drift_exhaustion", f"pnl={pnl_pct:+.2%} hit +10% target"
    if hold_minutes >= 1950:
        return True, "time_stop", f"held {hold_minutes}min -- time stop"
    return False, None, f"pnl={pnl_pct:+.2%} hold {hold_minutes}min -- hold"


def _query_open_positions(symbols):
    """Read OPEN rows from prime_trade_log for the given symbols."""
    target = {s.upper() for s in symbols}
    return [
        t for t in get_open_trades()
        if (t.get("symbol") or "").upper() in target
    ]


def _hold_minutes(entry_time_iso):
    if not entry_time_iso:
        return 0
    try:
        dt = datetime.fromisoformat(entry_time_iso)
        return int((datetime.now() - dt).total_seconds() / 60)
    except Exception:
        return 0


def review(execute=False):
    print("=" * 72)
    print(f"  PRIME v1.0 Sprint 13 -- Item 1 -- Open Position Review ({', '.join(SYMBOLS)})")
    print(f"  Run at: {datetime.now().isoformat()}  |  Mode: {'EXECUTE' if execute else 'DRY-RUN'}")
    print("=" * 72)

    rows = _query_open_positions(SYMBOLS)
    if not rows:
        print(f"  No OPEN positions for {SYMBOLS} in prime_trade_log.")
        print("  Deliverable: nothing to close. Scan complete.")
        print("=" * 72)
        return 0

    quotes = _fetch_quotes(SYMBOLS)

    closed = 0
    held = 0
    for r in rows:
        log_id = r["log_id"]
        symbol = (r.get("symbol") or "").upper()
        direction = r.get("direction") or ""
        shares = r.get("shares") or 0
        entry_price = r.get("entry_price") or r.get("price_at_scan")
        entry_time = r.get("entry_time") or ""
        batch_id = r.get("mata_batch_id")

        current = quotes.get(symbol)
        held_min = _hold_minutes(entry_time)
        should_close, trigger, reason = _evaluate_exit(
            symbol, entry_price, current, held_min, direction=direction
        )

        print(f"\n  {symbol:6s}  batch={batch_id}  dir={direction}  shares={shares}")
        print(f"    log_id={log_id}")
        print(f"    entry={entry_price}  current={current}  hold={held_min}min")
        print(f"    decision: {'CLOSE' if should_close else 'HOLD'} ({reason})")

        if should_close and execute:
            try:
                if (direction or "LONG").upper() == "SHORT":
                    pnl_dollars = (float(entry_price) - float(current)) * float(shares)
                    pnl_pct_val = ((float(entry_price) - float(current)) / float(entry_price)) * 100.0
                else:
                    pnl_dollars = (float(current) - float(entry_price)) * float(shares)
                    pnl_pct_val = ((float(current) - float(entry_price)) / float(entry_price)) * 100.0
                close_trade(
                    log_id=log_id,
                    exit_price=float(current),
                    exit_time=datetime.now().isoformat(),
                    exit_reason=trigger,
                    pnl_dollars=pnl_dollars,
                    pnl_pct=pnl_pct_val,
                    hold_minutes=held_min,
                )
                try:
                    log_ops_event(
                        event_type="SPRINT13_CLOSE",
                        component="sprint13_position_review",
                        symbol=symbol,
                        detail=f"Closed {log_id} via {trigger}: {reason}",
                        severity="INFO",
                    )
                except Exception:
                    pass
                print(f"    close_trade() ok  trigger={trigger}  pnl=${pnl_dollars:+.2f}")
                closed += 1
            except Exception as e:
                print(f"    close_trade() FAILED: {e}")
                held += 1
        elif should_close:
            print("    (dry-run -- re-run with --execute to actually close)")
        else:
            held += 1

    print("\n" + "=" * 72)
    print(f"  Summary: {closed} closed, {held} held, {len(rows)} scanned.")
    print(f"  Deliverable: {SPRINT_TAG} -- scan + per-symbol decision logged above.")
    print("=" * 72)
    return 0


def main():
    parser = argparse.ArgumentParser(description=SPRINT_TAG)
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually close positions whose exit criteria are met.",
    )
    args = parser.parse_args()
    return review(execute=args.execute)


if __name__ == "__main__":
    sys.exit(main())
