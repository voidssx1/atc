import base64
import json
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.db.base import Base
from verify_clerk_tech_admin import (
    DEFAULT_CLERK_USER_ID,
    DEFAULT_ORIGIN,
    obtain_clerk_session_jwt,
    verify_worker_and_neon_linking,
)


def _fake_jwt(sub: str, sid: str, azp: str) -> str:
    hdr = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "kid": "k1"}).encode()).decode().rstrip("=")
    pl = base64.urlsafe_b64encode(json.dumps({"sub": sub, "sid": sid, "azp": azp}).encode()).decode().rstrip("=")
    return f"{hdr}.{pl}.fakesig_do_not_leak"


def test_backend_only_clerk_verification_flow_redacts_secrets(capsys):
    clerk_secret = "sk_test_super_secret_clerk_key_value"
    fake_session_jwt = _fake_jwt(DEFAULT_CLERK_USER_ID, "sess_abc123", DEFAULT_ORIGIN)
    fake_backend_token = "backend_opaque_token_secret_123456"
    db_uid = "5c63ff64-1144-4480-a77b-ea5ae4d65c7b"

    def clerk_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sign_in_tokens":
            assert request.headers["Authorization"] == f"Bearer {clerk_secret}"
            return httpx.Response(200, json={"token": "st_one_time_token"})
        if request.url.path == "/v1/dev_browser":
            assert request.headers["Origin"] == DEFAULT_ORIGIN
            return httpx.Response(200, json={"token": "dev_browser_jwt_secret"})
        if request.url.path == "/v1/client/sign_ins":
            assert request.headers["Origin"] == DEFAULT_ORIGIN
            assert request.url.params["__clerk_db_jwt"] == "dev_browser_jwt_secret"
            return httpx.Response(
                200,
                json={
                    "response": {"created_session_id": "sess_abc123"},
                    "client": {
                        "sessions": [
                            {"id": "sess_abc123", "last_active_token": {"jwt": fake_session_jwt}}
                        ]
                    },
                },
            )
        raise AssertionError(f"Unexpected Clerk URL: {request.url}")

    def worker_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/clerk/exchange":
            assert request.headers["Authorization"] == f"Bearer {fake_session_jwt}"
            return httpx.Response(
                200,
                json={
                    "session_token": fake_backend_token,
                    "access_token": fake_backend_token,
                    "token_type": "bearer",
                    "expires_in": 1800,
                },
            )
        if request.url.path == "/api/v1/admin/jobs/clerk-outbox":
            assert request.headers["Authorization"] == f"Bearer {fake_backend_token}"
            return httpx.Response(
                200,
                json={"pending_count": 0, "dead_letter_count": 0, "completed_count": 0, "items": []},
            )
        if request.url.path == "/api/v1/dashboard/admin":
            assert request.headers["Authorization"] == f"Bearer {fake_backend_token}"
            return httpx.Response(200, json={"total_students": 0, "total_admins": 1})
        if request.url.path == "/api/v1/auth/logout":
            assert request.headers["Authorization"] == f"Bearer {fake_backend_token}"
            return httpx.Response(200, json={"message": "Logged out"})
        raise AssertionError(f"Unexpected Worker URL: {request.url}")

    iso_eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=iso_eng)
    try:
        with iso_eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, username, role, is_active, is_verified, clerk_user_id, created_at, updated_at) "
                    "VALUES (:id, :email, :username, :role, 1, 1, :cid, '2026-11-11T00:00:00Z', '2026-11-11T00:00:00Z')"
                ),
                {
                    "id": uuid.UUID(db_uid).hex,
                    "email": "webatc77@gmail.com",
                    "username": "webatc77",
                    "role": "TECH_ADMIN",
                    "cid": DEFAULT_CLERK_USER_ID,
                },
            )

        with httpx.Client(transport=httpx.MockTransport(clerk_handler)) as c_client:
            minted = obtain_clerk_session_jwt(
                clerk_secret_key=clerk_secret,
                clerk_user_id=DEFAULT_CLERK_USER_ID,
                client=c_client,
            )
        assert minted == fake_session_jwt

        with httpx.Client(transport=httpx.MockTransport(worker_handler)) as w_client:
            report = verify_worker_and_neon_linking(
                minted,
                expected_db_user_id=uuid.UUID(db_uid).hex,
                revoke_after=True,
                client=w_client,
                db_engine=iso_eng,
            )

        serialized = json.dumps(report)
        print(serialized)
        captured = capsys.readouterr()
        combined = captured.out + captured.err + serialized
        assert clerk_secret not in combined
        assert fake_session_jwt not in combined
        assert fake_backend_token not in combined
        assert report["exchange_status"] == 200
        assert report["outbox_status"] == 200
        assert report["admin_dashboard_status"] == 200
        assert report["neon_clerk_user_id"] == DEFAULT_CLERK_USER_ID
        assert report["session_revoked_after_test"] is True
    finally:
        Base.metadata.drop_all(bind=iso_eng)
        iso_eng.dispose()
