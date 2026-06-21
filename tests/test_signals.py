"""
Sprint 33 Thread 2 / CIL-075 -- PEAD signal Dismiss.

Covers the soft-delete dismiss flow: the dismiss_signal() data-layer helper,
the POST /api/v1/signals/{signal_id}/dismiss endpoint (200/404/409), and the
exclusion of DISMISSED signals from the Signals tab query and the analytics
(effectiveness) signal counts -- while the row is preserved for ML training.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_data.prime_db import init_db
from prime_analytics.prime_signals_db import (
    init_signals_table,
    insert_signal,
    get_signals,
    get_analytics_summary,
    dismiss_signal,
)

_TOKEN = "test-token-abc123"


def _mock_config():
    cfg = MagicMock()
    cfg.trading_mode = "PAPER"
    cfg.api_token = _TOKEN
    return cfg


class _SignalsBase(unittest.TestCase):

    _counter = 0

    def setUp(self):
        self.db = Path(__file__).parent / "_test_signals.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)
        self._db_patcher = patch("prime_data.prime_db._db_path", return_value=self.db)
        self._db_patcher.start()

    def tearDown(self):
        self._db_patcher.stop()
        if self.db.exists():
            self.db.unlink()

    def _insert(self, symbol="AAPL", strategy="PEAD", status="APPROVED"):
        _SignalsBase._counter += 1
        ts = f"2026-06-20T10:{_SignalsBase._counter:02d}:00"
        return insert_signal(
            symbol=symbol, strategy=strategy, scan_ts=ts, score=80.0,
            tier="STRONG", status=status, db_path=self.db,
        )


# ---------------------------------------------------------------------------
# Data layer: dismiss_signal + query exclusions
# ---------------------------------------------------------------------------

class TestDismissDataLayer(_SignalsBase):

    def test_dismiss_sets_status(self):
        sid = self._insert()
        self.assertEqual(dismiss_signal(sid, db_path=self.db), "DISMISSED")
        rows = get_signals(status="DISMISSED", db_path=self.db)
        self.assertEqual([r["signal_id"] for r in rows], [sid])
        self.assertEqual(rows[0]["status"], "DISMISSED")

    def test_dismiss_unknown_returns_not_found(self):
        self.assertEqual(dismiss_signal("nope", db_path=self.db), "NOT_FOUND")

    def test_dismiss_twice_returns_already(self):
        sid = self._insert()
        dismiss_signal(sid, db_path=self.db)
        self.assertEqual(dismiss_signal(sid, db_path=self.db), "ALREADY_DISMISSED")

    def test_dismissed_signal_excluded_from_signals_tab(self):
        keep = self._insert(symbol="AAPL")
        drop = self._insert(symbol="TSLA")
        dismiss_signal(drop, db_path=self.db)
        # Default Signals-tab query (no status filter) hides the dismissed row.
        ids = [r["signal_id"] for r in get_signals(db_path=self.db)]
        self.assertIn(keep, ids)
        self.assertNotIn(drop, ids)
        # But the row is preserved and still fetchable by explicit status.
        ids_dismissed = [r["signal_id"] for r in get_signals(status="DISMISSED", db_path=self.db)]
        self.assertEqual(ids_dismissed, [drop])

    def test_dismissed_signal_excluded_from_effectiveness(self):
        self._insert(symbol="AAPL", strategy="PEAD")
        drop = self._insert(symbol="TSLA", strategy="PEAD")
        before = get_analytics_summary(db_path=self.db)["total_signals"]
        dismiss_signal(drop, db_path=self.db)
        after = get_analytics_summary(db_path=self.db)["total_signals"]
        # The dismissed signal drops out of the analytics signal count.
        self.assertEqual(after, before - 1)


# ---------------------------------------------------------------------------
# Endpoint: POST /api/v1/signals/{signal_id}/dismiss
# ---------------------------------------------------------------------------

class TestDismissEndpoint(_SignalsBase):

    def setUp(self):
        super().setUp()
        self._cfg_patcher = patch(
            "prime_config.prime_config.get_config", return_value=_mock_config()
        )
        self._cfg_patcher.start()
        from prime_api.prime_api_server import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self._cfg_patcher.stop()
        super().tearDown()

    def _post(self, signal_id):
        return self.client.post(
            f"/api/v1/signals/{signal_id}/dismiss",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

    def test_dismiss_signal_endpoint(self):
        sid = self._insert()
        resp = self._post(sid)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "DISMISSED")
        rows = get_signals(status="DISMISSED", db_path=self.db)
        self.assertEqual([r["signal_id"] for r in rows], [sid])

    def test_dismiss_unknown_returns_404(self):
        self.assertEqual(self._post("does-not-exist").status_code, 404)

    def test_dismiss_already_dismissed_returns_409(self):
        sid = self._insert()
        self.assertEqual(self._post(sid).status_code, 200)
        self.assertEqual(self._post(sid).status_code, 409)

    def test_dismiss_requires_token(self):
        sid = self._insert()
        resp = self.client.post(f"/api/v1/signals/{sid}/dismiss")  # no Authorization
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
