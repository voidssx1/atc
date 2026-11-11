import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

# Root-level health endpoint
app.include_router(health_router)

# Versioned API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Pre-build OpenAPI schema and middleware stack at module load so deploy-time Worker
# snapshot captures FastAPI route dependency introspection rather than charging it
# to the first HTTP request's CPU budget.
app.openapi()
app.middleware_stack = app.build_middleware_stack()

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
