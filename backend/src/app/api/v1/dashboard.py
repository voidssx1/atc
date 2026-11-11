from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db, require_role
from app.models.analytics import AuditLog
from app.models.draft import ContentDraft, DraftStatus
from app.models.progress import Bookmark, ProgressStatus, StudentProgress
from app.models.resource import AvailabilityStatus, Resource
from app.models.roadmap import RoadmapSubtopic, RoadmapTopic
from app.models.user import User, UserRole
from app.schemas.dashboard import AdminDashboardResponse, StudentDashboardResponse
from app.schemas.progress import BookmarkResponse

router = APIRouter(prefix="/dashboard", tags=["Dashboards"])


@router.get("/student", response_model=StudentDashboardResponse)
def get_student_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    total_topics = (
        db.query(RoadmapTopic)
        .filter(RoadmapTopic.is_published == True)  # noqa: E712
        .count()
    )
    total_subtopics = (
        db.query(RoadmapSubtopic)
        .filter(RoadmapSubtopic.is_published == True)  # noqa: E712
        .count()
    )

    completed_subtopics = (
        db.query(StudentProgress)
        .filter(
            StudentProgress.user_id == current_user.id,
            StudentProgress.status == ProgressStatus.COMPLETED,
        )
        .count()
    )

    in_progress_subtopics = (
        db.query(StudentProgress)
        .filter(
            StudentProgress.user_id == current_user.id,
            StudentProgress.status == ProgressStatus.IN_PROGRESS,
        )
        .count()
    )

    completion_percentage = (
        round((completed_subtopics / total_subtopics) * 100, 2)
        if total_subtopics > 0
        else 0.0
    )

    bookmarks_query = (
        db.query(Bookmark)
        .options(joinedload(Bookmark.resource))
        .filter(Bookmark.user_id == current_user.id)
    )
    total_bookmarks = bookmarks_query.count()
    recent_bookmarks = (
        bookmarks_query.order_by(Bookmark.created_at.desc()).limit(5).all()
    )

    return StudentDashboardResponse(
        total_topics=total_topics,
        total_subtopics=total_subtopics,
        completed_subtopics=completed_subtopics,
        in_progress_subtopics=in_progress_subtopics,
        completion_percentage=completion_percentage,
        total_bookmarks=total_bookmarks,
        recent_bookmarks=recent_bookmarks,
    )


@router.get(
    "/admin",
    response_model=AdminDashboardResponse,
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def get_admin_dashboard(db: Session = Depends(get_db)):
    total_students = (
        db.query(User).filter(User.role == UserRole.STUDENT).count()
    )
    total_admins = (
        db.query(User).filter(User.role != UserRole.STUDENT).count()
    )

    total_topics = db.query(RoadmapTopic).count()
    total_subtopics = db.query(RoadmapSubtopic).count()
    total_resources = (
        db.query(Resource).filter(Resource.is_archived == False).count()  # noqa: E712
    )

    broken_resources = (
        db.query(Resource)
        .filter(
            Resource.is_archived == False,  # noqa: E712
            Resource.availability_status == AvailabilityStatus.BROKEN,
        )
        .count()
    )
    degraded_resources = (
        db.query(Resource)
        .filter(
            Resource.is_archived == False,  # noqa: E712
            Resource.availability_status == AvailabilityStatus.DEGRADED,
        )
        .count()
    )

    pending_drafts = (
        db.query(ContentDraft)
        .filter(
            ContentDraft.status == DraftStatus.PENDING_APPROVAL,
            ContentDraft.is_archived == False,  # noqa: E712
        )
        .count()
    )

    recent_logs = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
        .all()
    )
    recent_audit_logs = [
        {
            "id": str(log.id),
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "details": log.details,
            "created_at": log.created_at.isoformat(),
        }
        for log in recent_logs
    ]

    return AdminDashboardResponse(
        total_students=total_students,
        total_admins=total_admins,
        total_topics=total_topics,
        total_subtopics=total_subtopics,
        total_resources=total_resources,
        broken_resources=broken_resources,
        degraded_resources=degraded_resources,
        pending_drafts=pending_drafts,
        recent_audit_logs=recent_audit_logs,
    )
