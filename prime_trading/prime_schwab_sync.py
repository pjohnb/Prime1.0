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

# Sector overrides for ETF / COLLECTIVE_INVESTMENT and common large-cap EQUITY
# symbols. CIL-077: equities NOT found here fall through to the shared
# prime_portfolio_factor.sector_map (single source of truth, 100+ symbols), then
# the Schwab fundamental API, then the 'Unknown' floor — sector is NEVER NULL.
SYMBOL_SECTOR_MAP: dict = {
    # ETFs / commodities / broad-market funds (COLLECTIVE_INVESTMENT + ETF).
    "GLD": "Commodities", "SLV": "Commodities", "GDX": "Commodities", "GDXJ": "Commodities",
    "USO": "Energy", "XLE": "Energy",
    "XLF": "Financials", "XLK": "Technology", "XLV": "Healthcare",
    "XLP": "Consumer Staples", "XLY": "Consumer Discretionary",
    "XLI": "Industrials", "XLB": "Materials", "XLU": "Utilities",
    "XLRE": "Real Estate",
    "SPY": "Broad Market", "QQQ": "Broad Market", "IWM": "Broad Market",
    "DIA": "Broad Market", "VTI": "Broad Market", "VOO": "Broad Market",
    # Common large-cap equities likely to be held (CIL-077). Explicit so the
    # named beta-blocker symbols resolve deterministically regardless of the
    # shared map; equities beyond this set still resolve via sector_map below.
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "AVGO": "Technology", "GOOGL": "Technology",
    "META": "Technology", "CRM": "Technology", "ADBE": "Technology",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "TJX": "Consumer Discretionary",
    "NKE": "Consumer Discretionary",
    "COST": "Consumer Staples", "WMT": "Consumer Staples", "PG": "Consumer Staples",
    "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "UNH": "Health Care", "JNJ": "Health Care", "LLY": "Health Care",
    "ABBV": "Health Care",
    "JPM": "Financials", "V": "Financials", "MA": "Financials", "BAC": "Financials",
    "XOM": "Energy", "CVX": "Energy",
}


def _fetch_schwab_fundamental_sector(symbol: str, schwab_client) -> Optional[str]:
    """Best-effort sector via the Schwab fundamental data API (CIL-077).

    GET /instruments?symbol=X&projection=fundamental. Returns the sector string
    or None on any failure (no client, unsupported method, non-200, no sector).
    Fully defensive so a sync never fails on the fundamental lookup.
    """
    if schwab_client is None:
        return None
    client = getattr(schwab_client, "client", None)
    if client is None or not hasattr(client, "get_instruments"):
        return None
    try:
        projection: Any = "fundamental"
        try:  # schwab-py exposes a Projection enum; fall back to the raw string.
            projection = client.Instrument.Projection.FUNDAMENTAL
        except Exception:
            projection = "fundamental"
        resp = client.get_instruments([symbol], projection)
        if getattr(resp, "status_code", 200) != 200:
            return None
        data = resp.json()
        rec: Any = None
        if isinstance(data, dict):
            instruments = data.get("instruments")
            if isinstance(instruments, list) and instruments:
                rec = instruments[0]
            else:
                rec = data.get(symbol) or data.get(symbol.upper())
        if not isinstance(rec, dict):
            return None
        fundamental = rec.get("fundamental") if isinstance(rec.get("fundamental"), dict) else {}
        sector = (fundamental.get("sector") or rec.get("sector") or "").strip()
        return sector or None
    except Exception as e:  # noqa: BLE001 - never let a sector lookup break sync
        logger.debug("Schwab fundamental sector lookup failed for %s: %s", symbol, e)
        return None


def _resolve_sector(symbol: str, asset_type: str, schwab_client=None) -> str:
    """Resolve a position's sector. CIL-077: never returns None/NULL.

    Order: explicit local override -> shared prime_portfolio_factor.sector_map
    (equities) -> COLLECTIVE_INVESTMENT default 'ETF' -> Schwab fundamental API
    -> 'Unknown' floor.
    """
    sector = SYMBOL_SECTOR_MAP.get(symbol)
    if sector:
        return sector

    try:
        from prime_intelligence.prime_portfolio_factor import sector_map
        mapped = sector_map(symbol)
        if mapped and mapped != "Unknown":
            return mapped
    except Exception:
        pass

    if asset_type == "COLLECTIVE_INVESTMENT":
        return "ETF"

    fundamental = _fetch_schwab_fundamental_sector(symbol, schwab_client)
    if fundamental:
        return fundamental

    return "Unknown"

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


def get_schwab_cash_total(schwab_client=None) -> Optional[float]:
    """Return the total Cash & Sweep Vehicle balance across all Schwab accounts.

    Returns None if Schwab is not connected or an error occurs.
    Used by the Portfolio tab Cash Available tile (PORT-03).
    """
    if schwab_client is None:
        try:
            from prime_trading.prime_schwab import SchwabClient
            schwab_client = SchwabClient()
            schwab_client.connect()
        except Exception as e:
            logger.debug("get_schwab_cash_total: Schwab not connected: %s", e)
            return None

    total_cash = 0.0
    try:
        resp = schwab_client.client.get_account_numbers()
        if resp.status_code != 200:
            return None
        accounts = resp.json()
    except Exception as e:
        logger.debug("get_schwab_cash_total: cannot fetch account numbers: %s", e)
        return None

    for acct in accounts:
        hash_val = acct.get("hashValue", "")
        suffix = (acct.get("accountNumber") or "")[-4:]
        try:
            resp2 = schwab_client.client.get_account(hash_val)
            if resp2.status_code != 200:
                continue
            balances = resp2.json().get("securitiesAccount", {}).get("currentBalances", {})
            cash = float(balances.get("cashBalance") or 0)
            sweep = float(balances.get("sweepCashBalance") or balances.get("moneyMarketFund") or 0)
            total_cash += cash + sweep
            logger.debug("Account ...%s: cash=%.2f sweep=%.2f", suffix, cash, sweep)
        except Exception as e:
            logger.debug("get_schwab_cash_total: error for account ...%s: %s", suffix, e)

    return round(total_cash, 2)


def _recently_closed_symbols(grace_hours: float, db_path: Optional[Path] = None) -> Dict[str, str]:
    """Return {symbol_upper: exit_time} for positions closed within the last grace_hours.

    Queries prime_trade_log for CLOSED records whose exit_time is within the
    grace period. Used by CIL-NEW-07 to prevent re-importing freshly-closed
    positions that Schwab may still show briefly after the close.
    """
    from prime_data.prime_db import get_connection
    from datetime import timedelta

    cutoff = (datetime.utcnow() - timedelta(hours=grace_hours)).isoformat()
    try:
        with get_connection(db_path) as conn:
            rows = conn.execute(
                "SELECT symbol, exit_time FROM prime_trade_log "
                "WHERE status='CLOSED' AND exit_time >= ?",
                (cutoff,),
            ).fetchall()
        return {row[0].upper(): row[1] for row in rows}
    except Exception as e:
        logger.warning("_recently_closed_symbols query failed: %s", e)
        return {}


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

    # CIL-NEW-07: load re-import grace period from ops_config.json.
    grace_hours = 24.0
    try:
        ops_path = Path(__file__).resolve().parent.parent / "ops_config.json"
        import json as _json
        with open(ops_path) as _f:
            grace_hours = float(_json.load(_f).get("schwab_reimport_grace_hours", 24.0))
    except Exception:
        pass
    recently_closed = _recently_closed_symbols(grace_hours, db_path)

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
            if asset_type not in ("EQUITY", "ETF", "COLLECTIVE_INVESTMENT"):
                logger.debug(
                    "Skipping %s ...%s: unsupported asset type %s",
                    instrument.get("symbol", "?"), suffix, asset_type,
                )
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

            # CIL-NEW-07: skip symbols closed within the grace period.
            if symbol in recently_closed:
                exit_time = recently_closed[symbol]
                logger.debug(
                    "Skipping re-import of recently closed position: %s closed at %s",
                    symbol, exit_time,
                )
                result["skipped"] += 1
                continue

            dedup_key = (symbol, suffix)
            if dedup_key in existing:
                logger.debug("Skipping %s ...%s: already in prime_trade_log OPEN", symbol, suffix)
                result["skipped"] += 1
                continue

            # CIL-077: resolve sector for EVERY position (equities included) and
            # never store NULL — 'Unknown' is the floor. Equities resolve via the
            # shared sector_map; unknowns fall back to the Schwab fundamental API.
            sector = _resolve_sector(symbol, asset_type, schwab_client)

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
                    sector=sector,
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
