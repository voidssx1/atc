import uuid
from datetime import datetime
from typing import Optional, Any, Dict
from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TrackClickRequest(BaseModel):
    resource_id: uuid.UUID


AnalyticsEventCreate = TrackClickRequest


class ResourceAnalyticsSummary(BaseModel):
    resource_id: uuid.UUID
    title: str
    url: str
    total_clicks: int
    unique_students: int
    model_config = ConfigDict(from_attributes=True)
