import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, HttpUrl, Field

from app.models.resource import ResourceType, AvailabilityStatus


class ResourceBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    url: str
    resource_type: ResourceType = ResourceType.ARTICLE
    difficulty: str = "medium"
    subtopic_id: Optional[uuid.UUID] = None
    order_index: int = 0


class ResourceCreate(ResourceBase):
    pass


class ResourceUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    url: Optional[str] = None
    resource_type: Optional[ResourceType] = None
    difficulty: Optional[str] = None
    subtopic_id: Optional[uuid.UUID] = None
    order_index: Optional[int] = None
    availability_status: Optional[AvailabilityStatus] = None
    is_archived: Optional[bool] = None


class ResourceResponse(ResourceBase):
    id: uuid.UUID
    availability_status: AvailabilityStatus
    last_checked_at: Optional[datetime] = None
    status_code: Optional[int] = None
    is_archived: bool
    created_by_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ResourceCheckResult(BaseModel):
    resource_id: uuid.UUID
    url: str
    availability_status: AvailabilityStatus
    status_code: Optional[int] = None
    checked_at: datetime
