# Database Schema & Architecture

This document describes the relational schema, constraints, indexes, Alembic migration history, and Neon PostgreSQL + Cloudflare Hyperdrive connection architecture for the **ATC DSA Roadmap & Resource Hub** backend (`13` application tables).

> **Verification Status**: All 13 SQLAlchemy models and migrations (`001_initial_schema` -> `002_profile_analytics` -> `003_clerk_auth_policies`) are verified against in-memory SQLite in `pytest` (`78 passed`) and Alembic offline/metadata inspection. Live execution of `003_clerk_auth_policies` and live Hyperdrive queries against production **Neon PostgreSQL** are **not yet verified** until `alembic upgrade head` is run against Neon and the Worker is deployed.

---

## 1. Neon PostgreSQL & Cloudflare Hyperdrive Architecture

- **Database Engine**: Neon Serverless PostgreSQL (`postgresql+psycopg://...`, `psycopg[binary]>=3.2.0`)
- **Edge Connection Pooling / Caching**: Cloudflare Hyperdrive
  - Binding Name: `HYPERDRIVE`
  - Configuration ID: `34e6e006848f46c4b75e382dbfafd02a` (configured in `backend/wrangler.toml`)
- **SQLAlchemy Pooling Strategy (`app/db/session.py`)**:
  - Uses **`NullPool`** (`poolclass=NullPool`) for all non-SQLite PostgreSQL URLs. Connections are never held idle inside Python Worker isolates; Cloudflare Hyperdrive maintains pooled PostgreSQL connections at the edge (`extract_hyperdrive_url(env)` reads `HYPERDRIVE.connectionString` per request or scheduled invocation).
  - Engines and `sessionmaker`s are cached in `_engines` / `_session_locals` keyed by normalized connection string.
  - All `alembic` commands (`alembic/env.py`) also connect via `NullPool` using `settings.DATABASE_URL`.
- **Plan Consideration**: Cloudflare Workers Free plan permits up to `100,000` Hyperdrive queries/day; inactivity write debounces (`60s` for admin sessions, `15m` for student sessions) help avoid unnecessary `UPDATE user_sessions` writes on every request.

---

## 2. Entity-Relationship Overview

```
users (1) ──┬── (0..1) student_profiles      [CASCADE]
            ├── (0..*) user_sessions         [CASCADE]
            ├── (0..*) student_progress      [CASCADE]
            ├── (0..*) bookmarks             [CASCADE]
            ├── (0..*) content_drafts (sub)  [CASCADE]
            ├── (0..*) content_drafts (rev)  [SET NULL]
            ├── (0..*) resources (creator)   [SET NULL]
            ├── (0..*) audit_logs            [SET NULL]
            └── (0..*) resource_analytics    [SET NULL]

roadmap_topics (1) ── (0..*) roadmap_subtopics   [CASCADE]
roadmap_subtopics (1) ──┬── (0..*) roadmap_subtopics (parent_subtopic_id) [CASCADE]
                        ├── (0..*) resources     [CASCADE]
                        └── (0..*) student_progress [CASCADE]

resources (1) ──┬── (0..*) bookmarks             [CASCADE]
                └── (0..*) resource_analytics    [CASCADE]

Standalone operational tables:
- webhook_events (Svix webhook idempotency ledger)
- clerk_outbox (Retry/dead-letter queue for remote Clerk user deletions)
```

---

## 3. Complete Table Specifications (`13` Tables)

> **UUID Storage**: SQLAlchemy `sa.Uuid(as_uuid=True)` maps to native PostgreSQL `UUID` in Neon/Alembic (`001`–`003`) and `CHAR(32)` under SQLite tests. Timestamps use `DateTime(timezone=True)` (`TIMESTAMPTZ` in PostgreSQL).

### 3.1 `users`

Primary account table for students and administrators.

| Column | Type | Nullable | Unique / Default / Notes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `email` | `VARCHAR(255)` | No | Unique (`ix_users_email`) |
| `clerk_user_id` | `VARCHAR(255)` | Yes | Unique (`ix_users_clerk_user_id`, added in `003`) |
| `clerk_updated_at` | `BIGINT` | Yes | Monotonic Clerk webhook guard (added in `003`) |
| `hashed_password` | `VARCHAR(255)` | Yes | Nullable in `003` for Clerk-only accounts |
| `username` | `VARCHAR(100)` | Yes | Unique (`ix_users_username`) |
| `role` | `VARCHAR(50)` | No | Indexed (`ix_users_role`), default `"student"` |
| `is_active` | `BOOLEAN` | No | Default `true` |
| `is_verified` | `BOOLEAN` | No | Default `false` |
| `verification_token` | `VARCHAR(255)` | Yes | Indexed (`ix_users_verification_token`) |
| `verification_token_expires_at` | `TIMESTAMPTZ` | Yes | Legacy verification expiry |
| `reset_password_token` | `VARCHAR(255)` | Yes | Indexed (`ix_users_reset_password_token`) |
| `reset_password_expires_at` | `TIMESTAMPTZ` | Yes | Legacy reset expiry |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Updated on modification |

### 3.2 `student_profiles`

Minimal profile table trimmed to `full_name` in migration `002`.

| Column | Type | Nullable | Constraints / Notes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `user_id` | `UUID` | No | FK -> `users.id` (`ON DELETE CASCADE`), Unique (`ix_student_profiles_user_id`) |
| `full_name` | `VARCHAR(100)` | Yes | Resized from `150` -> `100` in `002` |
| `created_at` | `TIMESTAMPTZ` | No | Default `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | Updated on modification |

### 3.3 `user_sessions`

Opaque SHA-256 session store supporting Clerk session binding, rotation grace windows, and Tech Admin single-session enforcement.

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `user_id` | `UUID` | No | FK -> `users.id` (`ON DELETE CASCADE`), indexed (`ix_user_sessions_user_id`) |
| `token` | `VARCHAR(500)` | No | SHA-256 hex digest of raw token; Unique (`ix_user_sessions_token`) |
| `clerk_session_id` | `VARCHAR(255)` | Yes | Indexed (`ix_user_sessions_clerk_session_id`, added in `003`) |
| `grace_until` | `TIMESTAMPTZ` | Yes | Rotation grace expiry (added in `003`) |
| `enforce_single_session` | `BOOLEAN` | No | Default `false`; `true` for `tech_admin` (added in `003`) |
| `expires_at` | `TIMESTAMPTZ` | No | Sliding inactivity expiry (clamped by `created_at + 30d` max lifetime) |
| `is_revoked` | `BOOLEAN` | No | Default `false` |
| `user_agent` | `VARCHAR(255)` | Yes | Request User-Agent |
| `ip_address` | `VARCHAR(100)` | Yes | Request client IP |
| `created_at` | `TIMESTAMPTZ` | No | Session creation timestamp |

**Partial Unique Indexes (added in `003`)**:
- `uq_active_primary_clerk_session` on `(user_id, clerk_session_id)`:
  - PostgreSQL `WHERE is_revoked = false AND grace_until IS NULL AND clerk_session_id IS NOT NULL`
- `uq_tech_single_active_session` on `(user_id)`:
  - PostgreSQL `WHERE is_revoked = false AND grace_until IS NULL AND enforce_single_session = true`

### 3.4 `webhook_events` *(added in `003`)*

Idempotency ledger preventing duplicate Clerk/Svix webhook processing.

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `svix_id` | `VARCHAR(255)` | No | Unique (`ix_webhook_events_svix_id`) |
| `event_type` | `VARCHAR(100)` | No | Indexed (`ix_webhook_events_event_type`) |
| `svix_timestamp` | `BIGINT` | No | Unix seconds timestamp from Svix header |
| `processed_at` | `TIMESTAMPTZ` | No | Processing timestamp |

### 3.5 `clerk_outbox` *(added in `003`)*

Transactional retry queue for remote Clerk user deletions that fail transiently.

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `clerk_user_id` | `VARCHAR(255)` | No | Indexed (`ix_clerk_outbox_clerk_user_id`) |
| `audit_target_id` | `VARCHAR(100)` | No | Local UUID string for audit correlation |
| `operation` | `VARCHAR(50)` | No | Default `"delete_user"` |
| `status` | `VARCHAR(30)` | No | Indexed (`ix_clerk_outbox_status`); `"pending"`, `"completed"`, `"dead_letter"` |
| `attempts` | `INTEGER` | No | Default `0` |
| `max_attempts` | `INTEGER` | No | Default `5` |
| `next_retry_at` | `TIMESTAMPTZ` | No | Indexed (`ix_clerk_outbox_next_retry_at`) |
| `last_error` | `TEXT` | Yes | Last provider/network error summary |
| `created_at` | `TIMESTAMPTZ` | No | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | No | Last retry/update timestamp |

### 3.6 `roadmap_topics`

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `title` | `VARCHAR(150)` | No | Topic title |
| `slug` | `VARCHAR(150)` | No | Unique (`ix_roadmap_topics_slug`) |
| `description` | `TEXT` | Yes | Optional markdown/plain description |
| `order_index` | `INTEGER` | No | Indexed (`ix_roadmap_topics_order_index`), default `0` |
| `is_published` | `BOOLEAN` | No | Indexed (`ix_roadmap_topics_is_published`), default `true` |
| `created_at` | `TIMESTAMPTZ` | No | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | No | Update timestamp |

### 3.7 `roadmap_subtopics`

Supports arbitrary nesting via self-referential `parent_subtopic_id`.

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `topic_id` | `UUID` | No | FK -> `roadmap_topics.id` (`ON DELETE CASCADE`), indexed |
| `parent_subtopic_id` | `UUID` | Yes | FK -> `roadmap_subtopics.id` (`ON DELETE CASCADE`), indexed |
| `title` | `VARCHAR(150)` | No | Subtopic title |
| `slug` | `VARCHAR(150)` | No | Indexed (`ix_roadmap_subtopics_slug`, non-unique across topics) |
| `description` | `TEXT` | Yes | Description |
| `order_index` | `INTEGER` | No | Indexed (`ix_roadmap_subtopics_order_index`), default `0` |
| `difficulty` | `VARCHAR(50)` | No | Default `"medium"` |
| `is_published` | `BOOLEAN` | No | Indexed (`ix_roadmap_subtopics_is_published`), default `true` |
| `created_at` | `TIMESTAMPTZ` | No | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | No | Update timestamp |

### 3.8 `resources`

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `subtopic_id` | `UUID` | Yes | FK -> `roadmap_subtopics.id` (`ON DELETE CASCADE`), indexed |
| `title` | `VARCHAR(255)` | No | Resource title |
| `url` | `TEXT` | No | Validated HTTP/HTTPS URL |
| `resource_type` | `VARCHAR(50)` | No | Indexed (`ix_resources_resource_type`), default `"article"` |
| `difficulty` | `VARCHAR(50)` | No | Default `"medium"` |
| `availability_status` | `VARCHAR(50)` | No | Indexed (`ix_resources_availability_status`), default `"unchecked"` |
| `last_checked_at` | `TIMESTAMPTZ` | Yes | Last URL availability check |
| `status_code` | `INTEGER` | Yes | HTTP status code from last scan |
| `is_archived` | `BOOLEAN` | No | Indexed (`ix_resources_is_archived`), default `false` |
| `order_index` | `INTEGER` | No | Indexed (`ix_resources_order_index`), default `0` |
| `created_by_id` | `UUID` | Yes | FK -> `users.id` (`ON DELETE SET NULL`) |
| `created_at` | `TIMESTAMPTZ` | No | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | No | Update timestamp |

### 3.9 `content_drafts`

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `entity_type` | `VARCHAR(50)` | No | Indexed (`ix_content_drafts_entity_type`); `"topic"`, `"subtopic"`, `"resource"` |
| `entity_id` | `UUID` | Yes | Indexed (`ix_content_drafts_entity_id`); target live entity UUID after/for publish |
| `title` | `VARCHAR(255)` | No | Draft label |
| `payload` | `JSON` | No | Draft JSON object |
| `status` | `VARCHAR(50)` | No | Indexed (`ix_content_drafts_status`), default `"draft"` |
| `version_number` | `INTEGER` | No | Increments on edits, default `1` |
| `submitted_by_id` | `UUID` | No | FK -> `users.id` (`ON DELETE CASCADE`), indexed |
| `reviewed_by_id` | `UUID` | Yes | FK -> `users.id` (`ON DELETE SET NULL`) |
| `review_feedback` | `TEXT` | Yes | Reviewer notes |
| `is_archived` | `BOOLEAN` | No | Indexed (`ix_content_drafts_is_archived`), default `false` |
| `created_at` | `TIMESTAMPTZ` | No | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | No | Update timestamp |

### 3.10 `student_progress`

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `user_id` | `UUID` | No | FK -> `users.id` (`ON DELETE CASCADE`), indexed |
| `subtopic_id` | `UUID` | No | FK -> `roadmap_subtopics.id` (`ON DELETE CASCADE`), indexed |
| `status` | `VARCHAR(50)` | No | Indexed (`ix_student_progress_status`), default `"completed"` |
| `notes` | `TEXT` | Yes | Student study notes |
| `completed_at` | `TIMESTAMPTZ` | Yes | Set when status becomes `"completed"` |
| `created_at` | `TIMESTAMPTZ` | No | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | No | Update timestamp |

- **Unique Constraint**: `uq_user_subtopic_progress` on `(user_id, subtopic_id)`

### 3.11 `bookmarks`

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `user_id` | `UUID` | No | FK -> `users.id` (`ON DELETE CASCADE`), indexed |
| `resource_id` | `UUID` | No | FK -> `resources.id` (`ON DELETE CASCADE`), indexed |
| `notes` | `TEXT` | Yes | Optional personal notes |
| `created_at` | `TIMESTAMPTZ` | No | Creation timestamp |

- **Unique Constraint**: `uq_user_resource_bookmark` on `(user_id, resource_id)`

### 3.12 `audit_logs`

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `user_id` | `UUID` | Yes | FK -> `users.id` (`ON DELETE SET NULL`), indexed (`ix_audit_logs_user_id`) |
| `action` | `VARCHAR(100)` | No | Indexed (`ix_audit_logs_action`) |
| `target_type` | `VARCHAR(100)` | Yes | Indexed (`ix_audit_logs_target_type`) |
| `target_id` | `VARCHAR(100)` | Yes | Target identifier |
| `details` | `JSON` | Yes | Structured audit context |
| `ip_address` | `VARCHAR(100)` | Yes | Origin IP |
| `created_at` | `TIMESTAMPTZ` | No | Indexed (`ix_audit_logs_created_at`) |

### 3.13 `resource_analytics`

Records individual outbound resource click events (`event_type` column removed in migration `002`).

| Column | Type | Nullable | Constraints / Indexes |
|---|---|---|---|
| `id` | `UUID` | No | Primary Key |
| `resource_id` | `UUID` | No | FK -> `resources.id` (`ON DELETE CASCADE`), indexed |
| `user_id` | `UUID` | Yes | FK -> `users.id` (`ON DELETE SET NULL`), indexed (`null` for anonymous clicks) |
| `created_at` | `TIMESTAMPTZ` | No | Indexed (`ix_resource_analytics_created_at`) |

---

## 4. Alembic Migration History & Rollback Safety

| Revision ID | Parent | Summary of Schema Changes |
|---|---|---|
| `001_initial_schema` | `<base>` | Creates core 11 tables (`users`, `student_profiles`, `user_sessions`, `roadmap_topics`, `roadmap_subtopics`, `resources`, `content_drafts`, `student_progress`, `bookmarks`, `audit_logs`, `resource_analytics`) with UUID PKs, FKs, and indexes. |
| `002_profile_analytics` | `001_initial_schema` | Drops legacy profile columns (`academic_year`, `college`, `github_url`, `linkedin_url`, `leetcode_url`, `codeforces_url`, `bio`), shrinks `student_profiles.full_name` to `VARCHAR(100)`, and drops `resource_analytics.event_type`. |
| `003_clerk_auth_policies` *(head)* | `002_profile_analytics` | Adds `clerk_user_id` (unique) & `clerk_updated_at` on `users`; makes `users.hashed_password` nullable; adds `clerk_session_id`, `grace_until`, `enforce_single_session`, and partial unique indexes `uq_active_primary_clerk_session` & `uq_tech_single_active_session` on `user_sessions`; creates `webhook_events` and `clerk_outbox`. |

### Guarded Downgrade for Migration `003`

`003_clerk_auth_policies.downgrade()` intentionally aborts with an exception if any `users` rows have `hashed_password IS NULL` (Clerk-only accounts), preventing data loss or insecure placeholder passwords. Backfill/notify affected users before running `python -m alembic downgrade 002_profile_analytics`.
