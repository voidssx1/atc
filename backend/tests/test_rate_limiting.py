import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.rate_limit import (
    RateLimitMiddleware,
    extract_client_ip,
    resolve_rate_limit_policy,
)
from app.main import app
from app.api.deps import get_db


def test_rate_limit_policy_resolution():
    assert resolve_rate_limit_policy("POST", "/api/v1/auth/clerk/exchange", "/api/v1") == (
        "RL_AUTH_EXCHANGE",
        "auth_exchange",
        60,
    )
    assert resolve_rate_limit_policy("POST", "/api/v1/auth/refresh", "/api/v1") == (
        "RL_AUTH_REFRESH",
        "auth_refresh",
        60,
    )
    assert resolve_rate_limit_policy("POST", "/api/v1/analytics/click", "/api/v1") == (
        "RL_ANALYTICS",
        "analytics",
        60,
    )
    assert resolve_rate_limit_policy("POST", "/api/v1/analytics/event", "/api/v1") == (
        "RL_ANALYTICS",
        "analytics",
        60,
    )
    assert resolve_rate_limit_policy("POST", "/api/v1/webhooks/clerk", "/api/v1") == (
        "RL_WEBHOOKS",
        "webhooks_clerk",
        60,
    )
    # Unprotected endpoints and OPTIONS preflight are not matched
    assert resolve_rate_limit_policy("GET", "/health", "/api/v1") is None
    assert resolve_rate_limit_policy("GET", "/api/v1/roadmap/topics", "/api/v1") is None


def test_extract_client_ip_precedence():
    scope_cf = {
        "headers": [
            (b"x-forwarded-for", b"198.51.100.99, 10.0.0.1"),
            (b"cf-connecting-ip", b"203.0.113.10"),
        ],
        "client": ("127.0.0.1", 12345),
    }
    assert extract_client_ip(scope_cf) == "203.0.113.10"

    scope_xff = {
        "headers": [(b"x-forwarded-for", b"198.51.100.5, 10.0.0.2")],
        "client": ("127.0.0.1", 12345),
    }
    assert extract_client_ip(scope_xff) == "198.51.100.5"

    scope_client = {"headers": [], "client": ("192.0.2.4", 9999)}
    assert extract_client_ip(scope_client) == "192.0.2.4"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path,binding_name,expected_key_prefix,body",
    [
        (
            "/api/v1/auth/clerk/exchange",
            "RL_AUTH_EXCHANGE",
            "auth_exchange",
            {"token": "dummy"},
        ),
        (
            "/api/v1/auth/refresh",
            "RL_AUTH_REFRESH",
            "auth_refresh",
            {"session_token": "dummy"},
        ),
        (
            "/api/v1/analytics/click",
            "RL_ANALYTICS",
            "analytics",
            {"resource_id": str(uuid.uuid4())},
        ),
        (
            "/api/v1/analytics/event",
            "RL_ANALYTICS",
            "analytics",
            {"resource_id": str(uuid.uuid4())},
        ),
        (
            "/api/v1/webhooks/clerk",
            "RL_WEBHOOKS",
            "webhooks_clerk",
            {"type": "user.updated"},
        ),
    ],
)
async def test_protected_endpoints_enforce_429_with_cors(
    path, binding_name, expected_key_prefix, body
):
    recorded_keys = []

    async def fake_limit(payload):
        recorded_keys.append(payload["key"])
        return SimpleNamespace(success=False)

    worker_env = {
        "ENVIRONMENT": "development",
        "AUTH_MODE": "hybrid",
        "ALLOWED_ORIGINS": "https://atc-dsa.pages.dev",
        binding_name: SimpleNamespace(limit=fake_limit),
    }

    async def env_injecting_app(scope, receive, send):
        scope["env"] = worker_env
        await app(scope, receive, send)

    transport = ASGITransport(app=env_injecting_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            path,
            json=body,
            headers={
                "Origin": "https://atc-dsa.pages.dev",
                "CF-Connecting-IP": "203.0.113.42",
            },
        )

    assert resp.status_code == 429
    assert resp.json()["detail"] == "Too many requests. Please try again later."
    assert resp.headers.get("retry-after") == "60"
    assert resp.headers.get("access-control-allow-origin") == "https://atc-dsa.pages.dev"
    assert recorded_keys == [f"{expected_key_prefix}:203.0.113.42"]


@pytest.mark.anyio
async def test_rate_limit_allows_when_success_true_and_fails_open_on_binding_error(db_session):
    calls = []

    async def allow_limit(payload):
        calls.append(payload["key"])
        return {"success": True}

    async def broken_limit(payload):
        raise RuntimeError("Edge rate limiter unavailable")

    worker_env = SimpleNamespace(
        ENVIRONMENT="development",
        AUTH_MODE="hybrid",
        ALLOWED_ORIGINS="https://atc-dsa.pages.dev",
        RL_AUTH_EXCHANGE=SimpleNamespace(limit=allow_limit),
        RL_AUTH_REFRESH=SimpleNamespace(limit=broken_limit),
    )

    async def env_injecting_app(scope, receive, send):
        scope["cloudflare"] = {"env": worker_env}
        await app(scope, receive, send)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=env_injecting_app)
    try:
      async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Allowed request reaches auth handler (fails JWT check with 401, not 429)
        r_allow = await ac.post(
            "/api/v1/auth/clerk/exchange",
            json={"token": "malformed"},
            headers={"CF-Connecting-IP": "198.51.100.11"},
        )
        assert r_allow.status_code == 401
        assert calls == ["auth_exchange:198.51.100.11"]

        # Failing limiter fails open to route handler (401 invalid session, not 500/429)
        r_broken = await ac.post(
            "/api/v1/auth/refresh",
            json={"session_token": "nonexistent"},
            headers={"CF-Connecting-IP": "198.51.100.11"},
        )
        assert r_broken.status_code == 401

        # Unprotected endpoint & OPTIONS preflight bypass rate limit
        r_health = await ac.get("/health")
        assert r_health.status_code == 200
        r_opt = await ac.options(
            "/api/v1/auth/clerk/exchange",
            headers={
                "Origin": "https://atc-dsa.pages.dev",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r_opt.status_code == 200
    finally:
      app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_rate_limit_middleware_passes_through_without_binding():
    downstream = AsyncMock()
    mw = RateLimitMiddleware(downstream)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/clerk/exchange",
        "headers": [],
        "env": {},
    }
    receive = MagicMock()
    send = MagicMock()
    await mw(scope, receive, send)
    downstream.assert_awaited_once_with(scope, receive, send)
