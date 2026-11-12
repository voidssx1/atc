import uuid
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.community import CommunityKind


class CommunityItemCreate(BaseModel):
    kind: CommunityKind
    title: str = Field(min_length=1, max_length=180)
    slug: str = Field(min_length=1, max_length=180, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: Optional[str] = None
    image_url: Optional[HttpUrl] = None
    external_url: Optional[HttpUrl] = None
    location: Optional[str] = Field(default=None, max_length=255)
    starts_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_published: bool = False
    order_index: int = 0


class CommunityItemUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=180)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=180, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: Optional[str] = None
    image_url: Optional[HttpUrl] = None
    external_url: Optional[HttpUrl] = None
    location: Optional[str] = Field(default=None, max_length=255)
    starts_at: Optional[datetime] = None
    metadata: Optional[dict[str, Any]] = None
    is_published: Optional[bool] = None
    order_index: Optional[int] = None


class CommunityItemResponse(BaseModel):
    id: uuid.UUID
    kind: CommunityKind
    title: str
    slug: str
    description: Optional[str]
    image_url: Optional[str]
    external_url: Optional[str]
    location: Optional[str]
    starts_at: Optional[datetime]
    metadata: dict[str, Any]
    is_published: bool
    order_index: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
