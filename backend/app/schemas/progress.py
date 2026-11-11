import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict

from app.models.progress import ProgressStatus
from app.schemas.resource import ResourceResponse


class ProgressUpdateRequest(BaseModel):
    status: ProgressStatus = ProgressStatus.COMPLETED
    notes: Optional[str] = None


class ProgressResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    subtopic_id: uuid.UUID
    status: ProgressStatus
    notes: Optional[str] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BookmarkCreateRequest(BaseModel):
    resource_id: uuid.UUID
    notes: Optional[str] = None


class BookmarkResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    resource_id: uuid.UUID
    notes: Optional[str] = None
    created_at: datetime
    resource: Optional[ResourceResponse] = None
    model_config = ConfigDict(from_attributes=True)
