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
from prime_intelligence.prime_smart_selector import (
    select_entries, _get_score, _read_max_trades,
)

logger = logging.getLogger(__name__)

# Sprint 16 Item 2: ops_config.json key toggling the AI ranker in the
# execution path. Read fresh on every call so the toggle takes effect at
# runtime without restarting the scanner or scheduler.
USE_AI_RANKER_KEY = "use_ai_ranker"


def _read_use_ai_ranker(config_path: Optional[Path] = None) -> bool:
    """Read use_ai_ranker from ops_config.json at runtime. Default True."""
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "ops_config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            return bool(data.get(USE_AI_RANKER_KEY, True))
    except Exception:
        pass
    return True

SYSTEM_PROMPT = """You are the PRIME AI Signal Ranker. You are given a list of
approved trade candidates, the current open positions, and a Max Trades limit N.
Rank the candidates by PORTFOLIO FIT (diversification vs. existing sector
exposure, correlation, regime alignment) -- not by raw score alone.

DK context (use to break ties and adjust ranking confidence):
  dk_status CONFIRMING = institutional dark-pool buying aligns with signal --
    prefer CONFIRMING over NEUTRAL when portfolio fit is otherwise equal.
  dk_status NEUTRAL = no dark-pool signal; rank on portfolio fit alone.
  dk_status NULLIFYING = dark-pool activity opposes signal direction -- rank
    last within tier; note the opposition in rationale.
dk_conviction (0-1) quantifies signal strength; higher = more decisive DK data.

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
            # Sprint 20 Item 4: dk_status and dk_conviction included so the ranker
            # can prefer CONFIRMING over NEUTRAL when portfolio fit is otherwise equal.
            {"symbol": s.get("symbol"), "strategy": s.get("strategy"),
             "score": _get_score(s), "tier": s.get("tier"), "sector": s.get("sector"),
             "dk_status": s.get("dk_status") or "NEUTRAL",
             "dk_conviction": s.get("dk_conviction")}
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


def select_for_execution(
    approved: List[Dict[str, Any]],
    open_positions: Optional[List[Dict[str, Any]]] = None,
    max_trades: Optional[int] = None,
    scanner: str = "PSA",
    api_key: Optional[str] = None,
    db_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Toggle-aware execution-path selection (Sprint 16 Item 2).

    Routing:
      * use_ai_ranker=True  AND approvals > max_trades -> AI ranker (select_top_n)
      * otherwise (toggle off, or no overflow)          -> score-sort (select_entries)

    The no-overflow case always uses select_entries() so there is no API cost
    when nothing would be dropped. The chosen path is logged to prime_ops_health
    for every run. The use_ai_ranker toggle is read fresh from ops_config.json on
    each call, so flipping it takes effect at runtime without a restart.
    """
    if max_trades is None:
        max_trades = _read_max_trades(config_path)
    use_ai = _read_use_ai_ranker(config_path)
    overflow = len(approved) > max_trades

    if use_ai and overflow:
        path = "ai_ranker"
        selected = select_top_n(approved, open_positions, max_trades,
                                api_key=api_key, db_path=db_path)
    else:
        path = "score_sort"
        selected = select_entries(approved, max_trades=max_trades,
                                  config_path=config_path)

    try:
        from prime_data.prime_db import log_ops_event
        detail = ("path={0} use_ai_ranker={1} approvals={2} max_trades={3} "
                  "overflow={4} selected={5}").format(
            path, use_ai, len(approved), max_trades, overflow, len(selected))
        log_ops_event("PSA_SELECT", "{0}_runner".format(str(scanner).lower()),
                      detail=detail, severity="INFO", db_path=db_path)
    except Exception as e:
        logger.debug("could not log selection path: %s", e)

    return selected
