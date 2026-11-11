import base64
import hashlib
import json
import logging
import zlib
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import _IncludedRouter
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.router import api_router
from app.api.v1.health import router as health_router
from app.core.config import (
    get_settings,
    settings,
    worker_settings_context,
)
from app.core.rate_limit import RateLimitMiddleware
from app.db.session import open_db_session
from app.services.scheduler import job_runner

logger = logging.getLogger("app_main")


class DynamicCORSMiddleware(CORSMiddleware):
    """CORS middleware evaluating per-request allowed origins from active Worker/local settings."""

    def is_allowed_origin(self, origin: str) -> bool:
        allowed = get_settings().get_allowed_origins()
        return origin in allowed


class WorkerEnvMiddleware:
    """
    Outermost ASGI middleware binding Cloudflare Worker `env` variables/secrets
    into an isolated request-scoped ContextVar without mutating global settings.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        env = scope.get("env")
        if env is None and isinstance(scope.get("cloudflare"), dict):
            env = scope["cloudflare"].get("env")

        if env is None:
            await self.app(scope, receive, send)
            return

        try:
            with worker_settings_context(env):
                await self.app(scope, receive, send)
        except ValueError as e:
            logger.error(f"Worker environment configuration validation failed: {e}")
            resp = JSONResponse(
                status_code=500,
                content={"detail": "Server configuration validation error"},
            )
            await resp(scope, receive, send)


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
)

# Register RateLimitMiddleware innermost, then DynamicCORSMiddleware (so 429 responses
# still receive CORS headers), then WorkerEnvMiddleware outermost so request `env` is
# bound before rate limiting, CORS, and route dependencies evaluate settings.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    DynamicCORSMiddleware,
    allow_origins=(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(WorkerEnvMiddleware)

def _extract_subrouter_prefixes(
    sub_router: _IncludedRouter, combined_prefix: str
) -> Optional[Tuple[str, ...]]:
    """Derive safe static HTTP path prefixes for a sub-router without building dependants."""
    if sub_router.original_router.prefix:
        clean = combined_prefix.rstrip("/")
        return (clean,) if clean else None

    parent_base = combined_prefix.rstrip("/")
    prefixes: list[str] = []
    for child in sub_router.original_router.routes:
        if isinstance(child, _IncludedRouter):
            return None
        child_path = getattr(child, "path", "")
        if not child_path or child_path.startswith("/{") or child_path == "/":
            return None
        static_seg = child_path.split("{", 1)[0].rstrip("/")
        if not static_seg:
            return None
        prefixes.append(f"{parent_base}{static_seg}")
    return tuple(dict.fromkeys(prefixes)) if prefixes else None


class _PrefixGuardedIncludedRouter(_IncludedRouter):
    """Skip non-matching sub-router prefixes before FastAPI builds route dependants lazily."""

    def __init__(
        self,
        *args: Any,
        allowed_prefixes: Optional[Tuple[str, ...]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._allowed_prefixes = allowed_prefixes

    def matches(self, scope: Scope) -> Tuple[Match, Scope]:
        if (
            self._allowed_prefixes
            and scope.get("type") == "http"
            and not scope.get("root_path")
        ):
            path = scope.get("path") or ""
            if not any(
                path == pfx or path.startswith(f"{pfx}/")
                for pfx in self._allowed_prefixes
            ):
                return Match.NONE, {}
        return super().matches(scope)


def _install_prefix_guarded_api_router(
    fastapi_app: FastAPI, router_obj: Any, *, prefix: str
) -> None:
    """Include `api_router` and flatten child `_IncludedRouter`s with prefix guards."""
    fastapi_app.include_router(router_obj, prefix=prefix)
    api_inc = next(
        (
            r
            for r in fastapi_app.router.routes
            if isinstance(r, _IncludedRouter) and r.original_router is router_obj
        ),
        None,
    )
    if api_inc is None:
        return

    # Only flatten when every entry in `api_router` is itself an included sub-router
    if not router_obj.routes or not all(
        isinstance(child, _IncludedRouter) for child in router_obj.routes
    ):
        return

    guarded: list[Any] = []
    for child in router_obj.routes:
        assert isinstance(child, _IncludedRouter)
        combined_ctx = api_inc.include_context.combine(child.include_context)
        allowed = _extract_subrouter_prefixes(child, combined_ctx.prefix)
        guarded.append(
            _PrefixGuardedIncludedRouter(
                original_router=child.original_router,
                include_context=combined_ctx,
                allowed_prefixes=allowed,
            )
        )

    rebuilt_routes: list[Any] = []
    for r in fastapi_app.router.routes:
        if r is api_inc:
            rebuilt_routes.extend(guarded)
        else:
            rebuilt_routes.append(r)
    fastapi_app.router.routes[:] = rebuilt_routes


def _collect_route_signatures(
    routes: Any, prefix: str = ""
) -> list[Tuple[str, Tuple[str, ...], str]]:
    sigs: list[Tuple[str, Tuple[str, ...], str]] = []
    for route in routes:
        if isinstance(route, _IncludedRouter):
            sub_prefix = f"{prefix}{route.include_context.prefix}"
            sigs.extend(
                _collect_route_signatures(route.original_router.routes, prefix=sub_prefix)
            )
        elif hasattr(route, "path") and hasattr(route, "methods"):
            methods = tuple(sorted(getattr(route, "methods", None) or ()))
            sigs.append(
                (f"{prefix}{route.path}", methods, getattr(route, "name", "") or "")
            )
    return sigs


def compute_openapi_signature(fastapi_app: FastAPI) -> str:
    """Compute a fast deterministic signature of registered routes & app OpenAPI metadata."""
    payload = {
        "title": fastapi_app.title,
        "version": fastapi_app.version,
        "openapi_url": fastapi_app.openapi_url,
        "routes": sorted(_collect_route_signatures(fastapi_app.routes)),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Root-level health endpoint
app.include_router(health_router)

# Versioned API routes guarded by static sub-router prefixes (avoids eager 86-route cold build)
_install_prefix_guarded_api_router(app, api_router, prefix=settings.API_V1_STR)


def _lazy_custom_openapi() -> Dict[str, Any]:
    """
    Serve OpenAPI schema lazily using pre-compressed snapshot when routes match,
    avoiding ~2s/3.4MB cold runtime schema generation while falling back transparently
    to `get_openapi()` if routes are dynamically mutated.
    """
    routes_version = app.router._get_routes_version()
    if app.openapi_schema is not None and app._openapi_routes_version == routes_version:
        return app.openapi_schema

    try:
        from app._openapi_snapshot import (
            OPENAPI_SNAPSHOT_B85,
            OPENAPI_SNAPSHOT_SIGNATURE,
        )

        if OPENAPI_SNAPSHOT_SIGNATURE == compute_openapi_signature(app):
            app.openapi_schema = json.loads(
                zlib.decompress(base64.b85decode(OPENAPI_SNAPSHOT_B85)).decode("utf-8")
            )
            app._openapi_routes_version = routes_version
            return app.openapi_schema
    except Exception:
        pass

    app.openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        summary=app.summary,
        description=app.description,
        terms_of_service=app.terms_of_service,
        contact=app.contact,
        license_info=app.license_info,
        routes=app.routes,
        webhooks=app.webhooks.routes,
        tags=app.openapi_tags,
        servers=app.servers,
        separate_input_output_schemas=app.separate_input_output_schemas,
        external_docs=app.openapi_external_docs,
    )
    app._openapi_routes_version = routes_version
    return app.openapi_schema


app.openapi = _lazy_custom_openapi  # type: ignore[method-assign]

# Cloudflare Python Workers SDK Entrypoint (fetch + scheduled)
try:
    from workers import WorkerEntrypoint, asgi

    _HAS_CLOUDFLARE_WORKERS = True
except ImportError:
    _HAS_CLOUDFLARE_WORKERS = False
    asgi = None

    class WorkerEntrypoint:  # type: ignore[no-redef]
        env = None
        ctx = None


class Default(WorkerEntrypoint):
    """Cloudflare Python Worker entrypoint supporting FastAPI ASGI HTTP and Cron Triggers."""

    async def fetch(self, request):
        if asgi is None:
            raise RuntimeError("Cloudflare 'workers' SDK is required for Worker fetch execution")
        return await asgi.fetch(app, request, self.env, getattr(self, "ctx", None))

    async def scheduled(self, controller, env=None, ctx=None):
        worker_env = env if env is not None else getattr(self, "env", None)
        cron_expr = getattr(controller, "cron", None)
        try:
            with worker_settings_context(worker_env):
                with open_db_session(env=worker_env) as db:
                    return await job_runner.run_scheduled_batch(db, cron_expr=cron_expr)
        except Exception as e:
            logger.error(f"Scheduled Worker execution failed: {type(e).__name__}")
            return {"skipped": False, "errors": [type(e).__name__]}
