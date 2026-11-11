# ATC DSA Roadmap & Resource Hub — API Documentation

> **Project**: ATC DSA Roadmap & Resource Hub Backend (`atc-dsa-backend`, v`0.1.0`)
> **API Prefix**: `/api/v1`
> **Verification Status**: All **72 OpenAPI operations** have explicit automated test coverage (`78 passed` in `pytest` using in-memory SQLite and mocked Clerk, Resend/Brevo, and Cloudflare `RateLimit` bindings). Live deployment on Cloudflare Python Workers (`*.workers.dev`), live Neon PostgreSQL execution, live Clerk JWKS/Svix webhook traffic, live Cloudflare edge rate limiting, and live Resend/Brevo email delivery are **not yet verified** against production infrastructure.

---

## 1. Interactive Docs & Discovery URLs

| Resource | Relative Path | Production Worker Origin URL |
|---|---|---|
| OpenAPI JSON Schema | `/api/v1/openapi.json` | `https://atc-dsa-backend.webatc77.workers.dev/api/v1/openapi.json` |
| Swagger UI | `/api/v1/docs` | `https://atc-dsa-backend.webatc77.workers.dev/api/v1/docs` |
| ReDoc | `/api/v1/redoc` | `https://atc-dsa-backend.webatc77.workers.dev/api/v1/redoc` |
| Root Health Check | `/health` | `https://atc-dsa-backend.webatc77.workers.dev/health` |
| Versioned Health Check | `/api/v1/health` | `https://atc-dsa-backend.webatc77.workers.dev/api/v1/health` |

> **CORS Note**: `DynamicCORSMiddleware` allows `allow_credentials=True`, all methods, and all headers **only** for origins explicitly listed in `ALLOWED_ORIGINS`. In `backend/wrangler.toml`, `ALLOWED_ORIGINS` currently holds the placeholder `https://REPLACE_WITH_FRONTEND_DOMAIN` and must be updated before browser calls succeed.

---

## 2. Authentication, Roles & Global Conventions

### 2.1 Token & Authentication Scheme

- **Clerk Session Exchange**: Clients authenticate via Clerk React on the frontend, obtain a short-lived Clerk session JWT (`getToken()`), and exchange it at `POST /api/v1/auth/clerk/exchange` for an opaque backend `session_token`.
- **Authenticated API Calls**: Send the opaque token on protected requests via standard HTTP Bearer auth:
  ```http
  Authorization: Bearer <session_token>
  ```
- **No Cookies**: The backend does **not** set `Set-Cookie` / `HttpOnly` cookies anywhere; all auth responses return JSON (`Token`).
- **Role-Based Access Control (`UserRole`)**:
  - `student` — Standard learner (`@bennett.edu.in` domain enforced). Access to roadmap (published), resources (non-archived), own profile, progress, bookmarks, student dashboard, and analytics tracking.
  - `viewer` — Read-only administrative observer. Can read admin users, drafts, admin dashboard, resource analytics, audit logs, and data exports; **cannot** mutate content, users, or drafts.
  - `content_admin` — Content editor. Inherits `viewer` read access plus direct CRUD on roadmap topics/subtopics/reorder, resources (and URL checks), and draft creation/editing/submission/archiving/restoring. **Cannot** approve/reject/publish drafts (`tech_admin` only), manage users, or import bulk data.
  - `tech_admin` — Full system administrator. Unrestricted access across all admin endpoints, draft approval/rejection/publication, user/role/status management, background jobs, Clerk outbox recovery, and bulk JSON/CSV imports.
- **`AUTH_MODE` Behavior**:
  - Production (`wrangler.toml`) sets `AUTH_MODE = "clerk"`. Legacy password endpoints (`/auth/register`, `/auth/verify-email`, `/auth/login`, `/auth/forgot-password`, `/auth/reset-password`) return **`410 Gone`** when `AUTH_MODE=clerk`. They remain active only in `hybrid` or `legacy` development/test modes.

### 2.2 Rate Limiting (`429 Too Many Requests`)

Cloudflare Workers native `[[ratelimits]]` bindings protect five high-risk routes using `CF-Connecting-IP` (client IP extracted by `RateLimitMiddleware`):

| Protected Endpoint | Wrangler Binding | Configured Threshold (in `wrangler.toml`) |
|---|---|---|
| `POST /api/v1/auth/clerk/exchange` | `RL_AUTH_EXCHANGE` | `10` requests / `60` seconds per IP |
| `POST /api/v1/auth/refresh` | `RL_AUTH_REFRESH` | `20` requests / `60` seconds per IP |
| `POST /api/v1/analytics/click` | `RL_ANALYTICS` | `60` requests / `60` seconds per IP |
| `POST /api/v1/analytics/event` | `RL_ANALYTICS` | `60` requests / `60` seconds per IP |
| `POST /api/v1/webhooks/clerk` | `RL_WEBHOOKS` | `100` requests / `60` seconds per IP |

When tripped, the middleware returns HTTP **`429`** with CORS headers preserved:
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
Content-Type: application/json

{"detail": "Too many requests. Please try again later."}
```
> **Note**: Edge rate limiting fails closed in local/test only when a mock binding is attached, and live Cloudflare enforcement is **unverified** until deployed to `workers.dev`.

### 2.3 Standard Error Responses

| Status Code | Meaning | Response Body Shape |
|---|---|---|
| `400 Bad Request` | Business rule or payload validation error (e.g., invalid URL, invalid draft state transition, bad webhook signature, missing token) | `{"detail": "<reason>"}` |
| `401 Unauthorized` | Missing, expired, inactive, or revoked Bearer `session_token` or invalid Clerk JWT (includes `WWW-Authenticate: Bearer` on token checks) | `{"detail": "<reason>"}` |
| `403 Forbidden` | Authenticated user lacks required `UserRole`, tried reading another user's profile without admin role, used non-`bennett.edu.in` student email, or requested privileged flags | `{"detail": "<reason>"}` |
| `404 Not Found` | Requested topic, subtopic, resource, draft, user, progress record, bookmark, or outbox item does not exist (or hidden unpublished/archived item) | `{"detail": "<reason>"}` |
| `409 Conflict` | Duplicate slug/email/username or Clerk-Neon identity linking conflict | `{"detail": "<reason>"}` |
| `410 Gone` | Legacy auth endpoint disabled because `AUTH_MODE=clerk` | `{"detail": "..."}` |
| `422 Unprocessable Entity` | FastAPI/Pydantic schema validation error (`HTTPValidationError`) | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` |
| `429 Too Many Requests` | Rate limit exceeded (`RateLimitMiddleware`) | `{"detail": "Too many requests. Please try again later."}` |
| `500 Internal Server Error` | Unhandled backend/provider failure (e.g., `CLERK_WEBHOOK_SECRET` missing on webhook call, email provider error on `test-email`, or missing Clerk config on ASGI request) | `{"detail": "<reason>"}` |

---

## 3. Endpoint Reference (All 72 Operations)

### 3.1 Health (`2` Operations)

#### 1. `GET /health`
- **Auth**: Public
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "project": "ATC DSA Roadmap & Resource Hub",
  "environment": "production",
  "auth_mode": "clerk",
  "docs_url": "/api/v1/docs"
}
```

#### 2. `GET /api/v1/health`
- **Auth**: Public
- **Response `200 OK`**: Identical payload to `GET /health`.

---

### 3.2 Authentication (`10` Operations)

#### 3. `POST /api/v1/auth/clerk/exchange` *(Rate Limited: `RL_AUTH_EXCHANGE`)*
Exchanges a verified Clerk session JWT (`RS256` via `CLERK_JWKS_URL` or `CLERK_JWT_KEY`, plus optional `azp` origin check against `CLERK_ALLOWED_ORIGINS`) for an opaque backend session token stored as a SHA-256 hash in `user_sessions`.
- **Auth**: Public (requires valid Clerk JWT via `Authorization: Bearer <clerk_jwt>` header **or** JSON body `{"token": "<clerk_jwt>"}`)
- **Request Body** (`ClerkExchangeRequest | null`, optional if sent in `Authorization` header):
```json
{
  "token": "eyJhbGciOiJSUzI1NiIsImtpZCI6..."
}
```
- **Response `200 OK`** (`Token`):
```json
{
  "session_token": "opaque_random_urlsafe_token_string",
  "access_token": "opaque_random_urlsafe_token_string",
  "token_type": "bearer",
  "expires_in": 2592000
}
```
  - `expires_in` is `SESSION_MAX_LIFETIME_DAYS * 86400` (`2592000` seconds = 30 days) for students and `SESSION_ADMIN_INACTIVITY_MINUTES * 60` (`1800` seconds = 30 minutes) for admin roles.
- **Errors**:
  - `401`: Missing, malformed, expired, wrong-issuer, untrusted-`azp`, or unverified Clerk JWT.
  - `403`: Student email outside `STUDENT_ALLOWED_EMAIL_DOMAIN` (`bennett.edu.in`), unverified email in Clerk claim, or local account disabled (`is_active=false`).
  - `409`: Identity conflict when linking Clerk `sub` to an existing user record.
  - `429`: Rate limit exceeded.

#### 4. `POST /api/v1/auth/refresh` *(Rate Limited: `RL_AUTH_REFRESH`)*
Rotates an active opaque session token. Issues a new `session_token` / `access_token` while keeping the previous token valid only for a `SESSION_ROTATION_GRACE_SECONDS` (`30s`) grace window (`grace_until`) to handle concurrent in-flight tabs/requests.
- **Auth**: Public (uses opaque token in body)
- **Request Body** (`SessionTokenRequest` — accepts either `session_token` or `refresh_token`):
```json
{
  "session_token": "current_opaque_session_token"
}
```
- **Response `200 OK`** (`Token`): New `session_token`, matching `access_token`, `"token_type": "bearer"`, and `expires_in`.
- **Errors**:
  - `400`: Neither `session_token` nor `refresh_token` provided.
  - `401`: Session not found, revoked, grace-window expired, past max lifetime, inactive, or user disabled.
  - `429`: Rate limit exceeded.

#### 5. `POST /api/v1/auth/logout`
Revokes the caller's current opaque session (or the token explicitly supplied in `SessionTokenRequest` if it belongs to the caller).
- **Auth**: Authenticated (`Any role`, `Bearer <session_token>`)
- **Request Body** (`SessionTokenRequest | null`, optional):
```json
{
  "session_token": "optional_target_token"
}
```
- **Response `200 OK`**:
```json
{
  "message": "Logged out successfully"
}
```
- **Errors**: `401` if unauthenticated.

#### 6. `POST /api/v1/auth/sessions/revoke-all`
Hard-revokes **all** opaque `user_sessions` belonging to the authenticated user (e.g., after password reset or security action).
- **Auth**: Authenticated (`Any role`)
- **Response `200 OK`**:
```json
{
  "message": "All sessions revoked",
  "revoked_count": 3
}
```
- **Errors**: `401` if unauthenticated.

#### 7. `DELETE /api/v1/auth/account`
Deletes the authenticated user's local Neon account (cascading sessions, profile, progress, bookmarks) and attempts Clerk user deletion via `CLERK_SECRET_KEY`. If Clerk API deletion fails transiently, enqueues a `clerk_outbox` record for Cron/admin retry.
- **Auth**: Authenticated (`Any role`)
- **Response `200 OK`**:
```json
{
  "message": "Account deleted successfully",
  "user_id": "550e8400-e29b-41d4-a716-446655440000"
}
```
- **Errors**: `401` if unauthenticated.

#### 8. `POST /api/v1/auth/register` *(Legacy — `410 Gone` when `AUTH_MODE=clerk`)*
- **Auth**: Public
- **Request Body** (`StudentRegisterRequest`): `email`, `password` (`minLength: 8`), `username?` (`3..50`), `full_name?` (`2..100`).
- **Response `201 Created`** (`UserResponse`) when enabled (`hybrid`/`legacy`); **`410 Gone`** when `AUTH_MODE=clerk`.
- **Errors**: `400` (duplicate email/username), `403` (domain mismatch), `410` (Clerk mode), `422`.

#### 9. `POST /api/v1/auth/verify-email` *(Legacy — `410 Gone` when `AUTH_MODE=clerk`)*
- **Auth**: Public
- **Request Body** (`VerifyEmailRequest`): `{"token": "..."}`.
- **Response `200 OK`**: `{"message": "Email verified successfully"}` (`hybrid`/`legacy`).
- **Errors**: `400` (invalid/expired token), `410` (`AUTH_MODE=clerk`), `422`.

#### 10. `POST /api/v1/auth/login` *(Legacy — `410 Gone` when `AUTH_MODE=clerk`)*
- **Auth**: Public
- **Request Body** (`LoginRequest`): `{"email": "user@bennett.edu.in", "password": "..."}`.
- **Response `200 OK`** (`Token`) when enabled.
- **Errors**: `401` (invalid credentials), `403` (inactive/unverified), `410` (`AUTH_MODE=clerk`), `422`.

#### 11. `POST /api/v1/auth/forgot-password` *(Legacy — `410 Gone` when `AUTH_MODE=clerk`)*
- **Auth**: Public
- **Request Body** (`ForgotPasswordRequest`): `{"email": "user@bennett.edu.in"}`.
- **Response `200 OK`**: `{"message": "If this email is registered, a password reset link has been sent"}`.
- **Errors**: `410` (`AUTH_MODE=clerk`), `422`.

#### 12. `POST /api/v1/auth/reset-password` *(Legacy — `410 Gone` when `AUTH_MODE=clerk`)*
- **Auth**: Public
- **Request Body** (`ResetPasswordRequest`): `{"token": "...", "new_password": "..."}` (`minLength: 8`).
- **Response `200 OK`**: `{"message": "Password has been reset successfully"}`.
- **Errors**: `400` (invalid/expired token), `403` (`TECH_ADMIN` policy / restrictions), `410` (`AUTH_MODE=clerk`), `422`.

---

### 3.3 Webhooks (`1` Operation)

#### 13. `POST /api/v1/webhooks/clerk` *(Rate Limited: `RL_WEBHOOKS`)*
Receives Clerk lifecycle webhooks verified with Svix HMAC-SHA256 headers (`svix-id`, `svix-timestamp`, `svix-signature` against `CLERK_WEBHOOK_SECRET`). Deduplicates deliveries using `webhook_events.svix_id`.
- **Auth**: Public + Svix signature verification (no Bearer token)
- **Headers**:
  - `svix-id`: string (unique event ID)
  - `svix-timestamp`: string (Unix seconds string; checked for replay skew)
  - `svix-signature`: string (`v1,<base64_sig>`)
- **Handled Events**:
  - `user.updated` — Updates `email`, `username`, `is_verified`, `full_name`, and monotonic `clerk_updated_at`. Enforces `@bennett.edu.in` for students (deactivating non-compliant students if changed).
  - `user.deleted` — Deletes corresponding local user and cascades related data.
  - `session.revoked` — Hard-revokes matching local sessions (`clerk_session_id`).
  - `session.ended` / `session.removed` — Recorded in `webhook_events`; intentionally **non-revoking** per backend policy.
- **Response `200 OK`**:
```json
{"status": "processed", "event_type": "user.updated"}
```
  Or if already seen:
```json
{"status": "duplicate_ignored", "svix_id": "msg_2abc123"}
```
- **Errors**:
  - `400`: Missing/invalid Svix headers, invalid signature, replay timestamp out of tolerance, or invalid JSON event structure.
  - `429`: Rate limit exceeded.
  - `500`: `CLERK_WEBHOOK_SECRET` not configured on the Worker/environment.

---

### 3.4 Admin User Management (`6` Operations)

#### 14. `GET /api/v1/admin/users/`
Lists registered users with optional filtering and pagination.
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Query Params**:
  - `role`: `UserRole | null` (`student`, `tech_admin`, `content_admin`, `viewer`)
  - `is_active`: `boolean | null`
  - `search`: `string | null` (matches email/username)
  - `skip`: `integer >= 0` (default `0`)
  - `limit`: `integer 1..100` (default `50`)
- **Response `200 OK`**: `UserResponse[]`
- **Errors**: `401`, `403`, `422`.

#### 15. `POST /api/v1/admin/users/`
Creates a new user (e.g., `viewer`, `content_admin`, `tech_admin`, or `student`). Checks email/username uniqueness (`409` on duplicate).
- **Auth**: `tech_admin` only
- **Request Body** (`AdminCreateUserRequest`):
```json
{
  "email": "curator@bennett.edu.in",
  "password": "StrongPassword123!",
  "username": "curator_atc",
  "role": "content_admin"
}
```
- **Response `201 Created`** (`UserResponse`):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "curator@bennett.edu.in",
  "username": "curator_atc",
  "clerk_user_id": null,
  "role": "content_admin",
  "is_active": true,
  "is_verified": true,
  "created_at": "2026-11-10T12:00:00Z",
  "profile": null
}
```
- **Errors**: `400` (domain/validation violation), `401`, `403`, `409` (duplicate email or username), `422`.

#### 16. `GET /api/v1/admin/users/{user_id}`
Retrieves detailed account information for a single user.
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Path Param**: `user_id` (`uuid`)
- **Response `200 OK`**: `UserResponse`
- **Errors**: `401`, `403`, `404`, `422`.

#### 17. `PATCH /api/v1/admin/users/{user_id}/role`
Changes a user's RBAC role and writes an audit log entry.
- **Auth**: `tech_admin` only
- **Path Param**: `user_id` (`uuid`)
- **Request Body** (`AdminUpdateRoleRequest`):
```json
{"role": "content_admin"}
```
- **Response `200 OK`**: `UserResponse`
- **Errors**: `401`, `403`, `404`, `422`.

#### 18. `PATCH /api/v1/admin/users/{user_id}/status`
Activates or suspends (`is_active: false`) a user account (revoking sessions on deactivation) and records an audit log.
- **Auth**: `tech_admin` only
- **Path Param**: `user_id` (`uuid`)
- **Request Body** (`AdminUpdateStatusRequest`):
```json
{"is_active": false}
```
- **Response `200 OK`**: `UserResponse`
- **Errors**: `401`, `403`, `404`, `422`.

#### 19. `DELETE /api/v1/admin/users/{user_id}`
Deletes a target user (prevents self-deletion) and enqueues/executes Clerk deletion if linked.
- **Auth**: `tech_admin` only
- **Path Param**: `user_id` (`uuid`)
- **Response `200 OK`**:
```json
{
  "message": "User deleted successfully",
  "user_id": "550e8400-e29b-41d4-a716-446655440000"
}
```
- **Errors**: `400` (attempting to delete own account), `401`, `403`, `404`, `422`.

---

### 3.5 Student Profiles (`3` Operations)

#### 20. `GET /api/v1/profile/me`
Returns the authenticated user's profile (auto-creating an empty `student_profiles` row if absent).
- **Auth**: Authenticated (`Any role`)
- **Response `200 OK`** (`StudentProfileResponse`):
```json
{
  "id": "a1b2c3d4-0000-4000-8000-000000000001",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "full_name": "Aarav Sharma",
  "email": "e23cseu0001@bennett.edu.in",
  "created_at": "2026-11-10T10:00:00Z",
  "updated_at": "2026-11-10T10:00:00Z"
}
```
- **Errors**: `401`.

#### 21. `PUT /api/v1/profile/me`
Updates the authenticated user's `full_name` (`2..100` chars or `null`).
- **Auth**: Authenticated (`Any role`)
- **Request Body** (`StudentProfileUpdate`):
```json
{"full_name": "Aarav Sharma"}
```
- **Response `200 OK`**: `StudentProfileResponse`
- **Errors**: `401`, `422`.

#### 22. `GET /api/v1/profile/{user_id}`
Reads a specific user's profile. Students can **only** view their own `user_id`; `tech_admin`, `content_admin`, and `viewer` may read any user's profile.
- **Auth**: Authenticated (Self **or** `tech_admin` / `content_admin` / `viewer`)
- **Path Param**: `user_id` (`uuid`)
- **Response `200 OK`**: `StudentProfileResponse`
- **Errors**: `401`, `403` (non-admin requesting another user's profile), `404`, `422`.

---

### 3.6 DSA Roadmap (`9` Operations)

#### 23. `GET /api/v1/roadmap/topics`
Lists roadmap topics with nested hierarchical `subtopics` (`children` arrays ordered by `order_index`).
- **Auth**: Optional Auth (Public/Students see only `is_published=true` topics and subtopics; `include_unpublished=true` requires `tech_admin`, `content_admin`, or `viewer`).
- **Query Param**: `include_unpublished` (`boolean`, default `false`)
- **Response `200 OK`**: `RoadmapTopicDetailResponse[]`
```json
[
  {
    "id": "11111111-1111-4111-8111-111111111111",
    "title": "Arrays & Hashing",
    "slug": "arrays-and-hashing",
    "description": "Core array patterns and hash maps.",
    "order_index": 0,
    "is_published": true,
    "created_at": "2026-11-10T09:00:00Z",
    "updated_at": "2026-11-10T09:00:00Z",
    "subtopics": [
      {
        "id": "22222222-2222-4222-8222-222222222222",
        "topic_id": "11111111-1111-4111-8111-111111111111",
        "parent_subtopic_id": null,
        "title": "Two Sum & Frequency Maps",
        "slug": "two-sum-frequency-maps",
        "description": null,
        "order_index": 0,
        "difficulty": "easy",
        "is_published": true,
        "created_at": "2026-11-10T09:05:00Z",
        "updated_at": "2026-11-10T09:05:00Z",
        "children": []
      }
    ]
  }
]
```
- **Errors**: `401` (if invalid token passed or `include_unpublished=true` unauthenticated), `403` (`include_unpublished=true` by student).

#### 24. `GET /api/v1/roadmap/topics/{topic_slug}`
Returns one topic and its nested `subtopics` tree by URL slug. Enforces publication visibility (`is_published=true` unless authorized admin passes `include_unpublished=true`).
- **Auth**: Optional Auth (`include_unpublished=true` requires admin role)
- **Path Param**: `topic_slug` (`string`)
- **Query Param**: `include_unpublished` (`boolean`, default `false`)
- **Response `200 OK`**: `RoadmapTopicDetailResponse`
- **Errors**: `401`, `403`, `404` (missing or unpublished topic).

#### 25. `POST /api/v1/roadmap/topics`
Creates a top-level roadmap topic.
- **Auth**: `tech_admin`, `content_admin`
- **Request Body** (`RoadmapTopicCreate`):
```json
{
  "title": "Dynamic Programming",
  "slug": "dynamic-programming",
  "description": "1D, 2D, and knapsack DP patterns.",
  "order_index": 10,
  "is_published": true
}
```
- **Response `201 Created`**: `RoadmapTopicSummaryResponse` (includes `subtopics_count`)
- **Errors**: `400` (duplicate slug), `401`, `403`, `422`.

#### 26. `PUT /api/v1/roadmap/topics/{topic_id}`
Updates an **unpublished** (`is_published=false`) topic. Published topics are immutable (`409 Conflict`) and must be modified via `ContentDraft` approval.
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `topic_id` (`uuid`)
- **Request Body** (`RoadmapTopicUpdate`, all fields optional)
- **Response `200 OK`**: `RoadmapTopicSummaryResponse`
- **Errors**: `400` (duplicate slug), `401`, `403`, `404`, `409` (topic is already published), `422`.

#### 27. `DELETE /api/v1/roadmap/topics/{topic_id}`
Deletes an **unpublished** (`is_published=false`) topic (cascades subtopics, resources under subtopics, and student progress). Published topics return `409 Conflict` until unpublished through an approved draft.
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `topic_id` (`uuid`)
- **Response `200 OK`**: `{"message": "Topic deleted successfully", "id": "<uuid>"}`
- **Errors**: `401`, `403`, `404`, `409` (topic is published), `422`.

#### 28. `POST /api/v1/roadmap/subtopics`
Creates a subtopic under `topic_id` (and optionally nests under `parent_subtopic_id` within the same topic).
- **Auth**: `tech_admin`, `content_admin`
- **Request Body** (`RoadmapSubtopicCreate`):
```json
{
  "topic_id": "11111111-1111-4111-8111-111111111111",
  "parent_subtopic_id": null,
  "title": "Sliding Window",
  "slug": "sliding-window",
  "description": "Fixed and variable size windows.",
  "order_index": 1,
  "difficulty": "medium",
  "is_published": true
}
```
- **Response `201 Created`**: `RoadmapSubtopicResponse`
- **Errors**: `400` (parent belongs to different topic), `401`, `403`, `404` (parent topic or subtopic not found), `422`.

#### 29. `PUT /api/v1/roadmap/subtopics/{subtopic_id}`
Updates an **unpublished** (`is_published=false`) subtopic and checks parent hierarchy integrity/cycles. Published subtopics return `409 Conflict`.
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `subtopic_id` (`uuid`)
- **Request Body** (`RoadmapSubtopicUpdate`)
- **Response `200 OK`**: `RoadmapSubtopicResponse`
- **Errors**: `400` (cross-topic parent or cycle), `401`, `403`, `404`, `409` (subtopic is published), `422`.

#### 30. `DELETE /api/v1/roadmap/subtopics/{subtopic_id}`
Deletes an **unpublished** (`is_published=false`) subtopic (cascades child subtopics, linked resources, and progress). Published subtopics return `409 Conflict`.
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `subtopic_id` (`uuid`)
- **Response `200 OK`**: `{"message": "Subtopic deleted successfully", "id": "<uuid>"}`
- **Errors**: `401`, `403`, `404`, `409` (subtopic is published), `422`.

#### 31. `POST /api/v1/roadmap/reorder`
Bulk updates `order_index` values for `"topic"`, `"subtopic"`, or `"resource"` entities.
- **Auth**: `tech_admin`, `content_admin`
- **Request Body** (`BulkReorderRequest`):
```json
{
  "entity_type": "topic",
  "items": [
    {"id": "11111111-1111-4111-8111-111111111111", "order_index": 0},
    {"id": "22222222-2222-4222-8222-222222222222", "order_index": 1}
  ]
}
```
- **Response `200 OK`**: `{"message": "Successfully reordered 2 items"}`
- **Errors**: `400` (unsupported `entity_type`), `401`, `403`, `422`.

---

### 3.7 Resources (`7` Operations)

#### 32. `GET /api/v1/resources/`
Lists curated learning resources filtered by subtopic, type, difficulty, and availability status.
- **Auth**: Optional Auth (`include_archived=true` requires `tech_admin`, `content_admin`, or `viewer`)
- **Query Params**:
  - `subtopic_id`: `uuid | null`
  - `resource_type`: `ResourceType | null` (`article`, `video`, `problem`, `repository`, `book`, `course`)
  - `difficulty`: `string | null`
  - `availability_status`: `AvailabilityStatus | null` (`available`, `broken`, `degraded`, `unchecked`)
  - `include_archived`: `boolean` (default `false`)
  - `skip`: `integer >= 0` (default `0`)
  - `limit`: `integer 1..100` (default `50`)
- **Response `200 OK`**: `ResourceResponse[]`
- **Errors**: `401`, `403` (`include_archived=true` without admin role), `422`.

#### 33. `GET /api/v1/resources/{resource_id}`
Retrieves one resource by ID. Archived resources return `404` unless `include_archived=true` with an authorized admin role.
- **Auth**: Optional Auth (`include_archived=true` requires admin role)
- **Path Param**: `resource_id` (`uuid`)
- **Query Param**: `include_archived` (`boolean`, default `false`)
- **Response `200 OK`**: `ResourceResponse`
- **Errors**: `401`, `403`, `404`, `422`.

#### 34. `POST /api/v1/resources/`
Creates a resource with HTTP/HTTPS URL validation.
- **Auth**: `tech_admin`, `content_admin`
- **Request Body** (`ResourceCreate`):
```json
{
  "title": "NeetCode Two Sum Walkthrough",
  "url": "https://neetcode.io/problems/two-integer-sum",
  "resource_type": "problem",
  "difficulty": "easy",
  "subtopic_id": "22222222-2222-4222-8222-222222222222",
  "order_index": 0
}
```
- **Response `201 Created`**: `ResourceResponse`
- **Errors**: `400` (invalid URL scheme/format), `401`, `403`, `404` (`subtopic_id` not found), `422`.

#### 35. `PUT /api/v1/resources/{resource_id}`
Updates an **archived** (`is_archived=true`) resource's metadata/URL, or soft-archives an active resource via `{"is_archived": true}`. Direct edits to active published resources return `409 Conflict` and must go through `ContentDraft` review.
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `resource_id` (`uuid`)
- **Request Body** (`ResourceUpdate`)
- **Response `200 OK`**: `ResourceResponse`
- **Errors**: `400` (invalid URL), `401`, `403`, `404`, `409` (active published resource is immutable), `422`.

#### 36. `DELETE /api/v1/resources/{resource_id}`
Soft-archives (`is_archived=true`) a resource by default (`tech_admin` or `content_admin`). Permanent deletion (`hard_delete=true`) requires `tech_admin` and requires the resource to already be archived (`is_archived=true`).
- **Auth**: `tech_admin`, `content_admin` (`hard_delete=true`: `tech_admin` only)
- **Path Param**: `resource_id` (`uuid`)
- **Query Param**: `hard_delete` (`boolean`, default `false`)
- **Response `200 OK`**:
```json
{"message": "Resource resource_archived successfully", "id": "33333333-3333-4333-8333-333333333333"}
```
  (Or `"Resource resource_deleted successfully"` when `hard_delete=true`.)
- **Errors**: `401`, `403`, `404`, `409` (`hard_delete=true` attempted on active non-archived resource), `422`.

#### 37. `POST /api/v1/resources/{resource_id}/check-availability`
Runs an immediate live HTTP check (`HEAD` with `GET` fallback) on the resource URL and updates `availability_status`, `status_code`, and `last_checked_at`.
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `resource_id` (`uuid`)
- **Response `200 OK`** (`ResourceCheckResult`):
```json
{
  "resource_id": "33333333-3333-4333-8333-333333333333",
  "url": "https://neetcode.io/problems/two-integer-sum",
  "availability_status": "available",
  "status_code": 200,
  "checked_at": "2026-11-10T12:15:00Z"
}
```
- **Errors**: `401`, `403`, `404`, `422`.

#### 38. `POST /api/v1/resources/check-all-availability`
Scans up to `limit` non-archived resources in sequence and updates their availability fields.
- **Auth**: `tech_admin`, `content_admin`
- **Query Param**: `limit` (`integer 1..50`, default `50`)
- **Response `200 OK`**:
```json
{
  "message": "Availability scan completed",
  "summary": {
    "total_checked": 10,
    "available": 9,
    "degraded": 0,
    "broken": 1
  }
}
```
- **Errors**: `401`, `403`, `422`.

---

### 3.8 Content Drafts & Review Workflow (`10` Operations)

State transitions: `draft` / `rejected` -> (`submit`) -> `pending_approval` -> (`approve` / `reject` by an independent `tech_admin`) -> `approved` -> (`publish` by an independent `tech_admin`) -> `published`.
- **Separation of Duties**: A draft's `submitted_by_id` creator cannot `approve`, `reject`, or `publish` their own draft (`403 Forbidden`).
- **Immutability & Revisions**: Once `published`, drafts become immutable revision records (`PUT`, `submit`, `approve`, `reject`, `archive`, and `restore` return `400`), and subsequent revisions for the same `entity_id` increment `version_number` and log `previous_snapshot` + `published_snapshot` in `audit_logs`.

#### 39. `GET /api/v1/drafts/`
Lists content drafts or published revision history filtered by status, entity type, `entity_id`, and archive state (ordered by `version_number DESC, updated_at DESC`).
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Query Params**: `status` (`DraftStatus | null`), `entity_type` (`EntityType | null`), `entity_id` (`uuid | null`), `include_archived` (`boolean`, default `false`), `skip` (`0`), `limit` (`50`, max `100`).
- **Response `200 OK`**: `ContentDraftResponse[]`
- **Errors**: `401`, `403`, `422`.

#### 40. `GET /api/v1/drafts/{draft_id}`
Retrieves a single draft by ID.
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Path Param**: `draft_id` (`uuid`)
- **Response `200 OK`**: `ContentDraftResponse`
- **Errors**: `401`, `403`, `404`, `422`.

#### 41. `POST /api/v1/drafts/`
Creates a new draft (`status="draft"`, `version_number=1`) for a `topic`, `subtopic`, or `resource` payload.
- **Auth**: `tech_admin`, `content_admin`
- **Request Body** (`ContentDraftCreate`):
```json
{
  "entity_type": "resource",
  "entity_id": null,
  "title": "Add Striver DP Playlist",
  "payload": {
    "title": "Striver DP Series",
    "url": "https://takeuforward.org/dynamic-programming/striver-dp-series-dynamic-programming-problems/",
    "resource_type": "video",
    "difficulty": "medium",
    "subtopic_id": "22222222-2222-4222-8222-222222222222",
    "order_index": 2
  }
}
```
- **Response `201 Created`**: `ContentDraftResponse`
- **Errors**: `401`, `403`, `422`.

#### 42. `PUT /api/v1/drafts/{draft_id}`
Updates draft `title` and/or `payload` (`version_number` increments; resets `rejected` -> `draft`).
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `draft_id` (`uuid`)
- **Request Body** (`ContentDraftUpdate`)
- **Response `200 OK`**: `ContentDraftResponse`
- **Errors**: `400` (already `published` or `archived`), `401`, `403`, `404`, `422`.

#### 43. `POST /api/v1/drafts/{draft_id}/submit`
Submits a draft for `tech_admin` approval (`status` becomes `"pending_approval"`).
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `draft_id` (`uuid`)
- **Response `200 OK`**: `ContentDraftResponse`
- **Errors**: `400` (cannot re-submit `published`, `archived`, or non-submittable drafts), `401`, `403`, `404`, `422`.

#### 44. `POST /api/v1/drafts/{draft_id}/approve`
Approves a `pending_approval` draft (`status="approved"`). Fails with `403` if caller is the draft's `submitted_by_id` creator.
- **Auth**: `tech_admin` only (must differ from `submitted_by_id`)
- **Path Param**: `draft_id` (`uuid`)
- **Request Body** (`ContentDraftReview | null`, optional): `{"feedback": "Looks accurate."}`
- **Response `200 OK`**: `ContentDraftResponse`
- **Errors**: `400` (not in `pending_approval`), `401`, `403` (non-`tech_admin` or self-approval attempt), `404`, `422`.

#### 45. `POST /api/v1/drafts/{draft_id}/reject`
Rejects a `pending_approval` draft with feedback (`status="rejected"`). Fails with `403` if caller is `submitted_by_id`.
- **Auth**: `tech_admin` only (must differ from `submitted_by_id`)
- **Path Param**: `draft_id` (`uuid`)
- **Request Body** (`ContentDraftReview`): `{"feedback": "Please fix broken URL."}`
- **Response `200 OK`**: `ContentDraftResponse`
- **Errors**: `400` (not in `pending_approval`), `401`, `403` (non-`tech_admin` or self-rejection attempt), `404`, `422`.

#### 46. `POST /api/v1/drafts/{draft_id}/publish`
Publishes an `approved` draft into `roadmap_topics`, `roadmap_subtopics`, or `resources` (validating resource URLs, subtopic parent hierarchy/cycles, and explicit allowed fields; sets `draft.status="published"`, advances sequential `version_number` for `entity_id`, and logs `previous_snapshot` + `published_snapshot` to `audit_logs`). Fails with `403` if caller is `submitted_by_id`.
- **Auth**: `tech_admin` only (must differ from `submitted_by_id`)
- **Path Param**: `draft_id` (`uuid`)
- **Response `200 OK`**: `ContentDraftResponse`
- **Errors**: `400` (draft not approved or invalid payload/cycle), `401`, `403`, `404` (referenced entity missing), `422`.

#### 47. `POST /api/v1/drafts/{draft_id}/archive`
Soft-archives an unpublished draft (`is_archived=true`, `status="archived"`). Fails with `400` for `published` or already archived drafts.
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `draft_id` (`uuid`)
- **Response `200 OK`**: `ContentDraftResponse`
- **Errors**: `400`, `401`, `403`, `404`, `422`.

#### 48. `POST /api/v1/drafts/{draft_id}/restore`
Restores an archived draft back to `status="draft"` (`is_archived=false`). Fails with `400` for `published` or non-archived drafts.
- **Auth**: `tech_admin`, `content_admin`
- **Path Param**: `draft_id` (`uuid`)
- **Response `200 OK`**: `ContentDraftResponse`
- **Errors**: `400`, `401`, `403`, `404`, `422`.

---

### 3.9 Student Progress (`4` Operations)

#### 49. `GET /api/v1/progress/`
Returns all progress entries for the currently authenticated user.
- **Auth**: Authenticated (`Any role`)
- **Response `200 OK`**: `ProgressResponse[]`
- **Errors**: `401`.

#### 50. `GET /api/v1/progress/{subtopic_id}`
Returns the authenticated user's progress record for a specific subtopic.
- **Auth**: Authenticated (`Any role`)
- **Path Param**: `subtopic_id` (`uuid`)
- **Response `200 OK`**: `ProgressResponse`
- **Errors**: `401`, `404` (subtopic or progress entry not found), `422`.

#### 51. `POST /api/v1/progress/{subtopic_id}`
Creates or updates (`upsert`) progress status (`not_started`, `in_progress`, `completed`) and optional notes for a published subtopic. Sets `completed_at` when `status == "completed"`.
- **Auth**: Authenticated (`Any role`)
- **Path Param**: `subtopic_id` (`uuid`)
- **Request Body** (`ProgressUpdateRequest`):
```json
{
  "status": "completed",
  "notes": "Solved O(n) hash map solution."
}
```
- **Response `200 OK`**: `ProgressResponse`
- **Errors**: `401`, `404` (subtopic not found), `422`.

#### 52. `DELETE /api/v1/progress/{subtopic_id}`
Deletes/resets the user's progress entry for `subtopic_id`.
- **Auth**: Authenticated (`Any role`)
- **Path Param**: `subtopic_id` (`uuid`)
- **Response `200 OK`**: `{"message": "Progress reset successfully", "subtopic_id": "<uuid>"}`
- **Errors**: `401`, `422`.

---

### 3.10 Bookmarks (`3` Operations)

#### 53. `GET /api/v1/bookmarks/`
Lists all bookmarks created by the current user, including joined `resource` metadata.
- **Auth**: Authenticated (`Any role`)
- **Response `200 OK`**: `BookmarkResponse[]`
- **Errors**: `401`.

#### 54. `POST /api/v1/bookmarks/`
Bookmarks a non-archived resource (or updates bookmark notes if already bookmarked).
- **Auth**: Authenticated (`Any role`)
- **Request Body** (`BookmarkCreateRequest`):
```json
{
  "resource_id": "33333333-3333-4333-8333-333333333333",
  "notes": "Revisit before mid-sem."
}
```
- **Response `201 Created`**: `BookmarkResponse`
- **Errors**: `401`, `404` (resource not found or archived), `422`.

#### 55. `DELETE /api/v1/bookmarks/{resource_id}`
Removes a bookmark by `resource_id` for the current user.
- **Auth**: Authenticated (`Any role`)
- **Path Param**: `resource_id` (`uuid`)
- **Response `200 OK`**: `{"message": "Bookmark removed", "resource_id": "<uuid>"}`
- **Errors**: `401`, `404` (bookmark not found), `422`.

---

### 3.11 Dashboards (`2` Operations)

#### 56. `GET /api/v1/dashboard/student`
Returns aggregated learning metrics and the latest bookmarks for the current user.
- **Auth**: Authenticated (`Any role`)
- **Response `200 OK`** (`StudentDashboardResponse`):
```json
{
  "total_topics": 12,
  "total_subtopics": 64,
  "completed_subtopics": 18,
  "in_progress_subtopics": 4,
  "completion_percentage": 28.13,
  "total_bookmarks": 7,
  "recent_bookmarks": []
}
```
- **Errors**: `401`.

#### 57. `GET /api/v1/dashboard/admin`
Returns platform-wide operational counts and the 10 most recent `audit_logs` records.
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Response `200 OK`** (`AdminDashboardResponse`):
```json
{
  "total_students": 420,
  "total_admins": 6,
  "total_topics": 12,
  "total_subtopics": 64,
  "total_resources": 180,
  "broken_resources": 2,
  "degraded_resources": 3,
  "pending_drafts": 4,
  "recent_audit_logs": []
}
```
- **Errors**: `401`, `403`.

---

### 3.12 Analytics & Audit Logs (`4` Operations)

#### 58. `POST /api/v1/analytics/click` *(Rate Limited: `RL_ANALYTICS`)*
Records an outbound click event on an existing resource (`user_id` is populated when a valid Bearer token is provided, or `null` for anonymous visits).
- **Auth**: Optional Auth
- **Request Body** (`TrackClickRequest`):
```json
{"resource_id": "33333333-3333-4333-8333-333333333333"}
```
- **Response `201 Created`**: `{"message": "Click recorded"}`
- **Errors**: `404` (resource does not exist), `422`, `429`.

#### 59. `POST /api/v1/analytics/event` *(Rate Limited: `RL_ANALYTICS`)*
Alias endpoint sharing the same handler and behavior as `POST /api/v1/analytics/click`.
- **Auth**: Optional Auth
- **Request Body** (`TrackClickRequest`)
- **Response `201 Created`**: `{"message": "Click recorded"}`
- **Errors**: `404`, `422`, `429`.

#### 60. `GET /api/v1/analytics/resources`
Returns aggregated click counts and unique student clickers sorted by popularity (`total_clicks` desc).
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Query Param**: `limit` (`integer 1..100`, default `50`)
- **Response `200 OK`**: `ResourceAnalyticsSummary[]`
- **Errors**: `401`, `403`, `422`.

#### 61. `GET /api/v1/admin/audit-logs`
Lists immutable administrative audit log entries ordered newest first.
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Query Params**: `action` (`string | null`), `target_type` (`string | null`), `skip` (`0`), `limit` (`50`, max `100`).
- **Response `200 OK`**: `AuditLogResponse[]`
- **Errors**: `401`, `403`, `422`.

---

### 3.13 Scheduled Jobs & Email Integration (`6` Operations)

All 6 endpoints require **`tech_admin`** only. In production, Cloudflare Cron Triggers (`0 0 * * *` and `0 1-23 * * *`) invoke `job_runner.run_scheduled_batch(...)` automatically without HTTP; these endpoints allow manual execution and monitoring.

#### 62. `POST /api/v1/admin/jobs/run-availability-scan`
- **Auth**: `tech_admin`
- **Query Param**: `limit` (`integer 1..50`, default `50`)
- **Response `200 OK`**: `{"message": "Resource availability scan executed", "results": {"total_checked": ..., "available": ..., "degraded": ..., "broken": ...}}`
- **Errors**: `401`, `403`, `422`.

#### 63. `POST /api/v1/admin/jobs/cleanup-expired-sessions`
Deletes stale revoked/expired `user_sessions` rows.
- **Auth**: `tech_admin`
- **Response `200 OK`**: `{"message": "Expired sessions cleaned up", "deleted_count": 12}`
- **Errors**: `401`, `403`.

#### 64. `POST /api/v1/admin/jobs/process-clerk-outbox`
Processes due `pending` `clerk_outbox` remote deletion jobs against Clerk Backend API.
- **Auth**: `tech_admin`
- **Query Param**: `limit` (`integer 1..100`, default `25`)
- **Response `200 OK`**: `{"message": "Clerk outbox processed", "results": {"processed": ..., "completed": ..., "requeued": ..., "dead_lettered": ...}}`
- **Errors**: `401`, `403`, `422`.

#### 65. `GET /api/v1/admin/jobs/clerk-outbox`
Lists `clerk_outbox` queue counts and items, optionally filtered by `ClerkOutboxStatus` (`pending`, `completed`, `dead_letter`).
- **Auth**: `tech_admin`
- **Query Params**: `status` (`ClerkOutboxStatus | null`), `limit` (`integer 1..200`, default `50`)
- **Response `200 OK`**: `ClerkOutboxListResponse`
- **Errors**: `401`, `403`, `422`.

#### 66. `POST /api/v1/admin/jobs/clerk-outbox/{outbox_id}/retry`
Immediately retries a `pending` or `dead_letter` Clerk outbox deletion item.
- **Auth**: `tech_admin`
- **Path Param**: `outbox_id` (`uuid`)
- **Response `200 OK`**: `ClerkOutboxItemResponse`
- **Errors**: `401`, `403`, `404`, `422`.

#### 67. `POST /api/v1/admin/jobs/test-email`
Dispatches a test transactional email via Resend -> Brevo fallback chain (`email_service.send_email`).
- **Auth**: `tech_admin`
- **Request Body** (`TestEmailRequest`):
```json
{
  "to_email": "admin@bennett.edu.in",
  "subject": "ATC DSA Hub Test Email",
  "body": "This is a test notification from the ATC DSA Hub."
}
```
- **Response `200 OK`**: `{"message": "Test email processed successfully", "to": "admin@bennett.edu.in"}`
- **Errors**: `401`, `403`, `422`, `500` (provider credentials missing or delivery failed).

---

### 3.14 Data Import/Export & Bulk Operations (`5` Operations)

#### 68. `GET /api/v1/admin/data/export/roadmap/json`
Exports the complete topic and nested subtopic hierarchy as JSON.
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Response `200 OK`**: `{"roadmap": [...]}`
- **Errors**: `401`, `403`.

#### 69. `GET /api/v1/admin/data/export/roadmap/csv`
Exports roadmap structure as a downloadable `text/csv` attachment (`Content-Disposition: attachment; filename="roadmap_export.csv"`).
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Response `200 OK`**: `text/csv` stream
- **Errors**: `401`, `403`.

#### 70. `POST /api/v1/admin/data/import/roadmap/json`
Bounded, duplicate-safe bulk import for topics and nested subtopics (max `512 KiB`, `50` topics, `250` subtopics; skips existing slugs cleanly and rejects parent cycles).
- **Auth**: `tech_admin` only
- **Request Body**: `array<object>` (roadmap topic list matching export shape)
- **Response `200 OK`**: `{"message": "Roadmap imported successfully", "imported_topics": 4, "skipped_topics": 0, "imported_subtopics": 8, "skipped_subtopics": 0, "rejected_count": 0}`
- **Errors**: `400` (payload exceeds size/row bounds, invalid topic/subtopic data, cycle, or parent integrity failure), `401`, `403`, `422`.

#### 71. `GET /api/v1/admin/data/export/resources/csv`
Exports up to `1000` non-archived resources as a downloadable `text/csv` attachment (`Content-Disposition: attachment; filename=resources.csv`).
- **Auth**: `tech_admin`, `content_admin`, `viewer`
- **Response `200 OK`**: `text/csv` stream
- **Errors**: `401`, `403`.

#### 72. `POST /api/v1/admin/data/import/resources/csv`
Bounded, duplicate-safe CSV bulk import for resources (max `512 KiB`, `500` rows; skips duplicate canonical HTTP/HTTPS URLs cleanly and rejects invalid URLs or invalid `subtopic_id` UUIDs).
- **Auth**: `tech_admin` only
- **Request Body** (`ImportCSVRequest`):
```json
{
  "csv_content": "title,url,resource_type,difficulty,subtopic_id\nBinary Search Guide,https://cp-algorithms.com/num_methods/binary_search.html,article,easy,"
}
```
- **Response `200 OK`**: `{"message": "Resources imported successfully", "imported_count": 1, "skipped_count": 0, "rejected_count": 0}`
- **Errors**: `400` (payload exceeds size/row bounds, malformed CSV, invalid URL, invalid `subtopic_id` UUID, or missing subtopic), `401`, `403`, `422`.

---

## 4. Enum & Schema Reference (53 OpenAPI Component Schemas)

### 4.1 Enumerations

| Enum Schema | Exact Values |
|---|---|
| `UserRole` | `"student"`, `"tech_admin"`, `"content_admin"`, `"viewer"` |
| `ProgressStatus` | `"not_started"`, `"in_progress"`, `"completed"` |
| `ResourceType` | `"article"`, `"video"`, `"problem"`, `"repository"`, `"book"`, `"course"` |
| `AvailabilityStatus` | `"available"`, `"broken"`, `"degraded"`, `"unchecked"` |
| `DraftStatus` | `"draft"`, `"pending_approval"`, `"approved"`, `"rejected"`, `"published"`, `"archived"` |
| `EntityType` | `"topic"`, `"subtopic"`, `"resource"` |
| `ClerkOutboxStatus` | `"pending"`, `"completed"`, `"dead_letter"` |

### 4.2 Complete Component Schema Index

1. `AdminCreateUserRequest`: `email*` (`email`), `password` (`string? >=8`), `username` (`string?`), `role` (`UserRole`, default `"viewer"`).
2. `AdminDashboardResponse`: `total_students*`, `total_admins*`, `total_topics*`, `total_subtopics*`, `total_resources*`, `broken_resources*`, `degraded_resources*`, `pending_drafts*`, `recent_audit_logs*` (`object[]`).
3. `AdminUpdateRoleRequest`: `role*` (`UserRole`).
4. `AdminUpdateStatusRequest`: `is_active*` (`boolean`).
5. `AuditLogResponse`: `id*` (`uuid`), `user_id` (`uuid?`), `action*`, `target_type` (`string?`), `target_id` (`string?`), `details` (`object?`), `ip_address` (`string?`), `created_at*` (`date-time`).
6. `AvailabilityStatus`: enum.
7. `BookmarkCreateRequest`: `resource_id*` (`uuid`), `notes` (`string?`).
8. `BookmarkResponse`: `id*`, `user_id*`, `resource_id*`, `notes` (`string?`), `created_at*`, `resource` (`ResourceResponse?`).
9. `BulkReorderRequest`: `entity_type*` (`string`), `items*` (`ReorderItem[]`).
10. `ClerkExchangeRequest`: `token` (`string?`).
11. `ClerkOutboxItemResponse`: `id*`, `clerk_user_id*`, `audit_target_id*`, `operation*`, `status*`, `attempts*`, `max_attempts*`, `next_retry_at*`, `last_error` (`string?`), `created_at*`, `updated_at*`.
12. `ClerkOutboxListResponse`: `pending_count*`, `dead_letter_count*`, `completed_count*`, `items*` (`ClerkOutboxItemResponse[]`).
13. `ClerkOutboxStatus`: enum.
14. `ContentDraftCreate`: `entity_type*` (`EntityType`), `entity_id` (`uuid?`), `title*` (`1..255`), `payload*` (`object`).
15. `ContentDraftResponse`: `id*`, `entity_type*`, `entity_id` (`uuid?`), `title*`, `payload*`, `status*` (`DraftStatus`), `version_number*`, `submitted_by_id*`, `reviewed_by_id` (`uuid?`), `review_feedback` (`string?`), `is_archived*`, `created_at*`, `updated_at*`.
16. `ContentDraftReview`: `feedback` (`string?`).
17. `ContentDraftUpdate`: `title` (`string? 1..255`), `payload` (`object?`).
18. `DraftStatus`: enum.
19. `EntityType`: enum.
20. `ForgotPasswordRequest`: `email*` (`email`).
21. `HTTPValidationError`: `detail` (`ValidationError[]`).
22. `ImportCSVRequest`: `csv_content*` (`string`).
23. `LoginRequest`: `email*` (`email`), `password*` (`string`).
24. `ProgressResponse`: `id*`, `user_id*`, `subtopic_id*`, `status*` (`ProgressStatus`), `notes` (`string?`), `completed_at` (`date-time?`), `created_at*`, `updated_at*`.
25. `ProgressStatus`: enum.
26. `ProgressUpdateRequest`: `status` (`ProgressStatus`, default `"completed"`), `notes` (`string?`).
27. `ReorderItem`: `id*` (`uuid`), `order_index*` (`integer`).
28. `ResetPasswordRequest`: `token*`, `new_password*` (`minLength: 8`).
29. `ResourceAnalyticsSummary`: `resource_id*`, `title*`, `url*`, `total_clicks*`, `unique_students*`.
30. `ResourceCheckResult`: `resource_id*`, `url*`, `availability_status*`, `status_code` (`int?`), `checked_at*`.
31. `ResourceCreate`: `title*` (`1..255`), `url*`, `resource_type` (`ResourceType`, default `"article"`), `difficulty` (default `"medium"`), `subtopic_id` (`uuid?`), `order_index` (default `0`).
32. `ResourceResponse`: `ResourceCreate` fields plus `id*`, `availability_status*`, `last_checked_at`, `status_code`, `is_archived*`, `created_by_id`, `created_at*`, `updated_at*`.
33. `ResourceType`: enum.
34. `ResourceUpdate`: All `ResourceCreate` fields optional + `availability_status` (`AvailabilityStatus?`), `is_archived` (`boolean?`).
35. `RoadmapSubtopicCreate`: `title*` (`1..150`), `slug*` (`1..150`), `description`, `order_index` (`0`), `difficulty` (`"medium"`), `is_published` (`true`), `parent_subtopic_id` (`uuid?`), `topic_id*` (`uuid`).
36. `RoadmapSubtopicResponse`: `RoadmapSubtopicCreate` fields plus `id*`, `created_at*`, `updated_at*`, `children` (`RoadmapSubtopicResponse[]`).
37. `RoadmapSubtopicUpdate`: All subtopic fields optional (`title`, `slug`, `description`, `order_index`, `difficulty`, `is_published`, `parent_subtopic_id`).
38. `RoadmapTopicCreate`: `title*` (`1..150`), `slug*` (`1..150`), `description`, `order_index` (`0`), `is_published` (`true`).
39. `RoadmapTopicDetailResponse`: Topic fields plus `id*`, `created_at*`, `updated_at*`, `subtopics` (`RoadmapSubtopicResponse[]`).
40. `RoadmapTopicSummaryResponse`: Topic fields plus `id*`, `created_at*`, `updated_at*`, `subtopics_count` (`0`).
41. `RoadmapTopicUpdate`: All topic fields optional.
42. `SessionTokenRequest`: `session_token` (`string?`), `refresh_token` (`string?`).
43. `StudentDashboardResponse`: `total_topics*`, `total_subtopics*`, `completed_subtopics*`, `in_progress_subtopics*`, `completion_percentage*` (`number`), `total_bookmarks*`, `recent_bookmarks*` (`BookmarkResponse[]`).
44. `StudentProfileResponse`: `id*`, `user_id*`, `full_name` (`string? 2..100`), `email` (`email?`), `created_at*`, `updated_at*`.
45. `StudentProfileUpdate`: `full_name` (`string? 2..100`).
46. `StudentRegisterRequest`: `email*` (`email`), `password*` (`>=8`), `username` (`string? 3..50`), `full_name` (`string? 2..100`).
47. `TestEmailRequest`: `to_email*` (`email`), `subject` (default `"ATC DSA Hub Test Email"`), `body` (default `"This is a test notification from the ATC DSA Hub."`).
48. `Token`: `session_token*`, `access_token*`, `token_type` (`"bearer"`), `expires_in*` (`integer`).
49. `TrackClickRequest`: `resource_id*` (`uuid`).
50. `UserResponse`: `id*`, `email*`, `username*` (`string?`), `clerk_user_id` (`string?`), `role*` (`UserRole`), `is_active*`, `is_verified*`, `created_at*`, `profile` (`StudentProfileResponse?`).
51. `UserRole`: enum.
52. `ValidationError`: `loc*`, `msg*`, `type*`, `input?`, `ctx?`.
53. `VerifyEmailRequest`: `token*`.
