import enum
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session


class ClerkOutboxStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"

from app.api.deps import get_db, require_role
from app.models.clerk import ClerkOutbox
from app.models.user import UserRole
from app.schemas.user import ClerkOutboxItemResponse, ClerkOutboxListResponse
from app.services.clerk import ClerkOutboxService
from app.services.email import email_service
from app.services.scheduler import job_runner

router = APIRouter(prefix="/admin/jobs", tags=["Scheduled Jobs & Email Integration"])


class TestEmailRequest(BaseModel):
    to_email: EmailStr
    subject: str = "ATC DSA Hub Test Email"
    body: str = "This is a test notification from the ATC DSA Hub."


@router.post(
    "/run-availability-scan",
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
async def trigger_availability_scan(
    limit: int = Query(default=50, ge=1, le=50), db: Session = Depends(get_db)
):
    results = await job_runner.run_resource_availability_scan(db, limit=limit)
    return {"message": "Resource availability scan executed", "results": results}


@router.post(
    "/cleanup-expired-sessions",
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def trigger_cleanup_sessions(db: Session = Depends(get_db)):
    count = job_runner.cleanup_expired_sessions(db)
    return {"message": "Expired sessions cleaned up", "deleted_count": count}


@router.post(
    "/process-clerk-outbox",
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
async def trigger_process_clerk_outbox(
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    counts = await ClerkOutboxService.process_pending(db, limit=limit)
    return {"message": "Clerk outbox processed", "results": counts}


@router.get(
    "/clerk-outbox",
    response_model=ClerkOutboxListResponse,
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def list_clerk_outbox(
    status_filter: Optional[ClerkOutboxStatus] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    pending_count = db.query(ClerkOutbox).filter(ClerkOutbox.status == "pending").count()
    dead_letter_count = (
        db.query(ClerkOutbox).filter(ClerkOutbox.status == "dead_letter").count()
    )
    completed_count = (
        db.query(ClerkOutbox).filter(ClerkOutbox.status == "completed").count()
    )

    query = db.query(ClerkOutbox)
    if status_filter is not None:
        query = query.filter(ClerkOutbox.status == status_filter.value)
    items = query.order_by(ClerkOutbox.updated_at.desc()).limit(limit).all()

    return ClerkOutboxListResponse(
        pending_count=pending_count,
        dead_letter_count=dead_letter_count,
        completed_count=completed_count,
        items=items,
    )


@router.post(
    "/clerk-outbox/{outbox_id}/retry",
    response_model=ClerkOutboxItemResponse,
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
async def retry_clerk_outbox_item(
    outbox_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    item = db.query(ClerkOutbox).filter(ClerkOutbox.id == outbox_id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Outbox item not found"
        )
    if item.status == "completed":
        return item

    # Reset dead-letter or pending item for immediate retry
    item.status = "pending"
    item.attempts = 0
    item.next_retry_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)

    await ClerkOutboxService.dispatch_item(db, item)
    db.refresh(item)
    return item


@router.post(
    "/test-email",
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
async def test_email_dispatch(payload: TestEmailRequest):
    success = await email_service.send_email(
        to_email=payload.to_email,
        subject=payload.subject,
        html_content=f"<p>{payload.body}</p>",
        text_content=payload.body,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to dispatch test email",
        )
    return {"message": "Test email processed successfully", "to": payload.to_email}
