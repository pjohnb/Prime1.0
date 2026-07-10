"""
WO-PRIME-SIGNALS-TOOLTIPS-01 acceptance tests.

All 10 Signals tab column headers must carry data-tooltip attributes with
the specific copy mandated by the work order.  The Tier column's existing
(?) help-icon span is preserved for backward compatibility with
test_tier_tooltip.py.

AC6 (edge-collision) uses a JS floating-div approach: getBoundingClientRect()
measures the rendered tooltip and clamps it within the viewport on both edges.
The old CSS nth-child static-anchor approach is intentionally removed.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML  = (PROJECT_ROOT / "prime_ui" / "index.html").read_text(encoding="utf-8")
SIGNALS_JS  = (PROJECT_ROOT / "prime_ui" / "signals.js").read_text(encoding="utf-8")


def _get_th_tooltip(html: str, header_text: str) -> str:
    """Return the data-tooltip value for a <th> whose visible text starts with header_text."""
    pattern = rf'<th\s[^>]*data-tooltip="([^"]*)"[^>]*>{re.escape(header_text)}'
    m = re.search(pattern, html)
    if not m:
        return ""
    return m.group(1)


class TestSignalsColumnTooltips(unittest.TestCase):
    # AC 1: all 10 headers have data-tooltip
    def test_all_headers_have_tooltip(self):
        for col in ("Time", "Symbol", "Score", "Strategy", "Trigger",
                    "Tier", "DK Status", "Entry", "Status", "Actions"):
            with self.subTest(col=col):
                self.assertIn(
                    f'>{col}' if col not in ("Tier",) else f'>{col} ',
                    INDEX_HTML,
                    msg=f"header text '{col}' not found",
                )
                pattern = rf'<th[^>]+data-tooltip="[^"]+"[^>]*>{re.escape(col)}'
                self.assertRegex(INDEX_HTML, pattern,
                                 msg=f"column '{col}' is missing data-tooltip")

    # AC 2: Tier tooltip — revised copy (AC4 sign-off)
    def test_tier_tooltip_two_condition_gate(self):
        tip = _get_th_tooltip(INDEX_HTML, "Tier")
        self.assertIn("83.3", tip, "Tier tooltip must cite Score >= 83.3")
        self.assertIn("120%", tip, "Tier tooltip must cite 120% volume threshold")
        self.assertIn("both required", tip,
                      "Tier tooltip must state both conditions are required")
        self.assertIn("does not guarantee STRONG tier", tip,
                      "Tier tooltip must state high Score alone does not guarantee STRONG tier")
        self.assertIn("volume does not confirm", tip,
                      "Tier tooltip must reference volume confirmation failure")

    # AC 2: Status tooltip — revised copy (AC4 sign-off)
    def test_status_tooltip_approved_not_volume_confirmed(self):
        tip = _get_th_tooltip(INDEX_HTML, "Status")
        self.assertIn("all screening gates", tip,
                      "Status tooltip must say 'all screening gates'")
        self.assertIn("APPROVED does not mean volume-confirmed", tip,
                      "Status tooltip must clarify APPROVED != volume-confirmed")
        self.assertIn("WEAK tier signal can still be APPROVED", tip,
                      "Status tooltip must note WEAK tier signals can be APPROVED")
        self.assertIn("REJECTED_STAGE0", tip,
                      "Status tooltip must mention REJECTED_STAGE0")

    # AC 3: Score tooltip notes strategy-specific calculation and non-comparability
    def test_score_tooltip_non_comparability(self):
        tip = _get_th_tooltip(INDEX_HTML, "Score")
        self.assertIn("not comparable across strategies", tip,
                      "Score tooltip must state scores are not cross-strategy comparable")
        self.assertIn("7 discrete values", tip,
                      "Score tooltip must note only 7 discrete values are possible")

    # AC 5: tooltip CSS uses max-width >= 320px
    def test_tooltip_css_max_width(self):
        m = re.search(r'max-width:\s*(\d+)px', INDEX_HTML)
        self.assertIsNotNone(m, "data-tooltip CSS max-width rule not found")
        self.assertGreaterEqual(int(m.group(1)), 320,
                                "Tooltip max-width must be at least 320px")

    # AC 5: tooltip transition includes 300ms delay
    def test_tooltip_css_delay(self):
        m = re.search(r'transition:\s*opacity[^;]+;', INDEX_HTML)
        self.assertIsNotNone(m, "tooltip transition rule not found")
        self.assertIn("0.3s", m.group(0), "tooltip must have 0.3s (300ms) transition delay")

    # AC 6: edge-collision fix — JS floating div with getBoundingClientRect clamping.
    # The old CSS nth-child static-anchor approach is replaced; these tests verify
    # the JS implementation is present and correct.

    def test_edge_collision_js_tooltip_div_in_html(self):
        """A #sig-col-tip CSS block must exist for the JS-created floating div."""
        self.assertIn("#sig-col-tip", INDEX_HTML,
                      "#sig-col-tip CSS rule must be present for the JS tooltip div")
        self.assertIn("position: fixed", INDEX_HTML,
                      "#sig-col-tip must use position:fixed for viewport-relative placement")

    def test_edge_collision_sig_header_pseudo_suppressed(self):
        """::after pseudo must be suppressed on sig-table headers (JS replaces it)."""
        self.assertIn(
            "#sig-table thead th[data-tooltip]::after { display: none; }",
            INDEX_HTML,
            "CSS must suppress ::after on sig-table headers so only the JS tooltip renders",
        )

    def test_edge_collision_js_function_exists(self):
        self.assertIn("function initSigColTooltips()", SIGNALS_JS,
                      "initSigColTooltips() must be defined in signals.js")

    def test_edge_collision_js_uses_get_bounding_client_rect(self):
        self.assertIn("getBoundingClientRect()", SIGNALS_JS,
                      "initSigColTooltips must call getBoundingClientRect() to measure tooltip size")

    def test_edge_collision_js_clamps_left_overflow(self):
        self.assertIn("left < MARGIN", SIGNALS_JS,
                      "initSigColTooltips must detect and clamp left-edge overflow")
        self.assertIn("left = MARGIN", SIGNALS_JS,
                      "initSigColTooltips must snap tooltip to left margin when it would overflow")

    def test_edge_collision_js_clamps_right_overflow(self):
        self.assertIn("left > maxLeft", SIGNALS_JS,
                      "initSigColTooltips must detect and clamp right-edge overflow")
        self.assertIn("left = maxLeft", SIGNALS_JS,
                      "initSigColTooltips must snap tooltip to maxLeft when it would overflow right")

    def test_edge_collision_old_css_rules_absent(self):
        """Regression: the insufficient nth-child CSS rules must be gone."""
        self.assertNotIn(
            "nth-child(-n+2)[data-tooltip]::after",
            INDEX_HTML,
            "Old static CSS left-anchor rule must be removed (replaced by JS clamping)",
        )
        self.assertNotIn(
            "nth-last-child(-n+2)[data-tooltip]::after",
            INDEX_HTML,
            "Old static CSS right-anchor rule must be removed (replaced by JS clamping)",
        )

    # backward compat: existing tier-help-icon span preserved
    def test_tier_help_icon_preserved(self):
        self.assertIn('id="tier-help-icon"', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
