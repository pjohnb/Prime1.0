"""
Sprint 13 Item 2 (CIL-TS-001) acceptance tests -- TS Token Refresh.
Covers single refresh on cold start, no refresh when token valid,
refresh on 401, refresh count metric in digest footer.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_trading.prime_ts_auth import (
    REFRESH_BUFFER_SECONDS,
    TSTokenManager,
    get_refresh_count,
    reset_refresh_count,
)


class TestTSTokenManager(unittest.TestCase):

    def setUp(self):
        self.mgr = TSTokenManager()

    def test_cold_start_refreshes_once(self):
        refresh_fn = MagicMock(return_value=("token-A", 1200))
        token = self.mgr.get_token(refresh_fn)
        self.assertEqual(token, "token-A")
        self.assertEqual(self.mgr.refresh_count, 1)
        refresh_fn.assert_called_once()

    def test_valid_token_no_refresh(self):
        refresh_fn = MagicMock(return_value=("token-A", 1200))
        self.mgr.get_token(refresh_fn)
        refresh_fn.reset_mock()
        token2 = self.mgr.get_token(refresh_fn)
        self.assertEqual(token2, "token-A")
        refresh_fn.assert_not_called()
        self.assertEqual(self.mgr.refresh_count, 1)

    def test_no_refresh_fn_returns_none(self):
        token = self.mgr.get_token(refresh_fn=None)
        self.assertIsNone(token)
        self.assertEqual(self.mgr.refresh_count, 0)

    def test_force_refresh_increments_count(self):
        refresh_fn = MagicMock(return_value=("token-A", 1200))
        self.mgr.get_token(refresh_fn)
        self.assertEqual(self.mgr.refresh_count, 1)
        refresh_fn.return_value = ("token-B", 1200)
        self.mgr.force_refresh(refresh_fn)
        self.assertEqual(self.mgr.refresh_count, 2)

    def test_refresh_failure_returns_none(self):
        refresh_fn = MagicMock(return_value=("", 0))
        token = self.mgr.get_token(refresh_fn)
        self.assertIsNone(token)

    def test_call_with_retry_401_triggers_refresh(self):
        refresh_fn = MagicMock(return_value=("token-A", 1200))
        # First API call returns 401, second returns 200
        responses = [MagicMock(status_code=401), MagicMock(status_code=200, json=lambda: {"ok": True})]
        api_call = MagicMock(side_effect=responses)

        result = self.mgr.call_with_retry(api_call, refresh_fn)

        self.assertEqual(result.status_code, 200)
        # One get_token call + one force_refresh = 2 refreshes
        self.assertEqual(self.mgr.refresh_count, 2)
        self.assertEqual(api_call.call_count, 2)

    def test_call_with_retry_no_401_no_extra_refresh(self):
        refresh_fn = MagicMock(return_value=("token-A", 1200))
        api_call = MagicMock(return_value=MagicMock(status_code=200))
        self.mgr.call_with_retry(api_call, refresh_fn)
        self.assertEqual(self.mgr.refresh_count, 1)
        self.assertEqual(api_call.call_count, 1)

    def test_refresh_buffer_60s(self):
        # Per WO: refresh only when within 60s of expiry
        self.assertEqual(REFRESH_BUFFER_SECONDS, 60)

    def test_reset_count(self):
        refresh_fn = MagicMock(return_value=("token-A", 1200))
        self.mgr.get_token(refresh_fn)
        self.mgr.reset_count()
        self.assertEqual(self.mgr.refresh_count, 0)

    def test_expired_token_refreshes(self):
        from datetime import timedelta
        refresh_fn = MagicMock(return_value=("token-A", 1200))
        self.mgr.get_token(refresh_fn)
        # Force expiry by overwriting internal state to just past buffer
        self.mgr._expiry = self.mgr._expiry - timedelta(seconds=1500)
        refresh_fn.return_value = ("token-B", 1200)
        token = self.mgr.get_token(refresh_fn)
        self.assertEqual(token, "token-B")
        self.assertEqual(self.mgr.refresh_count, 2)


class TestDigestTokenRefreshCount(unittest.TestCase):
    """AC: token_refresh_count visible in digest footer."""

    def setUp(self):
        reset_refresh_count()

    def tearDown(self):
        reset_refresh_count()

    def test_count_in_digest_dict(self):
        from prime_notifications.prime_digest import assemble_digest
        digest, _ = assemble_digest("uoa_scanner", [], [])
        self.assertIn("token_refresh_count", digest)
        self.assertEqual(digest["token_refresh_count"], 0)

    def test_count_in_digest_text(self):
        from prime_notifications.prime_digest import assemble_digest
        _, text = assemble_digest("uoa_scanner", [], [])
        self.assertIn("TS token refreshes:", text)

    def test_count_reflects_refreshes(self):
        from prime_trading.prime_ts_auth import get_manager
        from prime_notifications.prime_digest import assemble_digest
        mgr = get_manager()
        mgr.reset_count()
        mgr.get_token(lambda: ("tok", 1200))
        mgr.force_refresh(lambda: ("tok2", 1200))
        digest, _ = assemble_digest("uoa_scanner", [], [])
        self.assertEqual(digest["token_refresh_count"], 2)


if __name__ == "__main__":
    unittest.main()
