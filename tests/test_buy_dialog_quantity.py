"""
WO-PRIME-BUY-DIALOG-QUANTITY-01 acceptance tests.

Verifies that the Execute Buy Signal dialog includes a share quantity field,
that the confirm button is disabled until qty > 0, and that the qty value
is passed through to the API where it overrides the auto-computed share count.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

INDEX_HTML  = (PROJECT_ROOT / "prime_ui" / "index.html").read_text(encoding="utf-8")
SIGNALS_JS  = (PROJECT_ROOT / "prime_ui" / "signals.js").read_text(encoding="utf-8")
ROUTES_SRC  = (PROJECT_ROOT / "prime_api" / "prime_api_routes.py").read_text(encoding="utf-8")


class TestBuyDialogQtyHTML(unittest.TestCase):

    def test_qty_input_exists_in_modal(self):
        self.assertIn('id="buy-signal-qty"', INDEX_HTML,
                      "buy-signal-qty input must exist in the buy modal")

    def test_qty_input_type_number(self):
        m = re.search(r'id="buy-signal-qty"[^>]*type="number"'
                      r'|type="number"[^>]*id="buy-signal-qty"', INDEX_HTML)
        self.assertIsNotNone(m, "buy-signal-qty must be type=number")

    def test_qty_row_before_staged_row(self):
        qty_pos    = INDEX_HTML.index('id="buy-signal-qty-row"')
        staged_pos = INDEX_HTML.index('id="buy-signal-staged-row"')
        self.assertLess(qty_pos, staged_pos,
                        "Qty row must appear before staged-entry row in the modal")

    def test_qty_input_calls_update_confirm(self):
        m = re.search(r'id="buy-signal-qty"[^>]*oninput="updateBuyConfirmBtn\(\)"'
                      r'|oninput="updateBuyConfirmBtn\(\)"[^>]*id="buy-signal-qty"', INDEX_HTML)
        self.assertIsNotNone(m, "buy-signal-qty must call updateBuyConfirmBtn() on input")


class TestBuyDialogQtyJS(unittest.TestCase):

    def test_update_buy_confirm_btn_function_exists(self):
        self.assertIn("function updateBuyConfirmBtn()", SIGNALS_JS,
                      "updateBuyConfirmBtn must be defined in signals.js")

    def test_update_buy_confirm_btn_disables_on_zero(self):
        self.assertIn("confirmBtn.disabled", SIGNALS_JS,
                      "updateBuyConfirmBtn must set confirmBtn.disabled")

    def test_submit_buy_signal_reads_qty(self):
        self.assertIn("buy-signal-qty", SIGNALS_JS,
                      "submitBuySignal must read buy-signal-qty element")

    def test_submit_buy_signal_includes_qty_in_payload(self):
        self.assertIn("qty: qty", SIGNALS_JS,
                      "submitBuySignal payload must include qty field")

    def test_submit_buy_signal_validates_qty(self):
        self.assertIn("Share quantity is required", SIGNALS_JS,
                      "submitBuySignal must show error when qty is missing/zero")

    def test_close_modal_resets_qty(self):
        close_start = SIGNALS_JS.index("function closeBuySignalModal()")
        close_body  = SIGNALS_JS[close_start:SIGNALS_JS.index("\n}", close_start) + 2]
        self.assertIn("buy-signal-qty", close_body,
                      "closeBuySignalModal must reset buy-signal-qty")

    def test_open_modal_resets_qty(self):
        open_start = SIGNALS_JS.index("function openBuySignalConfirm(")
        open_body  = SIGNALS_JS[open_start:SIGNALS_JS.index("\n}", open_start) + 2]
        self.assertIn("buy-signal-qty", open_body,
                      "openBuySignalConfirm must reset buy-signal-qty")

    def test_open_modal_calls_update_confirm(self):
        open_start = SIGNALS_JS.index("function openBuySignalConfirm(")
        open_body  = SIGNALS_JS[open_start:SIGNALS_JS.index("\n}", open_start) + 2]
        self.assertIn("updateBuyConfirmBtn()", open_body,
                      "openBuySignalConfirm must call updateBuyConfirmBtn() to disable confirm initially")


class TestBuyDialogQtyAPI(unittest.TestCase):

    def test_user_qty_read_from_payload(self):
        self.assertIn("user_qty", ROUTES_SRC,
                      "execute endpoint must read user_qty from payload")
        self.assertIn('payload.get("qty")', ROUTES_SRC,
                      'execute endpoint must call payload.get("qty")')

    def test_live_branch_uses_user_qty(self):
        # The LIVE branch must prefer user_qty over auto-compute
        self.assertIn("user_qty if user_qty > 0 else", ROUTES_SRC,
                      "LIVE branch must use user_qty when provided, else fall back to auto-compute")

    def test_paper_branch_uses_user_qty(self):
        # Count occurrences — there should be two (LIVE + PAPER)
        count = ROUTES_SRC.count("user_qty if user_qty > 0 else")
        self.assertEqual(count, 2,
                         "Both LIVE and PAPER branches must use user_qty when provided")

    def test_user_qty_zero_does_not_override(self):
        """user_qty=0 must fall through to auto-compute (not force 0 shares)."""
        import re
        # When user_qty == 0 the expression must use the auto-compute path.
        # Static check: both branches use "user_qty if user_qty > 0 else <auto>"
        auto_patterns = re.findall(
            r"user_qty if user_qty > 0 else int\(", ROUTES_SRC
        )
        self.assertEqual(len(auto_patterns), 2,
                         "Both LIVE and PAPER share-count lines must fall back to auto-compute when user_qty==0")

    def test_user_qty_negative_clamped_to_zero(self):
        """Negative qty from payload must be clamped to 0 (treated as absent)."""
        self.assertIn("user_qty < 0", ROUTES_SRC,
                      "Negative user_qty must be clamped to 0")


if __name__ == "__main__":
    unittest.main()
