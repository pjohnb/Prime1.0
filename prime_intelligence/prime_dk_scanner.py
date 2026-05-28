"""
PRIME v1.0 Dark Pool Scanner Entry Point (CIL-PRIME-DK-001).

Iterates UOA Tier 1 + Tier 2 watchlist symbols, calls score_dk_signal()
for each, and writes dk_score/dk_status to prime_signals for matching
signal records.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_intelligence.prime_dark_pool import score_dk_signal

logger = logging.getLogger("prime_dk_scanner")


def _get_uoa_watchlist() -> List[str]:
    """Return UOA Tier 1 + Tier 2 watchlist symbols."""
    try:
        from prime_scanners.prime_uoa_scanner import get_watchlist
        return get_watchlist()
    except ImportError:
        logger.warning("UOA scanner not available -- using default watchlist")
        return [
            "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
            "AVGO", "JPM", "UNH", "V", "MA", "HD", "PG", "XOM",
        ]


def run_dk_scan(
    symbols: Optional[List[str]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run DK scan on watchlist symbols. Returns summary."""
    if symbols is None:
        symbols = _get_uoa_watchlist()

    results = {
        "scanned": 0,
        "confirming": [],
        "nullifying": [],
        "neutral": [],
        "unavailable": [],
        "errors": [],
        "timestamp": datetime.utcnow().isoformat(),
    }

    for symbol in symbols:
        try:
            dk = score_dk_signal(symbol)
            results["scanned"] += 1
            status = dk["dk_status"]

            if status == "CONFIRMING":
                results["confirming"].append({"symbol": symbol, **dk})
            elif status == "NULLIFYING":
                results["nullifying"].append({"symbol": symbol, **dk})
            elif status == "UNAVAILABLE":
                results["unavailable"].append(symbol)
            else:
                results["neutral"].append(symbol)

            _update_signal_dk(symbol, dk, db_path)

        except Exception as e:
            logger.error("DK scan error for %s: %s", symbol, e)
            results["errors"].append({"symbol": symbol, "error": str(e)})

    logger.info(
        "DK scan complete: %d scanned, %d confirming, %d nullifying, %d unavailable",
        results["scanned"], len(results["confirming"]),
        len(results["nullifying"]), len(results["unavailable"]),
    )

    return results


def _update_signal_dk(
    symbol: str,
    dk: Dict[str, Any],
    db_path: Optional[Path] = None,
) -> None:
    """Update dk_score and dk_status on matching prime_signals records."""
    try:
        from prime_data.prime_db import get_connection
        with get_connection(db_path) as conn:
            conn.execute(
                """UPDATE prime_signals
                   SET dk_score=?, dk_status=?
                   WHERE symbol=? AND status IN ('NEW', 'OPEN', 'TRADED')""",
                (dk.get("dk_score"), dk["dk_status"], symbol.upper()),
            )
            conn.commit()
    except Exception as e:
        logger.debug("Could not update prime_signals DK for %s: %s", symbol, e)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = run_dk_scan()
    print(f"DK Scan: {result['scanned']} symbols scanned")
    print(f"  Confirming:  {len(result['confirming'])}")
    print(f"  Nullifying:  {len(result['nullifying'])}")
    print(f"  Unavailable: {len(result['unavailable'])}")
