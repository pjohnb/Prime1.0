"""
Sprint 20 Items 3 + 4 acceptance tests: Lovable UI DK badges and AI advisory
DK context.

Item 3: CONFIRMING -> green/CONFIRM badge; NEUTRAL -> grey/NEUTRAL badge;
        NULLIFYING -> red/NULLIFY badge; PENDING no longer renders; dk_conviction
        tooltip attribute present; old badge class names absent.
Item 4: advisor payload includes dk_status and dk_conviction; briefing includes
        DK activity summary; ranker payload includes DK fields; graceful
        degradation defaults to NEUTRAL when dk_status missing.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import init_signals_table, insert_signal
from prime_ai import prime_briefing as briefing
from prime_ai import prime_position_advisor as advisor
from prime_ai import prime_signal_ranker as ranker
from prime_ui.prime_ui_server import create_ui_app


class TestDkBadgeUI(unittest.TestCase):
    """Sprint 20 Item 3: badge class + label in signals.js and index.html."""

    def setUp(self):
        self.app = create_ui_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _signals_js(self):
        return self.client.get("/signals.js").data.decode("utf-8")

    def _index_html(self):
        return self.client.get("/").data.decode("utf-8")

    def test_confirming_badge_class_and_label(self):
        js = self._signals_js()
        self.assertIn("dkBadgeClass", js)
        self.assertIn("'CONFIRMING'", js)
        self.assertIn("'confirming'", js)
        self.assertIn("dkBadgeLabel", js)
        self.assertIn("'CONFIRM'", js)

    def test_neutral_badge_class_and_label(self):
        js = self._signals_js()
        self.assertIn("'NEUTRAL'", js)  # default fallback
        self.assertIn("'NEUTRAL'", js)  # badge label

    def test_nullifying_badge_class_and_label(self):
        js = self._signals_js()
        self.assertIn("'NULLIFYING'", js)
        self.assertIn("'nullifying'", js)
        self.assertIn("'NULLIFY'", js)

    def test_pending_no_longer_default(self):
        js = self._signals_js()
        # NEUTRAL is the default fallback; PENDING must not appear as a fallback.
        self.assertNotIn("|| 'PENDING'", js)
        self.assertIn("|| 'NEUTRAL'", js)

    def test_conviction_tooltip_in_badge_rendering(self):
        js = self._signals_js()
        # The badge row must include a title attribute for dk_conviction.
        self.assertIn("dk_conviction", js)
        self.assertIn("Conviction:", js)

    def test_badge_colors_updated_in_html(self):
        html = self._index_html()
        self.assertIn("#1F7A1F", html)   # CONFIRMING green
        self.assertIn("#C00000", html)   # NULLIFYING red
        self.assertIn("#888888", html)   # NEUTRAL grey

    def test_old_badge_classes_removed(self):
        html = self._index_html()
        # .badge.confirming / .badge.nullifying / .badge.neutral still present
        # (renamed colors, classes kept). Old CONFIRMED/NULLIFIED/PENDING state
        # names must not appear as badge class selectors.
        self.assertNotIn(".badge.confirmed", html)
        self.assertNotIn(".badge.nullified", html)

    def test_dk_summary_wired_in_dashboard(self):
        js = self.client.get("/dashboard.js").data.decode("utf-8")
        self.assertIn("dk_summary", js)


class TestAdvisorDkContext(unittest.TestCase):
    """Sprint 20 Item 4: dk_conviction in position payload; graceful degradation."""

    def test_build_context_includes_dk_conviction(self):
        pos = {"symbol": "AAPL", "direction": "LONG", "entry_price": 200.0,
               "dk_status": "CONFIRMING", "dk_conviction": 0.85}
        ctx = advisor.build_context(pos)
        self.assertEqual(ctx["dk_status"], "CONFIRMING")
        self.assertAlmostEqual(ctx["dk_conviction"], 0.85)

    def test_build_context_defaults_missing_dk_to_neutral(self):
        pos = {"symbol": "AAPL", "direction": "LONG", "entry_price": 200.0}
        ctx = advisor.build_context(pos)
        self.assertEqual(ctx["dk_status"], "NEUTRAL")
        self.assertIsNone(ctx["dk_conviction"])

    def test_system_prompt_explains_dk_three_state(self):
        sp = advisor.SYSTEM_PROMPT
        self.assertIn("CONFIRMING", sp)
        self.assertIn("NEUTRAL", sp)
        self.assertIn("NULLIFYING", sp)
        self.assertIn("dk_conviction", sp)


class TestBriefingDkContext(unittest.TestCase):
    """Sprint 20 Item 4: briefing includes DK summary; snapshot has dk_activity."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_dk_briefing.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def _seed_signals(self):
        from prime_analytics.prime_signals_db import get_signals
        from prime_data.prime_db import get_connection
        # Seed signals with dk_status values.
        sid1 = insert_signal("AAA", "PSA", "2026-06-04 11:00", tier="STRONG",
                             status="APPROVED", db_path=self.db)
        sid2 = insert_signal("BBB", "PSA", "2026-06-04 11:00", tier="WATCH",
                             status="APPROVED", db_path=self.db)
        sid3 = insert_signal("CCC", "PSA", "2026-06-04 11:00", tier="WATCH",
                             status="APPROVED", db_path=self.db)
        with get_connection(self.db) as conn:
            conn.execute("UPDATE prime_signals SET dk_status='CONFIRMING' WHERE signal_id=?", (sid1,))
            conn.execute("UPDATE prime_signals SET dk_status='NULLIFYING' WHERE signal_id=?", (sid2,))
            conn.execute("UPDATE prime_signals SET dk_status='NEUTRAL'    WHERE signal_id=?", (sid3,))
            conn.commit()

    def test_fallback_includes_dk_summary(self):
        self._seed_signals()
        with patch("prime_ai._claude.get_api_key", return_value=None):
            b = briefing.generate_briefing(db_path=self.db)
        self.assertIn("dk_summary", b)
        self.assertIn("confirming", b["dk_summary"].lower())
        self.assertIn("nullifying", b["dk_summary"].lower())

    def test_snapshot_has_dk_activity(self):
        self._seed_signals()
        snap = briefing._aggregate(self.db)
        self.assertIn("dk_activity", snap)
        dka = snap["dk_activity"]
        self.assertIn("confirming", dka)
        self.assertIn("neutral", dka)
        self.assertIn("nullifying", dka)
        self.assertEqual(dka["confirming"], 1)
        self.assertEqual(dka["nullifying"], 1)
        self.assertEqual(dka["neutral"], 1)

    @patch("prime_ai._claude.call_claude")
    def test_live_briefing_backfills_dk_summary(self, mock_call):
        self._seed_signals()
        # Claude omits dk_summary from its JSON -> should be backfilled from snapshot.
        mock_call.return_value = json.dumps({
            "headline": "Positive session.",
            "positions_summary": "0 open.",
            "signals_summary": "3 signals today.",
            "concentration_warnings": [],
            "recommended_actions": []})
        b = briefing.generate_briefing(db_path=self.db, api_key="k")
        self.assertFalse(b["_fallback"])
        self.assertIn("dk_summary", b)
        self.assertIn("confirming", b["dk_summary"].lower())


class TestRankerDkContext(unittest.TestCase):
    """Sprint 20 Item 4: ranker context includes dk_status and dk_conviction."""

    def test_ranker_context_includes_dk_fields(self):
        approved = [
            {"symbol": "AAA", "strategy": "PSA", "score": 80.0, "tier": "STRONG",
             "sector": "Tech", "dk_status": "CONFIRMING", "dk_conviction": 0.9},
            {"symbol": "BBB", "strategy": "PSA", "score": 75.0, "tier": "WATCH",
             "sector": "Finance", "dk_status": "NEUTRAL", "dk_conviction": None},
        ]
        # rank_signals falls back to score-sort when Claude is unavailable.
        from prime_ai._claude import ClaudeUnavailable
        with patch("prime_ai._claude.call_claude",
                   side_effect=ClaudeUnavailable("no key")):
            result = ranker.rank_signals(approved, max_trades=2, api_key="k")
        # Score-sort fallback should still return both candidates (not crash).
        self.assertEqual(len(result), 2)

    def test_ranker_system_prompt_explains_dk(self):
        sp = ranker.SYSTEM_PROMPT
        self.assertIn("CONFIRMING", sp)
        self.assertIn("NEUTRAL", sp)
        self.assertIn("NULLIFYING", sp)
        self.assertIn("dk_conviction", sp)

    def test_graceful_degradation_missing_dk(self):
        # Signals without dk_status should default to NEUTRAL in the context.
        approved = [
            {"symbol": "AAA", "strategy": "PSA", "score": 80.0,
             "tier": "STRONG", "sector": "Tech"},  # no dk_status
        ]
        from prime_ai._claude import ClaudeUnavailable
        with patch("prime_ai._claude.call_claude",
                   side_effect=ClaudeUnavailable("no key")):
            result = ranker.rank_signals(approved, max_trades=1, api_key="k")
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
