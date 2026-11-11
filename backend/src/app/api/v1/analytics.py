import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_current_user, require_role
from app.models.analytics import AuditLog, ResourceAnalytics
from app.models.resource import Resource
from app.models.user import User, UserRole
from app.schemas.analytics import (
    AuditLogResponse,
    ResourceAnalyticsSummary,
    TrackClickRequest,
)

router = APIRouter(tags=["Analytics & Audit Logs"])


@router.post("/analytics/click", status_code=201)
@router.post("/analytics/event", status_code=201)
def track_resource_click(
    payload: TrackClickRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    resource = db.query(Resource).filter(Resource.id == payload.resource_id).first()
    if not resource or resource.is_archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",
        )

    event = ResourceAnalytics(
        resource_id=payload.resource_id,
        user_id=current_user.id if current_user else None,
    )
    db.add(event)
    db.commit()
    return {"message": "Click recorded"}


@router.get(
    "/analytics/resources",
    response_model=List[ResourceAnalyticsSummary],
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def get_resource_analytics_summary(
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if start_date is not None and start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date is not None and end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    if start_date is not None and end_date is not None:
        if start_date > end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_date must be before or equal to end_date",
            )
        if (end_date - start_date) > timedelta(days=365):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Date range cannot exceed 365 days",
            )

    join_conditions = [ResourceAnalytics.resource_id == Resource.id]
    if start_date is not None:
        join_conditions.append(ResourceAnalytics.created_at >= start_date)
    if end_date is not None:
        join_conditions.append(ResourceAnalytics.created_at <= end_date)

    rows = (
        db.query(
            Resource.id,
            Resource.title,
            Resource.url,
            func.count(ResourceAnalytics.id).label("total_clicks"),
            func.count(func.distinct(ResourceAnalytics.user_id)).label(
                "unique_students"
            ),
        )
        .outerjoin(
            ResourceAnalytics, and_(*join_conditions)
        )
        .filter(Resource.is_archived == False)  # noqa: E712
        .group_by(
            Resource.id,
            Resource.title,
            Resource.url,
            Resource.order_index,
        )
        .order_by(Resource.order_index.asc())
        .limit(limit)
        .all()
    )
    return [
        ResourceAnalyticsSummary(
            resource_id=row.id,
            title=row.title,
            url=row.url,
            total_clicks=row.total_clicks or 0,
            unique_students=row.unique_students or 0,
        )
        for row in rows
    ]


@router.get(
    "/admin/audit-logs",
    response_model=List[AuditLogResponse],
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def list_audit_logs(
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if target_type:
        query = query.filter(AuditLog.target_type == target_type)

    return query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
