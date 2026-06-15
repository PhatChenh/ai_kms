"""
tests/test_mcp_server/test_api_key_cache.py

P9-E-02: API Key Read-Once — tests that _daemon_api_key caches the env var
and require_key() does not re-read os.environ on every call.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request


def _make_request(bearer_token: str | None) -> Request:
    """Build a minimal Starlette Request with an optional Authorization header."""
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
    }
    if bearer_token is not None:
        scope["headers"].append(
            (b"authorization", f"Bearer {bearer_token}".encode())
        )
    return Request(scope)


class TestApiKeyReadOnce:
    """P9-MCP-17: _daemon_api_key is read once at module load time."""

    def test_cached_key_used_for_matching_token(self):
        """Set _daemon_api_key directly → require_key matches the bearer token."""
        import mcp_server.api as api

        # Set the module-level cache
        api._daemon_api_key = "test-key-abc"

        request = _make_request("test-key-abc")
        result = api.require_key(request)

        assert result == "test-key-abc"

    def test_cached_key_ignores_env_mutation(self, monkeypatch):
        """After _daemon_api_key is set, mutating os.environ has no effect.

        This proves the key is read-once — require_key uses the cached
        variable, not the current value in os.environ.
        """
        import mcp_server.api as api

        # Set the module-level cache
        api._daemon_api_key = "cached-key"

        # Mutate os.environ to a different value
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "new-key-from-env")

        # A request with the cached key should still be accepted
        request = _make_request("cached-key")
        result = api.require_key(request)
        assert result == "cached-key"

        # A request with the new env value should be REJECTED
        # (proves we are NOT reading from os.environ)
        request_bad = _make_request("new-key-from-env")
        result_bad = api.require_key(request_bad)
        assert result_bad is None

    def test_lazy_fallback_when_cache_is_none(self, monkeypatch):
        """When _daemon_api_key is None, first call reads os.environ and caches it.

        This covers the lazy-init path used when the env var is patched
        after module import (e.g. in tests).
        """
        import mcp_server.api as api

        # Simulate import-time None (env not set)
        api._daemon_api_key = None

        # Now set the env var
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "lazy-key")

        # First call should read from env and cache
        request = _make_request("lazy-key")
        result = api.require_key(request)
        assert result == "lazy-key"
        # Cache should now be populated
        assert api._daemon_api_key == "lazy-key"

        # Mutate env to a different value
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "different-key")

        # Second call must use the cached value, NOT the new env value
        request2 = _make_request("lazy-key")
        result2 = api.require_key(request2)
        assert result2 == "lazy-key"

        # The new env value should be rejected
        request3 = _make_request("different-key")
        result3 = api.require_key(request3)
        assert result3 is None
