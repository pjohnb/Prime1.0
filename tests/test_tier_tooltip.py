"""
Sprint 29 SIG-02 acceptance tests.

The TIER column header carries a (?) help icon whose native tooltip explains the
two-part [Strength]-[Direction] naming convention. Pure-UI change, so these tests
assert the tooltip markup and required content are present in index.html.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

INDEX_HTML = (PROJECT_ROOT / "prime_ui" / "index.html").read_text(encoding="utf-8")


class TestTierTooltip(unittest.TestCase):
    def test_help_icon_present_on_tier_header(self):
        self.assertIn('id="tier-help-icon"', INDEX_HTML)
        # The icon sits inside the signals table header cell, right after "Tier".
        self.assertRegex(INDEX_HTML, r'Tier\s*<span id="tier-help-icon"')

    def test_tooltip_explains_two_part_convention(self):
        # Extract the icon's title attribute and check the required content.
        start = INDEX_HTML.index('id="tier-help-icon"')
        title_start = INDEX_HTML.index('title="', start) + len('title="')
        title = INDEX_HTML[title_start:INDEX_HTML.index('"', title_start)]
        for phrase in (
            "[Strength]-[Direction]",
            "STRONG", "WATCH", "WEAK", "TRANCHE_1", "TRANCHE_2",
            "LONG", "SHORT",
            "high conviction", "monitor closely", "informational only",
            "scaling in", "DIR column",
        ):
            self.assertIn(phrase, title, msg=f"tooltip missing: {phrase}")

    def test_icon_uses_help_cursor(self):
        start = INDEX_HTML.index('id="tier-help-icon"')
        tag = INDEX_HTML[start:INDEX_HTML.index(">", start)]
        self.assertIn("cursor:help", tag)


if __name__ == "__main__":
    unittest.main()
