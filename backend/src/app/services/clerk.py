import base64
import hashlib
import hmac
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.webcrypto import call_subtle, get_js_ffi, has_web_crypto
from app.models.analytics import AuditLog
from app.models.clerk import ClerkOutbox

logger = logging.getLogger("clerk_service")

# Optional CPython fallback for RS256 verification in local tests/CLI
try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False


class ClerkJWTVerificationError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ClerkAPIError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class WebhookVerificationError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ClerkUserIdentity:
    clerk_user_id: str
    email: Optional[str]
    email_verified: bool
    full_name: Optional[str]
    banned: bool
    locked: bool
    updated_at: Optional[int]


def _b64url_decode(data: str) -> bytes:
    padding_len = (4 - len(data) % 4) % 4
    return base64.urlsafe_b64decode(data + ("=" * padding_len))


# Module-level JWKS cache keyed by JWKS URL -> (fetched_at_monotonic, keys_list)
_jwks_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_JWKS_CACHE_TTL_SECONDS = 3600.0


async def _fetch_jwks_keys(jwks_url: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
    now_mono = time.monotonic()
    if not force_refresh and jwks_url in _jwks_cache:
        cached_at, keys = _jwks_cache[jwks_url]
        if (now_mono - cached_at) < _JWKS_CACHE_TTL_SECONDS:
            return keys

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(jwks_url)
        if resp.status_code != 200:
            raise ClerkJWTVerificationError(
                f"Failed to fetch Clerk JWKS (HTTP {resp.status_code})", status_code=503
            )
        data = resp.json()
        keys = data.get("keys", [])
        if not isinstance(keys, list) or not keys:
            raise ClerkJWTVerificationError("Clerk JWKS contained no keys", status_code=503)
        _jwks_cache[jwks_url] = (now_mono, keys)
        return keys
    except ClerkJWTVerificationError:
        raise
    except Exception as e:
        raise ClerkJWTVerificationError(
            f"Unable to reach Clerk JWKS endpoint: {type(e).__name__}", status_code=503
        )


def _is_valid_rsa_sig_jwk(k: Dict[str, Any], kid: str) -> bool:
    """Strictly match JWK by kid, RSA key type, signing usage, and RS256 algorithm."""
    if not isinstance(k, dict) or k.get("kid") != kid:
        return False
    if k.get("kty") != "RSA":
        return False
    if "use" in k and k.get("use") != "sig":
        return False
    if "alg" in k and k.get("alg") != "RS256":
        return False
    return bool(k.get("n") and k.get("e"))


async def _resolve_jwk(kid: str) -> Dict[str, Any]:
    cfg = get_settings()
    # 1. Static JWK/JWKS key if configured
    if cfg.CLERK_JWT_KEY:
        try:
            parsed = json.loads(cfg.CLERK_JWT_KEY)
            if "keys" in parsed and isinstance(parsed["keys"], list):
                keys = parsed["keys"]
            else:
                keys = [parsed]
            for k in keys:
                if _is_valid_rsa_sig_jwk(k, kid):
                    return k
        except Exception:
            pass
        if not cfg.CLERK_JWKS_URL:
            raise ClerkJWTVerificationError("Matching RSA signing key not found in Clerk JWK")

    # 2. Remote JWKS URL
    jwks_url = cfg.CLERK_JWKS_URL
    if cfg._is_placeholder(jwks_url):
        raise ClerkJWTVerificationError("Clerk JWKS configuration is missing or placeholder", status_code=500)

    keys = await _fetch_jwks_keys(jwks_url, force_refresh=False)
    for k in keys:
        if _is_valid_rsa_sig_jwk(k, kid):
            return k

    # Key rotation: force refresh once if kid not found in cache
    keys = await _fetch_jwks_keys(jwks_url, force_refresh=True)
    for k in keys:
        if _is_valid_rsa_sig_jwk(k, kid):
            return k

    raise ClerkJWTVerificationError("Matching RSA signing key not found in Clerk JWKS")


async def _verify_rs256_webcrypto(jwk: Dict[str, Any], signing_input: bytes, signature: bytes) -> bool:
    js_mod, to_js_fn = get_js_ffi()
    if js_mod is None or to_js_fn is None:
        raise RuntimeError("Pyodide JS runtime is unavailable")

    global_obj = getattr(js_mod, "globalThis", js_mod)
    object_ctor = getattr(js_mod, "Object", None) or getattr(global_obj, "Object")

    jwk_js = to_js_fn(jwk, dict_converter=object_ctor.fromEntries)
    algo = object_ctor.new()
    algo.name = "RSASSA-PKCS1-v1_5"
    algo.hash = "SHA-256"

    crypto_key = await call_subtle(
        "importKey",
        "jwk",
        jwk_js,
        algo,
        False,
        to_js_fn(["verify"]),
    )
    return bool(
        await call_subtle(
            "verify",
            "RSASSA-PKCS1-v1_5",
            crypto_key,
            to_js_fn(signature),
            to_js_fn(signing_input),
        )
    )


def _verify_rs256_cpython(jwk: Dict[str, Any], signing_input: bytes, signature: bytes) -> bool:
    if not _HAS_CRYPTOGRAPHY:
        raise ClerkJWTVerificationError(
            "No RS256 verification provider available in environment", status_code=500
        )
    if jwk.get("kty") != "RSA" or "n" not in jwk or "e" not in jwk:
        raise ClerkJWTVerificationError("Invalid RSA JWK format")

    n_int = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    e_int = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    pub_key = rsa.RSAPublicNumbers(e_int, n_int).public_key()
    try:
        pub_key.verify(
            signature,
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        return False


async def verify_clerk_jwt(token: str) -> Dict[str, Any]:
    """
    Cryptographically verify a Clerk session JWT (RS256) and validate strict claims.
    Never trusts unsigned headers or unverified frontend payloads.
    """
    if not token or not isinstance(token, str):
        raise ClerkJWTVerificationError("Missing Clerk session token")

    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ClerkJWTVerificationError("Malformed JWT structure")

    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        claims = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        signature = _b64url_decode(sig_b64)
    except Exception:
        raise ClerkJWTVerificationError("Unable to decode JWT segments")

    # 1. Strict algorithm and key ID (kid) enforcement
    if header.get("alg") != "RS256":
        raise ClerkJWTVerificationError("Unsupported JWT algorithm; RS256 required")

    kid = header.get("kid")
    if not kid or not isinstance(kid, str) or not kid.strip():
        raise ClerkJWTVerificationError("Clerk JWT header missing required key ID (kid)")

    cfg = get_settings()

    # 2. Fail-closed verification of mandatory Clerk configuration
    if cfg._is_placeholder(cfg.CLERK_ISSUER):
        raise ClerkJWTVerificationError(
            "CLERK_ISSUER must be configured with a valid issuer URL", status_code=500
        )
    allowed_azps = cfg.get_clerk_allowed_origins()
    if not allowed_azps or any(cfg._is_placeholder(o) for o in allowed_azps):
        raise ClerkJWTVerificationError(
            "CLERK_ALLOWED_ORIGINS must be configured with valid allowed origins", status_code=500
        )

    jwk = await _resolve_jwk(kid)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    # 3. Verify RS256 cryptographic signature
    if has_web_crypto():
        valid_sig = await _verify_rs256_webcrypto(jwk, signing_input, signature)
    else:
        valid_sig = _verify_rs256_cpython(jwk, signing_input, signature)

    if not valid_sig:
        raise ClerkJWTVerificationError("Invalid Clerk JWT signature")

    # 4. Validate timestamps with 30s clock-skew tolerance
    now = int(time.time())
    leeway = 30

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp < (now - leeway):
        raise ClerkJWTVerificationError("Clerk session token has expired")

    nbf = claims.get("nbf")
    if nbf is not None:
        if not isinstance(nbf, (int, float)) or nbf > (now + leeway):
            raise ClerkJWTVerificationError("Clerk session token is not yet valid")

    # 5. Mandatory issuer enforcement
    if claims.get("iss") != cfg.CLERK_ISSUER.rstrip("/"):
        raise ClerkJWTVerificationError("Invalid Clerk token issuer")

    # 6. Mandatory authorized party (azp) enforcement
    azp = claims.get("azp")
    if not azp or azp not in allowed_azps:
        raise ClerkJWTVerificationError("Unauthorized token azp origin")

    # 6. Validate required Clerk identity claims (sub + sid)
    sub = claims.get("sub")
    sid = claims.get("sid")
    if not sub or not isinstance(sub, str) or not sid or not isinstance(sid, str):
        raise ClerkJWTVerificationError("Clerk JWT missing required sub or sid claim")

    return claims


def parse_clerk_user_payload(data: Dict[str, Any]) -> ClerkUserIdentity:
    """Extract authoritative email, verification status, and lock/ban flags from Clerk User JSON."""
    clerk_user_id = data.get("id", "")
    primary_email_id = data.get("primary_email_address_id")
    email_addresses = data.get("email_addresses") or []

    chosen_email: Optional[str] = None
    verified = False

    for entry in email_addresses:
        if entry.get("id") == primary_email_id or (not primary_email_id and len(email_addresses) == 1):
            chosen_email = (entry.get("email_address") or "").lower().strip() or None
            verification = entry.get("verification") or {}
            verified = verification.get("status") == "verified"
            break

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    full_name = f"{first_name} {last_name}".strip() or None

    return ClerkUserIdentity(
        clerk_user_id=clerk_user_id,
        email=chosen_email,
        email_verified=verified,
        full_name=full_name,
        banned=bool(data.get("banned", False)),
        locked=bool(data.get("locked", False)),
        updated_at=data.get("updated_at"),
    )


async def fetch_clerk_user(clerk_user_id: str) -> ClerkUserIdentity:
    """Fetch authoritative user record from Clerk Backend API."""
    cfg = get_settings()
    if not cfg.CLERK_SECRET_KEY:
        raise ClerkAPIError("CLERK_SECRET_KEY is not configured", status_code=500)

    url = f"https://api.clerk.com/v1/users/{clerk_user_id}"
    headers = {"Authorization": f"Bearer {cfg.CLERK_SECRET_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:
        logger.warning(f"Clerk user fetch network failure for {clerk_user_id}: {type(e).__name__}")
        raise ClerkAPIError("Unable to verify identity with authentication provider", status_code=503)

    if resp.status_code == 404:
        raise ClerkAPIError("Clerk user account not found", status_code=401)
    if resp.status_code != 200:
        logger.warning(f"Clerk Backend API returned HTTP {resp.status_code} fetching user")
        raise ClerkAPIError("Authentication provider error", status_code=502)

    return parse_clerk_user_payload(resp.json())


async def delete_clerk_user(clerk_user_id: str) -> Tuple[bool, bool, Optional[int], Optional[str]]:
    """
    Asynchronously delete a user from Clerk Backend API via httpx.AsyncClient.
    Returns (success, is_retryable, retry_after_seconds, sanitized_error).
    """
    cfg = get_settings()
    if not cfg.CLERK_SECRET_KEY:
        return False, True, None, "CLERK_SECRET_KEY not configured"

    url = f"https://api.clerk.com/v1/users/{clerk_user_id}"
    headers = {"Authorization": f"Bearer {cfg.CLERK_SECRET_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.delete(url, headers=headers)
    except httpx.RequestError as e:
        return False, True, None, f"NetworkError: {type(e).__name__}"
    except Exception as e:
        return False, True, None, f"UnexpectedError: {type(e).__name__}"

    if resp.status_code in (200, 204, 404):
        return True, False, None, None

    if resp.status_code == 429:
        retry_after = None
        ra_hdr = resp.headers.get("Retry-After")
        if ra_hdr and ra_hdr.isdigit():
            retry_after = int(ra_hdr)
        return False, True, retry_after, "HTTP 429 Rate Limited"

    if resp.status_code >= 500:
        return False, True, None, f"HTTP {resp.status_code}"

    # Permanent 4xx client errors (400, 401, 403)
    return False, False, None, f"HTTP {resp.status_code}"


def verify_svix_webhook(
    raw_body: bytes,
    svix_id: Optional[str],
    svix_timestamp: Optional[str],
    svix_signature: Optional[str],
    tolerance_seconds: int = 300,
) -> int:
    """
    Verify Svix webhook signature and timestamp replay window.
    Returns parsed integer svix_timestamp on success.
    """
    cfg = get_settings()
    if not cfg.CLERK_WEBHOOK_SECRET:
        raise WebhookVerificationError("Webhook secret is not configured", status_code=500)

    if not svix_id or not svix_timestamp or not svix_signature:
        raise WebhookVerificationError("Missing required Svix signature headers", status_code=400)

    try:
        ts = int(svix_timestamp)
    except ValueError:
        raise WebhookVerificationError("Invalid svix-timestamp header", status_code=400)

    now = int(time.time())
    if abs(now - ts) > tolerance_seconds:
        raise WebhookVerificationError("Stale or future webhook timestamp rejected", status_code=400)

    secret_str = cfg.CLERK_WEBHOOK_SECRET
    if secret_str.startswith("whsec_"):
        secret_str = secret_str[len("whsec_"):]
    try:
        secret_bytes = base64.b64decode(secret_str)
    except Exception:
        raise WebhookVerificationError("Invalid webhook signing secret configuration", status_code=500)

    signed_content = f"{svix_id}.{svix_timestamp}.{raw_body.decode('utf-8')}".encode("utf-8")
    expected_sig_b64 = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode("ascii")

    # svix-signature may contain space-separated versioned signatures: "v1,abc v1,def"
    for token in svix_signature.split(" "):
        parts = token.split(",", 1)
        if len(parts) == 2 and parts[0] == "v1":
            if hmac.compare_digest(parts[1], expected_sig_b64):
                return ts

    raise WebhookVerificationError("Invalid Svix webhook signature", status_code=401)


class ClerkOutboxService:
    """Manages exponential backoff retries, dead-letter transitions, and privacy-safe audit logs."""

    BACKOFF_SECONDS = [60, 300, 900, 3600]  # 1m, 5m, 15m, 60m

    @classmethod
    def calculate_backoff(cls, attempt_num: int, retry_after: Optional[int] = None, jitter: bool = True) -> int:
        idx = min(max(attempt_num - 1, 0), len(cls.BACKOFF_SECONDS) - 1)
        base = cls.BACKOFF_SECONDS[idx]
        if retry_after and retry_after > base:
            base = retry_after
        if jitter:
            base += random.randint(0, max(1, int(base * 0.1)))
        return base

    @classmethod
    async def dispatch_item(cls, db: Session, item: ClerkOutbox, jitter: bool = True) -> str:
        """
        Attempt to dispatch a single outbox item asynchronously.
        Updates status/attempts and logs anonymized audit entry on completion or dead-letter.
        Returns resulting status: 'completed' | 'pending' | 'dead_letter'.
        """
        success, is_retryable, retry_after, err_msg = await delete_clerk_user(item.clerk_user_id)
        now = datetime.now(timezone.utc)
        item.attempts += 1
        item.updated_at = now

        if success:
            item.status = "completed"
            item.last_error = None
            db.add(
                AuditLog(
                    user_id=None,
                    action="clerk_user_deleted",
                    target_type="user",
                    target_id=item.audit_target_id,
                    details={"outbox_id": str(item.id), "attempts": item.attempts},
                )
            )
            db.commit()
            return "completed"

        item.last_error = err_msg
        if not is_retryable or item.attempts >= item.max_attempts:
            item.status = "dead_letter"
            db.add(
                AuditLog(
                    user_id=None,
                    action="clerk_deletion_dead_letter",
                    target_type="clerk_outbox",
                    target_id=str(item.id),
                    details={
                        "deleted_user_id": item.audit_target_id,
                        "attempts": item.attempts,
                        "error": err_msg,
                    },
                )
            )
            db.commit()
            return "dead_letter"

        backoff_sec = cls.calculate_backoff(item.attempts, retry_after=retry_after, jitter=jitter)
        item.status = "pending"
        item.next_retry_at = now + timedelta(seconds=backoff_sec)
        db.commit()
        return "pending"

    @classmethod
    async def process_pending(cls, db: Session, limit: int = 25, jitter: bool = True) -> Dict[str, int]:
        """Process due pending deletion tasks and purge completed rows older than 7 days."""
        now = datetime.now(timezone.utc)
        query = (
            db.query(ClerkOutbox)
            .filter(
                ClerkOutbox.status == "pending",
                ClerkOutbox.next_retry_at <= now,
            )
            .order_by(ClerkOutbox.next_retry_at.asc())
            .limit(limit)
        )

        # Row-level skip lock on PostgreSQL for safe concurrent cron execution
        if db.bind and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)

        due_items = query.all()
        counts = {"processed": 0, "completed": 0, "rescheduled": 0, "dead_letter": 0, "purged": 0}

        for item in due_items:
            counts["processed"] += 1
            result = await cls.dispatch_item(db, item, jitter=jitter)
            if result == "completed":
                counts["completed"] += 1
            elif result == "dead_letter":
                counts["dead_letter"] += 1
            else:
                counts["rescheduled"] += 1

        # Purge completed outbox records older than 7 days to minimize external ID retention
        purge_cutoff = now - timedelta(days=7)
        purged = (
            db.query(ClerkOutbox)
            .filter(
                ClerkOutbox.status == "completed",
                ClerkOutbox.updated_at < purge_cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        counts["purged"] = purged
        return counts
