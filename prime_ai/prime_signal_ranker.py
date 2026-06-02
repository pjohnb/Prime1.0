"""
PRIME v1.0 AI Signal Ranker (Sprint 15 Item 3).

When approved signals exceed the Max Trades setting, Claude ranks the
candidates by portfolio fit (sector balance, correlation with existing
holdings, regime) rather than raw score. Falls back to the deterministic
score-sort (ML-19 smart selector) whenever the API is unavailable, so the
execution path always returns a top-N selection.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from prime_ai import _claude
from prime_intelligence.prime_smart_selector import select_entries, _get_score

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the PRIME AI Signal Ranker. You are given a list of
approved trade candidates, the current open positions, and a Max Trades limit N.
Rank the candidates by PORTFOLIO FIT (diversification vs. existing sector
exposure, correlation, regime alignment) -- not by raw score alone.

Respond ONLY with a JSON array, best first, no prose outside it:
[{"symbol": str, "rank": int, "rationale": "one sentence",
  "portfolio_fit_score": number 0-100}]"""


def _annotate(signal: Dict[str, Any], rank: int, fit: Optional[float],
              rationale: str) -> Dict[str, Any]:
    out = dict(signal)
    out["ai_rank"] = rank
    out["portfolio_fit_score"] = fit
    out["ai_rationale"] = rationale
    return out


def _score_fallback(approved: List[Dict[str, Any]], max_trades: int,
                    reason: str) -> List[Dict[str, Any]]:
    """Deterministic top-N by composite score (ML-19), annotated as fallback."""
    selected = select_entries(approved, max_trades=max_trades)
    ranked = sorted(selected, key=lambda s: -_get_score(s))
    return [_annotate(s, i + 1, None, f"score-sort fallback ({reason})")
            for i, s in enumerate(ranked)]


def rank_signals(
    approved: List[Dict[str, Any]],
    open_positions: Optional[List[Dict[str, Any]]] = None,
    max_trades: int = 5,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the top-N approved signals ranked by portfolio fit.

    Uses Claude when available; otherwise deterministic score-sort. Never raises.
    """
    open_positions = open_positions or []
    if len(approved) <= max_trades:
        # Still rank for display, but nothing is dropped.
        max_trades = len(approved)

    by_symbol = {s.get("symbol"): s for s in approved}
    context = {
        "max_trades": max_trades,
        "candidates": [
            {"symbol": s.get("symbol"), "strategy": s.get("strategy"),
             "score": _get_score(s), "tier": s.get("tier"), "sector": s.get("sector")}
            for s in approved
        ],
        "open_positions": [
            {"symbol": p.get("symbol"), "strategy": p.get("strategy"),
             "sector": p.get("sector")} for p in open_positions
        ],
    }
    prompt = ("Rank these candidates by portfolio fit and return the JSON array:\n"
              + json.dumps(context, indent=2, default=str))

    try:
        text = _claude.call_claude(SYSTEM_PROMPT, prompt, api_key=api_key)
        ranking = _claude.parse_json(text)
        if not isinstance(ranking, list) or not ranking:
            return _score_fallback(approved, max_trades, "empty ranking")
        ordered = sorted(ranking, key=lambda r: r.get("rank", 9999))
        result = []
        for r in ordered:
            sig = by_symbol.get(r.get("symbol"))
            if sig is None:
                continue
            result.append(_annotate(sig, r.get("rank", len(result) + 1),
                                    r.get("portfolio_fit_score"),
                                    r.get("rationale", "")))
        if not result:
            return _score_fallback(approved, max_trades, "no symbol match")
        return result[:max_trades]
    except Exception as e:
        logger.warning("signal ranker failed: %s", e)
        return _score_fallback(approved, max_trades, str(e))


def select_top_n(
    approved: List[Dict[str, Any]],
    open_positions: Optional[List[Dict[str, Any]]] = None,
    max_trades: int = 5,
    api_key: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """PSA execution-path entry point: select top-N, AI-ranked when over Max Trades.

    Logs the ranker decision to prime_ops_health for audit. When approvals do
    not exceed Max Trades, returns them unchanged (no AI call).
    """
    if len(approved) <= max_trades:
        return approved

    if api_key is None:
        api_key = _claude.get_api_key()
    ranked = rank_signals(approved, open_positions, max_trades, api_key=api_key)

    try:
        from prime_data.prime_db import log_ops_event
        ai = bool(ranked) and ranked[0].get("portfolio_fit_score") is not None
        detail = "method={0} approvals={1} max_trades={2} selected={3}".format(
            "AI" if ai else "score-sort", len(approved), max_trades, len(ranked))
        log_ops_event("AI_RANK", "signal_ranker",
                      detail=detail, severity="INFO", db_path=db_path)
    except Exception as e:
        logger.debug("could not log ranker event: %s", e)

    return ranked
