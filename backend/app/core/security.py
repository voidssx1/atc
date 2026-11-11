import base64
import hashlib
import hmac
import logging
import secrets
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger("security")

# Detect Pyodide / Cloudflare Python Workers JavaScript environment
try:
    import js
    from pyodide.ffi import to_js

    _HAS_WEB_CRYPTO = hasattr(js, "crypto") and hasattr(js.crypto, "subtle")
except (ImportError, AttributeError):
    js = None
    to_js = None
    _HAS_WEB_CRYPTO = False

# Detect bcrypt for optional legacy verification in non-production environments
try:
    import bcrypt

    _HAS_BCRYPT = True
except ImportError:
    bcrypt = None
    _HAS_BCRYPT = False


def is_legacy_bcrypt_hash(hashed_password: Optional[str]) -> bool:
    """Check if a stored password hash uses legacy bcrypt format ($2a$, $2b$, $2y$)."""
    if not hashed_password:
        return False
    return hashed_password.startswith(("$2a$", "$2b$", "$2y$"))


async def _derive_pbkdf2_webcrypto(password: str, salt: bytes, iterations: int) -> bytes:
    """Derive 256 bits (32 bytes) using Cloudflare Worker native Web Crypto API."""
    pw_bytes = password.encode("utf-8")
    pw_js = to_js(pw_bytes)
    salt_js = to_js(salt)

    # In Pyodide, constructing a JS Object directly ensures proper property lookup by Web Crypto
    algo = js.Object.new()
    algo.name = "PBKDF2"
    algo.salt = salt_js
    algo.iterations = iterations
    algo.hash = "SHA-256"

    key = await js.crypto.subtle.importKey(
        "raw",
        pw_js,
        "PBKDF2",
        False,
        to_js(["deriveBits"]),
    )

    derived_bits = await js.crypto.subtle.deriveBits(
        algo,
        key,
        256,  # 256 bits = 32 bytes
    )
    return bytes(js.Uint8Array.new(derived_bits))


def _derive_pbkdf2_cpython(password: str, salt: bytes, iterations: int) -> bytes:
    """Derive 256 bits (32 bytes) using standard CPython hashlib.pbkdf2_hmac."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=32,
    )


async def derive_pbkdf2_key(password: str, salt: bytes, iterations: int) -> bytes:
    """
    Derive a 32-byte PBKDF2-HMAC-SHA256 key.
    
    Uses Cloudflare Web Crypto in Workers runtime; falls back to CPython hashlib
    in local development, tests, or when Web Crypto is unavailable.
    """
    if _HAS_WEB_CRYPTO:
        try:
            return await _derive_pbkdf2_webcrypto(password, salt, iterations)
        except Exception as e:
            logger.warning(f"Web Crypto deriveBits encountered exception: {e}; falling back to hashlib")
            return _derive_pbkdf2_cpython(password, salt, iterations)
    return _derive_pbkdf2_cpython(password, salt, iterations)


def _resolve_iterations(iterations: Optional[int] = None) -> int:
    """Enforce strict 600,000 iterations in production environment."""
    cfg = get_settings()
    if cfg.ENVIRONMENT.lower() == "production":
        return 600_000
    return iterations or cfg.PBKDF2_ITERATIONS


async def hash_password(password: str, iterations: Optional[int] = None) -> str:
    """
    Asynchronously hash a password using PBKDF2-HMAC-SHA256.
    
    Requirements:
      - 16-byte cryptographically secure unique salt per password
      - 256-bit (32-byte) derived key
      - 600,000 iterations enforced in production
      - Versioned, self-describing modular crypt format:
          $pbkdf2-sha256$i=<iterations>$<salt_b64>$<hash_b64>
    """
    iters = _resolve_iterations(iterations)
    salt = secrets.token_bytes(16)
    dk = await derive_pbkdf2_key(password, salt, iters)

    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"$pbkdf2-sha256$i={iters}${salt_b64}${dk_b64}"


def hash_password_sync(password: str, iterations: Optional[int] = None) -> str:
    """Synchronous password hashing for test fixtures and seed scripts."""
    iters = _resolve_iterations(iterations)
    salt = secrets.token_bytes(16)
    dk = _derive_pbkdf2_cpython(password, salt, iters)

    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"$pbkdf2-sha256$i={iters}${salt_b64}${dk_b64}"


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Asynchronously verify a password against a stored hash using constant-time comparison.
    
    Supports:
      1. $pbkdf2-sha256$ format with iteration metadata and unique salt.
      2. Legacy bcrypt hashes: In production/Workers, expensive bcrypt verification is
         safely suppressed to protect the 10ms CPU limit; existing accounts are guided
         to reset their password. In local dev, bcrypt is checked if available.
    """
    if not plain_password or not hashed_password:
        return False

    try:
        if hashed_password.startswith("$pbkdf2-sha256$"):
            parts = hashed_password.split("$")
            if len(parts) != 5:
                return False
            iter_part = parts[2]
            if not iter_part.startswith("i="):
                return False
            iters = int(iter_part[2:])
            salt = base64.b64decode(parts[3].encode("ascii"))
            expected_dk = base64.b64decode(parts[4].encode("ascii"))

            computed_dk = await derive_pbkdf2_key(plain_password, salt, iters)
            return hmac.compare_digest(computed_dk, expected_dk)

        if is_legacy_bcrypt_hash(hashed_password):
            # In production / Workers, reject expensive bcrypt execution to protect CPU quota
            if get_settings().ENVIRONMENT.lower() == "production" or _HAS_WEB_CRYPTO:
                logger.warning("Legacy bcrypt hash encountered in production; suppression applied.")
                return False

            if _HAS_BCRYPT:
                return bcrypt.checkpw(
                    plain_password.encode("utf-8"),
                    hashed_password.encode("utf-8"),
                )
            return False

        return False
    except Exception:
        return False


def verify_password_sync(plain_password: str, hashed_password: str) -> bool:
    """Synchronous password verification for test fixtures and offline utilities."""
    if not plain_password or not hashed_password:
        return False

    try:
        if hashed_password.startswith("$pbkdf2-sha256$"):
            parts = hashed_password.split("$")
            if len(parts) != 5:
                return False
            iter_part = parts[2]
            if not iter_part.startswith("i="):
                return False
            iters = int(iter_part[2:])
            salt = base64.b64decode(parts[3].encode("ascii"))
            expected_dk = base64.b64decode(parts[4].encode("ascii"))

            computed_dk = _derive_pbkdf2_cpython(plain_password, salt, iters)
            return hmac.compare_digest(computed_dk, expected_dk)

        if is_legacy_bcrypt_hash(hashed_password):
            if get_settings().ENVIRONMENT.lower() == "production":
                return False
            if _HAS_BCRYPT:
                return bcrypt.checkpw(
                    plain_password.encode("utf-8"),
                    hashed_password.encode("utf-8"),
                )
            return False

        return False
    except Exception:
        return False


def generate_session_token(nbytes: int = 48) -> str:
    """Generate an opaque, high-entropy server-side session token."""
    return secrets.token_urlsafe(nbytes)


def hash_session_token(raw_token: str) -> str:
    """
    Compute deterministic SHA-256 hex digest of an opaque session token.
    
    Never store plaintext application session tokens in Neon.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def is_allowed_student_email(email: str) -> bool:
    """
    Enforce exact PRD student domain rule (@bennett.edu.in only).
    Rejects subdomains (e.g. @atc.bennett.edu.in) and external domains.
    """
    cleaned = (email or "").lower().strip()
    if "@" not in cleaned or cleaned.count("@") != 1:
        return False
    local_part, domain = cleaned.split("@", 1)
    if not local_part:
        return False
    return domain == get_settings().STUDENT_ALLOWED_EMAIL_DOMAIN.lower().strip()


def generate_random_token(nbytes: int = 32) -> str:
    """Generate a high-entropy random URL-safe token for email verification / password resets."""
    return secrets.token_urlsafe(nbytes)
