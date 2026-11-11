"""Shared outbound HTTP client helpers avoiding repeated SSLContext / CA store loading."""

import ssl
from typing import Any, Optional

import httpx

_SHARED_SSL_CONTEXT: Optional[ssl.SSLContext] = None


def get_shared_ssl_context() -> Optional[ssl.SSLContext]:
    """Lazily construct and reuse one SSLContext per Worker isolate."""
    global _SHARED_SSL_CONTEXT
    if _SHARED_SSL_CONTEXT is None:
        try:
            _SHARED_SSL_CONTEXT = ssl.create_default_context()
        except Exception:
            return None
    return _SHARED_SSL_CONTEXT


def create_async_http_client(**kwargs: Any) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient reusing the isolate's cached SSLContext."""
    if "verify" not in kwargs:
        ssl_ctx = get_shared_ssl_context()
        if ssl_ctx is not None:
            kwargs["verify"] = ssl_ctx
    return httpx.AsyncClient(**kwargs)
