import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core.config import settings
from app.core.security import generate_session_token, hash_session_token
from app.models.analytics import AuditLog
from app.models.clerk import ClerkOutbox, WebhookEvent
from app.models.user import StudentProfile, User, UserRole, UserSession
from app.services.clerk import ClerkOutboxService, ClerkUserIdentity


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_b64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return _b64url(n.to_bytes(length, "big"))


@pytest.fixture(scope="module")
def rsa_test_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_numbers = private_key.public_key().public_numbers()
    kid = "test-kid-1"
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_b64url(pub_numbers.n),
        "e": _int_to_b64url(pub_numbers.e),
    }
    return private_key, jwk, kid


def _sign_jwt(
    private_key,
    kid: str,
    claims: dict,
    alg: str = "RS256",
) -> str:
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    h_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    if alg == "RS256":
        sig = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    else:
        sig = b"unsigned"
    return f"{h_b64}.{p_b64}.{_b64url(sig)}"


def _sign_svix(secret_b64_with_prefix: str, svix_id: str, ts: int, body_bytes: bytes) -> str:
    secret_raw = base64.b64decode(secret_b64_with_prefix.replace("whsec_", ""))
    signed = f"{svix_id}.{ts}.{body_bytes.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(secret_raw, signed, hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode('ascii')}"


@pytest.fixture
def clerk_env(rsa_test_keys):
    private_key, jwk, kid = rsa_test_keys
    orig = {
        "CLERK_JWT_KEY": settings.CLERK_JWT_KEY,
        "CLERK_ISSUER": settings.CLERK_ISSUER,
        "CLERK_ALLOWED_ORIGINS": settings.CLERK_ALLOWED_ORIGINS,
        "CLERK_SECRET_KEY": settings.CLERK_SECRET_KEY,
        "CLERK_WEBHOOK_SECRET": settings.CLERK_WEBHOOK_SECRET,
    }
    secret_bytes = b"test_webhook_secret_key_32_bytes!"
    settings.CLERK_JWT_KEY = json.dumps({"keys": [jwk]})
    settings.CLERK_ISSUER = "https://clerk.test.local"
    settings.CLERK_ALLOWED_ORIGINS = "https://atc-dsa.pages.dev"
    settings.CLERK_SECRET_KEY = "sk_test_mock_secret"
    settings.CLERK_WEBHOOK_SECRET = "whsec_" + base64.b64encode(secret_bytes).decode("ascii")
    yield private_key, kid
    for k, v in orig.items():
        setattr(settings, k, v)


def test_clerk_jwt_verification_rejections(client, clerk_env):
    private_key, kid = clerk_env
    now = int(time.time())

    # 1. Expired token
    expired = _sign_jwt(
        private_key,
        kid,
        {
            "sub": "user_1",
            "sid": "sess_1",
            "iss": "https://clerk.test.local",
            "azp": "https://atc-dsa.pages.dev",
            "exp": now - 120,
        },
    )
    res = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {expired}"})
    assert res.status_code == 401

    # 2. Not before in future
    future = _sign_jwt(
        private_key,
        kid,
        {
            "sub": "user_1",
            "sid": "sess_1",
            "iss": "https://clerk.test.local",
            "azp": "https://atc-dsa.pages.dev",
            "nbf": now + 300,
            "exp": now + 600,
        },
    )
    assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {future}"}).status_code == 401

    # 3. Unsupported alg=none
    alg_none = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_1", "sid": "sess_1", "exp": now + 60},
        alg="none",
    )
    assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {alg_none}"}).status_code == 401

    # 4. Tampered signature
    valid = _sign_jwt(
        private_key,
        kid,
        {
            "sub": "user_1",
            "sid": "sess_1",
            "iss": "https://clerk.test.local",
            "azp": "https://atc-dsa.pages.dev",
            "exp": now + 300,
        },
    )
    tampered = valid[:-4] + ("AAAA" if valid[-4:] != "AAAA" else "BBBB")
    assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401

    # 5. Wrong issuer & unauthorized azp
    bad_iss = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_1", "sid": "sess_1", "iss": "https://evil.com", "azp": "https://atc-dsa.pages.dev", "exp": now + 60},
    )
    assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {bad_iss}"}).status_code == 401

    bad_azp = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_1", "sid": "sess_1", "iss": "https://clerk.test.local", "azp": "https://evil.pages.dev", "exp": now + 60},
    )
    assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {bad_azp}"}).status_code == 401


def test_clerk_exchange_domain_linking_and_idempotent_rotation(client, db_session, clerk_env):
    private_key, kid = clerk_env
    now = int(time.time())

    # 1. Non-@bennett.edu.in and subdomain @atc.bennett.edu.in rejected
    for rejected_email in ["student@atc.bennett.edu.in", "outsider@gmail.com"]:
        jwt_tok = _sign_jwt(
            private_key,
            kid,
            {
                "sub": "user_ext",
                "sid": "sess_ext",
                "iss": "https://clerk.test.local",
                "azp": "https://atc-dsa.pages.dev",
                "exp": now + 300,
            },
        )
        identity = ClerkUserIdentity(
            clerk_user_id="user_ext",
            email=rejected_email,
            email_verified=True,
            full_name="Test",
            banned=False,
            locked=False,
            updated_at=now * 1000,
        )
        with patch("app.api.v1.auth.fetch_clerk_user", return_value=identity):
            res = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {jwt_tok}"})
            assert res.status_code == 403

    # 2. Unverified @bennett.edu.in rejected
    jwt_unver = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_unv", "sid": "sess_unv", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 300},
    )
    unver_id = ClerkUserIdentity("user_unv", "unver@bennett.edu.in", False, "Unver", False, False, now * 1000)
    with patch("app.api.v1.auth.fetch_clerk_user", return_value=unver_id):
        assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {jwt_unver}"}).status_code == 403

    # 3. Valid @bennett.edu.in exchange creates Student + hashed session
    jwt_stu = _sign_jwt(
        private_key,
        kid,
        {
            "sub": "user_stu_1",
            "sid": "sess_stu_1",
            "iss": "https://clerk.test.local",
            "azp": "https://atc-dsa.pages.dev",
            "public_metadata": {"role": "tech_admin"},  # Spoofed metadata must be ignored
            "exp": now + 300,
        },
    )
    stu_id = ClerkUserIdentity("user_stu_1", "alice@bennett.edu.in", True, "Alice", False, False, now * 1000)
    with patch("app.api.v1.auth.fetch_clerk_user", return_value=stu_id):
        res1 = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {jwt_stu}"})
    assert res1.status_code == 200
    tok1 = res1.json()["session_token"]
    assert 604790 <= res1.json()["expires_in"] <= 604800

    # Neon role is STUDENT (spoofed metadata ignored) and token is stored hashed
    user_db = db_session.query(User).filter(User.clerk_user_id == "user_stu_1").first()
    assert user_db.role == UserRole.STUDENT
    assert db_session.query(UserSession).filter(UserSession.token == tok1).first() is None
    sess1_db = db_session.query(UserSession).filter(UserSession.token == hash_session_token(tok1)).first()
    assert sess1_db is not None
    assert sess1_db.clerk_session_id == "sess_stu_1"
    orig_created_at = sess1_db.created_at

    # 4. Repeated/concurrent exchange with SAME sid rotates token + preserves created_at + 30s grace window
    with patch("app.api.v1.auth.fetch_clerk_user", return_value=stu_id):
        res2 = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {jwt_stu}"})
    assert res2.status_code == 200
    tok2 = res2.json()["session_token"]
    assert tok2 != tok1

    db_session.expire_all()
    sess1_after = db_session.query(UserSession).filter(UserSession.token == hash_session_token(tok1)).first()
    sess2_db = db_session.query(UserSession).filter(UserSession.token == hash_session_token(tok2)).first()
    assert sess1_after.grace_until is not None
    assert sess2_db.grace_until is None
    assert sess2_db.created_at == orig_created_at

    # Both tok1 (within 30s grace) and tok2 work during concurrent overlap
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {tok1}"}).status_code == 200
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {tok2}"}).status_code == 200

    # Once grace expires, tok1 fails with 401 while tok2 remains valid
    sess1_after.grace_until = datetime.now(timezone.utc) - timedelta(seconds=5)
    db_session.commit()
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {tok1}"}).status_code == 401
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {tok2}"}).status_code == 200

    # 5. Conflicting clerk_user_id for same verified email returns 409 Conflict
    jwt_conflict = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_attacker", "sid": "sess_att", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 300},
    )
    att_id = ClerkUserIdentity("user_attacker", "alice@bennett.edu.in", True, "Attacker", False, False, now * 1000)
    with patch("app.api.v1.auth.fetch_clerk_user", return_value=att_id):
        res_conf = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {jwt_conflict}"})
        assert res_conf.status_code == 409


def test_admin_provisioning_30min_inactivity_and_tech_single_session(client, db_session, tech_admin_headers, clerk_env):
    private_key, kid = clerk_env
    now = int(time.time())

    # 1. Tech Admin provisions a Content Admin without password
    prov_res = client.post(
        "/api/v1/admin/users/",
        json={"email": "newcontent@bennett.edu.in", "username": "newcontent", "role": "content_admin"},
        headers=tech_admin_headers,
    )
    assert prov_res.status_code == 201

    # Content Admin links safely on first verified Clerk login
    c_jwt_1 = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_ca", "sid": "sess_ca_1", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 300},
    )
    c_jwt_2 = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_ca", "sid": "sess_ca_2", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 300},
    )
    ca_id = ClerkUserIdentity("user_ca", "newcontent@bennett.edu.in", True, "Content Admin", False, False, now * 1000)
    with patch("app.api.v1.auth.fetch_clerk_user", return_value=ca_id):
        ca_res_1 = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {c_jwt_1}"})
        ca_res_2 = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {c_jwt_2}"})

    assert ca_res_1.status_code == 200
    assert ca_res_2.status_code == 200
    # 30-minute inactivity timeout reflected in expires_in (~1800s)
    assert 1790 <= ca_res_1.json()["expires_in"] <= 1800

    ca_tok_1 = ca_res_1.json()["session_token"]
    ca_tok_2 = ca_res_2.json()["session_token"]
    # Content Admin multi-session allowed: both work
    assert client.get("/api/v1/admin/users/", headers={"Authorization": f"Bearer {ca_tok_1}"}).status_code == 200
    assert client.get("/api/v1/admin/users/", headers={"Authorization": f"Bearer {ca_tok_2}"}).status_code == 200

    # Verify 30-minute Admin inactivity expiry
    ca_sess_1 = db_session.query(UserSession).filter(UserSession.token == hash_session_token(ca_tok_1)).first()
    ca_sess_1.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    expired_ca = client.get("/api/v1/admin/users/", headers={"Authorization": f"Bearer {ca_tok_1}"})
    assert expired_ca.status_code == 401
    assert "30 minutes" in expired_ca.json()["detail"]

    # 2. Tech Admin single active session restriction across different sids
    tech_user = db_session.query(User).filter(User.role == UserRole.TECH_ADMIN).first()
    t_jwt_1 = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_tech", "sid": "sess_tech_1", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 300},
    )
    t_jwt_2 = _sign_jwt(
        private_key,
        kid,
        {"sub": "user_tech", "sid": "sess_tech_2", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 300},
    )
    tech_id = ClerkUserIdentity("user_tech", tech_user.email, True, "Tech Admin", False, False, now * 1000)
    with patch("app.api.v1.auth.fetch_clerk_user", return_value=tech_id):
        t_res_1 = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {t_jwt_1}"})
        t_tok_1 = t_res_1.json()["session_token"]
        assert client.get("/api/v1/admin/users/", headers={"Authorization": f"Bearer {t_tok_1}"}).status_code == 200

        t_res_2 = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {t_jwt_2}"})
        t_tok_2 = t_res_2.json()["session_token"]

    # Prior Tech session AND fixture session must be hard-revoked
    assert client.get("/api/v1/admin/users/", headers={"Authorization": f"Bearer {t_tok_1}"}).status_code == 401
    assert client.get("/api/v1/admin/users/", headers=tech_admin_headers).status_code == 401
    assert client.get("/api/v1/admin/users/", headers={"Authorization": f"Bearer {t_tok_2}"}).status_code == 200


def test_dynamic_expires_in_clamped_to_30_day_max(client, db_session):
    user = User(
        email="clamp_stu@bennett.edu.in",
        username="clamp_stu",
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()

    raw_tok = generate_session_token(48)
    created = datetime.now(timezone.utc) - timedelta(days=28)
    session = UserSession(
        user_id=user.id,
        token=hash_session_token(raw_tok),
        created_at=created,
        expires_at=datetime.now(timezone.utc) + timedelta(days=2),
        is_revoked=False,
    )
    db_session.add(session)
    db_session.commit()

    res = client.post("/api/v1/auth/refresh", json={"session_token": raw_tok})
    assert res.status_code == 200
    expires_in = res.json()["expires_in"]
    # On Day 28, refresh must clamp expires_in to ~2 days (~172800s), not 7 days (604800s)
    assert 172000 <= expires_in <= 172805


def test_webhook_verification_idempotency_and_session_rules(client, db_session, clerk_env):
    now = int(time.time())
    user = User(
        email="webhook_stu@bennett.edu.in",
        username="webhook_stu",
        clerk_user_id="user_wh_1",
        clerk_updated_at=now * 1000,
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()

    raw_tok = generate_session_token(48)
    sess = UserSession(
        user_id=user.id,
        token=hash_session_token(raw_tok),
        clerk_session_id="sess_wh_1",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        is_revoked=False,
    )
    db_session.add(sess)
    db_session.commit()

    # 1. Invalid Svix signature & stale timestamp rejected
    body_end = json.dumps({"type": "session.ended", "data": {"id": "sess_wh_1", "user_id": "user_wh_1"}}).encode("utf-8")
    assert (
        client.post(
            "/api/v1/webhooks/clerk",
            content=body_end,
            headers={"svix-id": "msg_bad", "svix-timestamp": str(now), "svix-signature": "v1,invalidsig"},
        ).status_code
        == 401
    )
    stale_ts = now - 600
    assert (
        client.post(
            "/api/v1/webhooks/clerk",
            content=body_end,
            headers={
                "svix-id": "msg_stale",
                "svix-timestamp": str(stale_ts),
                "svix-signature": _sign_svix(settings.CLERK_WEBHOOK_SECRET, "msg_stale", stale_ts, body_end),
            },
        ).status_code
        == 400
    )

    # 2. session.ended and session.removed MUST NOT revoke active 30-day Student session
    for ev_id, ev_type in [("msg_end_1", "session.ended"), ("msg_rem_1", "session.removed")]:
        b = json.dumps({"type": ev_type, "data": {"id": "sess_wh_1", "user_id": "user_wh_1"}}).encode("utf-8")
        r = client.post(
            "/api/v1/webhooks/clerk",
            content=b,
            headers={
                "svix-id": ev_id,
                "svix-timestamp": str(now),
                "svix-signature": _sign_svix(settings.CLERK_WEBHOOK_SECRET, ev_id, now, b),
            },
        )
        assert r.status_code == 200
        assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {raw_tok}"}).status_code == 200

    # Duplicate svix-id returns 200 duplicate_ignored
    dup_r = client.post(
        "/api/v1/webhooks/clerk",
        content=body_end,
        headers={
            "svix-id": "msg_end_1",
            "svix-timestamp": str(now),
            "svix-signature": _sign_svix(settings.CLERK_WEBHOOK_SECRET, "msg_end_1", now, body_end),
        },
    )
    assert dup_r.status_code == 200
    assert dup_r.json()["status"] == "duplicate_ignored"

    # 3. session.revoked immediately hard-revokes matching session
    body_rev = json.dumps({"type": "session.revoked", "data": {"id": "sess_wh_1", "user_id": "user_wh_1"}}).encode("utf-8")
    rev_r = client.post(
        "/api/v1/webhooks/clerk",
        content=body_rev,
        headers={
            "svix-id": "msg_rev_1",
            "svix-timestamp": str(now),
            "svix-signature": _sign_svix(settings.CLERK_WEBHOOK_SECRET, "msg_rev_1", now, body_rev),
        },
    )
    assert rev_r.status_code == 200
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {raw_tok}"}).status_code == 401

    # 4. user.updated banned=true deactivates user + revokes new active sessions
    raw_tok2 = generate_session_token(48)
    db_session.add(
        UserSession(
            user_id=user.id,
            token=hash_session_token(raw_tok2),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            is_revoked=False,
        )
    )
    db_session.commit()

    body_ban = json.dumps(
        {
            "type": "user.updated",
            "data": {"id": "user_wh_1", "banned": True, "locked": False, "updated_at": (now + 5) * 1000},
        }
    ).encode("utf-8")
    ban_r = client.post(
        "/api/v1/webhooks/clerk",
        content=body_ban,
        headers={
            "svix-id": "msg_ban_1",
            "svix-timestamp": str(now),
            "svix-signature": _sign_svix(settings.CLERK_WEBHOOK_SECRET, "msg_ban_1", now, body_ban),
        },
    )
    assert ban_r.status_code == 200
    db_session.expire_all()
    assert db_session.query(User).filter(User.id == user.id).first().is_active is False
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {raw_tok2}"}).status_code == 401


def test_account_deletion_outbox_retry_dead_letter_and_tech_visibility(
    client, db_session, tech_admin_headers, student_headers
):
    # Test 1: Student deletion when Clerk returns transient 503 -> schedules outbox retry
    stu = db_session.query(User).filter(User.email == "student@bennett.edu.in").first()
    stu.clerk_user_id = "user_del_outbox"
    db_session.commit()
    stu_id_str = str(stu.id)

    with patch(
        "app.services.clerk.delete_clerk_user",
        new_callable=AsyncMock,
        return_value=(False, True, None, "HTTP 503"),
    ):
        del_res = client.delete("/api/v1/auth/account", headers=student_headers)
    assert del_res.status_code == 200

    # Local user & sessions permanently deleted, anonymized AuditLog retained
    assert db_session.query(User).filter(User.id == stu.id).first() is None
    audit_del = db_session.query(AuditLog).filter(AuditLog.action == "account_deleted", AuditLog.target_id == stu_id_str).first()
    assert audit_del is not None
    assert audit_del.user_id is None

    # Outbox item pending with attempts=1
    outbox = db_session.query(ClerkOutbox).filter(ClerkOutbox.clerk_user_id == "user_del_outbox").first()
    assert outbox.status == "pending"
    assert outbox.attempts == 1
    next_retry = (
        outbox.next_retry_at.replace(tzinfo=timezone.utc)
        if outbox.next_retry_at.tzinfo is None
        else outbox.next_retry_at
    )
    assert next_retry > datetime.now(timezone.utc)

    # Tech Admin can inspect outbox; non-Tech denied
    vis_res = client.get("/api/v1/admin/jobs/clerk-outbox?status=pending", headers=tech_admin_headers)
    assert vis_res.status_code == 200
    assert vis_res.json()["pending_count"] == 1
    assert len(vis_res.json()["items"]) == 1

    # Simulate permanent failure (HTTP 403) -> transitions to dead_letter & writes anonymized audit log
    with patch(
        "app.services.clerk.delete_clerk_user",
        new_callable=AsyncMock,
        return_value=(False, False, None, "HTTP 403"),
    ):
        asyncio.run(ClerkOutboxService.dispatch_item(db_session, outbox, jitter=False))

    db_session.expire_all()
    outbox_dl = db_session.query(ClerkOutbox).filter(ClerkOutbox.id == outbox.id).first()
    assert outbox_dl.status == "dead_letter"
    assert db_session.query(AuditLog).filter(AuditLog.action == "clerk_deletion_dead_letter", AuditLog.target_id == str(outbox.id)).first() is not None

    # Tech Admin manual retry recovers dead_letter item cleanly when Clerk succeeds
    with patch(
        "app.services.clerk.delete_clerk_user",
        new_callable=AsyncMock,
        return_value=(True, False, None, None),
    ):
        retry_res = client.post(
            f"/api/v1/admin/jobs/clerk-outbox/{outbox.id}/retry",
            headers=tech_admin_headers,
        )
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "completed"


def test_revoke_all_sessions_endpoint(client, db_session, student_headers):
    res = client.post("/api/v1/auth/sessions/revoke-all", headers=student_headers)
    assert res.status_code == 200
    assert res.json()["revoked_count"] >= 1
    assert client.get("/api/v1/profile/me", headers=student_headers).status_code == 401


def test_async_httpx_used_for_clerk_deletion_and_process_pending(
    client, db_session, tech_admin_headers, clerk_env
):
    from app.services.clerk import delete_clerk_user

    mock_resp = MagicMock()
    mock_resp.status_code = 204
    with patch.object(
        httpx.AsyncClient, "delete", new_callable=AsyncMock, return_value=mock_resp
    ) as mock_del:
        ok, retryable, ra, err = asyncio.run(delete_clerk_user("user_async_check"))
        assert ok is True
        assert mock_del.call_count == 1

    # Verify /admin/jobs/process-clerk-outbox awaits async outbox runner
    outbox_due = ClerkOutbox(
        clerk_user_id="user_due_1",
        audit_target_id=str(uuid.uuid4()),
        operation="delete_user",
        status="pending",
        attempts=0,
        max_attempts=5,
        next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    db_session.add(outbox_due)
    db_session.commit()

    with patch.object(
        httpx.AsyncClient, "delete", new_callable=AsyncMock, return_value=mock_resp
    ):
        proc_res = client.post("/api/v1/admin/jobs/process-clerk-outbox", headers=tech_admin_headers)
    assert proc_res.status_code == 200
    assert proc_res.json()["results"]["completed"] == 1


def test_auth_mode_clerk_guards_and_hybrid_protection(client, db_session):
    clerk_user = User(
        email="hybrid_clerk@bennett.edu.in",
        username="hybrid_clerk",
        clerk_user_id="user_hybrid_1",
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    db_session.add(clerk_user)
    db_session.commit()

    # 1. AUTH_MODE=clerk blocks /verify-email, /forgot-password, /reset-password (410)
    orig_mode = settings.AUTH_MODE
    try:
        settings.AUTH_MODE = "clerk"
        assert client.post("/api/v1/auth/verify-email", json={"token": "abc"}).status_code == 410
        assert client.post("/api/v1/auth/forgot-password", json={"email": "hybrid_clerk@bennett.edu.in"}).status_code == 410
        assert (
            client.post(
                "/api/v1/auth/reset-password",
                json={"token": "abc", "new_password": "NewPassword123!"},
            ).status_code
            == 410
        )

        # 2. Hybrid mode prevents local reset token creation or reset on Clerk-linked accounts
        settings.AUTH_MODE = "hybrid"
        client.post("/api/v1/auth/forgot-password", json={"email": "hybrid_clerk@bennett.edu.in"})
        db_session.expire_all()
        assert db_session.query(User).filter(User.id == clerk_user.id).first().reset_password_token is None

        # Even if a token existed, reset-password refuses modification of Clerk-linked accounts (403)
        clerk_user.reset_password_token = "forced_tok"
        clerk_user.reset_password_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        db_session.commit()
        r = client.post(
            "/api/v1/auth/reset-password",
            json={"token": "forced_tok", "new_password": "NewPassword123!"},
        )
        assert r.status_code == 403
    finally:
        settings.AUTH_MODE = orig_mode


def test_mandatory_clerk_production_config_and_fail_closed_jwt(client, clerk_env):
    from app.core.config import Settings

    # 1. Production Clerk config rejects missing/placeholder values at Settings instantiation
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            AUTH_MODE="clerk",
            CLERK_ISSUER="https://REPLACE_WITH_CLERK_CUSTOM_DOMAIN",
            CLERK_ALLOWED_ORIGINS="https://app.example.com",
            CLERK_SECRET_KEY="sk_live_real",
            CLERK_WEBHOOK_SECRET="whsec_real",
            CLERK_JWKS_URL="https://clerk.example.com/.well-known/jwks.json",
        )

    # 2. verify_clerk_jwt fails closed (500) if CLERK_ISSUER or CLERK_ALLOWED_ORIGINS is empty
    private_key, kid = clerk_env
    now = int(time.time())
    tok = _sign_jwt(
        private_key,
        kid,
        {"sub": "u1", "sid": "s1", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 60},
    )
    orig_iss = settings.CLERK_ISSUER
    orig_azp = settings.CLERK_ALLOWED_ORIGINS
    try:
        settings.CLERK_ISSUER = ""
        assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {tok}"}).status_code == 500
        settings.CLERK_ISSUER = orig_iss
        settings.CLERK_ALLOWED_ORIGINS = ""
        assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {tok}"}).status_code == 500
    finally:
        settings.CLERK_ISSUER = orig_iss
        settings.CLERK_ALLOWED_ORIGINS = orig_azp


def test_role_change_revokes_existing_sessions(
    client, db_session, tech_admin_headers, student_user, student_headers
):
    # Student has valid active session
    assert client.get("/api/v1/profile/me", headers=student_headers).status_code == 200

    # Promote Student -> TECH_ADMIN
    role_res = client.patch(
        f"/api/v1/admin/users/{student_user.id}/role",
        json={"role": "tech_admin"},
        headers=tech_admin_headers,
    )
    assert role_res.status_code == 200

    # Existing Student multi-sessions must immediately be revoked (401)
    assert client.get("/api/v1/profile/me", headers=student_headers).status_code == 401


def test_refresh_rejects_rotated_grace_token(client, db_session, clerk_env):
    private_key, kid = clerk_env
    now = int(time.time())
    jwt_tok = _sign_jwt(
        private_key,
        kid,
        {"sub": "u_grace", "sid": "s_grace", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 300},
    )
    identity = ClerkUserIdentity("u_grace", "grace@bennett.edu.in", True, "Grace", False, False, now * 1000)

    with patch("app.api.v1.auth.fetch_clerk_user", return_value=identity):
        tok1 = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {jwt_tok}"}).json()["session_token"]
        tok2 = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {jwt_tok}"}).json()["session_token"]

    # tok1 is inside 30s grace window -> GET still works, but /auth/refresh MUST reject tok1 (401)
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {tok1}"}).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"session_token": tok1}).status_code == 401

    # tok2 is primary -> /auth/refresh succeeds
    assert client.post("/api/v1/auth/refresh", json={"session_token": tok2}).status_code == 200


def test_jwt_requires_non_empty_kid_and_valid_rsa_sig_jwk(client, clerk_env):
    private_key, kid = clerk_env
    now = int(time.time())

    # 1. Missing kid in JWT header -> 401
    h_b64 = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    p_b64 = _b64url(
        json.dumps(
            {"sub": "u_nk", "sid": "s_nk", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 60}
        ).encode("utf-8")
    )
    sig = private_key.sign(f"{h_b64}.{p_b64}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    no_kid_jwt = f"{h_b64}.{p_b64}.{_b64url(sig)}"
    assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {no_kid_jwt}"}).status_code == 401

    # 2. JWK with use="enc" instead of "sig" must be rejected
    parsed = json.loads(settings.CLERK_JWT_KEY)
    parsed["keys"][0]["use"] = "enc"
    orig_key = settings.CLERK_JWT_KEY
    try:
        settings.CLERK_JWT_KEY = json.dumps(parsed)
        valid_tok = _sign_jwt(
            private_key,
            kid,
            {"sub": "u_enc", "sid": "s_enc", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 60},
        )
        assert client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {valid_tok}"}).status_code == 401
    finally:
        settings.CLERK_JWT_KEY = orig_key


def test_concurrent_first_time_clerk_signup_integrity_recovery(client, db_session, clerk_env):
    from sqlalchemy.exc import IntegrityError

    private_key, kid = clerk_env
    now = int(time.time())
    jwt_tok = _sign_jwt(
        private_key,
        kid,
        {"sub": "u_race", "sid": "s_race", "iss": "https://clerk.test.local", "azp": "https://atc-dsa.pages.dev", "exp": now + 300},
    )
    identity = ClerkUserIdentity("u_race", "race@bennett.edu.in", True, "Race User", False, False, now * 1000)

    # Pre-seed user row representing a concurrent request committing right before this savepoint
    winner = User(
        email="race@bennett.edu.in",
        username="race",
        clerk_user_id="u_race",
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    db_session.add(winner)
    db_session.commit()

    # Force initial lookup to miss (simulating pre-commit race), then hit IntegrityError on savepoint flush
    real_query = db_session.query
    first_miss_done = {"sub_miss": False, "email_miss": False}

    class EmptyFirstQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    def race_query(*entities, **kwargs):
        if entities and entities[0] is User:
            if not first_miss_done["sub_miss"]:
                first_miss_done["sub_miss"] = True
                return EmptyFirstQuery()
            if not first_miss_done["email_miss"]:
                first_miss_done["email_miss"] = True
                return EmptyFirstQuery()
        return real_query(*entities, **kwargs)

    with patch("app.api.v1.auth.fetch_clerk_user", return_value=identity):
        with patch.object(db_session, "query", side_effect=race_query):
            res = client.post("/api/v1/auth/clerk/exchange", headers={"Authorization": f"Bearer {jwt_tok}"})
    assert res.status_code == 200
    assert "session_token" in res.json()


def test_migration_003_downgrade_fails_safely_on_null_passwords():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    mig_path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "003_clerk_auth_policies.py"
    )
    spec = importlib.util.spec_from_file_location("mig_003", str(mig_path))
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    sqlite_eng = create_engine("sqlite:///:memory:")
    with sqlite_eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, hashed_password TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password) VALUES ('1', 'clerk@bennett.edu.in', NULL)"
            )
        )
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            with pytest.raises(RuntimeError, match="Cannot safely downgrade migration 003"):
                mig.downgrade()


def test_email_sync_conflict_webhook_audits_and_revokes_sessions(client, db_session, clerk_env):
    now = int(time.time())
    user1 = User(
        email="u1@bennett.edu.in",
        username="u1_sync",
        clerk_user_id="user_sync_1",
        clerk_updated_at=now * 1000,
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    user2 = User(
        email="u2@bennett.edu.in",
        username="u2_sync",
        clerk_user_id="user_sync_2",
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    db_session.add_all([user1, user2])
    db_session.flush()

    raw_tok = generate_session_token(48)
    db_session.add(
        UserSession(
            user_id=user1.id,
            token=hash_session_token(raw_tok),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            is_revoked=False,
        )
    )
    db_session.commit()

    # Deliver user.updated attempting to change user1's email to user2's email
    payload = {
        "type": "user.updated",
        "data": {
            "id": "user_sync_1",
            "primary_email_address_id": "em_1",
            "email_addresses": [
                {"id": "em_1", "email_address": "u2@bennett.edu.in", "verification": {"status": "verified"}}
            ],
            "banned": False,
            "locked": False,
            "updated_at": (now + 10) * 1000,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    r = client.post(
        "/api/v1/webhooks/clerk",
        content=body,
        headers={
            "svix-id": "msg_conflict_1",
            "svix-timestamp": str(now),
            "svix-signature": _sign_svix(settings.CLERK_WEBHOOK_SECRET, "msg_conflict_1", now, body),
        },
    )
    assert r.status_code == 200
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "clerk_email_sync_conflict", AuditLog.target_id == str(user1.id))
        .first()
        is not None
    )
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {raw_tok}"}).status_code == 401


def test_read_request_debounces_redundant_session_expiry_writes(client, db_session, student_user):
    raw_tok = generate_session_token(48)
    initial_exp = datetime.now(timezone.utc) + timedelta(days=7)
    sess = UserSession(
        user_id=student_user.id,
        token=hash_session_token(raw_tok),
        expires_at=initial_exp,
        is_revoked=False,
    )
    db_session.add(sess)
    db_session.commit()

    # Rapid GET within 15m debounce threshold must NOT update expires_at
    assert client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {raw_tok}"}).status_code == 200
    db_session.expire_all()
    sess_after = db_session.query(UserSession).filter(UserSession.id == sess.id).first()
    exp_after = sess_after.expires_at.replace(tzinfo=timezone.utc) if sess_after.expires_at.tzinfo is None else sess_after.expires_at
    assert exp_after == initial_exp

