# ATC DSA Hub — FastAPI backend

FastAPI API for the ATC DSA Roadmap & Resource Hub. The same application runs locally with Uvicorn and on Cloudflare Python Workers.

## Local setup

Copy .env.example to .env, run uv sync --extra dev, then start with uv run uvicorn app.main:app --reload.

The local API is available at http://localhost:8000; OpenAPI docs are at /api/v1/docs.

## Database

- PostgreSQL/Neon is the production database.
- Alembic migrations live in alembic/versions/.
- Use the direct, non-pooled Neon connection for migrations.
- The final deployment migration workflow runs alembic upgrade head and then idempotently imports the starter roadmap/resources from seeds/ through scripts/seed_initial_content.py.
- Community content (events, projects, gallery) is introduced by revision 004_community_content.

## Authentication

Clerk session JWTs are verified by the backend and exchanged for opaque application sessions. Configure Clerk issuer/JWKS, secret key, webhook secret, allowed frontend origins and Bennett email-domain policy in your own environment. Do not commit real credentials.

## Tests

Run uv run pytest -q.

## Cloudflare

Read ../docs/CLOUDFLARE_DEPLOYMENT.md before deploying. The Worker configuration is wrangler.toml; the Worker entry point is src/worker.py. GitHub Actions deploys this backend only from the final branch.
