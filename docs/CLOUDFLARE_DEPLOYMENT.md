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

The backend seed fixtures are in `backend/seeds/`. After migrations, import the initial roadmap and resources using the documented admin import endpoints and a properly authorized tech-admin account.

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
4. Add GitHub secrets and the `VITE_API_BASE_URL` variable.
5. Create the Pages project `atc-dsa-final-frontend`.
6. Run the database migration workflow once against your new database.
7. Run the backend deployment workflow.
8. Set the `VITE_API_BASE_URL` variable to the actual Worker API URL and run the frontend deployment workflow.
9. Verify `/health`, `/api/v1/docs`, browser CORS, authentication exchange, database-backed student flows, admin permissions, email delivery and scheduled jobs.

## 7. Current integration boundary / remaining application work

This branch assembles both applications and supplies deployment workflows, environment templates and a typed API request helper. It does **not** claim that the full product UI is complete:

- The React source copied from the `frontend` branch remains a mostly static landing page.
- Clerk React is not installed or wired into the React entry point yet. The backend is configured for Clerk production and documents the JWT exchange contract in `backend/FRONTEND_HANDOVER.md`.
- Dashboard, roadmap, resources, progress, bookmarks and admin screens still need frontend implementation and API wiring.
- Events, Projects and Gallery appear in the current landing-page navigation/cards, but the FastAPI API router does not expose corresponding feature APIs.
- Resend/Brevo sender verification, real Clerk webhook delivery and live end-to-end tests require your provider accounts and domains.

Do not treat a successful static Pages deployment as proof that authentication or application workflows are integrated.
