import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RoadmapTopic(Base):
    __tablename__ = "roadmap_topics"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(150), unique=True, index=True, nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, index=True
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    subtopics: Mapped[List["RoadmapSubtopic"]] = relationship(
        "RoadmapSubtopic",
        back_populates="topic",
        cascade="all, delete-orphan",
        order_by="RoadmapSubtopic.order_index",
    )


class RoadmapSubtopic(Base):
    __tablename__ = "roadmap_subtopics"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roadmap_topics.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    parent_subtopic_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roadmap_subtopics.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, index=True
    )
    difficulty: Mapped[str] = mapped_column(
        String(50), default="medium", nullable=False
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    topic: Mapped["RoadmapTopic"] = relationship(
        "RoadmapTopic", back_populates="subtopics"
    )
    parent_subtopic: Mapped[Optional["RoadmapSubtopic"]] = relationship(
        "RoadmapSubtopic",
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[List["RoadmapSubtopic"]] = relationship(
        "RoadmapSubtopic",
        back_populates="parent_subtopic",
        cascade="all, delete-orphan",
        order_by="RoadmapSubtopic.order_index",
    )
    resources: Mapped[List["Resource"]] = relationship(
        "Resource",
        back_populates="subtopic",
        cascade="all, delete-orphan",
        order_by="Resource.order_index",
    )
    progress_records: Mapped[List["StudentProgress"]] = relationship(
        "StudentProgress",
        back_populates="subtopic",
        cascade="all, delete-orphan",
    )
