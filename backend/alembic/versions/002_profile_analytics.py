"""Align student profiles and resource analytics with PRD

Revision ID: 002_profile_analytics
Revises: 001_initial_schema
Create Date: 2026-11-10 08:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002_profile_analytics"
down_revision: Union[str, Sequence[str], None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. student_profiles: Remove unsupported fields, preserve full_name (2-100 chars)
    op.drop_column("student_profiles", "academic_year")
    op.drop_column("student_profiles", "college")
    op.drop_column("student_profiles", "github_url")
    op.drop_column("student_profiles", "linkedin_url")
    op.drop_column("student_profiles", "leetcode_url")
    op.drop_column("student_profiles", "codeforces_url")
    op.drop_column("student_profiles", "bio")
    op.alter_column(
        "student_profiles",
        "full_name",
        type_=sa.String(length=100),
        existing_type=sa.String(length=150),
        existing_nullable=True,
    )

    # 2. resource_analytics: Drop generic event_type in favor of dedicated click tracking
    op.drop_index("ix_resource_analytics_event_type", table_name="resource_analytics")
    op.drop_column("resource_analytics", "event_type")


def downgrade() -> None:
    op.add_column(
        "resource_analytics",
        sa.Column("event_type", sa.String(length=50), nullable=False, server_default="click"),
    )
    op.create_index(
        "ix_resource_analytics_event_type", "resource_analytics", ["event_type"], unique=False
    )

    op.alter_column(
        "student_profiles",
        "full_name",
        type_=sa.String(length=150),
        existing_type=sa.String(length=100),
        existing_nullable=True,
    )
    op.add_column("student_profiles", sa.Column("bio", sa.Text(), nullable=True))
    op.add_column("student_profiles", sa.Column("codeforces_url", sa.String(length=255), nullable=True))
    op.add_column("student_profiles", sa.Column("leetcode_url", sa.String(length=255), nullable=True))
    op.add_column("student_profiles", sa.Column("linkedin_url", sa.String(length=255), nullable=True))
    op.add_column("student_profiles", sa.Column("github_url", sa.String(length=255), nullable=True))
    op.add_column("student_profiles", sa.Column("college", sa.String(length=200), nullable=True))
    op.add_column("student_profiles", sa.Column("academic_year", sa.String(length=50), nullable=True))
