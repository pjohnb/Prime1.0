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

    # AC 2: Tier tooltip states the two-condition gate
    def test_tier_tooltip_two_condition_gate(self):
        tip = _get_th_tooltip(INDEX_HTML, "Tier")
        self.assertIn("83.3", tip, "Tier tooltip must cite Score >= 83.3")
        self.assertIn("120%", tip, "Tier tooltip must cite 120% volume threshold")
        self.assertIn("WEAK", tip, "Tier tooltip must mention WEAK tier")
        self.assertIn("volume does not confirm", tip,
                      "Tier tooltip must state high Score alone does not guarantee STRONG tier")

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

    # backward compat: existing tier-help-icon span preserved
    def test_tier_help_icon_preserved(self):
        self.assertIn('id="tier-help-icon"', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
