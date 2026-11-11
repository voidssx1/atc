import uuid
from datetime import datetime
from typing import Optional, Any, Dict
from pydantic import BaseModel, ConfigDict, Field

from app.models.draft import DraftStatus, EntityType


class ContentDraftCreate(BaseModel):
    entity_type: EntityType
    entity_id: Optional[uuid.UUID] = None
    title: str = Field(min_length=1, max_length=255)
    payload: Dict[str, Any]


class ContentDraftUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    payload: Optional[Dict[str, Any]] = None


class ContentDraftReview(BaseModel):
    feedback: Optional[str] = None


class ContentDraftResponse(BaseModel):
    id: uuid.UUID
    entity_type: EntityType
    entity_id: Optional[uuid.UUID] = None
    title: str
    payload: Dict[str, Any]
    status: DraftStatus
    version_number: int
    submitted_by_id: uuid.UUID
    reviewed_by_id: Optional[uuid.UUID] = None
    review_feedback: Optional[str] = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
