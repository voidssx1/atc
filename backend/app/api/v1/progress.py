import uuid
from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.progress import ProgressStatus, StudentProgress
from app.models.roadmap import RoadmapSubtopic
from app.models.user import User
from app.schemas.progress import ProgressResponse, ProgressUpdateRequest

router = APIRouter(prefix="/progress", tags=["Student Progress"])


@router.get("/", response_model=List[ProgressResponse])
def get_user_progress(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(StudentProgress)
        .filter(StudentProgress.user_id == current_user.id)
        .all()
    )


@router.get("/{subtopic_id}", response_model=ProgressResponse)
def get_subtopic_progress(
    subtopic_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prog = (
        db.query(StudentProgress)
        .filter(
            StudentProgress.user_id == current_user.id,
            StudentProgress.subtopic_id == subtopic_id,
        )
        .first()
    )
    if not prog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Progress not recorded for this subtopic",
        )
    return prog


@router.post("/{subtopic_id}", response_model=ProgressResponse)
def update_subtopic_progress(
    subtopic_id: uuid.UUID,
    payload: ProgressUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = (
        db.query(RoadmapSubtopic)
        .filter(RoadmapSubtopic.id == subtopic_id)
        .first()
    )
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subtopic not found"
        )

    prog = (
        db.query(StudentProgress)
        .filter(
            StudentProgress.user_id == current_user.id,
            StudentProgress.subtopic_id == subtopic_id,
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    completed_time = now if payload.status == ProgressStatus.COMPLETED else None

    if not prog:
        prog = StudentProgress(
            user_id=current_user.id,
            subtopic_id=subtopic_id,
            status=payload.status,
            notes=payload.notes,
            completed_at=completed_time,
        )
        db.add(prog)
    else:
        prog.status = payload.status
        prog.notes = payload.notes
        prog.completed_at = completed_time

    db.commit()
    db.refresh(prog)
    return prog


@router.delete("/{subtopic_id}")
def reset_subtopic_progress(
    subtopic_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prog = (
        db.query(StudentProgress)
        .filter(
            StudentProgress.user_id == current_user.id,
            StudentProgress.subtopic_id == subtopic_id,
        )
        .first()
    )
    if prog:
        db.delete(prog)
        db.commit()
    return {"message": "Progress reset successfully", "subtopic_id": str(subtopic_id)}
