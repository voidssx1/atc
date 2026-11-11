# Frontend Integration & Handover Guide

This document provides the complete integration contract for connecting a React (or React/Next.js) frontend to the **ATC DSA Roadmap & Resource Hub** backend (`https://atc-dsa-backend.webatc77.workers.dev`, prefix `/api/v1`).

> **Integration Status (`Backend` branch)**:
> - **Backend Live (`https://atc-dsa-backend.webatc77.workers.dev`)**: Verified live `/health` (`200`), `/api/v1/docs` & `/api/v1/openapi.json` (`200`, 58 paths / 72 ops), `http://localhost:5173` CORS preflight (`200`), `GET /api/v1/roadmap/topics` (`200`, 4 topics / 8 subtopics / 2 nested), `GET /api/v1/resources/` (`200`, 8 resources), `TECH_ADMIN` Clerk JWT exchange + `/auth/logout` (`200`), and `410`/`401`/`400` guards.
> - **Frontend (`origin/frontend`) Pending Integration**: `origin/frontend` is currently a static landing page without `@clerk/clerk-react` or API wiring.
> - **Integration & Handover Notes**:
>   - Direct `PUT`/`DELETE` on published roadmap topics/subtopics and active resources returns **`409 Conflict`** — content edits must go through the `/api/v1/drafts/` workflow (`draft` -> `submit` -> independent `tech_admin` `approve`/`reject` -> `publish`, where creators cannot self-approve/self-reject `403`, and immutable revision history is queryable via `GET /api/v1/drafts/?entity_id=...&status=published`).
>   - Live student (`@bennett.edu.in`) round-trip, `/auth/refresh` live rotation, Resend/Brevo email delivery, Clerk webhooks, Cron Triggers, and production Cloudflare Pages origin (`ALLOWED_ORIGINS` / `CLERK_ALLOWED_ORIGINS`) remain pending manual/live verification once frontend deployment URLs and DNS settings are finalized.

---

## 1. Environment & CORS Configuration

### 1.1 Frontend Environment Variables (Example for Vite / React)

| Variable | Example / Value | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` (local) / `https://atc-dsa-backend.webatc77.workers.dev/api/v1` (prod) | Do not include a trailing slash |
| `VITE_CLERK_PUBLISHABLE_KEY` | `pk_test_...` | Obtained from Clerk Dashboard (`great-bug-2878.clerk.accounts.dev`) |

### 1.2 Backend CORS & Origin Allowlist Requirements

Before any browser request from your frontend origin can succeed:
1. **`ALLOWED_ORIGINS`** (in `backend/wrangler.toml` for Cloudflare, `.env` locally):
   - Must contain exact scheme + host (+ port if non-default), comma-separated with no trailing slashes (e.g., `https://atc-dsa.vercel.app,http://localhost:5173`).
   - Currently configured in `wrangler.toml` as `http://localhost:5173` (ready for local Vite dev server; add your deployed frontend domain before production frontend rollout).
   - `DynamicCORSMiddleware` configures `allow_credentials=True`, all HTTP methods, and all headers only for origins matching this list (including on `429` rate-limit responses).
2. **`CLERK_ALLOWED_ORIGINS`** (in `backend/wrangler.toml`):
   - Comma-separated exact origins validated against the `azp` (authorized party) claim inside Clerk JWTs during `POST /api/v1/auth/clerk/exchange` (currently `http://localhost:5173`).
   - When non-empty in production, any Clerk JWT whose `azp` claim does not match this allowlist is rejected with `401 Unauthorized`.

### 1.3 Prepared Initial Seed Dataset (`backend/seeds/`)

Validated initial DSA content fixtures are stored in:
- `backend/seeds/initial_roadmap.json` (4 topics, 8 subtopics including nested child subtopics)
- `backend/seeds/initial_resources.csv` (8 curated DSA resources)

To seed an environment once authenticated as a `tech_admin`, import via the supported API endpoints:
1. `POST /api/v1/admin/data/import/roadmap/json` with the JSON array from `backend/seeds/initial_roadmap.json`
2. `POST /api/v1/admin/data/import/resources/csv` with `{"csv_content": "<contents of backend/seeds/initial_resources.csv>"}`

---

## 2. Clerk React Authentication Setup

The backend runs with `AUTH_MODE = "clerk"` in production:
- Clerk Issuer: `https://great-bug-2878.clerk.accounts.dev`
- Clerk JWKS URL: `https://great-bug-2878.clerk.accounts.dev/.well-known/jwks.json`
- Student Email Domain Restriction: `@bennett.edu.in` (students signing in via Clerk with non-Bennett emails receive `403 Forbidden` during `/auth/clerk/exchange`).

Legacy password endpoints (`/auth/register`, `/auth/login`, `/auth/verify-email`, `/auth/forgot-password`, `/auth/reset-password`) return **`410 Gone`** in production. Use Clerk's hosted components (`<SignIn />`, `<SignUp />`, `<UserButton />`) or Clerk hooks (`useAuth()`, `useClerk()`).

---

## 3. Token Lifecycle: Exchange, Storage, Rotation & Logout

### 3.1 Important Cookie & Storage Contract

- The backend returns opaque tokens in **JSON response bodies** (`Token` schema: `session_token`, `access_token`, `token_type`, `expires_in`). Both `session_token` and `access_token` hold the same opaque token string (`Bearer` token).
- **The backend does NOT set `Set-Cookie` or `HttpOnly` cookies.**
- **Security Rule for Frontend**: Do **NOT** persist `session_token` in `localStorage` or `sessionStorage`. Hold the opaque backend token strictly in **in-memory React state / module memory** (or inside a server-side BFF cookie if using Next.js server routes).
- Because Clerk manages the browser authentication session cleanly across page reloads, on initial app mount / page refresh when `isSignedIn === true`:
  1. Call Clerk's `getToken()` to obtain a fresh Clerk session JWT.
  2. Call `POST /api/v1/auth/clerk/exchange` to receive a fresh in-memory backend `session_token`.
  3. Call `GET /api/v1/profile/me` (or decode role from `GET /api/v1/dashboard/student` / admin check — note `UserResponse` from `/admin/users` or `/profile/me` provides user details) to hydrate app state.

### 3.2 Clerk JWT -> Backend Session Exchange

Send the Clerk JWT either in the `Authorization` header **or** the JSON body `{"token": clerkJwt}`:

```typescript
export interface BackendTokenResponse {
  session_token: string;
  access_token: string;
  token_type: "bearer";
  expires_in: number; // 604800s (7d) inactivity for students; 1800s (30m) for admins
}

export async function exchangeClerkJwt(clerkJwt: string): Promise<BackendTokenResponse> {
  const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/auth/clerk/exchange`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${clerkJwt}`,
    },
    body: JSON.stringify({ token: clerkJwt }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Exchange failed" }));
    throw new ApiError(res.status, err.detail);
  }
  return res.json();
}
```

### 3.3 Opaque Session Refresh / Rotation

Backend sessions enforce:
- **Students**: `30 days` max lifetime, `7 days` sliding inactivity timeout.
- **Admins (`tech_admin`, `content_admin`, `viewer`)**: `30 days` max lifetime, `30 minutes` sliding inactivity timeout.
- **Tech Admin Single-Session Policy**: Logging in elsewhere revokes prior `tech_admin` sessions immediately.
- **Rotation Grace Window**: Calling `POST /api/v1/auth/refresh` rolls `session_token` **in place** (immediately replacing the prior token hash). By contrast, repeated `POST /api/v1/auth/clerk/exchange` calls sharing the same `clerk_session_id` issue a new token row while keeping the prior row valid for **`30 seconds`** (`SESSION_ROTATION_GRACE_SECONDS`) so concurrent in-flight API requests during Clerk exchange do not fail immediately with `401`.

```typescript
let inFlightRefresh: Promise<string> | null = null;

export async function rotateBackendSession(currentToken: string): Promise<string> {
  if (inFlightRefresh) return inFlightRefresh;

  inFlightRefresh = (async () => {
    const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_token: currentToken }),
    });
    if (!res.ok) {
      throw new ApiError(res.status, "Session refresh failed");
    }
    const data: BackendTokenResponse = await res.json();
    setMemoryToken(data.session_token);
    return data.session_token;
  })();

  try {
    return await inFlightRefresh;
  } finally {
    inFlightRefresh = null;
  }
}
```
> Deduplicate refresh calls with an in-flight promise (`inFlightRefresh`) so parallel requests hitting `401` share one rotation call and stay within `RL_AUTH_REFRESH` (`20` req / `60s`).

### 3.4 Logout & Session Revocation

- **Standard Logout**: Call `POST /api/v1/auth/logout` with the Bearer `session_token` first, clear in-memory token state, then call Clerk `signOut()`.
- **Revoke All Devices / Post-Password Reset**: Call `POST /api/v1/auth/sessions/revoke-all` with Bearer `session_token`. This hard-revokes every active backend session row (`revoked_count` returned) across all devices.
- **Self-Service Account Deletion**: Calling `DELETE /api/v1/auth/account` deletes the local Neon user/cascade records and triggers Clerk account deletion (with background outbox retry if Clerk's API is temporarily unavailable).

```typescript
export async function logoutAll(signOut: () => Promise<void>, sessionToken: string | null) {
  try {
    if (sessionToken) {
      await fetch(`${import.meta.env.VITE_API_BASE_URL}/auth/logout`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${sessionToken}`,
        },
        body: JSON.stringify({ session_token: sessionToken }),
      });
    }
  } finally {
    setMemoryToken(null);
    await signOut();
  }
}
```

---

## 4. Authenticated API Client Pattern

Use a central `apiFetch` wrapper that attaches `Authorization: Bearer <session_token>`, retries once via refresh/re-exchange on `401`, and surfaces typed HTTP errors cleanly.

```typescript
export class ApiError extends Error {
  constructor(public status: number, public detail: unknown, public retryAfter?: string | null) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  auth?: { getMemoryToken: () => string | null; ensureToken: () => Promise<string> }
): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const token = auth?.getMemoryToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let res = await fetch(`${import.meta.env.VITE_API_BASE_URL}${path}`, { ...options, headers });

  if (res.status === 401 && auth) {
    // Attempt rotation or fresh Clerk exchange once
    const newToken = await auth.ensureToken();
    headers.set("Authorization", `Bearer ${newToken}`);
    res = await fetch(`${import.meta.env.VITE_API_BASE_URL}${path}`, { ...options, headers });
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? body, res.headers.get("Retry-After"));
  }

  const contentType = res.headers.get("Content-Type") || "";
  if (contentType.includes("text/csv")) {
    return (await res.text()) as unknown as T;
  }
  return res.json();
}
```

---

## 5. Role Permissions Matrix (`UserRole`)

| Capability / Area | `student` | `viewer` | `content_admin` | `tech_admin` |
|---|---|---|---|---|
| Published Roadmap & Resources (`GET`) | Yes | Yes | Yes | Yes |
| Unpublished Roadmap / Archived Resources (`include_*=true`) | No (`403`) | Yes | Yes | Yes |
| Student Dashboard, Progress, Bookmarks, Own Profile | Yes | Yes | Yes | Yes |
| View Another User's Profile (`GET /profile/{user_id}`) | No (`403`) | Yes | Yes | Yes |
| Click Analytics Tracking (`POST /analytics/click` or `/event`) | Yes (+ anon) | Yes | Yes | Yes |
| Admin Dashboard, Resource Analytics, Audit Logs | No (`403`) | Yes | Yes | Yes |
| Admin User List / User Detail (`GET`) | No (`403`) | Yes | Yes | Yes |
| Roadmap / Subtopic / Resource CRUD & Reorder | No (`403`) | No (`403`) | Yes | Yes |
| Resource Availability Check (single / batch) | No (`403`) | No (`403`) | Yes | Yes |
| Drafts: List & Detail (`GET`) | No (`403`) | Yes | Yes | Yes |
| Drafts: Create / Edit / Submit / Archive / Restore | No (`403`) | No (`403`) | Yes | Yes |
| Drafts: Approve / Reject / Publish | No (`403`) | No (`403`) | No (`403`) | **Yes** |
| Users: Create / Change Role / Activate-Deactivate / Delete | No (`403`) | No (`403`) | No (`403`) | **Yes** |
| Data Export (Roadmap JSON/CSV, Resources CSV) | No (`403`) | Yes | Yes | Yes |
| Data Bulk Import (Roadmap JSON, Resources CSV) | No (`403`) | No (`403`) | No (`403`) | **Yes** |
| Scheduled Jobs, Clerk Outbox Monitoring/Retry, Test Email | No (`403`) | No (`403`) | No (`403`) | **Yes** |

---

## 6. Page-to-API Mapping

### 6.1 Public & Student Pages

| UI Page / Component | Backend Endpoints | Notes |
|---|---|---|
| **Auth Callback / Session Boot** | `POST /api/v1/auth/clerk/exchange` | Exchange Clerk `getToken()` after sign-in / reload |
| **Account Menu / Logout / Security** | `POST /api/v1/auth/logout`, `POST /api/v1/auth/sessions/revoke-all`, `DELETE /api/v1/auth/account` | Revoke backend session before Clerk `signOut()` |
| **Student Dashboard (`/dashboard`)** | `GET /api/v1/dashboard/student`, `GET /api/v1/profile/me` | Displays completion %, topic/subtopic counts, recent bookmarks |
| **DSA Roadmap Explorer (`/roadmap`)** | `GET /api/v1/roadmap/topics`, `GET /api/v1/progress/` | Merge user's `progress` array client-side by `subtopic_id` |
| **Topic Detail Page (`/roadmap/:slug`)** | `GET /api/v1/roadmap/topics/{topic_slug}`, `GET /api/v1/resources/?subtopic_id=...`, `POST /api/v1/progress/{subtopic_id}`, `DELETE /api/v1/progress/{subtopic_id}` | Track completion/in-progress status and notes per subtopic |
| **Resource Library / Topic Cards** | `GET /api/v1/resources/`, `GET /api/v1/resources/{resource_id}`, `POST /api/v1/analytics/click`, `POST /api/v1/bookmarks/` | Fire `POST /analytics/click` (`keepalive: true`) when opening outbound URLs |
| **Bookmarks Page (`/bookmarks`)** | `GET /api/v1/bookmarks/`, `POST /api/v1/bookmarks/`, `DELETE /api/v1/bookmarks/{resource_id}` | Note deletion uses `resource_id`, not `bookmark.id` |
| **Profile Settings (`/profile`)** | `GET /api/v1/profile/me`, `PUT /api/v1/profile/me` | Update `full_name` (`2..100` chars) |

### 6.2 Admin Panel Pages (`viewer`, `content_admin`, `tech_admin`)

| Admin Screen | Backend Endpoints | Minimum Role |
|---|---|---|
| **Admin Overview** | `GET /api/v1/dashboard/admin`, `GET /api/v1/analytics/resources`, `GET /api/v1/admin/audit-logs` | `viewer` |
| **Roadmap CMS** | `GET /api/v1/roadmap/topics?include_unpublished=true`, `POST/PUT/DELETE /api/v1/roadmap/topics...`, `POST/PUT/DELETE /api/v1/roadmap/subtopics...`, `POST /api/v1/roadmap/reorder` | `viewer` (read), `content_admin` (write) |
| **Resources CMS** | `GET /api/v1/resources/?include_archived=true`, `POST/PUT/DELETE /api/v1/resources...`, `POST /api/v1/resources/{id}/check-availability`, `POST /api/v1/resources/check-all-availability` | `viewer` (read), `content_admin` (write) |
| **Drafts Queue (Authoring)** | `GET /api/v1/drafts/?entity_id=...`, `GET /api/v1/drafts/{id}`, `POST /api/v1/drafts/`, `PUT /api/v1/drafts/{id}`, `POST /api/v1/drafts/{id}/submit`, `/archive`, `/restore` | `viewer` (read), `content_admin` (author) |
| **Drafts Review (Sign-off)** | `POST /api/v1/drafts/{id}/approve`, `/reject`, `/publish` | `tech_admin` (non-creator; `403` on self-approval) |
| **User Management** | `GET /api/v1/admin/users/`, `GET /api/v1/admin/users/{id}`, `GET /api/v1/profile/{user_id}`, `POST /api/v1/admin/users/`, `PATCH .../role`, `PATCH .../status`, `DELETE .../users/{id}` | `viewer` (read), `tech_admin` (write) |
| **Import / Export** | `GET /api/v1/admin/data/export/{roadmap/json,roadmap/csv,resources/csv}`, `POST /api/v1/admin/data/import/{roadmap/json,resources/csv}` | `viewer` (export), `tech_admin` (bounded duplicate-safe import) |
| **System Jobs & Outbox** | `POST /api/v1/admin/jobs/{run-availability-scan,cleanup-expired-sessions,process-clerk-outbox,test-email}`, `GET /api/v1/admin/jobs/clerk-outbox`, `POST .../retry` | `tech_admin` |

---

## 7. Handling Loading, Error & Unauthorized States

| State / HTTP Code | Recommended Frontend UI Behavior |
|---|---|
| **Booting (`!isLoaded \|\| exchanging`)** | Show full-page skeleton/spinner until Clerk SDK finishes initializing AND `/auth/clerk/exchange` resolves (prevents flashing logged-out views). |
| **`401 Unauthorized`** | First try silent `rotateBackendSession()` or Clerk `getToken({ skipCache: true })` + `/auth/clerk/exchange`. If exchange/refresh returns `401`, clear in-memory token, call Clerk `signOut()`, and redirect to `/sign-in?reason=session_expired`. |
| **`403 Forbidden`** | Distinguish three cases from `detail`: (1) Email domain rejection (`@bennett.edu.in` required for students) -> show institutional-email error screen with sign-out button; (2) Draft self-approval/self-publish restriction -> inform author that a second `tech_admin` must review; (3) Insufficient RBAC role / inactive account -> show "Access Denied" state rather than triggering a logout loop. |
| **`404 Not Found`** | Show clean empty/not-found state (also returned when students access unpublished topics/subtopics or archived resources). |
| **`409 Conflict`** | Display inline form error (duplicate username/email, identity conflict, or attempting direct `PUT`/`DELETE` on immutable published content — prompt to open a `ContentDraft` revision instead). |
| **`410 Gone`** | Do not call legacy `/auth/login`, `/auth/register`, etc., when Clerk mode is active; route users exclusively through Clerk UI. |
| **`422 Unprocessable Entity`** | Parse Pydantic's `detail` array (`loc[1]` gives field name, `msg` gives validation message) and bind errors directly to form inputs. |
| **`429 Too Many Requests`** | Read `Retry-After` header (default `60`s), disable duplicate submit clicks/retry storms, and show a friendly cooldown notice. |
| **`5xx / Network Error`** | Show non-blocking error banner with a manual "Retry" button; verify `ALLOWED_ORIGINS` if `fetch` fails with a CORS preflight error in DevTools. |

---

## 8. Frontend Integration Smoke-Test Checklist

Run these manual smoke tests against `http://localhost:5173` after wiring `@clerk/clerk-react`:
1. Public reads load without login: `GET /roadmap/topics` (`4` topics / `8` subtopics) and `GET /resources/` (`8` resources).
2. Student login with `@bennett.edu.in`: Clerk `getToken()` -> `POST /auth/clerk/exchange` (`200`) -> token held in memory only (`localStorage` stays empty).
3. Student write isolation: update `/profile/me`, toggle `/progress/{subtopic_id}`, add/remove `/bookmarks/{resource_id}`, reload page -> re-exchange restores state cleanly.
4. Token refresh & logout: call `POST /auth/refresh`, confirm new token works, call `POST /auth/logout` before `signOut()`, confirm prior token returns `401`.
5. Role guards: non-admin receives `403` on `/dashboard/admin`; `viewer` can open admin read views (`200`) and receives `403` on writes; `content_admin` submits draft (`200`) and distinct `tech_admin` approves/publishes (`200`).
