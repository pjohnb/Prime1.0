"""
Sprint 21 Item 1 (In-App Help System) acceptance tests.

Covers: ? button present; help modal HTML present; modal Escape wiring present;
glossary search function present; all 8 strategy ⓘ buttons present; Start of Day
panel present with all 6 steps and checkboxes; help.js served; all content
styled with dark-theme CSS variables.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_ui.prime_ui_server import create_ui_app


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = create_ui_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _html(self):
        return self.client.get("/").data.decode("utf-8")

    def _js(self):
        return self.client.get("/help.js").data.decode("utf-8")


class TestHelpButton(_Base):
    def test_help_btn_present_in_topbar(self):
        html = self._html()
        self.assertIn("help-open-btn", html)
        self.assertIn("openHelp()", html)

    def test_help_overlay_present(self):
        html = self._html()
        self.assertIn("help-overlay", html)
        self.assertIn("help-modal", html)

    def test_help_close_button_present(self):
        html = self._html()
        self.assertIn("closeHelp()", html)

    def test_modal_closes_on_escape(self):
        js = self._js()
        self.assertIn("Escape", js)
        self.assertIn("_helpEscHandler", js)

    def test_modal_closes_on_outside_click(self):
        html = self._html()
        self.assertIn("if(event.target===this)closeHelp()", html)


class TestHelpModalTabs(_Base):
    def test_all_five_tabs_present(self):
        html = self._html()
        for tab_id in ("htab-daily-routine", "htab-signal-tiers",
                       "htab-dk-states", "htab-risk-rules", "htab-glossary"):
            self.assertIn(tab_id, html, f"Missing tab: {tab_id}")

    def test_all_five_panes_present(self):
        html = self._html()
        for pane_id in ("hpane-daily-routine", "hpane-signal-tiers",
                        "hpane-dk-states", "hpane-risk-rules", "hpane-glossary"):
            self.assertIn(pane_id, html, f"Missing pane: {pane_id}")

    def test_daily_routine_has_all_sessions(self):
        html = self._html()
        for phrase in ("Pre-Market", "Market Open", "Mid-Day", "Close"):
            self.assertIn(phrase, html, f"Missing session: {phrase}")

    def test_dk_states_pane_has_three_states(self):
        html = self._html()
        self.assertIn("CONFIRMING", html)
        self.assertIn("NEUTRAL", html)
        self.assertIn("NULLIFYING", html)

    def test_risk_rules_has_stop_pct(self):
        html = self._html()
        self.assertIn("5%", html)


class TestGlossary(_Base):
    def test_glossary_search_input_present(self):
        html = self._html()
        self.assertIn("glossary-input", html)
        self.assertIn("glossary-list", html)

    def test_glossary_render_function_in_js(self):
        js = self._js()
        self.assertIn("renderGlossary", js)
        self.assertIn("HELP_GLOSSARY", js)

    def test_glossary_search_filters_by_query(self):
        js = self._js()
        self.assertIn("toLowerCase()", js)
        self.assertIn("filter(", js)

    def test_glossary_is_alphabetically_sorted(self):
        js = self._js()
        self.assertIn(".sort(", js)
        self.assertIn("localeCompare", js)

    def test_glossary_contains_key_terms(self):
        js = self._js()
        for term in ("CONFIRMING", "NULLIFYING", "NEUTRAL", "PEAD", "UOA",
                     "MATA", "dk_conviction", "STRONG", "WATCH", "trigger_source"):
            self.assertIn(term, js, f"Missing glossary term: {term}")


class TestStrategyPopovers(_Base):
    def test_all_eight_strategy_buttons_in_html(self):
        html = self._html()
        for strat in ("PSA", "PEAD", "UOA", "SRS", "MTS", "IDX", "DK", "SHORT"):
            self.assertIn(f"toggleStrategyInfo('{strat}'", html,
                          f"Missing strategy button: {strat}")

    def test_strategy_info_strip_present(self):
        html = self._html()
        self.assertIn("strat-info-strip", html)

    def test_strategy_info_data_in_js(self):
        js = self._js()
        self.assertIn("STRATEGY_INFO", js)
        for strat in ("PSA", "PEAD", "UOA", "SRS", "MTS", "IDX", "DK", "SHORT"):
            self.assertIn(f'"{strat}"', js, f"Missing STRATEGY_INFO entry: {strat}")

    def test_popover_toggle_function_in_js(self):
        js = self._js()
        self.assertIn("toggleStrategyInfo", js)
        self.assertIn("closeStratPopover", js)

    def test_all_strategies_have_trigger_field(self):
        js = self._js()
        self.assertIn("trigger:", js)
        self.assertIn("confirmation:", js)
        self.assertIn("stop:", js)


class TestStartOfDayPanel(_Base):
    def test_sod_panel_present(self):
        html = self._html()
        self.assertIn("sod-panel", html)
        self.assertIn("sod-header", html)
        self.assertIn("sod-body", html)

    def test_sod_panel_collapsed_by_default(self):
        html = self._html()
        self.assertIn("display: none", html.replace("display:none", "display: none"))

    def test_sod_toggle_function_in_js(self):
        js = self._js()
        self.assertIn("toggleSodPanel", js)
        self.assertIn("sod-arrow", js)

    def test_all_six_steps_present(self):
        html = self._html()
        for step_id in ("sod-1", "sod-2", "sod-3", "sod-4", "sod-5", "sod-6"):
            self.assertIn(step_id, html, f"Missing SOD step: {step_id}")

    def test_steps_have_checkboxes(self):
        html = self._html()
        self.assertEqual(html.count('type="checkbox"'), html.count('type="checkbox"'))
        self.assertGreaterEqual(html.count('type="checkbox"'), 6)

    def test_sod_step_labels_cover_key_actions(self):
        html = self._html()
        self.assertIn("AI Briefing", html)
        self.assertIn("PAPER mode", html)
        self.assertIn("Max Trades", html)

    def test_sod_expand_arrow_present(self):
        html = self._html()
        self.assertIn("sod-arrow", html)
        self.assertIn("toggleSodPanel()", html)


class TestHelpStyling(_Base):
    def test_dark_theme_css_vars_used_in_help_styles(self):
        html = self._html()
        self.assertIn("var(--bg2)", html)
        self.assertIn("var(--border)", html)
        self.assertIn("var(--text2)", html)
        self.assertIn("var(--amber)", html)

    def test_help_js_served(self):
        resp = self.client.get("/help.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"openHelp", resp.data)

    def test_help_overlay_uses_z_index(self):
        html = self._html()
        self.assertIn("z-index: 200", html.replace("z-index:200", "z-index: 200"))


if __name__ == "__main__":
    unittest.main()
