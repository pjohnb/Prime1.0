"""
PRIME v1.0 Smart Entry Selector (ML-Pattern-19).

When Max Trades < approved signals, selects top N by composite score
descending. Ties broken alphabetically. No alpha selection.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_TRADES = 5


def _read_max_trades(config_path: Optional[Path] = None) -> int:
    """Read max_trades from ops_config.json. Default 5 if missing."""
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "ops_config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            return int(data.get("max_trades", DEFAULT_MAX_TRADES))
    except Exception:
        pass
    return DEFAULT_MAX_TRADES


def _get_score(signal: Dict[str, Any]) -> float:
    """Extract composite score from signal, checking common field names."""
    for key in ("composite_score", "score", "signal_score"):
        val = signal.get(key)
        if val is not None:
            return float(val)
    return 0.0


def select_entries(
    approved: List[Dict[str, Any]],
    max_trades: Optional[int] = None,
    config_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Select top N signals by composite score. Tie-break: symbol alphabetically."""
    if max_trades is None:
        max_trades = _read_max_trades(config_path)

    if len(approved) <= max_trades:
        return approved

    ranked = sorted(
        approved,
        key=lambda s: (-_get_score(s), s.get("symbol", "")),
    )
    selected = ranked[:max_trades]

    logger.info(
        "Smart selection: %d approved, max_trades=%d, selected top %d by score",
        len(approved), max_trades, len(selected),
    )
    return selected
