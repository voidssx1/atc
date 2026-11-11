"""Add Clerk linking, session policies, webhook ledger, and deletion outbox

Revision ID: 003_clerk_auth_policies
Revises: 002_profile_analytics
Create Date: 2026-11-10 15:53:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003_clerk_auth_policies"
down_revision: Union[str, Sequence[str], None] = "002_profile_analytics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Additive Clerk identity fields on users & make hashed_password nullable
    op.add_column(
        "users",
        sa.Column("clerk_user_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("clerk_updated_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_users_clerk_user_id", "users", ["clerk_user_id"], unique=True)
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    # 2. Session correlation, rotation grace window, and Tech single-session guard
    op.add_column(
        "user_sessions",
        sa.Column("clerk_session_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user_sessions",
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_sessions",
        sa.Column(
            "enforce_single_session",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_user_sessions_clerk_session_id",
        "user_sessions",
        ["clerk_session_id"],
        unique=False,
    )
    op.create_index(
        "uq_active_primary_clerk_session",
        "user_sessions",
        ["user_id", "clerk_session_id"],
        unique=True,
        postgresql_where=sa.text(
            "is_revoked = false AND grace_until IS NULL AND clerk_session_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "is_revoked = 0 AND grace_until IS NULL AND clerk_session_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_tech_single_active_session",
        "user_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "is_revoked = false AND grace_until IS NULL AND enforce_single_session = true"
        ),
        sqlite_where=sa.text(
            "is_revoked = 0 AND grace_until IS NULL AND enforce_single_session = 1"
        ),
    )

    # 3. Webhook idempotency table
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("svix_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("svix_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_webhook_events_svix_id", "webhook_events", ["svix_id"], unique=True
    )
    op.create_index(
        "ix_webhook_events_event_type", "webhook_events", ["event_type"], unique=False
    )

    # 4. Clerk deletion outbox table
    op.create_table(
        "clerk_outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("clerk_user_id", sa.String(length=255), nullable=False),
        sa.Column("audit_target_id", sa.String(length=100), nullable=False),
        sa.Column(
            "operation",
            sa.String(length=50),
            nullable=False,
            server_default="delete_user",
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_clerk_outbox_clerk_user_id", "clerk_outbox", ["clerk_user_id"], unique=False
    )
    op.create_index("ix_clerk_outbox_status", "clerk_outbox", ["status"], unique=False)
    op.create_index(
        "ix_clerk_outbox_next_retry_at", "clerk_outbox", ["next_retry_at"], unique=False
    )


def downgrade() -> None:
    """
    Safe Rollback Procedure for Migration 003:
      If Clerk-only accounts (users.hashed_password IS NULL) exist, this downgrade
      deliberately fails rather than inventing weak/fake password hashes or deleting
      user accounts. To roll back in that state:
        1. Set AUTH_MODE=local (or maintenance mode) while preserving user rows.
        2. Export or notify affected users so they can establish local credentials,
           or run an approved admin recovery script to explicitly backfill hashes
           and require password reset on next login.
        3. Re-run `python -m alembic downgrade 002_profile_analytics` once zero rows
           have `hashed_password IS NULL`.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                DO $$
                DECLARE
                    null_pw_count INTEGER;
                BEGIN
                    SELECT COUNT(*) INTO null_pw_count FROM users WHERE hashed_password IS NULL;
                    IF null_pw_count > 0 THEN
                        RAISE EXCEPTION 'Cannot safely downgrade migration 003: % user account(s) have NULL hashed_password (Clerk-only accounts). Backfill or migrate accounts via an approved recovery procedure before downgrading.', null_pw_count;
                    END IF;
                END $$;
                """
            )
        )
    elif not op.get_context().as_sql:
        null_count = bind.execute(
            sa.text("SELECT COUNT(*) FROM users WHERE hashed_password IS NULL")
        ).scalar()
        if null_count and null_count > 0:
            raise RuntimeError(
                f"Cannot safely downgrade migration 003: {null_count} user account(s) have NULL "
                "hashed_password (Clerk-only accounts). Backfill or migrate accounts via an "
                "approved recovery procedure before downgrading."
            )

    op.drop_index("ix_clerk_outbox_next_retry_at", table_name="clerk_outbox")
    op.drop_index("ix_clerk_outbox_status", table_name="clerk_outbox")
    op.drop_index("ix_clerk_outbox_clerk_user_id", table_name="clerk_outbox")
    op.drop_table("clerk_outbox")

    op.drop_index("ix_webhook_events_event_type", table_name="webhook_events")
    op.drop_index("ix_webhook_events_svix_id", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index("uq_tech_single_active_session", table_name="user_sessions")
    op.drop_index("uq_active_primary_clerk_session", table_name="user_sessions")
    op.drop_index("ix_user_sessions_clerk_session_id", table_name="user_sessions")
    op.drop_column("user_sessions", "enforce_single_session")
    op.drop_column("user_sessions", "grace_until")
    op.drop_column("user_sessions", "clerk_session_id")

    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.drop_index("ix_users_clerk_user_id", table_name="users")
    op.drop_column("users", "clerk_updated_at")
    op.drop_column("users", "clerk_user_id")
