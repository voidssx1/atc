# Authentication & Authorization Guide

This document explains how Clerk authentication, Svix webhook synchronization, Neon-backed opaque session management, RBAC role enforcement, and institutional email domain restrictions work in the **ATC DSA Roadmap & Resource Hub** backend.

> **Verification Status**: Token exchange, JWT verification (`RS256` JWKS/PEM), Svix signature verification, session rotation/grace windows, single-session `tech_admin` enforcement, and outbox retries are all covered by mocked `pytest` tests (`78 passed` on SQLite). Live Clerk Dashboard integration (`great-bug-2878.clerk.accounts.dev`), live `CLERK_WEBHOOK_SECRET` deliveries, and live Cloudflare Worker `worker_settings_context` validation are **not yet verified** against production until secrets/placeholders are finalized and deployed.

---

## 1. Architecture Overview (`AUTH_MODE=clerk`)

In production (`backend/wrangler.toml`), the backend runs with `ENVIRONMENT = "production"` and `AUTH_MODE = "clerk"`:
- **Identity Provider**: Clerk handles passwordless/password/OAuth login, email verification, and password resets on the frontend.
- **Backend Session Store**: After authenticating in Clerk, the frontend exchanges a Clerk session JWT at `POST /api/v1/auth/clerk/exchange` for an **opaque backend `session_token`**, stored strictly as its **SHA-256 hex digest** in Neon (`user_sessions.token`).
- **Legacy Auth Deprecation**: Because `AUTH_MODE=clerk`, legacy password routes (`/api/v1/auth/register`, `/verify-email`, `/login`, `/forgot-password`, `/reset-password`) are disabled and return **`410 Gone`**.
  - `PBKDF2-HMAC-SHA256` (`PBKDF2_ITERATIONS = 600000`, with legacy `bcrypt` verification fallback when `bcrypt` is installed) is only used in `hybrid` / `legacy` dev modes or when `tech_admin` creates local password-enabled accounts via `POST /api/v1/admin/users/`.

### 1.1 Production Startup / Request Configuration Validation

When `ENVIRONMENT="production"` and `AUTH_MODE="clerk"`, `Settings.validate_production_clerk_config()` executes whenever Worker environment settings are bound (including inside `WorkerEnvMiddleware` on ASGI HTTP requests):

Required non-placeholder settings:
1. `CLERK_ISSUER` (configured in `wrangler.toml`: `https://great-bug-2878.clerk.accounts.dev`)
2. `CLERK_ALLOWED_ORIGINS` (must contain real frontend origin(s), **not** `REPLACE_WITH_*`)
3. `CLERK_SECRET_KEY` (Cloudflare secret)
4. `CLERK_WEBHOOK_SECRET` (Cloudflare secret)
5. `CLERK_JWKS_URL` **or** `CLERK_JWT_KEY` (`CLERK_JWKS_URL` is configured in `wrangler.toml`: `https://great-bug-2878.clerk.accounts.dev/.well-known/jwks.json`)

If any of these are missing or still contain placeholder strings when `ENVIRONMENT=production` and `AUTH_MODE=clerk`, `WorkerEnvMiddleware` catches `ValueError` and returns HTTP `500 {"detail": "Server configuration validation error"}`.

---

## 2. Clerk Setup & JWT Verification

During `POST /api/v1/auth/clerk/exchange`, the backend extracts the Clerk JWT from `Authorization: Bearer <jwt>` or JSON body `{"token": "<jwt>"}` and verifies (`app/services/clerk.py`):
1. **Algorithm & Signature**: Only `RS256` is accepted. Public keys are verified via `CLERK_JWT_KEY` (PEM) if configured, or dynamically fetched/cached by `kid` from `CLERK_JWKS_URL`.
2. **Issuer (`iss`)**: Must match `CLERK_ISSUER` (normalized without trailing slash).
3. **Timestamps (`exp`, `nbf`, `iat`)**: Validated with a **30-second** clock-skew tolerance.
4. **Authorized Party (`azp`)**: Mandatory check against `CLERK_ALLOWED_ORIGINS` (exact origin match). Missing or unmatched `azp` returns `401 Unauthorized`.
5. **Subject & Session Claims (`sub`, `sid`)**:
   - `sub` maps to `users.clerk_user_id`.
   - `sid` maps to `user_sessions.clerk_session_id`.
6. **Verified Primary Email Resolution**:
   - If the JWT does not include an explicit verified email claim, the backend fetches the Clerk user record using `CLERK_SECRET_KEY` (`GET https://api.clerk.com/v1/users/{sub}`), resolves the primary email (`primary_email_address_id`), and requires verified status before minting a local session.

### Identity Linking Rules (`clerk_user_id` <-> Neon `users`)

1. **Match by `clerk_user_id`**: Looks up existing `users.clerk_user_id == sub`. Rejects disabled users (`is_active=false`) with `403`. Updates email if changed (deactivating `student` accounts that leave `@bennett.edu.in`), rejecting conflicting duplicate emails with `409 Conflict`.
2. **Pre-provisioned Email Link**: If no `clerk_user_id` match exists, checks `users.email == identity.email`.
   - If the Neon user has `clerk_user_id = null`, links it to `sub` (only after verifying the Clerk email is verified).
   - If the Neon user is already linked to a *different* `clerk_user_id`, rejects with **`409 Conflict`**.
3. **New Public Signup**: If no local user exists, creates a new user strictly with `role = "student"` after enforcing `@bennett.edu.in` (Section 5).

---

## 3. Clerk Webhook Events (`POST /api/v1/webhooks/clerk`)

The backend exposes `POST /api/v1/webhooks/clerk` (protected by `RL_WEBHOOKS`: `100` req / `60s` per IP).

### 3.1 Svix Verification & Replay Protection

- Verifies `svix-id`, `svix-timestamp`, and `svix-signature` (`v1,<base64_sig>`) headers against `CLERK_WEBHOOK_SECRET` using constant-time HMAC-SHA256 over `"{svix_id}.{svix_timestamp}.{raw_body}"`.
- Enforces **`300` seconds** (5 minutes) timestamp skew tolerance.
- Returns `500` if `CLERK_WEBHOOK_SECRET` is unset, `400` if headers/signature/timestamp are invalid.
- Inserts `(svix_id, event_type, svix_timestamp)` into `webhook_events` (`UNIQUE (svix_id)`). Duplicate deliveries return `200 {"status": "duplicate_ignored", "svix_id": "..."}` cleanly.

### 3.2 Supported Event Catalog

| Clerk Event Type | Backend Behavior |
|---|---|
| `user.updated` | Uses monotonic `clerk_updated_at` guard to ignore stale out-of-order deliveries. Updates primary verified email, username, and `student_profiles.full_name`. If a `student` changes away from `@bennett.edu.in`, sets `is_active = false` and revokes all active sessions. |
| `user.deleted` | Deletes the matching local Neon `User` row (cascading profile, sessions, progress, bookmarks) and logs the action in `audit_logs`. |
| `session.revoked` | Hard-revokes (`is_revoked = true`) all local `user_sessions` rows matching `clerk_session_id == data.id` and records an audit event. |
| `session.ended` | Logged to `webhook_events` as **non-revoking** (does not kill existing valid backend opaque sessions under the non-inferential policy). |
| `session.removed` | Logged to `webhook_events` as **non-revoking**. |

---

## 4. Session Lifecycle & Security Policies

### 4.1 Token Format & Storage

- Tokens are cryptographically random URL-safe strings (`generate_session_token`).
- Only `hash_session_token(raw_token)` (`SHA-256` hex digest) is persisted in `user_sessions.token`. Raw tokens are never stored in PostgreSQL or audit logs.
- Because the backend sets **no cookies**, tokens are sent via `Authorization: Bearer <session_token>`.

### 4.2 Expiry, Inactivity & Single-Session Policies

| Policy Parameter | `student` Value | Admin (`tech_admin`, `content_admin`, `viewer`) Value | Configuration Variable |
|---|---|---|---|
| **Max Absolute Lifetime** | `30 days` | `30 days` | `SESSION_MAX_LIFETIME_DAYS` |
| **Sliding Inactivity Timeout** | `7 days` | `30 minutes` | `SESSION_INACTIVITY_DAYS` / `SESSION_ADMIN_INACTIVITY_MINUTES` |
| **Inactivity Write Debounce** | `15 minutes` (`expires_at` updated only after meaningful drift) | `60 seconds` | Hardcoded in `app/api/deps.py` |
| **Rotation Grace Window** | `30 seconds` | `30 seconds` | `SESSION_ROTATION_GRACE_SECONDS` |
| **Concurrent Sessions** | Multiple permitted (1 active primary per `clerk_session_id`) | `content_admin` & `viewer`: multiple; **`tech_admin`: Single Active Session (`enforce_single_session = true`)** | Partial unique indexes in `user_sessions` |

- **Tech Admin Single-Session Enforcement**: Whenever a `tech_admin` obtains a new session via `/auth/clerk/exchange` (or legacy `/auth/login`), all other active sessions for that user are revoked first, backed by partial unique index `uq_tech_single_active_session`.
- **Rotation Grace Window (`POST /api/v1/auth/refresh`)**: Rotating a token issues a brand-new `UserSession` row and marks the old row with `grace_until = now + 30s` instead of revoking it immediately. Once `now >= grace_until`, the old token returns `401`.
- **Clerk Deletion Outbox (`clerk_outbox`)**: When a user deletes their account (`DELETE /api/v1/auth/account`) or a `tech_admin` deletes a user (`DELETE /api/v1/admin/users/{user_id}`), if Clerk Backend API `DELETE /v1/users/{clerk_user_id}` fails transiently, a `clerk_outbox` record is enqueued and retried with exponential backoff `[60s, 300s, 900s, 3600s]` up to `max_attempts = 5` before moving to `dead_letter`.

---

## 5. Institutional Domain Restriction (`@bennett.edu.in`)

- Controlled by `STUDENT_ALLOWED_EMAIL_DOMAIN = "bennett.edu.in"` (`is_allowed_student_email` checks exact domain `@bennett.edu.in` after lowercasing/trimming; subdomains are not automatically matched unless exact domain matches).
- **Public Signup Enforcement**: Any brand-new user signing in through Clerk who does not match a pre-provisioned Neon user **must** have a verified `@bennett.edu.in` email or receives `403 Forbidden`.
- **Ongoing Student Enforcement**: If a `student` changes their primary verified email in Clerk away from `@bennett.edu.in` (`user.updated` webhook or `/auth/clerk/exchange`), their account is deactivated (`is_active = false`) and active sessions are revoked.
- **Non-Student Admin Accounts**: `viewer`, `content_admin`, and `tech_admin` accounts must be pre-created/promoted by a `tech_admin` (via `POST /api/v1/admin/users/` or `PATCH /api/v1/admin/users/{user_id}/role`); once assigned a non-student role, their email is not blocked by the student-only `@bennett.edu.in` check when linking via Clerk.

---

## 6. Role Assignment & Access Control Summary

| Role (`UserRole`) | How Assigned | Access Summary |
|---|---|---|
| `student` | Default on Clerk self-signup / exchange | Read published roadmap & non-archived resources; manage own profile, progress, bookmarks, student dashboard; delete own account. |
| `viewer` | Assigned by `tech_admin` | Read-only admin visibility: dashboards, resource analytics, audit logs, users list/detail, drafts list/detail, `include_unpublished` / `include_archived`, and data exports. Cannot modify any records. |
| `content_admin` | Assigned by `tech_admin` | All `viewer` permissions plus CRUD/reorder on topics/subtopics/resources, URL availability scans, and create/edit/submit/archive/restore on `content_drafts`. Cannot approve/reject/publish drafts or manage users/jobs/imports. |
| `tech_admin` | Seeded / promoted by existing `tech_admin` | Full control: draft approval/rejection/publication, user creation/role/status/deletion, CSV/JSON imports, cron/outbox manual triggers, test-email dispatch. Subject to single-active-session & 30m inactivity policy. |
