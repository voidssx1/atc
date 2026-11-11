import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


# Topic Schemas
class RoadmapTopicBase(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    slug: str = Field(min_length=1, max_length=150)
    description: Optional[str] = None
    order_index: int = 0
    is_published: bool = True


class RoadmapTopicCreate(RoadmapTopicBase):
    pass


class RoadmapTopicUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=150)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description: Optional[str] = None
    order_index: Optional[int] = None
    is_published: Optional[bool] = None


# Subtopic Schemas
class RoadmapSubtopicBase(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    slug: str = Field(min_length=1, max_length=150)
    description: Optional[str] = None
    order_index: int = 0
    difficulty: str = "medium"
    is_published: bool = True
    parent_subtopic_id: Optional[uuid.UUID] = None


class RoadmapSubtopicCreate(RoadmapSubtopicBase):
    topic_id: uuid.UUID


class RoadmapSubtopicUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=150)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description: Optional[str] = None
    order_index: Optional[int] = None
    difficulty: Optional[str] = None
    is_published: Optional[bool] = None
    parent_subtopic_id: Optional[uuid.UUID] = None


class RoadmapSubtopicResponse(RoadmapSubtopicBase):
    id: uuid.UUID
    topic_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    children: List["RoadmapSubtopicResponse"] = []
    model_config = ConfigDict(from_attributes=True)


class RoadmapTopicDetailResponse(RoadmapTopicBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    subtopics: List[RoadmapSubtopicResponse] = []
    model_config = ConfigDict(from_attributes=True)


class RoadmapTopicSummaryResponse(RoadmapTopicBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    subtopics_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class ReorderItem(BaseModel):
    id: uuid.UUID
    order_index: int


class BulkReorderRequest(BaseModel):
    entity_type: str  # "topic" or "subtopic"
    items: List[ReorderItem]
