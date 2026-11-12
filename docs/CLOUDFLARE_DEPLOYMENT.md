# Independent Cloudflare deployment guide

This guide applies only to the `final` branch. It deliberately does not reuse the previous developer's Cloudflare Worker, Hyperdrive ID, Neon database, Clerk instance, or secrets.

## 1. Create your own infrastructure

Create these resources under your own accounts:

1. Cloudflare Pages project: `atc-dsa-final-frontend` (production branch: `final`).
2. Cloudflare Worker name: `atc-dsa-final-api` (created by the Worker deployment workflow).
3. A new Neon PostgreSQL project/database.
4. A new Cloudflare Hyperdrive configuration pointing to your Neon PostgreSQL connection.
5. A new Clerk application configured for production.
6. Resend and/or Brevo credentials and a sender domain you are authorized to use.

Do not point this branch at another developer's Neon database or Hyperdrive configuration.

## 2. Configure backend Worker

Edit `backend/wrangler.toml` in this branch only:

- Replace `REPLACE_WITH_YOUR_HYPERDRIVE_ID` with the ID of your own Hyperdrive configuration.
- Replace `REPLACE_WITH_WORKERS_SUBDOMAIN` with your Cloudflare Workers subdomain.
- Replace `REPLACE_WITH_CLERK_ISSUER` in both Clerk settings with your Clerk production issuer (without a trailing slash).
- Replace `REPLACE_WITH_VERIFIED_SENDER_EMAIL` with a sender address verified by your email provider.
- Update `ALLOWED_ORIGINS` and `CLERK_ALLOWED_ORIGINS` if you use a custom Pages domain. Keep exact origins, including scheme, without trailing slash.

The default Pages origin configured here is `https://atc-dsa-final-frontend.pages.dev`. If Cloudflare assigns a different domain or you attach a custom domain, update both allowlists.

## 3. Configure Worker secrets

From `backend/`, run these commands and enter values from your own providers:

```bash
npx wrangler secret put SECRET_KEY
npx wrangler secret put CLERK_SECRET_KEY
npx wrangler secret put CLERK_WEBHOOK_SECRET
npx wrangler secret put RESEND_API_KEY
npx wrangler secret put BREVO_API_KEY
```

Use a cryptographically random `SECRET_KEY` of at least 32 characters. Upload at least one email provider key; configure both if you want fallback. Never commit secret values.

## 4. Database migrations

Use the Neon **direct, non-pooled** PostgreSQL connection URL for Alembic migrations. Do not use the Hyperdrive URL or PgBouncer transaction-pooling URL for schema migrations.

Locally:

```bash
cd backend
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@DIRECT_HOST/DB?sslmode=require'
uv sync --extra local
uv run alembic current
uv run alembic upgrade head
uv run alembic current
```

Or add the direct URL as GitHub Actions repository secret `DATABASE_URL` and manually run the **Run ATC database migrations** workflow. Review the target database before running migrations. Never use another developer's production database.

The migration workflow automatically runs `backend/scripts/seed_initial_content.py` after applying Alembic migrations. The script imports the bundled roadmap and resource fixtures idempotently; you can also run it locally with `uv run python scripts/seed_initial_content.py` from `backend/` after setting the direct `DATABASE_URL`.

## 5. Configure GitHub Actions

Add these repository secrets:

- `CLOUDFLARE_API_TOKEN`: token with Workers Scripts:Edit and Cloudflare Pages:Edit permissions for your account.
- `CLOUDFLARE_ACCOUNT_ID`: your Cloudflare account ID.
- `DATABASE_URL`: Neon direct connection URL; needed only by the migration workflow.

Add this repository variable:

- `VITE_API_BASE_URL`: `https://atc-dsa-final-api.YOUR_WORKERS_SUBDOMAIN.workers.dev/api/v1` (replace with your deployed Worker URL).
- `VITE_CLERK_PUBLISHABLE_KEY`: the publishable key from your own Clerk production application.

The Pages project must exist before the Pages workflow runs. The Worker name and Hyperdrive ID must be valid before the backend workflow runs.

## 6. Deployment order

1. Create the Neon database and Hyperdrive binding.
2. Configure Clerk production application, issuer, JWKS URL and webhook.
3. Update `backend/wrangler.toml` placeholders.
4. Add GitHub secrets and both frontend variables (`VITE_API_BASE_URL` and `VITE_CLERK_PUBLISHABLE_KEY`).
5. Create the Pages project `atc-dsa-final-frontend`.
6. Run the database migration workflow once against your new database; it also seeds the starter roadmap and resources.
7. Run the backend deployment workflow.
8. Set the `VITE_API_BASE_URL` variable to the actual Worker API URL and run the frontend deployment workflow.
9. Verify `/health`, `/api/v1/docs`, browser CORS, authentication exchange, database-backed student flows, admin permissions, email delivery and scheduled jobs.

## 7. Integration status and remaining deployment-only tasks

The React workspace is integrated with the FastAPI API: ClerkJS session authentication is bridged into React context; the Clerk JWT is exchanged for an opaque backend session; the application token is kept in memory and automatically refreshed through `/auth/refresh`; the current user role is loaded from `/auth/me`. Student screens cover dashboard, roadmap progress, resources, bookmarks and profile. Admin screens cover overview, user management, content draft submission/review and community content management. Events, Projects and Gallery are backed by the new `community_items` table and `/api/v1/community` routes.

The original club landing page remains available as a separate view. The remaining work is account configuration and live verification, not writing the UI:

- Add GitHub repository variables `VITE_API_BASE_URL` and `VITE_CLERK_PUBLISHABLE_KEY`.
- Configure your Clerk production instance: allowed origins/redirect URLs for your Pages domain, verified Bennett student email policy, and a webhook to `/api/v1/webhooks/clerk` with the required user/session events.
- Bootstrap the first `tech_admin`: create/sign in with your verified Bennett account, then use your own Neon SQL console to promote only that account (`UPDATE users SET role = 'tech_admin' WHERE email = 'YOUR_VERIFIED_BENNETT_EMAIL' AND clerk_user_id IS NOT NULL;`). Record its Clerk user ID and database user UUID. Set `CLERK_TECH_ADMIN_USER_ID`, `ATC_TECH_ADMIN_DB_USER_ID`, `ATC_WORKER_BASE`, `ATC_FRONTEND_ORIGIN`, `CLERK_FAPI_URL`, and `DATABASE_URL` in your local shell, then run `cd backend && uv run python scripts/verify_clerk_tech_admin.py`. The verifier defaults now point to placeholders, not the previous developer’s tenant. Never reuse another account’s IDs.
- Configure your own Neon database and Hyperdrive binding, then run Alembic migrations through revision `004_community_content`.
- Add Cloudflare credentials and backend secrets; verify Resend/Brevo sender DNS and inbox delivery.
- After deployment, run end-to-end checks for student sign-in, role hydration, token refresh, student progress/bookmarks, admin content approvals, community publishing, webhooks, CORS and Cron jobs.

No Cloudflare deployment or production database migration has been performed from this branch.
