import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import base64

from app.core.config import (
    Settings,
    build_settings_from_worker_env,
    get_settings,
    settings as global_settings,
    worker_settings_context,
)
from app.main import Default, app
from app.models.clerk import ClerkOutbox
from app.models.resource import AvailabilityStatus, Resource, ResourceType
from app.models.roadmap import RoadmapSubtopic, RoadmapTopic
from app.models.user import User, UserRole, UserSession
from app.services.clerk import ClerkUserIdentity
from app.services.scheduler import JobRunnerService, _scheduled_lock


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_b64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return _b64url(n.to_bytes(length, "big"))


def test_worker_env_bindings_without_mutating_global_settings():
    orig_issuer = global_settings.CLERK_ISSUER
    orig_env = global_settings.ENVIRONMENT
    orig_resend = global_settings.RESEND_API_KEY

    worker_env = SimpleNamespace(
        ENVIRONMENT="production",
        AUTH_MODE="clerk",
        CLERK_ISSUER="https://worker.clerk.test",
        CLERK_ALLOWED_ORIGINS="https://worker.pages.dev",
        CLERK_SECRET_KEY="sk_live_worker_only",
        CLERK_WEBHOOK_SECRET="whsec_worker_only",
        CLERK_JWKS_URL="https://worker.clerk.test/.well-known/jwks.json",
        ALLOWED_ORIGINS="https://worker.pages.dev",
        RESEND_API_KEY="re_worker_secret",
        HYPERDRIVE=SimpleNamespace(
            connectionString="postgresql://user:pass@hyperdrive-host:5432/neondb"
        ),
    )

    isolated = build_settings_from_worker_env(worker_env)
    assert isolated.CLERK_ISSUER == "https://worker.clerk.test"
    assert isolated.RESEND_API_KEY == "re_worker_secret"
    assert "postgresql+psycopg://user:pass@hyperdrive-host:5432/neondb" in isolated.DATABASE_URL
    assert isolated.get_allowed_origins() == ["https://worker.pages.dev"]

    # Global settings must remain completely untouched
    assert global_settings.CLERK_ISSUER == orig_issuer
    assert global_settings.ENVIRONMENT == orig_env
    assert global_settings.RESEND_API_KEY == orig_resend


def test_concurrent_worker_env_contexts_never_leak_secrets():
    env_a = SimpleNamespace(RESEND_API_KEY="re_secret_A", CLERK_ISSUER="https://a.clerk")
    env_b = SimpleNamespace(RESEND_API_KEY="re_secret_B", CLERK_ISSUER="https://b.clerk")

    async def task_a():
        with worker_settings_context(env_a):
            await asyncio.sleep(0.02)
            return get_settings().RESEND_API_KEY, get_settings().CLERK_ISSUER

    async def task_b():
        with worker_settings_context(env_b):
            await asyncio.sleep(0.01)
            return get_settings().RESEND_API_KEY, get_settings().CLERK_ISSUER

    async def run():
        return await asyncio.gather(task_a(), task_b())

    res_a, res_b = asyncio.run(run())
    assert res_a == ("re_secret_A", "https://a.clerk")
    assert res_b == ("re_secret_B", "https://b.clerk")
    # Global context restored after tasks complete
    assert get_settings() is global_settings


def test_worker_asgi_scope_env_drives_dynamic_cors_and_fail_closed():
    async def send_asgi(scope):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            messages.append(msg)

        await app(scope, receive, send)
        return messages

    # 1. Request with Worker env allowing https://pages-app.example succeeds CORS
    valid_env = SimpleNamespace(
        ENVIRONMENT="development",
        ALLOWED_ORIGINS="https://pages-app.example",
    )
    scope_ok = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "OPTIONS",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [
            (b"origin", b"https://pages-app.example"),
            (b"access-control-request-method", b"GET"),
        ],
        "env": valid_env,
    }
    msgs_ok = asyncio.run(send_asgi(scope_ok))
    assert msgs_ok[0]["status"] == 200
    hdrs = dict(msgs_ok[0]["headers"])
    assert hdrs[b"access-control-allow-origin"] == b"https://pages-app.example"

    # 2. Same app with missing production Clerk config in Worker env fails closed (500)
    bad_prod_env = SimpleNamespace(
        ENVIRONMENT="production",
        AUTH_MODE="clerk",
        CLERK_ISSUER="https://REPLACE_WITH_CLERK_CUSTOM_DOMAIN",
    )
    scope_bad = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "GET",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [],
        "env": bad_prod_env,
    }
    msgs_bad = asyncio.run(send_asgi(scope_bad))
    assert msgs_bad[0]["status"] == 500


def test_default_scheduled_worker_entrypoint_dispatches_jobs_and_handles_failures(db_session):
    # Prepare expired session, due outbox item, and active resource
    user = User(
        email="cron_user@bennett.edu.in",
        username="cron_user",
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()

    db_session.add(
        UserSession(
            user_id=user.id,
            token="expired_hash_for_cron",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            is_revoked=False,
        )
    )
    db_session.add(
        ClerkOutbox(
            clerk_user_id="user_cron_outbox",
            audit_target_id=str(user.id),
            operation="delete_user",
            status="pending",
            attempts=0,
            max_attempts=5,
            next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
    )
    topic = RoadmapTopic(title="Arrays", slug="arrays-cron", order_index=1)
    db_session.add(topic)
    db_session.flush()
    subtopic = RoadmapSubtopic(
        topic_id=topic.id, title="Two Pointers", slug="two-pointers-cron", order_index=1
    )
    db_session.add(subtopic)
    db_session.flush()
    db_session.add(
        Resource(
            title="Test Resource",
            url="https://example.com/dsa",
            resource_type=ResourceType.ARTICLE,
            subtopic_id=subtopic.id,
        )
    )
    db_session.commit()

    # Use db_session via open_db_session patch so Worker entrypoint tests against SQLite fixture
    from contextlib import contextmanager

    @contextmanager
    def mock_open_db(env=None):
        yield db_session

    worker = Default()
    worker_env = SimpleNamespace(
        ENVIRONMENT="development",
        CLERK_SECRET_KEY="sk_test_cron",
    )

    # 1. Hourly cron ('0 1-23 * * *') runs session cleanup + outbox, skips availability scan
    with patch("app.main.open_db_session", side_effect=mock_open_db):
        with patch(
            "app.services.clerk.delete_clerk_user",
            new_callable=AsyncMock,
            return_value=(True, False, None, None),
        ):
            hourly_res = asyncio.run(
                worker.scheduled(SimpleNamespace(cron="0 1-23 * * *"), worker_env, None)
            )
    assert hourly_res["skipped"] is False
    assert hourly_res["sessions_cleaned"] >= 1
    assert hourly_res["clerk_outbox"]["completed"] == 1
    assert hourly_res["availability_scan"] is None

    # 2. Daily midnight cron ('0 0 * * *') also runs resource availability scan, and isolates errors
    with patch("app.main.open_db_session", side_effect=mock_open_db):
        with patch.object(
            JobRunnerService,
            "cleanup_expired_sessions",
            side_effect=RuntimeError("simulated db glitch"),
        ):
            with patch(
                "app.services.scheduler.check_url_availability",
                new_callable=AsyncMock,
                return_value=(AvailabilityStatus.AVAILABLE, 200),
            ):
                daily_res = asyncio.run(
                    worker.scheduled(SimpleNamespace(cron="0 0 * * *"), worker_env, None)
                )
    assert "cleanup_expired_sessions:RuntimeError" in daily_res["errors"]
    assert daily_res["availability_scan"]["total_checked"] == 1
    assert daily_res["availability_scan"]["available"] == 1


def test_scheduled_overlap_prevention(db_session):
    async def run_with_lock_held():
        async with _scheduled_lock:
            return await JobRunnerService.run_scheduled_batch(db_session, cron_expr="0 0 * * *")

    res = asyncio.run(run_with_lock_held())
    assert res["skipped"] is True
    assert res["reason"] == "overlapping_execution"


def test_concurrent_existing_account_clerk_linking_recovery_and_conflict(client, db_session):
    from sqlalchemy.exc import IntegrityError

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_numbers = private_key.public_key().public_numbers()
    kid = "link-kid-1"
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_b64url(pub_numbers.n),
        "e": _int_to_b64url(pub_numbers.e),
    }

    orig_key = global_settings.CLERK_JWT_KEY
    orig_iss = global_settings.CLERK_ISSUER
    orig_azp = global_settings.CLERK_ALLOWED_ORIGINS
    orig_sec = global_settings.CLERK_SECRET_KEY

    global_settings.CLERK_JWT_KEY = json.dumps({"keys": [jwk]})
    global_settings.CLERK_ISSUER = "https://clerk.test.local"
    global_settings.CLERK_ALLOWED_ORIGINS = "https://atc-dsa.pages.dev"
    global_settings.CLERK_SECRET_KEY = "sk_test_link"

    try:
        now = int(time.time())
        admin = User(
            email="unlinked_admin@bennett.edu.in",
            username="unlinked_admin",
            clerk_user_id=None,
            role=UserRole.CONTENT_ADMIN,
            is_active=True,
            is_verified=True,
        )
        db_session.add(admin)
        db_session.commit()

        # Construct JWT
        header = {"alg": "RS256", "typ": "JWT", "kid": kid}
        claims = {
            "sub": "clerk_admin_same",
            "sid": "sess_admin_link_1",
            "iss": "https://clerk.test.local",
            "azp": "https://atc-dsa.pages.dev",
            "exp": now + 300,
        }
        h_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        p_b64 = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        sig = private_key.sign(
            f"{h_b64}.{p_b64}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
        )
        jwt_tok = f"{h_b64}.{p_b64}.{_b64url(sig)}"

        # Simulate race: Concurrent request links admin to 'clerk_admin_same' right before our flush
        identity_same = ClerkUserIdentity(
            "clerk_admin_same",
            "unlinked_admin@bennett.edu.in",
            True,
            "Content Admin",
            False,
            False,
            now * 1000,
        )

        with patch("app.api.v1.auth.fetch_clerk_user", return_value=identity_same):
            # Simulate concurrent commit setting clerk_user_id='clerk_admin_same' + IntegrityError
            real_begin_nested = db_session.begin_nested

            def simulate_same_identity_race():
                admin.clerk_user_id = "clerk_admin_same"
                raise IntegrityError("stmt", "params", "orig")

            with patch.object(db_session, "begin_nested", side_effect=simulate_same_identity_race):
                res_same = client.post(
                    "/api/v1/auth/clerk/exchange",
                    headers={"Authorization": f"Bearer {jwt_tok}"},
                )
        assert res_same.status_code == 200
        assert "session_token" in res_same.json()

        # Now simulate a DIFFERENT attacker Clerk identity attempting to link/exchange same email -> 409 Conflict
        claims_other = dict(claims, sub="clerk_admin_attacker", sid="sess_att")
        p2_b64 = _b64url(json.dumps(claims_other, separators=(",", ":")).encode("utf-8"))
        sig2 = private_key.sign(
            f"{h_b64}.{p2_b64}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
        )
        jwt_other = f"{h_b64}.{p2_b64}.{_b64url(sig2)}"
        identity_other = ClerkUserIdentity(
            "clerk_admin_attacker",
            "unlinked_admin@bennett.edu.in",
            True,
            "Attacker",
            False,
            False,
            now * 1000,
        )
        with patch("app.api.v1.auth.fetch_clerk_user", return_value=identity_other):
            res_conf = client.post(
                "/api/v1/auth/clerk/exchange",
                headers={"Authorization": f"Bearer {jwt_other}"},
            )
        assert res_conf.status_code == 409
    finally:
        global_settings.CLERK_JWT_KEY = orig_key
        global_settings.CLERK_ISSUER = orig_iss
        global_settings.CLERK_ALLOWED_ORIGINS = orig_azp
        global_settings.CLERK_SECRET_KEY = orig_sec
