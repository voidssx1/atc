from contextlib import contextmanager
from typing import Any, Dict, Generator, Iterator, Optional
from fastapi import Request
from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

from app.core.config import (
    extract_hyperdrive_url,
    get_settings,
    parse_database_url,
)

# Global engine cache keyed by connection string
_engines: Dict[str, Engine] = {}


def _create_engine_for_url(db_url: str) -> Engine:
    """Create a synchronous SQLAlchemy engine using NullPool for serverless/Hyperdrive."""
    normalized_url = parse_database_url(db_url)
    if "sqlite" in normalized_url:
        return create_engine(
            normalized_url,
            connect_args={"check_same_thread": False},
        )
    return create_engine(
        normalized_url,
        poolclass=NullPool,
    )


def get_engine(db_url: Optional[str] = None) -> Engine:
    """Get or create a cached engine for the specified database URL."""
    url = db_url or get_settings().DATABASE_URL
    if url not in _engines:
        _engines[url] = _create_engine_for_url(url)
    return _engines[url]


_session_locals: Dict[str, sessionmaker] = {}


def get_session_maker(db_url: Optional[str] = None) -> sessionmaker:
    """Get or create a cached sessionmaker bound to the resolved engine."""
    target_engine = get_engine(db_url)
    url = db_url or get_settings().DATABASE_URL
    if url not in _session_locals:
        _session_locals[url] = sessionmaker(
            autocommit=False, autoflush=False, bind=target_engine
        )
    return _session_locals[url]


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
    conn_str = extract_hyperdrive_url(env) or get_settings().DATABASE_URL
    target_engine = get_engine(conn_str)
    db = Session(bind=target_engine, autocommit=False, autoflush=False)
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
