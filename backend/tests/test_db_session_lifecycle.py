import asyncio
import types
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import NullPool

from app.db import session as db_session


def _fake_dialect():
    """Minimal PostgreSQL-like dialect implementing `create_connect_args`."""
    dialect = types.SimpleNamespace()

    def create_connect_args(url):
        return (
            [],
            {
                "host": url.host,
                "port": url.port or 5432,
                "dbname": url.database,
                "user": url.username,
                "password": url.password,
            },
        )

    dialect.create_connect_args = create_connect_args
    return dialect


def test_hyperdrive_engine_reused_across_rotating_credentials(monkeypatch):
    """
    Verify rotating HYPERDRIVE.connectionString tokens reuse a single NullPool Engine
    while each request's `do_connect` hook receives that request's fresh credentials.
    """
    created_urls = []
    captured_connect_params = []

    def fake_create_engine(url, **kwargs):
        created_urls.append((url, kwargs))
        eng = create_engine("sqlite:///:memory:")
        db_session._install_request_dsn_hook(eng)
        return eng

    monkeypatch.setattr(db_session, "_engines", {})
    monkeypatch.setattr(db_session, "_session_locals", {})
    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)

    env1 = {"HYPERDRIVE": {"connectionString": "postgresql://u_req1:tok1@hd.local:5432/neondb"}}
    env2 = {"HYPERDRIVE": {"connectionString": "postgresql://u_req2:tok2@hd.local:5432/neondb"}}

    with db_session.open_db_session(env=env1) as s1:
        assert len(created_urls) == 1
        assert created_urls[0][1].get("poolclass") is NullPool
        dialect = _fake_dialect()
        cargs, cparams = [], {}
        s1.bind.dialect.dispatch.do_connect(dialect, None, cargs, cparams)
        captured_connect_params.append((s1, dict(cparams)))

    with db_session.open_db_session(env=env2) as s2:
        # Must reuse cached "hyperdrive" Engine instead of calling create_engine again
        assert len(created_urls) == 1
        assert len(db_session._engines) == 1
        assert s1 is not s2
        dialect = _fake_dialect()
        cargs, cparams = [], {}
        s2.bind.dialect.dispatch.do_connect(dialect, None, cargs, cparams)
        captured_connect_params.append((s2, dict(cparams)))

    assert captured_connect_params[0][1]["user"] == "u_req1"
    assert captured_connect_params[0][1]["password"] == "tok1"
    assert captured_connect_params[1][1]["user"] == "u_req2"
    assert captured_connect_params[1][1]["password"] == "tok2"
    # ContextVar must be reset outside request scope
    assert db_session._active_db_url_ctx.get() is None


def test_concurrent_requests_do_not_share_sessions_or_dsn_context(monkeypatch):
    """Verify concurrent async tasks retain isolated Sessions and request DSNs."""
    created_count = 0

    def fake_create_engine(url, **kwargs):
        nonlocal created_count
        created_count += 1
        eng = create_engine("sqlite:///:memory:")
        db_session._install_request_dsn_hook(eng)
        return eng

    monkeypatch.setattr(db_session, "_engines", {})
    monkeypatch.setattr(db_session, "_session_locals", {})
    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)

    async def worker_task(req_id: str):
        env = {
            "HYPERDRIVE": {
                "connectionString": f"postgresql://user_{req_id}:secret_{req_id}@hd.local:5432/neondb"
            }
        }
        with db_session.open_db_session(env=env) as sess:
            await asyncio.sleep(0.01)
            dialect = _fake_dialect()
            cargs, cparams = [], {}
            sess.bind.dialect.dispatch.do_connect(dialect, None, cargs, cparams)
            return id(sess), cparams["user"], cparams["password"]

    async def run_both():
        return await asyncio.gather(worker_task("A"), worker_task("B"))

    (sid_a, user_a, pw_a), (sid_b, user_b, pw_b) = asyncio.run(run_both())
    assert created_count == 1
    assert sid_a != sid_b
    assert (user_a, pw_a) == ("user_A", "secret_A")
    assert (user_b, pw_b) == ("user_B", "secret_B")
    assert db_session._active_db_url_ctx.get() is None


def test_open_db_session_closes_and_rolls_back_on_error(monkeypatch):
    """Verify Session cleanup (close + rollback on exception) and ContextVar reset."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_session, "_engines", {"sqlite:///:memory:": eng})

    fake_session = MagicMock()
    with patch("app.db.session.Session", return_value=fake_session):
        with patch("app.db.session.get_settings") as mock_settings:
            mock_settings.return_value.DATABASE_URL = "sqlite:///:memory:"
            with pytest.raises(RuntimeError, match="boom"):
                with db_session.open_db_session(env=None) as sess:
                    assert sess is fake_session
                    assert db_session._active_db_url_ctx.get() == "sqlite:///:memory:"
                    raise RuntimeError("boom")

    fake_session.rollback.assert_called_once()
    fake_session.close.assert_called_once()
    assert db_session._active_db_url_ctx.get() is None


def test_postgresql_engine_uses_nullpool_and_distinct_sqlite_keys(monkeypatch):
    """Verify PostgreSQL uses NullPool (no persistent live socket pooling) and SQLite keys stay separate."""
    eng_pg = db_session._create_engine_for_url("postgresql://u:p@localhost:5432/atc_hub")
    assert isinstance(eng_pg.pool, NullPool)

    monkeypatch.setattr(db_session, "_engines", {})
    e1 = db_session.get_engine("sqlite:///./db_one.sqlite")
    e2 = db_session.get_engine("sqlite:///./db_two.sqlite")
    assert e1 is not e2
    assert len(db_session._engines) == 2

    # Clean up test sqlite engines
    e1.dispose()
    e2.dispose()
    eng_pg.dispose()
