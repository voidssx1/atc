def test_student_registration_and_login(client, db_session):
    from app.models.user import UserSession

    # 1. Register student (exact @bennett.edu.in required)
    reg_payload = {
        "email": "freshstudent@bennett.edu.in",
        "password": "Password123!",
        "username": "freshstudent",
        "full_name": "Fresh Student",
    }
    reg_res = client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == 201
    user_data = reg_res.json()
    assert user_data["email"] == "freshstudent@bennett.edu.in"
    assert user_data["role"] == "student"
    assert user_data["is_verified"] is False

    # Subdomain or external domain rejected
    sub_res = client.post(
        "/api/v1/auth/register",
        json={
            "email": "student@atc.bennett.edu.in",
            "password": "Password123!",
        },
    )
    assert sub_res.status_code == 403

    # 2. Duplicate registration should fail
    dup_res = client.post("/api/v1/auth/register", json=reg_payload)
    assert dup_res.status_code == 400

    # 3. Login returns opaque server-side session token (stored hashed in DB)
    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "freshstudent@bennett.edu.in", "password": "Password123!"},
    )
    assert login_res.status_code == 200
    token_data = login_res.json()
    assert "session_token" in token_data
    assert "access_token" in token_data

    # Ensure plaintext token is NOT stored in Neon/DB
    db_sess = db_session.query(UserSession).filter(UserSession.token == token_data["session_token"]).first()
    assert db_sess is None

    # 4. Refresh / extend session
    refresh_res = client.post(
        "/api/v1/auth/refresh",
        json={"session_token": token_data["session_token"]},
    )
    assert refresh_res.status_code == 200
    new_token = refresh_res.json()
    assert "session_token" in new_token

    # 5. Logout revokes the server-side session in the database
    headers = {"Authorization": f"Bearer {new_token['session_token']}"}
    logout_res = client.post(
        "/api/v1/auth/logout",
        json={"session_token": new_token["session_token"]},
        headers=headers,
    )
    assert logout_res.status_code == 200

    # Revoked session can no longer access protected endpoints (401)
    revoked_check = client.get("/api/v1/profile/me", headers=headers)
    assert revoked_check.status_code == 401

    # 6. Re-login and delete account
    relogin_res = client.post(
        "/api/v1/auth/login",
        json={"email": "freshstudent@bennett.edu.in", "password": "Password123!"},
    )
    fresh_headers = {"Authorization": f"Bearer {relogin_res.json()['session_token']}"}
    del_res = client.delete("/api/v1/auth/account", headers=fresh_headers)
    assert del_res.status_code == 200


def test_forgot_and_reset_password(client, db_session):
    from app.models.user import User

    # Register user
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "resetuser@bennett.edu.in",
            "password": "OldPassword123!",
            "username": "resetuser",
        },
    )

    # Forgot password
    forgot_res = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "resetuser@bennett.edu.in"},
    )
    assert forgot_res.status_code == 200

    # Fetch token directly from db for testing
    user = (
        db_session.query(User)
        .filter(User.email == "resetuser@bennett.edu.in")
        .first()
    )
    assert user.reset_password_token is not None

    # Reset password
    reset_res = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": user.reset_password_token,
            "new_password": "BrandNewPassword123!",
        },
    )
    assert reset_res.status_code == 200

    # Verify login with new password
    login_res = client.post(
        "/api/v1/auth/login",
        json={
            "email": "resetuser@bennett.edu.in",
            "password": "BrandNewPassword123!",
        },
    )
    assert login_res.status_code == 200


def test_session_inactivity_and_max_lifetime_expiry(client, db_session):
    from datetime import datetime, timedelta, timezone
    from app.models.user import User, UserSession
    from app.core.security import (
        hash_password_sync,
        generate_session_token,
        hash_session_token,
    )

    # Setup student
    user = User(
        email="expiry_test@bennett.edu.in",
        username="expiry_test",
        hashed_password=hash_password_sync("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    # 1. Test 7-day inactivity expiration
    # Session created 8 days ago, expires_at was 1 day ago
    inactive_token = generate_session_token(48)
    inactive_session = UserSession(
        user_id=user.id,
        token=hash_session_token(inactive_token),
        created_at=datetime.now(timezone.utc) - timedelta(days=8),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        is_revoked=False,
    )
    db_session.add(inactive_session)
    db_session.commit()

    inactive_res = client.get(
        "/api/v1/profile/me",
        headers={"Authorization": f"Bearer {inactive_token}"},
    )
    assert inactive_res.status_code == 401
    assert "inactivity" in inactive_res.json()["detail"].lower()

    # 2. Test 30-day maximum lifetime expiration
    # Session created 31 days ago (active recently, but past 30 days)
    old_token = generate_session_token(48)
    old_session = UserSession(
        user_id=user.id,
        token=hash_session_token(old_token),
        created_at=datetime.now(timezone.utc) - timedelta(days=31),
        expires_at=datetime.now(timezone.utc) + timedelta(days=5),
        is_revoked=False,
    )
    db_session.add(old_session)
    db_session.commit()

    old_res = client.get(
        "/api/v1/profile/me",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert old_res.status_code == 401
    assert "30-day" in old_res.json()["detail"].lower()
