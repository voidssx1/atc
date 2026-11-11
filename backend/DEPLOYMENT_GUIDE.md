# Deployment & Operations Guide

This guide covers configuring Neon PostgreSQL, Cloudflare Hyperdrive, Cloudflare Secrets & Variables, Clerk webhooks, Resend/Brevo email delivery, Cloudflare Workers Rate Limiting / optional Zone WAF rules, Python Workers deployment (`pywrangler`), smoke testing, and rollback procedures for `atc-dsa-backend`.

> **Current Verification Boundary (`Backend` branch)**:
> - **Verified Locally & Linux Packaging**: Python compilation (`python -m compileall -q src scripts tests`), full SQLite + mocked-external-provider `pytest` suite (**`108 passed`**, including RBAC across all 4 roles, draft self-approval/revision immutability, bounded duplicate-safe imports, Clerk/Svix mocks, Resend/Brevo mocks, Hyperdrive engine reuse, lazy OpenAPI snapshot parity, and prefix-guarded routing), plus Linux CI `pywrangler sync` & `wrangler deploy --dry-run` (`43077148840`, `Total Upload: 29323.54 KiB / gzip: 7506.77 KiB`).
> - **Verified Live (`https://atc-dsa-backend.webatc77.workers.dev`, Version `4232e353-deec-4aa0-8052-defae0bc499a`, commit `011bf25`)**: Worker deployed with Hyperdrive + Neon (`003_clerk_auth_policies` applied, seeded `4` topics / `8` subtopics / `8` resources, `TECH_ADMIN` linked); `43/43` live requests (cold bursts, 8-way simultaneous concurrency with `max_cpuMs=592`, `/health`, `/api/v1/openapi.json` `45–52ms` CPU, `/api/v1/docs`, `/api/v1/roadmap/topics`, `/api/v1/resources/`, `TECH_ADMIN` Clerk exchange/outbox/dashboard/logout, non-Bennett `403`, unauth `401`, `POST /admin/jobs/test-email` Resend `HTTP 403` -> Brevo `200`, `http://localhost:5173` CORS preflight) succeeded with **0 `503`/`1102`/`10021` errors**. Earlier on `d6c795b` (`94d36316`) & `9c1d5f6`, multi-role `STUDENT`/`VIEWER`/`CONTENT_ADMIN` RBAC + draft workflow, live Clerk Dashboard signed webhooks (`user.deleted`, `user.updated`, `session.revoked`), `RL_AUTH_EXCHANGE`/`RL_AUTH_REFRESH` `429 Retry-After: 60`, and the autonomous `18:00:00Z` hourly Cron (`cron="0 1-23 * * *"`, `outcome=ok`, `user_sessions` `7 -> 0`, `resources.last_checked_at` left `0/8`) all passed live.
> - **Pending External / Operational Items**: Resend/Brevo sender domain DNS authentication & inbox receipt verification (`noreply@bennett.edu.in` fails Resend with `HTTP 403` and risks `p=quarantine` DMARC on Brevo relays; no inbox or ESP dashboard credentials available here), live midnight `00:00:00 UTC` daily resource availability Cron observation (`0 0 * * *`), and updating `ALLOWED_ORIGINS` / `CLERK_ALLOWED_ORIGINS` for the future frontend origin.

---

## 1. Pre-Deployment Checklist & Configuration

### 1.1 Secrets vs. `wrangler.toml` Variables

Never commit real secrets into git or `backend/wrangler.toml`. Configure secrets in Cloudflare using Wrangler and keep non-secret config in `backend/wrangler.toml` `[vars]`.

#### Required Cloudflare Secrets (`wrangler secret put <NAME>`)

| Secret Name | Purpose | Status Notes |
|---|---|---|
| `SECRET_KEY` | Cryptographic backend secret (`>= 32` chars) | Uploaded on Cloudflare account |
| `CLERK_SECRET_KEY` | Clerk Backend API calls (user email fallback lookup & remote deletion) | Uploaded on Cloudflare account |
| `CLERK_WEBHOOK_SECRET` | Svix signing secret (`whsec_...`) for `POST /api/v1/webhooks/clerk` | **Must be uploaded before production HTTP traffic** (`validate_production_clerk_config` fails with HTTP `500` if missing in `ENVIRONMENT=production`, `AUTH_MODE=clerk`) |
| `RESEND_API_KEY` | Primary HTTP transactional email provider (`https://api.resend.com/emails`) | Upload at least one of `RESEND_API_KEY` or `BREVO_API_KEY` |
| `BREVO_API_KEY` | Fallback HTTP transactional email provider (`https://api.brevo.com/v3/smtp/email`) | Recommended as automatic failover on ambiguous/timeout/retryable Resend failures |

#### Variables in `backend/wrangler.toml` `[vars]`

| Variable | Current Value in `wrangler.toml` | Action Required Before Frontend Cutover |
|---|---|---|
| `ENVIRONMENT` | `"production"` | Keep as-is |
| `AUTH_MODE` | `"clerk"` | Keep as-is |
| `PROJECT_NAME` | `"ATC DSA Roadmap & Resource Hub"` | Keep as-is |
| `API_V1_STR` | `"/api/v1"` | Keep as-is |
| `STUDENT_ALLOWED_EMAIL_DOMAIN` | `"bennett.edu.in"` | Keep as-is |
| `EMAILS_FROM_EMAIL` | `"noreply@bennett.edu.in"` | Ensure sender/domain is verified in Resend/Brevo (or change to a verified domain) |
| `EMAILS_FROM_NAME` | `"ATC DSA Roadmap Hub"` | Keep as-is |
| `CLOUDFLARE_ORIGIN_URL` | `"https://atc-dsa-backend.webatc77.workers.dev"` | Set for `*.workers.dev` |
| `CLERK_JWKS_URL` | `"https://great-bug-2878.clerk.accounts.dev/.well-known/jwks.json"` | Configured for Clerk dev instance |
| `CLERK_ISSUER` | `"https://great-bug-2878.clerk.accounts.dev"` | Configured for Clerk dev instance |
| `ALLOWED_ORIGINS` | `"http://localhost:5173"` | Dev localhost active for testing; update with deployed frontend origin(s) before frontend production cutover |
| `CLERK_ALLOWED_ORIGINS` | `"http://localhost:5173"` | Dev localhost active for Clerk JWT `azp` claim; update with deployed frontend origin(s) before production cutover |

---

## 2. Database Setup: Neon PostgreSQL & Hyperdrive

### 2.1 Hyperdrive Binding (`backend/wrangler.toml`)

Hyperdrive is already configured in `backend/wrangler.toml`:
```toml
[[hyperdrive]]
binding = "HYPERDRIVE"
id = "34e6e006848f46c4b75e382dbfafd02a"
```
- The Cloudflare Hyperdrive config must point to your **Neon pooled or direct PostgreSQL connection string** via Cloudflare Dashboard / `wrangler hyperdrive update` (do not place DB credentials in `wrangler.toml`).
- Note that Workers Free plan supports up to `100,000` Hyperdrive queries/day.

### 2.2 Run Alembic Migrations Against Neon (Direct Connection)

Always run Alembic migrations against the **direct (non-pooled)** Neon connection string (`DATABASE_URL`), **not** via Hyperdrive or PgBouncer transaction pooling, to prevent DDL lock/prepared-statement issues:

```powershell
cd P:\CLUB\Algohub\atc\backend
$env:DATABASE_URL = "postgresql+psycopg://<USER>:<PASSWORD>@<NEON_DIRECT_HOST>/<DB>?sslmode=require"
python -m alembic current
python -m alembic upgrade head
python -m alembic current
```
Verify revision reaches `003_clerk_auth_policies (head)`.

---

## 3. Clerk Webhook & Email Sender Verification

### 3.1 Clerk Webhook Setup (`POST /api/v1/webhooks/clerk`)

1. Open **Clerk Dashboard -> Webhooks -> Add Endpoint**.
2. Set Endpoint URL:
   ```text
   https://atc-dsa-backend.webatc77.workers.dev/api/v1/webhooks/clerk
   ```
3. Subscribe to events:
   - `user.updated`
   - `user.deleted`
   - `session.revoked`
   - *(Optional, logged as non-revoking)* `session.ended`, `session.removed`
4. Copy the Svix **Signing Secret** (`whsec_...`) and upload it to Cloudflare Workers:
   ```powershell
   cd P:\CLUB\Algohub\atc\backend
   npx wrangler secret put CLERK_WEBHOOK_SECRET
   ```

### 3.2 Email Provider Verification (Resend & Brevo) & Required DNS Records

- The backend calls **Resend** first (`https://api.resend.com/emails`) and falls back to **Brevo** (`https://api.brevo.com/v3/smtp/email`) on `5xx`, `401`/`403`/`429`, `ConnectTimeout`, and network errors (skipping Brevo fallback on Resend ambiguous `ReadTimeout` or `400`/`422` validation errors to prevent duplicate emails).
- **Observed DNS State (`bennett.edu.in`)**: Public DNS shows Microsoft 365 MX (`bennett-edu-in.mail.protection.outlook.com`), `selector1`/`selector2._domainkey.bennett.edu.in` (CNAMEs to `bennettu.onmicrosoft.com`), strict SPF (`v=spf1 ip4:15.252.252.72 include:spf.protection.outlook.com -all`), and `p=quarantine` DMARC (`v=DMARC1; p=quarantine; rua=mailto:BUDNS.DMARC@bennett.edu.in; ruf=mailto:BUDNS.DMARC@bennett.edu.in`). Candidate subdomains (`mail`, `send`, `noreply`, `email`, `notifications`, `atc`) and Resend/Brevo DKIM selectors (`resend._domainkey`, `mail._domainkey`, `brevo1._domainkey`, `brevo2._domainkey`, `dkim._domainkey`) currently return `[]`.
- **Required DNS Records for Authorized Sender Authentication** *(or point `EMAILS_FROM_EMAIL` in `wrangler.toml` to a verified team-controlled domain)*:
  1. **Resend**: Add the sender domain (e.g. `bennett.edu.in` or subdomain `mail.bennett.edu.in`) in Resend Dashboard and publish:
     - `TXT` `resend._domainkey.<domain>` -> DKIM public key generated by Resend
     - `MX` `send.<domain>` -> Resend feedback/bounce MX target from dashboard (priority `10`)
     - `TXT` `send.<domain>` -> `v=spf1 include:amazonses.com ~all`
     - Optional DMARC on dedicated subdomain (`_dmarc.mail.bennett.edu.in`) if using subdomain isolation
  2. **Brevo**: Add and authenticate the same sender domain/subdomain in Brevo Senders & Domains and publish:
     - Brevo DKIM `TXT` / `CNAME` selector records (`mail._domainkey.<domain>`, `brevo1._domainkey.<domain>`, `brevo2._domainkey.<domain>` as issued by Brevo)
     - Include `include:spf.brevo.com` in the sending domain's SPF `TXT` record before `-all` / `~all` so DMARC alignment passes.
- Upload API keys without printing them:
  ```powershell
  npx wrangler secret put RESEND_API_KEY
  npx wrangler secret put BREVO_API_KEY
  ```
- Test delivery as a `tech_admin` using `POST /api/v1/admin/jobs/test-email` after DNS propagation and inspect raw `Authentication-Results` (`SPF=pass`, `DKIM=pass`, `DMARC=pass`) in the recipient mailbox.

### 3.3 Cold-Isolate Runtime Notes (Pyodide Timezone Warning & Cold DB Latency)

- **Observed Cold DB Latency (`011bf25`)**: First-ever DB-backed requests on a fresh Worker isolate incur cold PostgreSQL dialect/connection initialization (`~449–1182 ms` CPU on `/roadmap/topics`, `/resources/`, and `/auth/clerk/exchange`, vs `13–131 ms` CPU warm and `45–52 ms` CPU on `/api/v1/openapi.json` via `_openapi_snapshot.py`).
- **Non-Fatal Timezone Log**: Neon direct reports `parameter_status(b"TimeZone") == b"GMT"`, and Cloudflare Python Workers (Pyodide WASM) lacks OS `tzdata`, causing `psycopg` to emit `"unknown PostgreSQL timezone: 'UTC'; will use UTC"` once per cold DB isolate while caching `timezone.utc` for all subsequent requests on that isolate. This warning is non-fatal (no errors or corrupted timestamps) and does not justify risky PgBouncer `-c timezone` startup hacks.

---

## 4. Rate Limiting & Optional Zone WAF Configuration

> **Important Honesty Note**: Having `[[ratelimits]]` configured in `backend/wrangler.toml` does **not** activate live edge enforcement until the Worker is deployed. Furthermore, custom Cloudflare Zone WAF rules are **not** available on the shared `*.workers.dev` subdomain.

### 4.1 Workers Rate Limiting Bindings (`wrangler.toml`)

Configured bindings enforced by `RateLimitMiddleware` (`app/core/rate_limit.py`) using `CF-Connecting-IP`:

| Binding | Namespace ID | Threshold | Protected Route(s) |
|---|---|---|---|
| `RL_AUTH_EXCHANGE` | `1001` | `10` req / `60s` | `POST /api/v1/auth/clerk/exchange` |
| `RL_AUTH_REFRESH` | `1002` | `20` req / `60s` | `POST /api/v1/auth/refresh` |
| `RL_ANALYTICS` | `1003` | `60` req / `60s` | `POST /api/v1/analytics/click`, `POST /api/v1/analytics/event` |
| `RL_WEBHOOKS` | `1004` | `100` req / `60s` | `POST /api/v1/webhooks/clerk` |

### 4.2 Optional Cloudflare Dashboard WAF Rate Limiting (Custom Domain Only)

If you attach a custom Cloudflare-proxied domain route (e.g. `api.yourdomain.com/*`) later, you can add edge WAF Rate Limiting rules on the Cloudflare Free plan (**1 free rule**, 10-second period only; edge path filtering only):
1. Open **Cloudflare Dashboard -> `<Your Zone>` -> Security -> WAF -> Rate limiting rules -> Create rule**.
2. Recommended rule expressions & free-plan 10-second thresholds:
   - `(http.request.uri.path eq "/api/v1/auth/clerk/exchange" and http.request.method eq "POST")` -> `5` requests / `10` seconds per IP -> **Block** (`10s`).
   - Or combine high-risk auth/analytics paths into your 1 Free-plan WAF rule while keeping per-endpoint `60s` limits in the Worker's `[[ratelimits]]` bindings.

---

## 5. Deploying Python Workers (`pywrangler` via WSL2 / Linux)

Cloudflare Python Workers use `pywrangler` (`workers-py`) to bundle Python dependencies into the Worker bundle. On Windows, run `pywrangler` inside **WSL2 (Ubuntu/Linux)** (or Linux CI):

```bash
# Inside WSL2 / Linux terminal
cd /mnt/p/CLUB/Algohub/atc/backend

# Create/activate virtualenv with workers dependency group if not already installed
python3 -m venv .venv-wsl
source .venv-wsl/bin/activate
pip install --upgrade pip
pip install workers-py

# Optional dry-run packaging check
npx wrangler deploy --dry-run

# Sync Python packages and deploy to Cloudflare Workers
pywrangler sync
pywrangler deploy
```

### Cron Triggers Configured in `wrangler.toml`

Once deployed, Cloudflare Cron Triggers invoke `Default.scheduled()` in `worker.py` -> `app/main.py`:
- **`0 1-23 * * *` (Hourly)**: Runs `cleanup_expired_sessions` + `ClerkOutboxService.process_pending(limit=25)`.
- **`0 0 * * *` (Daily Midnight UTC)**: Runs session cleanup + Clerk outbox retries + `run_resource_availability_scan(limit=20)`.

---

## 6. Post-Deploy Smoke Tests

Run these checks after replacing placeholders (`ALLOWED_ORIGINS`, `CLERK_ALLOWED_ORIGINS`), uploading secrets, running `alembic upgrade head`, and deploying via `pywrangler deploy`:

```powershell
# 1. Health check (verify status="ok" and auth_mode="clerk")
Invoke-RestMethod -Uri "https://atc-dsa-backend.webatc77.workers.dev/health"

# 2. Versioned health & OpenAPI schema check
Invoke-RestMethod -Uri "https://atc-dsa-backend.webatc77.workers.dev/api/v1/openapi.json" | Select-Object -ExpandProperty info

# 3. Public roadmap read (must return ONLY published items)
Invoke-RestMethod -Uri "https://atc-dsa-backend.webatc77.workers.dev/api/v1/roadmap/topics"

# 4. Legacy endpoint check (must return HTTP 410 Gone)
try {
  Invoke-RestMethod -Method Post -Uri "https://atc-dsa-backend.webatc77.workers.dev/api/v1/auth/login" -ContentType "application/json" -Body '{"email":"test@bennett.edu.in","password":"password123"}'
} catch {
  $_.Exception.Response.StatusCode.value__
}

# 5. Unauthorized admin guard check (must return HTTP 401)
try {
  Invoke-RestMethod -Uri "https://atc-dsa-backend.webatc77.workers.dev/api/v1/dashboard/admin"
} catch {
  $_.Exception.Response.StatusCode.value__
}
```

Manual checks requiring live credentials / browser:
- Exchange a live Clerk JWT from your frontend domain (`ALLOWED_ORIGINS` / `CLERK_ALLOWED_ORIGINS`) -> verify `200 Token` response and CORS `Access-Control-Allow-Origin` header.
- Verify Clerk webhook delivery (`200 processed`) in **Clerk Dashboard -> Webhooks -> Logs**.
- Trigger `POST /api/v1/admin/jobs/test-email` as `tech_admin` -> verify Resend/Brevo email arrives.
- Verify `429 Too Many Requests` (`Retry-After: 60`) after exceeding `10` rapid `POST /api/v1/auth/clerk/exchange` calls from one IP.

---

## 7. Rollback Instructions

### 7.1 Worker Code / Configuration Rollback

```powershell
cd P:\CLUB\Algohub\atc\backend
npx wrangler deployments list
npx wrangler rollback <DEPLOYMENT_VERSION_ID>
```

### 7.2 Database Schema Rollback (`003_clerk_auth_policies` -> `002_profile_analytics`)

Migration `003` makes `users.hashed_password` nullable to support Clerk-only users. **`alembic downgrade 002_profile_analytics` intentionally fails** if any row in `users` still has `hashed_password IS NULL`:

1. Ensure no live Worker instance is writing Clerk-only rows (`AUTH_MODE=legacy` or maintenance window / rollback Worker first).
2. Inspect and handle any users with `hashed_password IS NULL` using an approved recovery script/notification process (do not invent weak static passwords).
3. Once `SELECT COUNT(*) FROM users WHERE hashed_password IS NULL` equals `0`, downgrade via direct Neon `DATABASE_URL`:
   ```powershell
   cd P:\CLUB\Algohub\atc\backend
   $env:DATABASE_URL = "postgresql+psycopg://<USER>:<PASSWORD>@<NEON_DIRECT_HOST>/<DB>?sslmode=require"
   python -m alembic downgrade 002_profile_analytics
   ```
