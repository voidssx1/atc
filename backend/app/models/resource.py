import enum
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ResourceType(str, enum.Enum):
    ARTICLE = "article"
    VIDEO = "video"
    PROBLEM = "problem"
    REPOSITORY = "repository"
    BOOK = "book"
    COURSE = "course"


class AvailabilityStatus(str, enum.Enum):
    AVAILABLE = "available"
    BROKEN = "broken"
    DEGRADED = "degraded"
    UNCHECKED = "unchecked"


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    subtopic_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roadmap_subtopics.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[ResourceType] = mapped_column(
        Enum(ResourceType, native_enum=False, length=50),
        default=ResourceType.ARTICLE,
        nullable=False,
        index=True,
    )
    difficulty: Mapped[str] = mapped_column(
        String(50), default="medium", nullable=False
    )
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        Enum(AvailabilityStatus, native_enum=False, length=50),
        default=AvailabilityStatus.UNCHECKED,
        nullable=False,
        index=True,
    )
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_archived: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, index=True
    )
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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
    subtopic: Mapped[Optional["RoadmapSubtopic"]] = relationship(
        "RoadmapSubtopic", back_populates="resources"
    )
    bookmarks: Mapped[List["Bookmark"]] = relationship(
        "Bookmark",
        back_populates="resource",
        cascade="all, delete-orphan",
    )
    analytics: Mapped[List["ResourceAnalytics"]] = relationship(
        "ResourceAnalytics",
        back_populates="resource",
        cascade="all, delete-orphan",
    )
