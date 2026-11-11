from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Generator, Iterator, Optional
from urllib.parse import urlsplit

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import (
    extract_hyperdrive_url,
    get_settings,
    parse_database_url,
)

# Global engine cache keyed by stable structural identity (never rotating tokens)
_engines: Dict[str, Engine] = {}
_session_locals: Dict[str, sessionmaker] = {}

# Request-scoped normalized DSN so a single cached Hyperdrive Engine (with NullPool)
# opens each request's connection using that request's exact ephemeral credentials.
_active_db_url_ctx: ContextVar[Optional[str]] = ContextVar(
    "active_db_url", default=None
)


def _engine_cache_key(normalized_url: str, *, is_hyperdrive: bool = False) -> str:
    """Return a stable cache key that ignores volatile Hyperdrive credentials."""
    if is_hyperdrive:
        return "hyperdrive"
    if "sqlite" in normalized_url:
        return normalized_url
    parts = urlsplit(normalized_url)
    host_part = parts.hostname or ""
    if parts.port:
        host_part = f"{host_part}:{parts.port}"
    return f"{parts.scheme}://{host_part}{parts.path}"


def _install_request_dsn_hook(engine: Engine) -> None:
    """Bind per-request DSN into NullPool connections without sharing live sockets."""

    @event.listens_for(engine, "do_connect")
    def _inject_request_connect_args(dialect, conn_rec, cargs, cparams):
        active_url = _active_db_url_ctx.get()
        if not active_url:
            return
        req_cargs, req_cparams = dialect.create_connect_args(make_url(active_url))
        cargs[:] = req_cargs
        cparams.clear()
        cparams.update(req_cparams)


def _create_engine_for_url(db_url: str) -> Engine:
    """Create a synchronous SQLAlchemy engine using NullPool for serverless/Hyperdrive."""
    normalized_url = parse_database_url(db_url)
    if "sqlite" in normalized_url:
        return create_engine(
            normalized_url,
            connect_args={"check_same_thread": False},
        )
    engine = create_engine(
        normalized_url,
        poolclass=NullPool,
    )
    _install_request_dsn_hook(engine)
    return engine


def get_engine(
    db_url: Optional[str] = None, *, cache_key: Optional[str] = None
) -> Engine:
    """Get or create a cached engine using a stable structural cache key."""
    raw_url = db_url or get_settings().DATABASE_URL
    normalized_url = parse_database_url(raw_url)
    key = cache_key or _engine_cache_key(normalized_url)
    if key not in _engines:
        _engines[key] = _create_engine_for_url(normalized_url)
    return _engines[key]


def get_session_maker(
    db_url: Optional[str] = None, *, cache_key: Optional[str] = None
) -> sessionmaker:
    """Get or create a cached sessionmaker bound to the resolved engine."""
    raw_url = db_url or get_settings().DATABASE_URL
    normalized_url = parse_database_url(raw_url)
    key = cache_key or _engine_cache_key(normalized_url)
    target_engine = get_engine(normalized_url, cache_key=key)
    if key not in _session_locals:
        _session_locals[key] = sessionmaker(
            autocommit=False, autoflush=False, bind=target_engine
        )
    return _session_locals[key]


def __getattr__(name: str) -> Any:
    """Lazily resolve default `engine` and `SessionLocal` without import-time DB init."""
    if name == "engine":
        return get_engine()
    if name == "SessionLocal":
        return get_session_maker()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def resolve_worker_env_from_request(request: Optional[Request]) -> Any:
    """Extract Cloudflare Worker `env` binding from FastAPI Request ASGI scope."""
    if request is None or not hasattr(request, "scope"):
        return None
    scope = request.scope
    env = scope.get("env")
    if env is not None:
        return env
    cf = scope.get("cloudflare")
    if isinstance(cf, dict):
        return cf.get("env")
    return None


@contextmanager
def open_db_session(env: Any = None) -> Iterator[Session]:
    """Open an isolated synchronous Session bound to Worker Hyperdrive `env` or active settings."""
    hd_url = extract_hyperdrive_url(env)
    raw_url = hd_url or get_settings().DATABASE_URL
    normalized_url = parse_database_url(raw_url)
    cache_key = _engine_cache_key(normalized_url, is_hyperdrive=bool(hd_url))
    target_engine = get_engine(normalized_url, cache_key=cache_key)

    token = _active_db_url_ctx.set(normalized_url)
    db = Session(bind=target_engine, autocommit=False, autoflush=False)
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        try:
            db.close()
        finally:
            try:
                _active_db_url_ctx.reset(token)
            except ValueError:
                _active_db_url_ctx.set(None)


def get_db(request: Request = None) -> Generator[Session, None, None]:
    """
    Request-scoped database session dependency.

    In Cloudflare Python Workers:
      Retrieves connection string from request.scope["env"].HYPERDRIVE if present.
    In local/test environments:
      Falls back to active settings DATABASE_URL.
    """
    env = resolve_worker_env_from_request(request)
    with open_db_session(env=env) as db:
        yield db
