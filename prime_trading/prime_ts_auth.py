"""
PRIME v1.0 TradeStation Token Manager (CIL-TS-001).

Eliminates excessive TS token refresh calls:
  (a) Cache token with expiry timestamp
  (b) Refresh only when within REFRESH_BUFFER_SECONDS of expiry or on 401
  (c) Expose token_refresh_count metric for digest footer

TradeStation is legacy. This module covers residual TS API calls only.
Schwab/TOS is the primary broker.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

TS_AUTH_URL = "https://signin.tradestation.com/oauth/token"
REFRESH_BUFFER_SECONDS = 60  # Refresh when within 60s of expiry (per WO)
DEFAULT_EXPIRES_IN = 1200  # 20 minutes


class TSTokenManager:
    """Thread-safe TS token manager with caching and 401 retry."""

    def __init__(self):
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._expiry: Optional[datetime] = None
        self._refresh_count: int = 0

    @property
    def refresh_count(self) -> int:
        return self._refresh_count

    def reset_count(self) -> None:
        with self._lock:
            self._refresh_count = 0

    def _token_is_fresh(self) -> bool:
        """True if cached token exists and is not within REFRESH_BUFFER of expiry."""
        if not self._token or not self._expiry:
            return False
        return datetime.now() < self._expiry - timedelta(seconds=REFRESH_BUFFER_SECONDS)

    def get_token(self, refresh_fn: Optional[Callable[[], Tuple[str, int]]] = None) -> Optional[str]:
        """Return current token, refreshing only if needed.

        refresh_fn: callable returning (access_token, expires_in_seconds)
                    Called only when token is stale or absent.
        """
        with self._lock:
            if self._token_is_fresh():
                return self._token
            if refresh_fn is None:
                return None
            return self._do_refresh(refresh_fn)

    def force_refresh(self, refresh_fn: Callable[[], Tuple[str, int]]) -> Optional[str]:
        """Force a refresh (called on 401 response). Always increments count."""
        with self._lock:
            return self._do_refresh(refresh_fn)

    def _do_refresh(self, refresh_fn: Callable[[], Tuple[str, int]]) -> Optional[str]:
        """Internal refresh (assumes lock held). Increments counter."""
        try:
            token, expires_in = refresh_fn()
            if not token:
                logger.warning("TS token refresh returned empty token")
                return None
            self._token = token
            self._expiry = datetime.now() + timedelta(
                seconds=expires_in if expires_in > 0 else DEFAULT_EXPIRES_IN
            )
            self._refresh_count += 1
            logger.info("TS token refreshed (count=%d, expires in %ds)",
                        self._refresh_count, expires_in)
            return token
        except Exception as e:
            logger.error("TS token refresh failed: %s", e)
            return None

    def call_with_retry(
        self,
        api_call: Callable[[str], Any],
        refresh_fn: Callable[[], Tuple[str, int]],
    ) -> Any:
        """Execute api_call(token); on 401, force_refresh and retry once.

        api_call must return a response-like object with .status_code attribute.
        """
        token = self.get_token(refresh_fn)
        if not token:
            return None
        resp = api_call(token)
        status = getattr(resp, "status_code", None)
        if status == 401:
            logger.info("TS 401 received -- forcing token refresh and retry")
            token = self.force_refresh(refresh_fn)
            if token:
                resp = api_call(token)
        return resp


# Module-level singleton
_manager = TSTokenManager()


def get_manager() -> TSTokenManager:
    return _manager


def get_refresh_count() -> int:
    """Public accessor for the digest footer metric."""
    return _manager.refresh_count


def reset_refresh_count() -> None:
    _manager.reset_count()


def build_ts_refresh_fn():
    """Build a refresh callable bound to current config. Returns (token, expires_in)."""
    import requests
    from prime_config.prime_config import get_config

    def refresh() -> Tuple[str, int]:
        cfg = get_config()
        ts = cfg.tradestation
        if not ts.client_id or not ts.refresh_token:
            return ("", 0)
        r = requests.post(TS_AUTH_URL, data={
            "grant_type": "refresh_token",
            "client_id": ts.client_id,
            "client_secret": ts.client_secret,
            "refresh_token": ts.refresh_token,
        }, timeout=10)
        if r.status_code != 200:
            logger.error("TS token refresh failed: HTTP %s", r.status_code)
            return ("", 0)
        body = r.json()
        return (body.get("access_token", ""), int(body.get("expires_in", DEFAULT_EXPIRES_IN)))

    return refresh
