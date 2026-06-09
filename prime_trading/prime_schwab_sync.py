"""
PRIME v1.0 Sprint 23 Item 1 -- Schwab Position Sync.

Reads live holdings from all three Schwab accounts and imports any position not
already in prime_trade_log (as OPEN) as a synthetic OPEN record tagged
trade_source='SCHWAB_IMPORT'. Safe to run repeatedly -- deduplication is by
(symbol, account suffix, status=OPEN).

Sprint 28 (reconciliation): when sync runs, OPEN SCHWAB_IMPORT records whose
symbol/account no longer appears in Schwab (0 shares or fully closed) are
auto-closed with exit_reason='SCHWAB_RECONCILE' and logged to prime_ops_health.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Account suffix labels (last 4 digits) for each Schwab account.
_ACCOUNT_LABELS = {
    "7926": "Joint",
    "0461": "Custodial",
    "8779": "IRA",
}


def _get_all_account_positions(
    schwab_client,
) -> List[Tuple[str, str, List[Dict[str, Any]]]]:
    """Return [(account_number, account_suffix, positions_list), ...] for all accounts.

    schwab_client must be a connected SchwabClient instance. Each account is
    fetched independently so a failure on one account does not abort the others.
    """
    try:
        resp = schwab_client.client.get_account_numbers()
        if resp.status_code != 200:
            raise RuntimeError(f"get_account_numbers failed: HTTP {resp.status_code}")
        accounts = resp.json()
    except Exception as e:
        raise RuntimeError(f"Cannot fetch Schwab account list: {e}") from e

    results = []
    for acct in accounts:
        acct_num = acct.get("accountNumber", "")
        hash_val = acct.get("hashValue", "")
        suffix = acct_num[-4:] if len(acct_num) >= 4 else acct_num
        try:
            resp2 = schwab_client.client.get_account(
                hash_val,
                fields=schwab_client.client.Account.Fields.POSITIONS,
            )
            if resp2.status_code != 200:
                logger.warning(
                    "Schwab positions fetch failed for account ...%s: HTTP %d",
                    suffix, resp2.status_code,
                )
                continue
            positions = (
                resp2.json()
                .get("securitiesAccount", {})
                .get("positions", [])
            )
            results.append((acct_num, suffix, positions))
            logger.info("Schwab account ...%s: %d positions", suffix, len(positions))
        except Exception as e:
            logger.warning("Schwab account ...%s fetch error: %s", suffix, e)
    return results


def _open_positions_index(db_path: Optional[Path] = None) -> set:
    """Return a set of (symbol_upper, account_suffix) for all OPEN trade records
    with trade_source='SCHWAB_IMPORT'. Used for dedup."""
    from prime_data.prime_db import get_connection
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, account FROM prime_trade_log "
            "WHERE status='OPEN' AND trade_source='SCHWAB_IMPORT'"
        ).fetchall()
    return {(row[0].upper(), (row[1] or "")) for row in rows}


def _get_open_schwab_import_records(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return all OPEN SCHWAB_IMPORT records with fields needed for reconciliation."""
    from prime_data.prime_db import get_connection
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT log_id, symbol, account, direction, shares, entry_price, entry_time "
            "FROM prime_trade_log "
            "WHERE status='OPEN' AND trade_source='SCHWAB_IMPORT'"
        ).fetchall()
    return [dict(row) for row in rows]


def _reconcile_closed_positions(
    live_schwab_keys: set,
    db_path: Optional[Path] = None,
) -> int:
    """Auto-close OPEN SCHWAB_IMPORT records no longer present in Schwab.

    live_schwab_keys: set of (symbol_upper, account_suffix) from current sync.
    Returns count of records closed.
    """
    from prime_data.prime_db import close_trade, log_ops_event

    open_records = _get_open_schwab_import_records(db_path)
    now_ts = datetime.utcnow().isoformat()
    closed = 0

    for rec in open_records:
        key = (rec["symbol"].upper(), rec["account"] or "")
        if key in live_schwab_keys:
            continue  # still open in Schwab

        log_id = rec["log_id"]
        symbol = rec["symbol"]
        direction = (rec["direction"] or "LONG").upper()
        shares = int(rec["shares"] or 0)
        entry_price = float(rec["entry_price"] or 0)

        # Use entry_price as exit_price fallback; reconcile P&L at cost basis = 0.
        exit_price = entry_price
        pnl_dollars = 0.0
        pnl_pct = 0.0

        hold_minutes = 0
        try:
            entry_dt = datetime.fromisoformat(rec["entry_time"])
            hold_minutes = max(0, int((datetime.utcnow() - entry_dt).total_seconds() / 60))
        except Exception:
            pass

        try:
            close_trade(
                log_id=log_id,
                exit_price=exit_price,
                exit_time=now_ts,
                exit_reason="SCHWAB_RECONCILE",
                pnl_dollars=pnl_dollars,
                pnl_pct=pnl_pct,
                hold_minutes=hold_minutes,
                db_path=db_path,
            )
            log_ops_event(
                event_type="SCHWAB_RECONCILE_CLOSE",
                component="schwab_sync",
                symbol=symbol,
                detail=(
                    f"Auto-closed {direction} {shares} {symbol} "
                    f"(entry=${entry_price:.2f}) — not found in Schwab positions"
                ),
                severity="INFO",
                db_path=db_path,
            )
            logger.info(
                "SCHWAB_RECONCILE: auto-closed %s %s (%s) log_id=%s",
                direction, symbol, rec["account"], log_id,
            )
            closed += 1
        except Exception as e:
            logger.error("SCHWAB_RECONCILE: failed to close %s log_id=%s: %s", symbol, log_id, e)

    return closed


def sync_schwab_positions(
    db_path: Optional[Path] = None,
    schwab_client=None,
) -> Dict[str, Any]:
    """Import Schwab holdings into prime_trade_log as synthetic OPEN records.

    Returns {imported: N, skipped: N, errors: [str]}.

    Dedup key: (symbol, account suffix, status=OPEN, trade_source=SCHWAB_IMPORT).
    A second sync call for the same holdings is a no-op.

    schwab_client may be injected for testing. If None, creates and connects a
    SchwabClient; if Schwab is not configured the function returns gracefully
    with an error entry rather than raising.
    """
    from prime_data.prime_db import insert_trade, TradeRecordError

    result: Dict[str, Any] = {"imported": 0, "skipped": 0, "reconciled": 0, "errors": []}

    if schwab_client is None:
        try:
            from prime_trading.prime_schwab import SchwabClient
            schwab_client = SchwabClient()
            schwab_client.connect()
        except Exception as e:
            msg = f"Schwab not connected: {e}"
            logger.info(msg)
            result["errors"].append(msg)
            return result

    try:
        all_accounts = _get_all_account_positions(schwab_client)
    except Exception as e:
        msg = f"Failed to fetch Schwab account positions: {e}"
        logger.error(msg)
        result["errors"].append(msg)
        return result

    existing = _open_positions_index(db_path)
    now_ts = datetime.now().isoformat()

    # Build set of live (symbol, account_suffix) keys for reconciliation.
    live_keys: set = set()

    for acct_num, suffix, positions in all_accounts:
        for pos in positions:
            instrument = pos.get("instrument", {})
            asset_type = instrument.get("assetType", "")
            if asset_type != "EQUITY":
                continue

            symbol = (instrument.get("symbol") or "").strip().upper()
            if not symbol:
                continue

            long_qty = float(pos.get("longQuantity") or 0)
            short_qty = float(pos.get("shortQuantity") or 0)
            net_qty = long_qty - short_qty

            if net_qty == 0:
                continue

            # Track this live position for reconciliation.
            live_keys.add((symbol, suffix))

            direction = "SHORT" if net_qty < 0 else "LONG"
            shares = int(abs(net_qty))
            avg_price = float(pos.get("averagePrice") or pos.get("averageLongPrice") or 0)

            if avg_price <= 0:
                logger.warning("Skipping %s ...%s: zero/missing average price", symbol, suffix)
                result["skipped"] += 1
                continue

            dedup_key = (symbol, suffix)
            if dedup_key in existing:
                logger.debug("Skipping %s ...%s: already in prime_trade_log OPEN", symbol, suffix)
                result["skipped"] += 1
                continue

            try:
                insert_trade(
                    strategy="SCHWAB_IMPORT",
                    symbol=symbol,
                    direction=direction,
                    mode="PAPER",
                    order_type="MARKET",
                    shares=shares,
                    entry_time=now_ts,
                    price_at_scan=avg_price,
                    entry_price=avg_price,
                    account=suffix,
                    signal_source="schwab_import",
                    trade_source="SCHWAB_IMPORT",
                    notes=f"Imported from Schwab account ...{suffix}",
                    db_path=db_path,
                )
                existing.add(dedup_key)
                result["imported"] += 1
                logger.info("Imported %s (%s) from Schwab account ...%s", symbol, direction, suffix)
            except TradeRecordError as e:
                msg = f"Skipped {symbol} ...{suffix}: {e}"
                logger.warning(msg)
                result["skipped"] += 1
            except Exception as e:
                msg = f"Error importing {symbol} ...{suffix}: {e}"
                logger.error(msg)
                result["errors"].append(msg)

    # Reconcile: auto-close OPEN SCHWAB_IMPORT records no longer in Schwab.
    result["reconciled"] = _reconcile_closed_positions(live_keys, db_path)

    logger.info(
        "Schwab sync complete: imported=%d skipped=%d reconciled=%d errors=%d",
        result["imported"], result["skipped"], result["reconciled"], len(result["errors"]),
    )
    return result
