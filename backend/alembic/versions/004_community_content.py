"""Add event, project and gallery content.

Revision ID: 004_community_content
Revises: 003_clerk_auth_policies
Create Date: 2026-11-13
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "004_community_content"
down_revision: Union[str, Sequence[str], None] = "003_clerk_auth_policies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "community_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("slug", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        sa.Column("external_url", sa.String(length=2048), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_community_items_kind", "community_items", ["kind"])
    op.create_index("ix_community_items_slug", "community_items", ["slug"], unique=True)
    op.create_index("ix_community_items_is_published", "community_items", ["is_published"])
    op.create_index("ix_community_items_is_archived", "community_items", ["is_archived"])


def downgrade() -> None:
    op.drop_index("ix_community_items_is_archived", table_name="community_items")
    op.drop_index("ix_community_items_is_published", table_name="community_items")
    op.drop_index("ix_community_items_slug", table_name="community_items")
    op.drop_index("ix_community_items_kind", table_name="community_items")
    op.drop_table("community_items")
