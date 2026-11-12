# ATC DSA Roadmap & Resource Hub — final deployment branch

This branch is an isolated deployment assembly. It is based on the FastAPI `Backend` branch and includes the React/Vite application from the `frontend` branch.

## Architecture

- **Frontend:** React 19 + TypeScript + Vite, deployed to Cloudflare Pages from `frontend/`.
- **Backend:** FastAPI on Cloudflare Python Workers, deployed from `backend/`.
- **Database:** Neon PostgreSQL, connected at runtime through a separately-created Cloudflare Hyperdrive configuration.
- **Authentication:** Clerk. Configure your own Clerk production instance and webhook.
- **Email:** Resend primary / Brevo fallback, with a verified sender domain.

## Branch safety

The `final` branch is separate. The existing `main`, `frontend`, and `Backend` branches are not modified by this assembly. Do not merge this branch into them unless you explicitly choose to.

## Start here

Read [docs/CLOUDFLARE_DEPLOYMENT.md](docs/CLOUDFLARE_DEPLOYMENT.md) before configuring infrastructure. Account-specific IDs, domains, database credentials, and API secrets are deliberately placeholders and must be supplied in your own accounts.

## Local development

Backend:

```bash
cd backend
cp .env.example .env
uv sync --extra dev
uv run uvicorn app.main:app --reload
```

Frontend (another terminal):

```bash
cd frontend
cp .env.example .env.local
npm ci
npm run dev
```

The frontend API helper is available at `frontend/src/lib/api.ts`. The current React UI is still the static club landing page; authenticated student/admin screens and feature-level API wiring are not complete in this branch yet.

## Intentionally excluded

The legacy Go `auth-service` and `club-service`, and their old `database/schema.sql`, are not part of the deployment. They expose user data without authentication/authorization and use a different schema from the FastAPI/Alembic application. They must be redesigned before being deployed.
