from typing import List, Dict, Any
from pydantic import BaseModel

from app.schemas.progress import BookmarkResponse


class StudentDashboardResponse(BaseModel):
    total_topics: int
    total_subtopics: int
    completed_subtopics: int
    in_progress_subtopics: int
    completion_percentage: float
    total_bookmarks: int
    recent_bookmarks: List[BookmarkResponse]


class AdminDashboardResponse(BaseModel):
    total_students: int
    total_admins: int
    total_topics: int
    total_subtopics: int
    total_resources: int
    broken_resources: int
    degraded_resources: int
    pending_drafts: int
    recent_audit_logs: List[Dict[str, Any]]
