"""
Sprint 32 Thread 2 acceptance tests (PM-HEALTH-04, PM-HEALTH-05).

Pure-frontend changes, so these tests assert the required markup and logic are
present in index.html / health.js / portfolio.js / settings.js — consistent
with the existing UI test pattern (e.g. test_tier_tooltip.py).
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_UI = PROJECT_ROOT / "prime_ui"
INDEX_HTML = (_UI / "index.html").read_text(encoding="utf-8")
HEALTH_JS = (_UI / "health.js").read_text(encoding="utf-8")
PORTFOLIO_JS = (_UI / "portfolio.js").read_text(encoding="utf-8")
SETTINGS_JS = (_UI / "settings.js").read_text(encoding="utf-8")


class TestHealthNav(unittest.TestCase):
    def test_health_tab_between_portfolio_and_history(self):
        self.assertRegex(
            INDEX_HTML,
            r"showView\('portfolio'\)\">Portfolio</div>\s*"
            r"<div class=\"tab\" id=\"health-tab-btn\" onclick=\"showView\('health'\)\">Health</div>\s*"
            r"<div class=\"tab\" onclick=\"showView\('history'\)\">History</div>",
        )

    def test_health_js_included(self):
        self.assertIn('<script src="health.js"></script>', INDEX_HTML)

    def test_view_health_section_present(self):
        self.assertIn('id="view-health"', INDEX_HTML)
        self.assertIn('id="health-summary"', INDEX_HTML)
        self.assertIn('id="health-rows"', INDEX_HTML)

    def test_showview_wires_health(self):
        self.assertIn("if (id === 'health') { loadHealth(); _startHealthAutoRefresh(); }", INDEX_HTML)
        # Auto-refresh is stopped when leaving any view.
        self.assertIn("_stopHealthAutoRefresh()", INDEX_HTML)

    def test_thesis_header_tooltip(self):
        for phrase in (
            "GREEN = thesis intact",
            "AMBER = thesis unconfirmed",
            "RED = reversal detected",
            "UNKNOWN = monitor not yet run",
        ):
            self.assertIn(phrase, INDEX_HTML, msg=f"missing thesis tooltip phrase: {phrase}")


class TestHealthJs(unittest.TestCase):
    def test_core_functions_defined(self):
        for fn in (
            "function loadHealth", "function _renderHealthSummary",
            "function _renderHealthRows", "function _thesisBadge",
            "function _updateTabBadge", "function _startHealthAutoRefresh",
            "function _stopHealthAutoRefresh", "function _isRTH",
        ):
            self.assertIn(fn, HEALTH_JS, msg=f"missing: {fn}")

    def test_fetches_health_endpoint(self):
        self.assertIn("/positions/health", HEALTH_JS)

    def test_thesis_badge_colors(self):
        # GREEN / AMBER / RED / UNKNOWN palette per the work order.
        for hexcode in ("#052e16", "#86efac", "#451a03", "#fde68a",
                        "#7f1d1d", "#fca5a5", "#1a1a2e", "#888888"):
            self.assertIn(hexcode, HEALTH_JS, msg=f"missing thesis color: {hexcode}")

    def test_autorefresh_five_minutes_and_rth(self):
        self.assertIn("300000", HEALTH_JS)
        self.assertIn("setInterval", HEALTH_JS)
        self.assertIn("clearInterval", HEALTH_JS)
        # RTH window 09:30–16:00, weekdays only.
        self.assertIn("9 * 60 + 30", HEALTH_JS)
        self.assertIn("16 * 60", HEALTH_JS)
        self.assertRegex(HEALTH_JS, r"day === 0 \|\| day === 6")

    def test_tab_badge_toggles_on_red_count(self):
        # Adds *N when red>0, resets to plain 'Health' when 0.
        self.assertRegex(HEALTH_JS, r"redCount > 0")
        self.assertIn("*${redCount}", HEALTH_JS)
        self.assertRegex(HEALTH_JS, r"btn\.innerHTML = 'Health'")

    def test_empty_state_message(self):
        self.assertIn("No open positions to monitor.", HEALTH_JS)


class TestPortfolioBanner(unittest.TestCase):
    def test_banner_container_present(self):
        self.assertIn('id="portfolio-health-banner"', INDEX_HTML)

    def test_render_banner_function(self):
        self.assertIn("function _renderHealthBanner", PORTFOLIO_JS)
        self.assertIn("/positions/health", PORTFOLIO_JS)

    def test_banner_called_after_load(self):
        # loadPortfolio invokes the banner re-evaluation.
        self.assertIn("_renderHealthBanner()", PORTFOLIO_JS)

    def test_banner_links_to_health_and_dismissable(self):
        self.assertIn("showView('health')", PORTFOLIO_JS)
        self.assertIn("reversal signals", PORTFOLIO_JS)
        self.assertIn("function _dismissHealthBanner", PORTFOLIO_JS)

    def test_banner_only_when_red(self):
        self.assertRegex(PORTFOLIO_JS, r"red > 0")

    def test_banner_silent_degrade_on_error(self):
        # The catch branch hides the banner rather than surfacing an error.
        m = re.search(r"catch \(e\) \{\s*_dismissHealthBanner\(\)", PORTFOLIO_JS)
        self.assertIsNotNone(m, "health banner must degrade silently on error")


class TestSettingsToggle(unittest.TestCase):
    def test_position_monitor_action_field(self):
        self.assertIn("position_monitor_action", SETTINGS_JS)
        self.assertIn("Alert Only", SETTINGS_JS)
        self.assertIn("Auto-Sell", SETTINGS_JS)

    def test_toggle_tooltip(self):
        self.assertIn("immediate MATA sell across all accounts", SETTINGS_JS)

    def test_saved_in_payload(self):
        self.assertIn("payload.position_monitor_action = _v('position_monitor_action')", SETTINGS_JS)


if __name__ == "__main__":
    unittest.main()
