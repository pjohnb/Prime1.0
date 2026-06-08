"""Sprint 26 Item 1: AI Position Advisor correctly handles SCHWAB_IMPORT positions."""
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(trade_source="SCHWAB_IMPORT", symbol="AAPL", direction="LONG",
                   shares=100, entry_price=150.0, stop_price=142.5, strategy=None):
    return {
        "log_id": "test-1",
        "symbol": symbol,
        "direction": direction,
        "shares": shares,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "trade_source": trade_source,
        "strategy": strategy or trade_source,
        "status": "OPEN",
    }


def _make_enriched(pos):
    """Simulate what enrich_position returns."""
    ep = {
        "current_price": pos["entry_price"] * 1.02,
        "unrealized_pnl": pos["entry_price"] * 0.02 * pos["shares"],
        "unrealized_pnl_pct": 2.0,
        "hold_minutes": 60,
        "stop_badge": "GREEN",
        "stop_price": pos.get("stop_price"),
    }
    ep.update(pos)
    return ep


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAdvisorSchawbImportEnrichment:

    def test_enrich_called_for_schwab_import(self):
        """advise_positions must call enrich_position on SCHWAB_IMPORT rows."""
        from prime_ai import prime_position_advisor as adv

        pos = _make_position()
        enriched = _make_enriched(pos)

        with patch("prime_data.prime_db.get_open_positions", return_value=[pos]), \
             patch("prime_api.prime_positions.enrich_position",
                   return_value=enriched) as mock_enrich, \
             patch.object(adv, "advise_one",
                          return_value={"recommendation": "HOLD", "reasoning": "ok"}):
            result = adv.advise_positions(api_key="fake")

        mock_enrich.assert_called_once()
        assert len(result) == 1

    def test_enrich_called_for_paper_position(self):
        """enrich_position is also called for regular PAPER positions."""
        from prime_ai import prime_position_advisor as adv

        pos = _make_position(trade_source="PAPER", strategy="UOA")
        enriched = _make_enriched(pos)

        with patch("prime_data.prime_db.get_open_positions", return_value=[pos]), \
             patch("prime_api.prime_positions.enrich_position",
                   return_value=enriched) as mock_enrich, \
             patch.object(adv, "advise_one",
                          return_value={"recommendation": "HOLD", "reasoning": "ok"}):
            adv.advise_positions(api_key="fake")

        mock_enrich.assert_called_once()

    def test_build_context_includes_trade_source(self):
        """build_context must include trade_source in the context dict."""
        from prime_ai.prime_position_advisor import build_context
        enriched = _make_enriched(_make_position())
        ctx = build_context(enriched)
        assert "trade_source" in ctx
        assert ctx["trade_source"] == "SCHWAB_IMPORT"

    def test_build_context_includes_stop_price(self):
        """build_context must include stop_price when set."""
        from prime_ai.prime_position_advisor import build_context
        enriched = _make_enriched(_make_position(stop_price=142.5))
        ctx = build_context(enriched)
        assert "stop_price" in ctx
        assert ctx["stop_price"] == 142.5

    def test_system_prompt_mentions_schwab_import(self):
        """SYSTEM_PROMPT must acknowledge SCHWAB_IMPORT positions."""
        from prime_ai.prime_position_advisor import SYSTEM_PROMPT
        assert "SCHWAB_IMPORT" in SYSTEM_PROMPT

    def test_no_positions_returns_empty(self):
        """advise_positions returns [] when there are no open positions."""
        from prime_ai import prime_position_advisor as adv

        with patch("prime_data.prime_db.get_open_positions", return_value=[]):
            result = adv.advise_positions(api_key="fake")

        assert result == []
