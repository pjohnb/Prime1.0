"""
PRIME v1.0 DK Feed abstraction layer (Sprint 16 Item 4).

Single entry point for all dark-pool (DK) print data. Every consumer of DK
prints goes through get_dk_prints(); the underlying source can be swapped in
one commit without touching any caller.

Interface contract:
    get_dk_prints(symbols, date=None) -> List[{
        "symbol":    str,
        "price":     float,    # print price
        "volume":    int,      # print size (shares)
        "timestamp": str,      # ISO-8601
        "venue":     str,      # reporting venue / ATS code ("" if unknown)
    }]

Implementations:
    * STUB (current): reads dark-pool print files written to scan_results/
      (dk_prints_*.json), filtered by symbol and date. Returns [] when no
      files / no matching prints are present.
    * Unusual Whales (DEFERRED to a future commit): when _USE_UNUSUAL_WHALES is
      flipped True and UW_API_KEY is configured, _get_prints_unusual_whales()
      becomes the source behind the SAME get_dk_prints() signature. No caller
      changes required -- that is the point of this layer.

UW_API_KEY is read from the environment, falling back to ops_config.json
(uw_api_key). The value is never committed (ops_config.json is gitignored).
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prime_dk_feed")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SCAN_RESULTS = _PROJECT_ROOT / "scan_results"

# Swap seam: flip to True (with UW_API_KEY configured) to source live prints
# from Unusual Whales instead of the scan_results stub. Single-commit swap.
_USE_UNUSUAL_WHALES = False

# Print record keys -- the stable contract every implementation must satisfy.
PRINT_KEYS = ("symbol", "price", "volume", "timestamp", "venue")


def _get_uw_api_key() -> Optional[str]:
    """Resolve UW_API_KEY: environment first, then ops_config.json (uw_api_key)."""
    key = os.environ.get("UW_API_KEY", "").strip()
    if key:
        return key
    try:
        cfg = _PROJECT_ROOT / "ops_config.json"
        if cfg.exists():
            data = json.loads(cfg.read_text())
            v = str(data.get("uw_api_key", "") or "").strip()
            return v or None
    except Exception:
        pass
    return None


def _shape_print(raw: Dict[str, Any], default_symbol: str = "") -> Dict[str, Any]:
    """Coerce a raw print dict into the stable get_dk_prints() contract shape."""
    symbol = str(raw.get("symbol") or default_symbol or "").upper()
    # Accept legacy tape-print keys (size/ts) as well as the canonical keys.
    volume = raw.get("volume", raw.get("size", 0)) or 0
    timestamp = raw.get("timestamp", raw.get("ts", "")) or ""
    return {
        "symbol": symbol,
        "price": float(raw.get("price", 0) or 0),
        "volume": int(volume),
        "timestamp": str(timestamp),
        "venue": str(raw.get("venue", "") or ""),
    }


def _matches_date(ts: str, date: Optional[str]) -> bool:
    """True if a print timestamp falls on `date` (YYYY-MM-DD). No date -> all."""
    if not date:
        return True
    return str(ts).startswith(date)


def _get_prints_from_scan_results(
    symbols: List[str],
    date: Optional[str],
    scan_results_dir: Path,
) -> List[Dict[str, Any]]:
    """STUB source: read dk_prints_*.json from scan_results/.

    File format (either is accepted):
        {"prints": [{symbol, price, volume, timestamp, venue}, ...]}
        or a bare JSON list of print dicts.
    """
    if not scan_results_dir.exists():
        return []
    wanted = {s.upper() for s in symbols} if symbols else None
    out: List[Dict[str, Any]] = []
    for path in sorted(scan_results_dir.glob("dk_prints_*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            logger.debug("could not read DK print file %s: %s", path, e)
            continue
        records = data.get("prints", []) if isinstance(data, dict) else data
        if not isinstance(records, list):
            continue
        for raw in records:
            if not isinstance(raw, dict):
                continue
            shaped = _shape_print(raw)
            if wanted is not None and shaped["symbol"] not in wanted:
                continue
            if not _matches_date(shaped["timestamp"], date):
                continue
            out.append(shaped)
    return out


def _get_prints_unusual_whales(
    symbols: List[str],
    date: Optional[str],
) -> List[Dict[str, Any]]:
    """FUTURE source: Unusual Whales live DK feed (deferred).

    When _USE_UNUSUAL_WHALES is enabled, this becomes the get_dk_prints()
    backend. Implementation is intentionally a stub until the UW contract is
    validated (Sprint 16 work order: feed deferred, stub ready).
    """
    api_key = _get_uw_api_key()
    if not api_key:
        logger.warning("UW_API_KEY not configured; Unusual Whales feed unavailable")
        return []
    # TODO(Sprint 17+): call the Unusual Whales API and map the response into
    # the PRINT_KEYS contract shape via _shape_print(). One-commit swap.
    logger.info("Unusual Whales feed not yet implemented; returning no prints")
    return []


def get_dk_prints(
    symbols: List[str],
    date: Optional[str] = None,
    scan_results_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return dark-pool prints for `symbols` (optionally filtered to `date`).

    The single entry point for all DK print data. Each record conforms to
    PRINT_KEYS. Returns [] when no prints are available. Never raises.
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    try:
        if _USE_UNUSUAL_WHALES:
            return _get_prints_unusual_whales(symbols, date)
        sr_dir = scan_results_dir or _DEFAULT_SCAN_RESULTS
        return _get_prints_from_scan_results(symbols, date, sr_dir)
    except Exception as e:
        logger.warning("get_dk_prints failed: %s", e)
        return []
