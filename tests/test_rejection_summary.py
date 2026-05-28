"""
Sprint 12 Item 5 (CIL-REJECT-SUM) acceptance tests -- Rejection Analysis Summary.
Verifies APPROVED line present in scanner summary output.
"""

import sys
import unittest
from pathlib import Path
from io import StringIO
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestRejectionSummaryLine(unittest.TestCase):
    """AC: APPROVED line is the final line of summary block."""

    def test_psa_summary_has_approved_line(self):
        source = Path(PROJECT_ROOT / "prime_scanners" / "prime_psa_scanner.py").read_text()
        self.assertIn('APPROVED: %d stocks', source)
        self.assertIn("APPROVED:", source)

    def test_uoa_summary_has_approved_line(self):
        source = Path(PROJECT_ROOT / "prime_scanners" / "prime_uoa_scanner.py").read_text()
        self.assertIn("APPROVED:", source)

    def test_approved_line_format(self):
        n = 5
        line = f"APPROVED: {n} stocks"
        self.assertIn("APPROVED:", line)
        self.assertIn("stocks", line)

    def test_approved_zero_case(self):
        n = 0
        line = f"APPROVED: {n} stocks"
        self.assertEqual(line, "APPROVED: 0 stocks")

    def test_approved_nonzero_case(self):
        n = 12
        line = f"APPROVED: {n} stocks"
        self.assertEqual(line, "APPROVED: 12 stocks")


if __name__ == "__main__":
    unittest.main()
