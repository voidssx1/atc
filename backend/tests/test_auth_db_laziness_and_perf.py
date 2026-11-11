import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import (
    build_settings_from_worker_env,
    get_settings,
    worker_settings_context,
)
from app.core.security import generate_session_token, hash_session_token
from app.db.base import Base
from app.db import session as db_session_mod
from app.main import app
from app.models.resource import Resource, ResourceType
from app.models.roadmap import RoadmapSubtopic, RoadmapTopic
from app.models.user import User, UserRole, UserSession


PROTECTED_UNAUTH_ENDPOINTS = [
    "/api/v1/dashboard/admin",
    "/api/v1/dashboard/student",
    "/api/v1/profile/me",
    "/api/v1/progress/",
    "/api/v1/bookmarks/",
    "/api/v1/drafts/",
    "/api/v1/admin/jobs/clerk-outbox",
    "/api/v1/admin/users/",
]


def test_unauthenticated_protected_endpoints_skip_db_completely():
    """Missing/blank Bearer credentials must return 401 with zero DB session init."""
    orig_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        with (
            patch("app.db.session.open_db_session") as mock_open_db,
            patch("app.api.deps.resolve_request_db_session") as mock_auth_db,
            TestClient(app) as raw_client,
        ):
            for path in PROTECTED_UNAUTH_ENDPOINTS:
                resp = raw_client.get(path)
                assert resp.status_code == 401, f"{path} returned {resp.status_code}"

                resp_empty_bearer = raw_client.get(
                    path, headers={"Authorization": "Bearer "}
                )
                assert resp_empty_bearer.status_code == 401

                resp_ws_bearer = raw_client.get(
                    path, headers={"Authorization": "Bearer    "}
                )
                assert resp_ws_bearer.status_code == 401

            assert mock_open_db.call_count == 0
            assert mock_auth_db.call_count == 0
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(orig_overrides)


def test_optional_auth_public_endpoints_skip_auth_db_when_unauthenticated():
    """Public roadmap & resources routes must not invoke auth DB session without Bearer token."""
    iso_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=iso_engine)
    Base.metadata.create_all(bind=iso_engine)

    s = TestingSession()
    t = RoadmapTopic(title="Arrays", slug="arrays", order_index=1, is_published=True)
    s.add(t)
    s.flush()
    s.add(
        RoadmapSubtopic(
            topic_id=t.id,
            title="Two Pointers",
            slug="two-pointers",
            order_index=1,
            is_published=True,
        )
    )
    s.add(
        Resource(
            title="Array Guide",
            url="https://example.com/array",
            resource_type=ResourceType.ARTICLE,
            is_archived=False,
        )
    )
    s.commit()
    s.close()

    from contextlib import contextmanager

    @contextmanager
    def fake_open_db_session(env=None):
        sess = TestingSession()
        try:
            yield sess
        finally:
            sess.close()

    orig_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        with (
            patch("app.api.deps.resolve_request_db_session") as mock_auth_db,
            patch(
                "app.db.session.open_db_session", side_effect=fake_open_db_session
            ) as mock_open,
            TestClient(app) as raw_client,
        ):
            r_topics = raw_client.get("/api/v1/roadmap/topics")
            assert r_topics.status_code == 200
            assert len(r_topics.json()) == 1
            assert len(r_topics.json()[0]["subtopics"]) == 1

            r_res = raw_client.get("/api/v1/resources/")
            assert r_res.status_code == 200
            assert len(r_res.json()) == 1

            # Optional auth skipped auth DB lookup entirely; only 1 DB open per route handler
            assert mock_auth_db.call_count == 0
            assert mock_open.call_count == 2
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(orig_overrides)
        Base.metadata.drop_all(bind=iso_engine)
        iso_engine.dispose()


def test_authenticated_request_reuses_single_db_session_without_double_open():
    """Authenticated requests share one request DB session between auth dep and route handler."""
    iso_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=iso_engine)
    Base.metadata.create_all(bind=iso_engine)

    raw_token = generate_session_token(32)
    sess = TestingSession()
    admin = User(
        email="perf_admin@bennett.edu.in",
        username="perf_admin",
        role=UserRole.TECH_ADMIN,
        is_active=True,
        is_verified=True,
    )
    sess.add(admin)
    sess.flush()
    sess.add(
        UserSession(
            user_id=admin.id,
            token=hash_session_token(raw_token),
            enforce_single_session=True,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
    )
    sess.commit()
    sess.close()

    from contextlib import contextmanager

    open_calls = 0

    @contextmanager
    def fake_open_db_session(env=None):
        nonlocal open_calls
        open_calls += 1
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    orig_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        with (
            patch("app.db.session.open_db_session", side_effect=fake_open_db_session),
            TestClient(app) as raw_client,
        ):
            r_profile = raw_client.get(
                "/api/v1/profile/me",
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            assert r_profile.status_code == 200
            assert r_profile.json()["email"] == "perf_admin@bennett.edu.in"
            assert open_calls == 1

            r_admin = raw_client.get(
                "/api/v1/dashboard/admin",
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            assert r_admin.status_code == 200
            assert open_calls == 2
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(orig_overrides)
        Base.metadata.drop_all(bind=iso_engine)
        iso_engine.dispose()


def test_worker_settings_cache_preserves_rotating_hyperdrive_and_isolation():
    """Cached Worker static settings must still apply per-request Hyperdrive DSNs without leaking."""
    env_1 = SimpleNamespace(
        ENVIRONMENT="development",
        CLERK_ISSUER="https://one.clerk.test",
        RESEND_API_KEY="re_one",
        HYPERDRIVE=SimpleNamespace(
            connectionString="postgresql://user_1:tok_1@hd.local:5432/neondb"
        ),
    )
    env_2 = SimpleNamespace(
        ENVIRONMENT="development",
        CLERK_ISSUER="https://one.clerk.test",
        RESEND_API_KEY="re_one",
        HYPERDRIVE=SimpleNamespace(
            connectionString="postgresql://user_2:tok_2@hd.local:5432/neondb"
        ),
    )
    env_other = SimpleNamespace(
        ENVIRONMENT="development",
        CLERK_ISSUER="https://two.clerk.test",
        RESEND_API_KEY="re_two",
        HYPERDRIVE=SimpleNamespace(
            connectionString="postgresql://user_3:tok_3@hd.local:5432/neondb"
        ),
    )

    s1 = build_settings_from_worker_env(env_1)
    s2 = build_settings_from_worker_env(env_2)
    s3 = build_settings_from_worker_env(env_other)

    assert "user_1:tok_1" in s1.DATABASE_URL
    assert "user_2:tok_2" in s2.DATABASE_URL
    assert "user_3:tok_3" in s3.DATABASE_URL
    assert s1.RESEND_API_KEY == "re_one"
    assert s3.RESEND_API_KEY == "re_two"
    assert s3.CLERK_ISSUER == "https://two.clerk.test"

    async def check_concurrent():
        async def probe(env, expected_user):
            with worker_settings_context(env):
                await asyncio.sleep(0.01)
                return get_settings().DATABASE_URL

        return await asyncio.gather(
            probe(env_1, "user_1"),
            probe(env_2, "user_2"),
        )

    url_1, url_2 = asyncio.run(check_concurrent())
    assert "user_1:tok_1" in url_1
    assert "user_2:tok_2" in url_2


def test_app_startup_defers_openapi_and_middleware_while_keeping_psycopg_utc_seed():
    """Fresh Worker import must defer OpenAPI/middleware allocation while serving OpenAPI on demand."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend_src = str(Path(__file__).resolve().parent.parent / "src")
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        f"{backend_src}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else backend_src
    )

    probe = (
        "import sys, app.main as m, app.core.config as cfg, app.db.session as sess; "
        "assert m.app.openapi_schema is None, 'openapi_schema eagerly built at startup'; "
        "assert m.app.middleware_stack is None, 'middleware_stack eagerly built at startup'; "
        "assert 'app._openapi_snapshot' not in sys.modules, 'openapi snapshot loaded eagerly'; "
        "assert len(cfg._STATIC_SETTINGS_CACHE) == 0; "
        "assert len(sess._engines) == 0"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.returncode == 0

    with TestClient(app) as client:
        openapi_resp = client.get("/api/v1/openapi.json")
        assert openapi_resp.status_code == 200
        spec = openapi_resp.json()
        assert len(spec.get("paths", {})) >= 50
        assert app.openapi_schema is not None

        docs_resp = client.get("/api/v1/docs")
        assert docs_resp.status_code == 200

    import psycopg._tz as psycopg_tz

    for alias in (None, b"", b"UTC", b"utc", b"Etc/UTC", b"GMT", b"Etc/GMT", b"Z"):
        assert psycopg_tz._timezones.get(alias) is timezone.utc


def test_psycopg_timezone_empty_and_utc_aliases_avoid_zoneinfo_warning(caplog):
    """Empty/UTC parameter_status aliases must resolve via _timezones without ZoneInfo errors."""
    import logging
    import psycopg._tz as psycopg_tz

    class _FakePGConn:
        def __init__(self, value):
            self._value = value

        def parameter_status(self, key: bytes):
            assert key == b"TimeZone"
            return self._value

    with caplog.at_level(logging.WARNING, logger="psycopg"):
        for raw_tz in (None, b"", b"UTC", b"utc", b"Etc/UTC", b"GMT", b"Etc/GMT", b"Z"):
            assert psycopg_tz.get_tzinfo(_FakePGConn(raw_tz)) is timezone.utc

    assert "unknown PostgreSQL timezone" not in caplog.text


def test_postgresql_engine_disables_native_hstore_for_cold_connect():
    """PostgreSQL engines skip unnecessary pg_type hstore introspection on cold connect."""
    from sqlalchemy.pool import NullPool

    eng = db_session_mod.get_engine(
        "postgresql+psycopg://u:p@localhost:5432/hstore_skip_test"
    )
    assert isinstance(eng.pool, NullPool)
    assert eng.dialect.use_native_hstore is False


def test_openapi_lazy_snapshot_matches_live_schema_and_falls_back_on_route_change():
    """Pre-compressed OpenAPI snapshot must match get_openapi() and fall back when routes change."""
    from fastapi.openapi.utils import get_openapi

    app.openapi_schema = None
    app._openapi_routes_version = None
    snapshot_spec = app.openapi()
    live_spec = get_openapi(
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
    assert snapshot_spec == live_spec

    # Dynamically adding a route must bypass snapshot and fall back cleanly to get_openapi()
    orig_routes = list(app.router.routes)
    try:
        @app.get("/_temp_openapi_fallback_probe")
        def _temp_probe():
            return {"ok": True}

        rebuilt = app.openapi()
        assert "/_temp_openapi_fallback_probe" in rebuilt["paths"]
    finally:
        app.router.routes[:] = orig_routes
        app.openapi_schema = None
        app._openapi_routes_version = None


def test_prefix_guarded_routers_skip_unrelated_subrouters():
    """Prefix-guarded sub-routers must not compile unrelated route groups on cold requests."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    backend_src = str(Path(__file__).resolve().parent.parent / "src")
    script = "\n".join(
        [
            "import json, sys",
            f"sys.path.insert(0, {backend_src!r})",
            "from fastapi.testclient import TestClient",
            "import app.main as m",
            "with TestClient(m.app) as c:",
            "    assert c.get('/api/v1/health').status_code == 200",
            "    assert c.get('/health').status_code == 200",
            "states = {str(r._allowed_prefixes): r._effective_candidates_version for r in m.app.router.routes if isinstance(r, m._PrefixGuardedIncludedRouter)}",
            "print(json.dumps(states))",
        ]
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    states = json.loads(proc.stdout.strip().splitlines()[-1])
    assert states.get("('/api/v1/health',)") is not None
    assert states.get("('/api/v1/auth',)") is None
    assert states.get("('/api/v1/admin/data',)") is None
