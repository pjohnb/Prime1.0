"""
CIL-NEW-08: Staged Entry — 2 or 3 tranche buys with TIME or DK_CONFIRM triggers.

Stage 1 executes immediately when the user confirms. Stages 2/3 are queued:
  - TIME trigger: APScheduler one-shot job fires after stage_interval_min minutes.
  - DK_CONFIRM trigger: PositionMonitor calls check_dk_staged_entries() each poll
    cycle; when dk_status turns CONFIRMING for the symbol, the next stage fires.

Pending entries are held in the module-level _pending dict (keyed by signal_id).
Entries survive for the process lifetime; a restart clears them (by design —
staged entries are intraday constructs).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# In-process registry: signal_id -> StagedEntry
_pending: Dict[str, "StagedEntry"] = {}


@dataclass
class StagedEntry:
    signal_id: str
    symbol: str
    strategy: str
    stage_count: int              # total tranches (2 or 3)
    stage_trigger: str            # 'TIME' or 'DK_CONFIRM'
    stage_interval_min: int       # minutes between stages for TIME trigger
    completed_stages: int         # how many tranches have executed
    paper_accounts: List[Dict]    # [{name, buying_power}, ...]
    live_accounts: List[Dict]     # [{suffix, hash_val, buying_power}, ...]
    execution_price: float
    order_type: str               # 'MARKET' or 'LIMIT'
    mode: str                     # 'PAPER' or 'LIVE'
    max_order_pct: float
    entry_price_scan: float
    db_path: Optional[Path] = None


def register(entry: StagedEntry) -> None:
    _pending[entry.signal_id] = entry


def get_pending(signal_id: str) -> Optional[StagedEntry]:
    return _pending.get(signal_id)


def pop_pending(signal_id: str) -> Optional[StagedEntry]:
    return _pending.pop(signal_id, None)


def list_dk_pending(symbol: str) -> List[StagedEntry]:
    """Return all DK_CONFIRM entries waiting on a given symbol."""
    return [
        e for e in _pending.values()
        if e.stage_trigger == "DK_CONFIRM"
        and e.symbol.upper() == symbol.upper()
        and e.completed_stages < e.stage_count
    ]


def execute_next_stage(
    entry: StagedEntry,
    schwab_client=None,
) -> Dict[str, Any]:
    """Execute the next pending tranche for this StagedEntry."""
    from prime_data.prime_db import insert_trade

    stage_number = entry.completed_stages + 1
    if stage_number > entry.stage_count:
        return {"error": "all stages already completed", "stage_number": stage_number}

    now = datetime.now()
    orders_placed: List[Dict] = []
    total_allocated = 0

    if entry.mode == "LIVE" and schwab_client is not None:
        for acct in entry.live_accounts:
            suffix = acct.get("suffix", "")
            hash_val = acct.get("hash_val", "")
            buying_power = float(acct.get("buying_power", 0))
            shares = int(buying_power * entry.max_order_pct / entry.execution_price) if entry.execution_price > 0 else 0
            if shares <= 0:
                continue
            try:
                from prime_trading.prime_schwab_orders import submit_order
                result = submit_order(
                    symbol=entry.symbol,
                    qty=shares,
                    side="BUY",
                    order_type=entry.order_type,
                    price=entry.execution_price,
                    account_hash=hash_val,
                    confirmed=True,
                    schwab_client=schwab_client,
                )
                log_id = insert_trade(
                    strategy=entry.strategy,
                    symbol=entry.symbol,
                    direction="LONG",
                    mode="LIVE",
                    order_type=entry.order_type,
                    shares=shares,
                    entry_time=now.isoformat(),
                    price_at_scan=entry.entry_price_scan,
                    entry_price=entry.execution_price,
                    account=suffix,
                    order_id=result.get("order_id"),
                    signal_source="STAGED_ENTRY",
                    trade_source="LIVE",
                    signal_id=entry.signal_id,
                    stage_number=stage_number,
                    stage_total=entry.stage_count,
                    db_path=entry.db_path,
                )
                orders_placed.append({
                    "account": suffix,
                    "shares": shares,
                    "status": "SUBMITTED",
                    "log_id": log_id,
                })
                total_allocated += shares
            except Exception as e:
                logger.error(
                    "staged_entry: stage %d LIVE order failed for %s: %s",
                    stage_number, suffix, e,
                )
                orders_placed.append({"account": suffix, "shares": shares, "status": "FAILED", "error": str(e)})
    else:
        accounts = entry.paper_accounts or [{"name": "PAPER", "buying_power": 100000}]
        for acct in accounts:
            bp = float(acct.get("buying_power", 100000))
            shares = int(bp * entry.max_order_pct / entry.execution_price) if entry.execution_price > 0 else 0
            if shares <= 0:
                continue
            acct_name = str(acct.get("name", "PAPER"))
            try:
                log_id = insert_trade(
                    strategy=entry.strategy,
                    symbol=entry.symbol,
                    direction="LONG",
                    mode="PAPER",
                    order_type=entry.order_type,
                    shares=shares,
                    entry_time=now.isoformat(),
                    price_at_scan=entry.entry_price_scan,
                    entry_price=entry.execution_price,
                    account=acct_name,
                    signal_source="STAGED_ENTRY",
                    trade_source="PAPER",
                    signal_id=entry.signal_id,
                    stage_number=stage_number,
                    stage_total=entry.stage_count,
                    db_path=entry.db_path,
                )
                orders_placed.append({
                    "account": acct_name,
                    "shares": shares,
                    "status": "PAPER_SIMULATED",
                    "log_id": log_id,
                })
                total_allocated += shares
            except Exception as e:
                orders_placed.append({"account": acct_name, "shares": shares, "status": "FAILED", "error": str(e)})

    entry.completed_stages = stage_number
    if entry.completed_stages >= entry.stage_count:
        pop_pending(entry.signal_id)
        logger.info("staged_entry: %s all %d stages complete", entry.symbol, entry.stage_count)
    else:
        logger.info(
            "staged_entry: %s stage %d/%d executed — %d remaining",
            entry.symbol, stage_number, entry.stage_count,
            entry.stage_count - entry.completed_stages,
        )

    return {
        "stage_number":    stage_number,
        "stage_count":     entry.stage_count,
        "orders_placed":   orders_placed,
        "total_allocated": total_allocated,
    }


def check_dk_staged_entries(symbol: str) -> None:
    """Called by PositionMonitor when dk_status=CONFIRMING for a symbol.

    Fires execute_next_stage() for any DK_CONFIRM pending entries on that symbol.
    """
    pending = list_dk_pending(symbol)
    for entry in pending:
        logger.info(
            "staged_entry: DK_CONFIRM trigger fired for %s — executing stage %d/%d",
            symbol, entry.completed_stages + 1, entry.stage_count,
        )
        try:
            execute_next_stage(entry)
        except Exception as e:
            logger.error("staged_entry: DK_CONFIRM execute_next_stage failed for %s: %s", symbol, e)
