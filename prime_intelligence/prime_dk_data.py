"""
PRIME v1.0 Dark Pool Data Fetchers (CIL-PRIME-DK-001).

Three data source proxies for the DK scanner:
  1. FINRA ATS weekly volume (7-day cache)
  2. Large tape prints (block trades >= 10,000 shares at mid +/- 0.05)
  3. FINRA daily short volume (1-day cache)

When live feeds are unavailable, returns None -- caller must handle gracefully.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "dk"
BLOCK_SIZE_THRESHOLD = 10_000


def _ensure_cache_dir():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _read_cache(key: str, max_age_days: int) -> Optional[Dict[str, Any]]:
    """Read cached data if fresh enough."""
    _ensure_cache_dir()
    cache_file = _CACHE_DIR / f"{key}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if datetime.utcnow() - cached_at > timedelta(days=max_age_days):
            return None
        return data
    except Exception:
        return None


def _write_cache(key: str, data: Dict[str, Any]):
    _ensure_cache_dir()
    data["_cached_at"] = datetime.utcnow().isoformat()
    cache_file = _CACHE_DIR / f"{key}.json"
    try:
        cache_file.write_text(json.dumps(data))
    except Exception as e:
        logger.debug("DK cache write failed for %s: %s", key, e)


def get_finra_ats_volume(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch FINRA ATS weekly volume baseline.

    Returns {symbol, ats_pct, week_ending} or None if unavailable.
    Caches locally for 7 days.
    """
    cache_key = f"ats_{symbol.upper()}"
    cached = _read_cache(cache_key, max_age_days=7)
    if cached and cached.get("symbol"):
        return cached

    # Live FINRA ATS feed not yet integrated -- return None per tiebreaker
    logger.debug("FINRA ATS data unavailable for %s -- feed not integrated", symbol)
    return None


def get_tape_prints(
    symbol: str,
    lookback_days: int = 5,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch large block prints (>= 10,000 shares) at mid +/- 0.05.

    Returns list of {ts, size, price, mid_offset} or None if unavailable.

    Sprint 16 Item 4: dark-pool prints now flow through the single DK data
    entry point, prime_data.prime_dk_feed.get_dk_prints(). This adapter maps the
    feed's canonical print shape into the legacy tape-print shape and preserves
    the None-on-empty contract (None == unavailable) the composite scorer relies
    on. The Unusual Whales live feed swaps in behind prime_dk_feed.
    """
    try:
        from prime_data.prime_dk_feed import get_dk_prints
        prints = get_dk_prints([symbol])
    except Exception as e:
        logger.debug("DK feed unavailable for %s: %s", symbol, e)
        return None
    if not prints:
        return None
    return [{"ts": p.get("timestamp", ""), "size": p.get("volume", 0),
             "price": p.get("price", 0.0), "mid_offset": 0.0,
             "venue": p.get("venue", "")} for p in prints]


def get_short_volume(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch FINRA daily short volume.

    Returns {symbol, short_pct, date} or None if unavailable.
    Caches locally for 1 day.
    """
    cache_key = f"short_{symbol.upper()}"
    cached = _read_cache(cache_key, max_age_days=1)
    if cached and cached.get("symbol"):
        return cached

    # Live FINRA short volume feed not yet integrated -- return None per tiebreaker
    logger.debug("FINRA short volume unavailable for %s -- feed not integrated", symbol)
    return None


def inject_test_data(
    symbol: str,
    ats_pct: Optional[float] = None,
    ats_rising: bool = False,
    tape_prints: Optional[List[Dict[str, Any]]] = None,
    short_pct: Optional[float] = None,
    short_avg_20d: Optional[float] = None,
) -> None:
    """Inject test/simulated data into cache for development and testing."""
    _ensure_cache_dir()
    if ats_pct is not None:
        _write_cache(f"ats_{symbol.upper()}", {
            "symbol": symbol.upper(),
            "ats_pct": ats_pct,
            "ats_rising": ats_rising,
            "week_ending": datetime.utcnow().strftime("%Y-%m-%d"),
        })
    if tape_prints is not None:
        _write_cache(f"tape_{symbol.upper()}", {
            "symbol": symbol.upper(),
            "prints": tape_prints,
        })
    if short_pct is not None:
        _write_cache(f"short_{symbol.upper()}", {
            "symbol": symbol.upper(),
            "short_pct": short_pct,
            "short_avg_20d": short_avg_20d or short_pct,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
        })
