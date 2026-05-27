"""
PRIME v1.0 Schwab broker module.
Handles Schwab API connection, position fetching, and trade reconciliation.
All broker interaction lives here. No scanner imports this directly.

Standalone usage:
    python prime_trading/prime_schwab.py --reconcile
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_config.prime_config import get_config, ConfigError
from prime_data.prime_db import (
    close_trade,
    get_open_positions,
    get_open_trades,
    init_db,
    log_ops_event,
)

logger = logging.getLogger(__name__)


class BrokerClient(Protocol):
    """Protocol for broker API clients -- enables testing without live Schwab."""

    def get_positions(self) -> List[Dict[str, Any]]: ...
    def get_pending_orders(self) -> List[Dict[str, Any]]: ...


class SchwabClient:
    """Schwab API client using schwab-py library."""

    def __init__(self):
        cfg = get_config()
        self.app_key = cfg.schwab_snapshot.schwab_app_key
        self.app_secret = cfg.schwab_snapshot.schwab_app_secret
        self.token_path = cfg.schwab_snapshot.schwab_token_path
        self.client = None
        self.account_hash = None
        self.connected = False

    def connect(self) -> bool:
        try:
            import schwab
        except ImportError:
            raise RuntimeError(
                "schwab-py not installed. Run: pip install schwab-py"
            )

        if not self.app_key or not self.app_secret:
            raise RuntimeError(
                "Schwab app_key or app_secret not configured in config.json"
            )
        if not self.token_path:
            raise RuntimeError(
                "schwab_token_path not configured in config.json"
            )

        self.client = schwab.auth.client_from_token_file(
            token_path=self.token_path,
            api_key=self.app_key,
            app_secret=self.app_secret,
        )

        resp = self.client.get_account_numbers()
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to fetch Schwab accounts: HTTP {resp.status_code}"
            )

        accounts = resp.json()
        if not accounts:
            raise RuntimeError("No Schwab accounts found")

        self.account_hash = accounts[0]["hashValue"]
        self.connected = True
        logger.info("Schwab connected. Account hash: %s...", self.account_hash[:8])
        return True

    def get_positions(self) -> List[Dict[str, Any]]:
        if not self.connected:
            return []
        resp = self.client.get_account(
            self.account_hash,
            fields=self.client.Account.Fields.POSITIONS,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Schwab positions fetch failed: HTTP {resp.status_code}")
        positions = (
            resp.json()
            .get("securitiesAccount", {})
            .get("positions", [])
        )
        return positions

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        if not self.connected:
            return []
        resp = self.client.get_orders_for_account(self.account_hash)
        if resp.status_code != 200:
            return []
        orders = resp.json()
        return [
            o for o in orders
            if o.get("status") in ("AWAITING_PARENT_ORDER", "AWAITING_CONDITION",
                                    "PENDING_ACTIVATION", "QUEUED", "WORKING",
                                    "PENDING_REPLACE")
        ]


def _extract_schwab_symbols(positions: List[Dict[str, Any]]) -> set:
    """Extract the set of equity symbols from Schwab position data."""
    symbols = set()
    for pos in positions:
        instrument = pos.get("instrument", {})
        if instrument.get("assetType") == "EQUITY":
            sym = instrument.get("symbol", "").upper()
            if sym:
                symbols.add(sym)
    return symbols


def _extract_pending_symbols(orders: List[Dict[str, Any]]) -> set:
    """Extract symbols from pending Schwab orders."""
    symbols = set()
    for order in orders:
        for leg in order.get("orderLegCollection", []):
            sym = leg.get("instrument", {}).get("symbol", "").upper()
            if sym:
                symbols.add(sym)
    return symbols


def reconcile_open_trades(
    client: BrokerClient,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compare OPEN trade records against Schwab holdings. Auto-close
    records where Schwab shows no position. Flag ambiguous records.

    Returns a summary dict with closed, flagged, and unchanged counts.
    """
    now_str = datetime.utcnow().isoformat()
    result = {
        "closed": [],
        "flagged": [],
        "unchanged": [],
        "errors": [],
        "schwab_error": None,
    }

    try:
        schwab_positions = client.get_positions()
    except Exception as e:
        error_msg = f"Schwab API failure during reconciliation: {e}"
        logger.error(error_msg)
        log_ops_event(
            event_type="SCHWAB_API_ERROR",
            component="prime_schwab",
            detail=error_msg,
            severity="ERROR",
            db_path=db_path,
        )
        result["schwab_error"] = error_msg
        return result

    schwab_symbols = _extract_schwab_symbols(schwab_positions)

    try:
        pending_orders = client.get_pending_orders()
    except Exception:
        pending_orders = []
    pending_symbols = _extract_pending_symbols(pending_orders)

    open_trades = get_open_trades(db_path=db_path)
    logger.info(
        "Reconciliation: %d OPEN trades, %d Schwab positions, %d pending orders",
        len(open_trades), len(schwab_symbols), len(pending_symbols),
    )

    for trade in open_trades:
        symbol = trade["symbol"].upper()
        log_id = trade["log_id"]

        if symbol in schwab_symbols:
            result["unchanged"].append(log_id)
            continue

        if symbol in pending_symbols:
            logger.info(
                "FLAGGED (pending order): %s [%s] -- not auto-closing",
                symbol, log_id,
            )
            log_ops_event(
                event_type="RECONCILE_FLAGGED",
                component="prime_schwab",
                symbol=symbol,
                detail=f"Pending order exists for {symbol}; trade {log_id} not auto-closed",
                severity="WARN",
                db_path=db_path,
            )
            result["flagged"].append({"log_id": log_id, "symbol": symbol, "reason": "pending_order"})
            continue

        try:
            entry_price = trade.get("entry_price") or trade.get("price_at_scan") or 0.0
            close_trade(
                log_id=log_id,
                exit_price=entry_price,
                exit_time=now_str,
                exit_reason="SCHWAB_RECONCILE",
                pnl_dollars=0.0,
                pnl_pct=0.0,
                hold_minutes=0,
                db_path=db_path,
            )
            log_ops_event(
                event_type="SCHWAB_RECONCILE",
                component="prime_schwab",
                symbol=symbol,
                detail=f"Auto-closed trade {log_id}: no Schwab position for {symbol}",
                severity="INFO",
                db_path=db_path,
            )
            logger.info("CLOSED (no Schwab position): %s [%s]", symbol, log_id)
            result["closed"].append({"log_id": log_id, "symbol": symbol})
        except Exception as e:
            logger.error("Error closing trade %s: %s", log_id, e)
            result["errors"].append({"log_id": log_id, "error": str(e)})

    return result


def main():
    parser = argparse.ArgumentParser(description="PRIME Schwab Broker")
    parser.add_argument("--reconcile", action="store_true", help="Run trade reconciliation")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.reconcile:
        logger.info("Starting Schwab trade reconciliation...")
        try:
            init_db()
            client = SchwabClient()
            client.connect()
            result = reconcile_open_trades(client)

            print(f"\nReconciliation complete:")
            print(f"  Closed:    {len(result['closed'])}")
            print(f"  Flagged:   {len(result['flagged'])}")
            print(f"  Unchanged: {len(result['unchanged'])}")
            if result["errors"]:
                print(f"  Errors:    {len(result['errors'])}")
            if result["schwab_error"]:
                print(f"  Schwab API Error: {result['schwab_error']}")
        except Exception as e:
            logger.error("Reconciliation failed: %s", e)
            try:
                log_ops_event(
                    event_type="RECONCILE_FAILED",
                    component="prime_schwab",
                    detail=str(e),
                    severity="ERROR",
                )
            except Exception:
                pass
            print(f"ERROR: {e}")
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
