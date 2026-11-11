import pytest
from unittest.mock import MagicMock
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.db.session import _create_engine_for_url, get_db, get_engine
from app.core.config import parse_database_url


def test_nullpool_for_postgres():
    postgres_url = "postgresql+psycopg://user:pass@ep-sample.us-east-2.aws.neon.tech/neondb"
    eng = _create_engine_for_url(postgres_url)
    assert isinstance(eng.pool, NullPool)


def test_parse_database_url():
    assert parse_database_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert parse_database_url("postgresql://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert parse_database_url("postgresql+psycopg://u:p@h/d") == "postgresql+psycopg://u:p@h/d"


def test_get_db_session_lifecycle():
    # Calling get_db without request yields a Session and closes cleanly
    db_gen = get_db()
    session = next(db_gen)
    assert isinstance(session, Session)
    # Closing the generator closes the session
    try:
        next(db_gen)
    except StopIteration:
        pass


def test_get_db_hyperdrive_binding_resolution():
    mock_request = MagicMock()
    mock_hyperdrive = MagicMock()
    mock_hyperdrive.connectionString = "postgresql://user:pass@hyperdrive.pooler.local:5432/neondb"
    mock_request.scope = {
        "env": MagicMock(HYPERDRIVE=mock_hyperdrive)
    }

    # Should resolve engine with hyperdrive connection string
    db_gen = get_db(mock_request)
    session = next(db_gen)
    assert isinstance(session, Session)
    try:
        next(db_gen)
    except StopIteration:
        pass


def test_lazy_engine_initialization_and_compat_getattr():
    import app.db as db_pkg
    import app.db.session as db_session
    from sqlalchemy import Engine
    from sqlalchemy.orm import sessionmaker

    orig_engines = dict(db_session._engines)
    orig_session_locals = dict(db_session._session_locals)
    try:
        db_session._engines.clear()
        db_session._session_locals.clear()
        assert len(db_session._engines) == 0

        # Backward-compatible attribute access initializes lazily
        eng = db_pkg.engine
        maker = db_pkg.SessionLocal
        assert isinstance(eng, Engine)
        assert isinstance(maker, sessionmaker)
        assert len(db_session._engines) == 1
    finally:
        db_session._engines.clear()
        db_session._engines.update(orig_engines)
        db_session._session_locals.clear()
        db_session._session_locals.update(orig_session_locals)
