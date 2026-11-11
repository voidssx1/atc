import inspect
import logging
from typing import Any, Dict, Optional, Tuple

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings

logger = logging.getLogger("rate_limit")

try:
    import js
    from pyodide.ffi import JsProxy, to_js

    _HAS_PYODIDE = True
except ImportError:
    js = None
    JsProxy = None
    to_js = None
    _HAS_PYODIDE = False

# Relative endpoint policies: (method, suffix) -> (binding_name, key_prefix, period_seconds)
_RATE_LIMIT_POLICIES: Dict[Tuple[str, str], Tuple[str, str, int]] = {
    ("POST", "/auth/clerk/exchange"): ("RL_AUTH_EXCHANGE", "auth_exchange", 60),
    ("POST", "/auth/refresh"): ("RL_AUTH_REFRESH", "auth_refresh", 60),
    ("POST", "/analytics/click"): ("RL_ANALYTICS", "analytics", 60),
    ("POST", "/analytics/event"): ("RL_ANALYTICS", "analytics", 60),
    ("POST", "/webhooks/clerk"): ("RL_WEBHOOKS", "webhooks_clerk", 60),
}


def resolve_rate_limit_policy(
    method: str, path: str, api_prefix: Optional[str] = None
) -> Optional[Tuple[str, str, int]]:
    """Return `(binding_name, key_prefix, period_seconds)` for rate-limited endpoints."""
    prefix = (api_prefix or get_settings().API_V1_STR or "").rstrip("/")
    normalized_path = path.rstrip("/") or "/"
    if prefix and normalized_path.startswith(prefix):
        rel_path = normalized_path[len(prefix) :] or "/"
    else:
        rel_path = normalized_path
    return _RATE_LIMIT_POLICIES.get((method.upper(), rel_path))


def _decode_header(scope: Scope, header_name: bytes) -> Optional[str]:
    for raw_k, raw_v in scope.get("headers") or ():
        if raw_k.lower() == header_name:
            try:
                val = raw_v.decode("latin-1").strip()
                if val:
                    return val
            except Exception:
                return None
    return None


def extract_client_ip(scope: Scope) -> str:
    """
    Extract client IP preferring Cloudflare's edge-injected `CF-Connecting-IP` header.
    """
    cf_ip = _decode_header(scope, b"cf-connecting-ip")
    if cf_ip:
        return cf_ip

    real_ip = _decode_header(scope, b"x-real-ip")
    if real_ip:
        return real_ip

    xff = _decode_header(scope, b"x-forwarded-for")
    if xff:
        first_hop = xff.split(",", 1)[0].strip()
        if first_hop:
            return first_hop

    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client and client[0]:
        return str(client[0])

    return "unknown"


def _extract_env_from_scope(scope: Scope) -> Any:
    env = scope.get("env")
    if env is None and isinstance(scope.get("cloudflare"), dict):
        env = scope["cloudflare"].get("env")
    return env


def _get_binding(env: Any, name: str) -> Any:
    if env is None:
        return None
    if isinstance(env, dict):
        return env.get(name)
    try:
        return getattr(env, name)
    except AttributeError:
        return None


async def check_cloudflare_rate_limit(limiter: Any, key: str) -> bool:
    """
    Evaluate a Cloudflare Workers `[[ratelimits]]` binding (`limiter.limit({"key": key})`).
    Fails open on unexpected telemetry exceptions while strictly rejecting when `success` is False.
    """
    if limiter is None:
        return True
    limit_fn = getattr(limiter, "limit", None)
    if not callable(limit_fn):
        return True

    try:
        payload: Any = {"key": key}
        if _HAS_PYODIDE and JsProxy is not None and isinstance(limiter, JsProxy):
            payload = to_js(payload, dict_converter=js.Object.fromEntries)

        result = limit_fn(payload)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, dict):
            return bool(result.get("success", True))
        return bool(getattr(result, "success", True))
    except Exception as exc:
        logger.warning(f"Rate limiter check error ({type(exc).__name__}); failing open")
        return True


class RateLimitMiddleware:
    """
    ASGI middleware enforcing Cloudflare Workers native `[[ratelimits]]` bindings on
    high-risk auth, webhook, and public analytics ingestion endpoints.

    Registered inside DynamicCORSMiddleware so 429 responses retain CORS headers, and
    inside WorkerEnvMiddleware so per-request Worker env & API_V1_STR are already bound.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "GET").upper()
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "/")
        policy = resolve_rate_limit_policy(method, path)
        if policy is None:
            await self.app(scope, receive, send)
            return

        env = _extract_env_from_scope(scope)
        binding_name, key_prefix, period_seconds = policy
        limiter = _get_binding(env, binding_name)
        if limiter is not None:
            client_ip = extract_client_ip(scope)
            rate_key = f"{key_prefix}:{client_ip}"
            allowed = await check_cloudflare_rate_limit(limiter, rate_key)
            if not allowed:
                resp = JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please try again later."},
                    headers={"Retry-After": str(period_seconds)},
                )
                await resp(scope, receive, send)
                return

        await self.app(scope, receive, send)
