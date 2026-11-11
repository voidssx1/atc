import asyncio
import hashlib
import base64
import pytest
from unittest.mock import patch

from app.core.config import settings
from app.core.security import (
    _HAS_BCRYPT,
    hash_password,
    hash_password_sync,
    is_legacy_bcrypt_hash,
    verify_password,
    verify_password_sync,
)


def test_async_pbkdf2_hash_and_verification():
    async def run():
        password = "SuperSecretPassword123!@#"
        hashed = await hash_password(password, iterations=2000)

        # Format verification
        assert hashed.startswith("$pbkdf2-sha256$i=2000$")
        parts = hashed.split("$")
        assert len(parts) == 5
        assert parts[1] == "pbkdf2-sha256"
        assert parts[2] == "i=2000"

        # Salt is 16 bytes, derived key is 32 bytes (256 bits)
        salt_bytes = base64.b64decode(parts[3])
        dk_bytes = base64.b64decode(parts[4])
        assert len(salt_bytes) == 16
        assert len(dk_bytes) == 32

        # Positive verification
        assert await verify_password(password, hashed) is True

        # Negative verification
        assert await verify_password("WrongPassword123!@#", hashed) is False
        assert await verify_password("", hashed) is False
        assert await verify_password(password, "") is False

    asyncio.run(run())


def test_unique_salts_produce_different_hashes():
    async def run():
        password = "IdenticalPassword123!"
        hash1 = await hash_password(password, iterations=2000)
        hash2 = await hash_password(password, iterations=2000)
        assert hash1 != hash2
        assert await verify_password(password, hash1) is True
        assert await verify_password(password, hash2) is True

    asyncio.run(run())


def test_malformed_hashes_fail_safely():
    async def run():
        password = "SomePassword123!"
        malformed_cases = [
            "",
            "notahash",
            "$pbkdf2-sha256$i=1000$invalid",
            "$pbkdf2-sha256$baditer$salt$hash",
            "$pbkdf2-sha256$i=abc$AAAA$BBBB",
            "$pbkdf2-sha256$i=1000$!!!invalidbase64$!!!invalidbase64",
            "$unknown$i=1000$AAAA$BBBB",
        ]
        for h in malformed_cases:
            assert await verify_password(password, h) is False
            assert verify_password_sync(password, h) is False

    asyncio.run(run())


def test_production_enforces_600000_iterations():
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "production"
        # Even if lower iterations are passed, production must enforce 600,000
        with patch("app.core.security._derive_pbkdf2_cpython", return_value=b"0" * 32) as mock_derive:
            hashed = hash_password_sync("Password123!", iterations=1000)
            assert "$pbkdf2-sha256$i=600000$" in hashed
            assert mock_derive.call_args[0][2] == 600_000
    finally:
        settings.ENVIRONMENT = orig_env


def test_pbkdf2_rfc_compatibility():
    # Known standard PBKDF2-HMAC-SHA256 vector compatibility check
    password = "TestSecretPassword"
    salt = b"1234567890abcdef"
    iters = 1000
    expected_hex = "2193ffac28387a529b4b00af0d71bc268ea8c93c7c2901212654cd7b3a773e2e"

    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, dklen=32)
    assert dk.hex() == expected_hex

    dk_b64 = base64.b64encode(dk).decode("ascii")
    formatted = f"$pbkdf2-sha256$i={iters}${salt_b64}${dk_b64}"
    assert verify_password_sync(password, formatted) is True


def test_legacy_bcrypt_handling_dev_vs_prod():
    if not _HAS_BCRYPT:
        pytest.skip("bcrypt not installed")
    import bcrypt

    password = "LegacyBcryptPassword456!"
    salt = bcrypt.gensalt(rounds=4)
    legacy_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    assert is_legacy_bcrypt_hash(legacy_hash) is True

    # In development, legacy bcrypt still verifies if bcrypt is installed
    assert verify_password_sync(password, legacy_hash) is True
    assert verify_password_sync("IncorrectPassword", legacy_hash) is False

    # In production, expensive bcrypt verification must be safely suppressed
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "production"
        assert verify_password_sync(password, legacy_hash) is False
    finally:
        settings.ENVIRONMENT = orig_env


def test_login_with_legacy_bcrypt_guides_password_reset(client, db_session):
    if not _HAS_BCRYPT:
        pytest.skip("bcrypt not installed")
    import bcrypt
    from app.models.user import User, UserRole

    legacy_hash = bcrypt.hashpw(b"OldPassword123!", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user = User(
        email="legacy_user@atc.bennett.edu.in",
        username="legacy_user",
        hashed_password=legacy_hash,
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.commit()

    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "production"
        res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "legacy_user@atc.bennett.edu.in",
                "password": "OldPassword123!",
            },
        )
        assert res.status_code == 403
        assert "forgot-password" in res.json()["detail"].lower()
    finally:
        settings.ENVIRONMENT = orig_env
