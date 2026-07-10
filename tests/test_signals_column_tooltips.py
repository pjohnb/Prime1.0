"""
WO-PRIME-SIGNALS-TOOLTIPS-01 acceptance tests.

All 10 Signals tab column headers must carry data-tooltip attributes with
the specific copy mandated by the work order.  The Tier column's existing
(?) help-icon span is preserved for backward compatibility with
test_tier_tooltip.py.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = (PROJECT_ROOT / "prime_ui" / "index.html").read_text(encoding="utf-8")


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

    # AC 6: edge-collision fix — CSS rules anchor leftmost and rightmost columns
    def test_edge_collision_css_left_anchor(self):
        self.assertIn(
            "#sig-table thead th:nth-child(-n+2)[data-tooltip]::after",
            INDEX_HTML,
            "Left-anchor CSS rule for first two columns not found",
        )
        # Rule must reset transform so the tooltip doesn't re-center off-screen
        left_rule_start = INDEX_HTML.index("#sig-table thead th:nth-child(-n+2)")
        left_rule = INDEX_HTML[left_rule_start:INDEX_HTML.index("}", left_rule_start)]
        self.assertIn("left: 0", left_rule, "Left-anchor rule must set left: 0")
        self.assertIn("transform: none", left_rule, "Left-anchor rule must clear transform")

    def test_edge_collision_css_right_anchor(self):
        self.assertIn(
            "#sig-table thead th:nth-last-child(-n+2)[data-tooltip]::after",
            INDEX_HTML,
            "Right-anchor CSS rule for last two columns not found",
        )
        right_rule_start = INDEX_HTML.index("#sig-table thead th:nth-last-child(-n+2)")
        right_rule = INDEX_HTML[right_rule_start:INDEX_HTML.index("}", right_rule_start)]
        self.assertIn("right: 0", right_rule, "Right-anchor rule must set right: 0")
        self.assertIn("transform: none", right_rule, "Right-anchor rule must clear transform")

    # backward compat: existing tier-help-icon span preserved
    def test_tier_help_icon_preserved(self):
        self.assertIn('id="tier-help-icon"', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
