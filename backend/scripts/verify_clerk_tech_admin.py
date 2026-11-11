"""
Backend-only verification procedure for Clerk -> Cloudflare Worker -> Neon TECH_ADMIN auth.

Usage (from `backend/` with `PYTHONPATH=src`):
  python scripts/verify_clerk_tech_admin.py

Secrets are read from environment or prompted via masked `getpass` input and are
never printed or persisted. No seed data or unrelated database writes are performed.
"""

import argparse
import base64
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy import text

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

DEFAULT_WORKER_BASE = "https://atc-dsa-backend.webatc77.workers.dev"
DEFAULT_CLERK_FAPI = "https://great-bug-2878.clerk.accounts.dev"
DEFAULT_CLERK_BAPI = "https://api.clerk.com"
DEFAULT_ORIGIN = "http://localhost:5173"
DEFAULT_CLERK_USER_ID = "user_3LxV5nDxv4y2LDmkb6DojcZORgS"
DEFAULT_DB_USER_ID = "5c63ff64-1144-4480-a77b-ea5ae4d65c7b"


def decode_unverified_jwt_claims(jwt_token: str) -> Dict[str, Any]:
    """Decode JWT payload segment strictly for claim structure validation."""
    parts = jwt_token.strip().split(".")
    if len(parts) != 3:
        raise ValueError("Malformed Clerk JWT format")
    payload_b64 = parts[1]
    padded = payload_b64 + ("=" * ((4 - len(payload_b64) % 4) % 4))
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


def _extract_jwt_from_sign_in_payload(data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Extract `(session_id, jwt)` from Clerk FAPI `/v1/client/sign_ins` JSON."""
    resp_obj = data.get("response") or {}
    sid = resp_obj.get("created_session_id")
    client_obj = data.get("client") or {}
    sessions = client_obj.get("sessions") or []

    for sess in sessions:
        if not isinstance(sess, dict):
            continue
        if sid and sess.get("id") != sid:
            continue
        chosen_sid = sid or sess.get("id")
        last_tok = sess.get("last_active_token") or {}
        if isinstance(last_tok, dict) and last_tok.get("jwt"):
            return chosen_sid, last_tok["jwt"]
        return chosen_sid, None
    return sid, None


def obtain_clerk_session_jwt(
    clerk_secret_key: str,
    clerk_user_id: str,
    *,
    origin: str = DEFAULT_ORIGIN,
    clerk_fapi: str = DEFAULT_CLERK_FAPI,
    clerk_bapi: str = DEFAULT_CLERK_BAPI,
    client: Optional[httpx.Client] = None,
) -> str:
    """
    Mint a short-lived Clerk Development session JWT (including `azp=origin`)
    without a frontend, using Clerk Backend API sign_in_tokens + FAPI dev_browser.
    """
    own_client = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        # 1. Create single-use Sign-In Token via Clerk Backend API
        r_si_tok = http.post(
            f"{clerk_bapi.rstrip('/')}/v1/sign_in_tokens",
            headers={
                "Authorization": f"Bearer {clerk_secret_key.strip()}",
                "Content-Type": "application/json",
            },
            json={"user_id": clerk_user_id, "expires_in_seconds": 300},
        )
        if r_si_tok.status_code not in (200, 201):
            raise RuntimeError(f"Clerk sign_in_tokens failed with HTTP {r_si_tok.status_code}")
        sign_in_token = r_si_tok.json().get("token")
        if not sign_in_token:
            raise RuntimeError("Clerk sign_in_tokens response missing token field")

        # 2. Create ephemeral Clerk Dev Browser context for the allowed origin
        r_db = http.post(
            f"{clerk_fapi.rstrip('/')}/v1/dev_browser",
            headers={"Origin": origin, "User-Agent": "atc-backend-verifier"},
        )
        if r_db.status_code != 200:
            raise RuntimeError(f"Clerk dev_browser failed with HTTP {r_db.status_code}")
        dev_browser_jwt = r_db.json().get("token")
        if not dev_browser_jwt:
            raise RuntimeError("Clerk dev_browser response missing token field")

        # 3. Consume sign_in_token through Clerk FAPI with Origin header so `azp` matches
        r_sign_in = http.post(
            f"{clerk_fapi.rstrip('/')}/v1/client/sign_ins",
            params={"__clerk_db_jwt": dev_browser_jwt},
            headers={
                "Origin": origin,
                "Content-Type": "application/json",
                "User-Agent": "atc-backend-verifier",
            },
            json={"strategy": "sign_in_token", "token": sign_in_token},
        )
        if r_sign_in.status_code != 200:
            raise RuntimeError(f"Clerk client sign_in failed with HTTP {r_sign_in.status_code}")

        sid, jwt_token = _extract_jwt_from_sign_in_payload(r_sign_in.json())

        # 4. Mint session JWT explicitly if not embedded in last_active_token
        if not jwt_token:
            if not sid:
                raise RuntimeError("Clerk sign_in response did not return a session ID")
            r_tok = http.post(
                f"{clerk_fapi.rstrip('/')}/v1/client/sessions/{sid}/tokens",
                params={"__clerk_db_jwt": dev_browser_jwt},
                headers={"Origin": origin, "User-Agent": "atc-backend-verifier"},
            )
            if r_tok.status_code != 200:
                raise RuntimeError(f"Clerk session token mint failed with HTTP {r_tok.status_code}")
            jwt_token = r_tok.json().get("jwt")

        if not jwt_token:
            raise RuntimeError("Unable to extract Clerk session JWT")
        return jwt_token
    finally:
        if own_client:
            http.close()


def verify_worker_and_neon_linking(
    clerk_jwt: str,
    *,
    worker_base: str = DEFAULT_WORKER_BASE,
    origin: str = DEFAULT_ORIGIN,
    expected_clerk_user_id: str = DEFAULT_CLERK_USER_ID,
    expected_db_user_id: str = DEFAULT_DB_USER_ID,
    revoke_after: bool = False,
    client: Optional[httpx.Client] = None,
    db_engine=None,
) -> Dict[str, Any]:
    """
    Exchange Clerk JWT at Cloudflare Worker, call protected TECH_ADMIN endpoints,
    and verify Neon `users.clerk_user_id` linking via read-only query.
    Returns only non-secret status metadata.
    """
    claims = decode_unverified_jwt_claims(clerk_jwt)
    if claims.get("sub") != expected_clerk_user_id:
        raise RuntimeError(
            f"Clerk JWT sub mismatch: expected '{expected_clerk_user_id}', got '{claims.get('sub')}'"
        )
    if claims.get("azp") != origin:
        raise RuntimeError(
            f"Clerk JWT azp mismatch: expected '{origin}', got '{claims.get('azp')}'"
        )

    own_client = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        base = worker_base.rstrip("/")
        # 1. Exchange Clerk JWT for backend opaque session token
        r_ex = http.post(
            f"{base}/api/v1/auth/clerk/exchange",
            headers={
                "Origin": origin,
                "Authorization": f"Bearer {clerk_jwt}",
                "Content-Type": "application/json",
                "User-Agent": "atc-backend-verifier",
            },
            json={"token": clerk_jwt},
        )
        if r_ex.status_code != 200:
            detail = r_ex.text[:200]
            raise RuntimeError(f"POST /api/v1/auth/clerk/exchange failed ({r_ex.status_code}): {detail}")

        ex_body = r_ex.json()
        session_token = ex_body.get("session_token") or ex_body.get("access_token")
        if not session_token:
            raise RuntimeError("Exchange response missing session_token")

        auth_headers = {
            "Authorization": f"Bearer {session_token}",
            "User-Agent": "atc-backend-verifier",
        }

        # 2. Verify strict TECH_ADMIN-only read-only endpoint
        r_outbox = http.get(f"{base}/api/v1/admin/jobs/clerk-outbox", headers=auth_headers)
        if r_outbox.status_code != 200:
            raise RuntimeError(
                f"GET /api/v1/admin/jobs/clerk-outbox failed ({r_outbox.status_code})"
            )
        outbox_data = r_outbox.json()

        # 3. Verify Admin Dashboard read-only endpoint
        r_dash = http.get(f"{base}/api/v1/dashboard/admin", headers=auth_headers)
        if r_dash.status_code != 200:
            raise RuntimeError(
                f"GET /api/v1/dashboard/admin failed ({r_dash.status_code})"
            )

        # 4. Verify Neon `users` row linked to exact Clerk User ID (read-only SELECT)
        if db_engine is None:
            from app.core.config import settings
            from app.db.session import get_engine

            db_engine = get_engine(settings.DATABASE_URL)

        with db_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, email, role, is_active, is_verified, clerk_user_id "
                    "FROM users WHERE id = :uid"
                ),
                {"uid": expected_db_user_id},
            ).fetchone()

        if not row:
            raise RuntimeError(f"Neon user '{expected_db_user_id}' not found")
        db_id, db_email, db_role, is_active, is_verified, linked_clerk_id = row
        if linked_clerk_id != expected_clerk_user_id:
            raise RuntimeError(
                f"Neon clerk_user_id mismatch: expected '{expected_clerk_user_id}', got '{linked_clerk_id}'"
            )

        revoked = False
        if revoke_after:
            r_logout = http.post(
                f"{base}/api/v1/auth/logout",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"session_token": session_token},
            )
            revoked = r_logout.status_code == 200

        return {
            "exchange_status": r_ex.status_code,
            "token_type": ex_body.get("token_type"),
            "expires_in": ex_body.get("expires_in"),
            "outbox_status": r_outbox.status_code,
            "outbox_pending": outbox_data.get("pending_count"),
            "admin_dashboard_status": r_dash.status_code,
            "neon_user_id": str(db_id),
            "neon_email": db_email,
            "neon_role": str(db_role),
            "neon_is_active": bool(is_active),
            "neon_is_verified": bool(is_verified),
            "neon_clerk_user_id": linked_clerk_id,
            "session_revoked_after_test": revoked,
        }
    finally:
        if own_client:
            http.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify TECH_ADMIN Clerk auth without frontend.")
    parser.add_argument("--worker-base", default=DEFAULT_WORKER_BASE)
    parser.add_argument("--clerk-fapi", default=DEFAULT_CLERK_FAPI)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--clerk-user-id", default=DEFAULT_CLERK_USER_ID)
    parser.add_argument("--db-user-id", default=DEFAULT_DB_USER_ID)
    parser.add_argument(
        "--prompt-jwt",
        action="store_true",
        help="Prompt for an existing Clerk session JWT instead of using CLERK_SECRET_KEY.",
    )
    parser.add_argument(
        "--revoke-after",
        action="store_true",
        help="Call POST /api/v1/auth/logout after verification checks succeed.",
    )
    args = parser.parse_args()

    if args.prompt_jwt:
        clerk_jwt = getpass.getpass("Clerk Session JWT (input hidden): ").strip()
    else:
        secret = (os.environ.get("CLERK_SECRET_KEY") or "").strip()
        if not secret:
            secret = getpass.getpass("Clerk Secret Key sk_test_... (input hidden): ").strip()
        if not secret:
            print("ERROR: CLERK_SECRET_KEY is required.", file=sys.stderr)
            return 1
        clerk_jwt = obtain_clerk_session_jwt(
            clerk_secret_key=secret,
            clerk_user_id=args.clerk_user_id,
            origin=args.origin,
            clerk_fapi=args.clerk_fapi,
        )

    report = verify_worker_and_neon_linking(
        clerk_jwt,
        worker_base=args.worker_base,
        origin=args.origin,
        expected_clerk_user_id=args.clerk_user_id,
        expected_db_user_id=args.db_user_id,
        revoke_after=args.revoke_after,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
