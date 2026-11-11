"""Initial database schema with UUID keys, indexes, and relationships

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-11-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="student"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_token", sa.String(length=255), nullable=True),
        sa.Column("verification_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_password_token", sa.String(length=255), nullable=True),
        sa.Column("reset_password_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_index(op.f("ix_users_verification_token"), "users", ["verification_token"], unique=False)
    op.create_index(op.f("ix_users_reset_password_token"), "users", ["reset_password_token"], unique=False)

    # 2. student_profiles
    op.create_table(
        "student_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(length=150), nullable=True),
        sa.Column("academic_year", sa.String(length=50), nullable=True),
        sa.Column("college", sa.String(length=200), nullable=True),
        sa.Column("github_url", sa.String(length=255), nullable=True),
        sa.Column("linkedin_url", sa.String(length=255), nullable=True),
        sa.Column("leetcode_url", sa.String(length=255), nullable=True),
        sa.Column("codeforces_url", sa.String(length=255), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    # 3. user_sessions
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(length=500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_sessions_token"), "user_sessions", ["token"], unique=True)

    # 4. roadmap_topics
    op.create_table(
        "roadmap_topics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("slug", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roadmap_topics_slug"), "roadmap_topics", ["slug"], unique=True)
    op.create_index(op.f("ix_roadmap_topics_order_index"), "roadmap_topics", ["order_index"], unique=False)
    op.create_index(op.f("ix_roadmap_topics_is_published"), "roadmap_topics", ["is_published"], unique=False)

    # 5. roadmap_subtopics
    op.create_table(
        "roadmap_subtopics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("parent_subtopic_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("slug", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("difficulty", sa.String(length=50), nullable=False, server_default="medium"),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["parent_subtopic_id"], ["roadmap_subtopics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["roadmap_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roadmap_subtopics_topic_id"), "roadmap_subtopics", ["topic_id"], unique=False)
    op.create_index(op.f("ix_roadmap_subtopics_parent_subtopic_id"), "roadmap_subtopics", ["parent_subtopic_id"], unique=False)
    op.create_index(op.f("ix_roadmap_subtopics_slug"), "roadmap_subtopics", ["slug"], unique=False)
    op.create_index(op.f("ix_roadmap_subtopics_order_index"), "roadmap_subtopics", ["order_index"], unique=False)
    op.create_index(op.f("ix_roadmap_subtopics_is_published"), "roadmap_subtopics", ["is_published"], unique=False)

    # 6. resources
    op.create_table(
        "resources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subtopic_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False, server_default="article"),
        sa.Column("difficulty", sa.String(length=50), nullable=False, server_default="medium"),
        sa.Column("availability_status", sa.String(length=50), nullable=False, server_default="unchecked"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subtopic_id"], ["roadmap_subtopics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_resources_subtopic_id"), "resources", ["subtopic_id"], unique=False)
    op.create_index(op.f("ix_resources_resource_type"), "resources", ["resource_type"], unique=False)
    op.create_index(op.f("ix_resources_availability_status"), "resources", ["availability_status"], unique=False)
    op.create_index(op.f("ix_resources_is_archived"), "resources", ["is_archived"], unique=False)
    op.create_index(op.f("ix_resources_order_index"), "resources", ["order_index"], unique=False)

    # 7. content_drafts
    op.create_table(
        "content_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="draft"),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("submitted_by_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("review_feedback", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_content_drafts_entity_type"), "content_drafts", ["entity_type"], unique=False)
    op.create_index(op.f("ix_content_drafts_entity_id"), "content_drafts", ["entity_id"], unique=False)
    op.create_index(op.f("ix_content_drafts_status"), "content_drafts", ["status"], unique=False)
    op.create_index(op.f("ix_content_drafts_is_archived"), "content_drafts", ["is_archived"], unique=False)

    # 8. student_progress
    op.create_table(
        "student_progress",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subtopic_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="completed"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["subtopic_id"], ["roadmap_subtopics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "subtopic_id", name="uq_user_subtopic_progress"),
    )
    op.create_index(op.f("ix_student_progress_user_id"), "student_progress", ["user_id"], unique=False)
    op.create_index(op.f("ix_student_progress_subtopic_id"), "student_progress", ["subtopic_id"], unique=False)
    op.create_index(op.f("ix_student_progress_status"), "student_progress", ["status"], unique=False)

    # 9. bookmarks
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "resource_id", name="uq_user_resource_bookmark"),
    )
    op.create_index(op.f("ix_bookmarks_user_id"), "bookmarks", ["user_id"], unique=False)
    op.create_index(op.f("ix_bookmarks_resource_id"), "bookmarks", ["resource_id"], unique=False)

    # 10. audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=True),
        sa.Column("target_id", sa.String(length=100), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_target_type"), "audit_logs", ["target_type"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)

    # 11. resource_analytics
    op.create_table(
        "resource_analytics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False, server_default="click"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_resource_analytics_resource_id"), "resource_analytics", ["resource_id"], unique=False)
    op.create_index(op.f("ix_resource_analytics_user_id"), "resource_analytics", ["user_id"], unique=False)
    op.create_index(op.f("ix_resource_analytics_event_type"), "resource_analytics", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_table("resource_analytics")
    op.drop_table("audit_logs")
    op.drop_table("bookmarks")
    op.drop_table("student_progress")
    op.drop_table("content_drafts")
    op.drop_table("resources")
    op.drop_table("roadmap_subtopics")
    op.drop_table("roadmap_topics")
    op.drop_table("user_sessions")
    op.drop_table("student_profiles")
    op.drop_table("users")
