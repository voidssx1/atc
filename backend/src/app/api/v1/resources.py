import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import (
    ensure_admin_for_flag,
    get_db,
    get_optional_current_user,
    require_role,
)
from app.models.resource import AvailabilityStatus, Resource, ResourceType
from app.models.roadmap import RoadmapSubtopic
from app.models.user import User, UserRole
from app.schemas.resource import (
    ResourceCheckResult,
    ResourceCreate,
    ResourceResponse,
    ResourceUpdate,
)
from app.services.audit import log_audit_event
from app.services.url_checker import check_url_availability, validate_url_format

router = APIRouter(prefix="/resources", tags=["Resources"])


@router.get("/", response_model=List[ResourceResponse])
def list_resources(
    subtopic_id: Optional[uuid.UUID] = None,
    resource_type: Optional[ResourceType] = None,
    difficulty: Optional[str] = None,
    availability_status: Optional[AvailabilityStatus] = None,
    include_archived: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin_for_flag(
        include_archived,
        current_user,
        "Admin privileges required to view archived resources",
    )

    query = db.query(Resource)
    if not include_archived:
        query = query.filter(Resource.is_archived == False)  # noqa: E712
    if subtopic_id:
        query = query.filter(Resource.subtopic_id == subtopic_id)
    if resource_type:
        query = query.filter(Resource.resource_type == resource_type)
    if difficulty:
        query = query.filter(Resource.difficulty == difficulty)
    if availability_status:
        query = query.filter(Resource.availability_status == availability_status)

    return query.order_by(Resource.order_index.asc()).offset(skip).limit(limit).all()


@router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource(
    resource_id: uuid.UUID,
    include_archived: bool = False,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin_for_flag(
        include_archived,
        current_user,
        "Admin privileges required to view archived resources",
    )

    res = db.query(Resource).filter(Resource.id == resource_id).first()
    if not res or (not include_archived and res.is_archived):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    return res


@router.post(
    "/",
    response_model=ResourceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
async def create_resource(
    payload: ResourceCreate,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    url_clean = str(payload.url).strip()
    if not validate_url_format(url_clean):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL format. Must begin with http:// or https:// and have a valid domain.",
        )

    if payload.subtopic_id:
        sub = (
            db.query(RoadmapSubtopic)
            .filter(RoadmapSubtopic.id == payload.subtopic_id)
            .first()
        )
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Subtopic not found"
            )

    # Probe live URL status
    avail_status, code = await check_url_availability(url_clean)

    res = Resource(
        title=payload.title,
        url=url_clean,
        resource_type=payload.resource_type,
        difficulty=payload.difficulty,
        subtopic_id=payload.subtopic_id,
        order_index=payload.order_index,
        availability_status=avail_status,
        status_code=code,
        last_checked_at=datetime.now(timezone.utc),
        created_by_id=current_admin.id,
    )
    db.add(res)
    db.commit()
    db.refresh(res)

    log_audit_event(
        db=db,
        action="resource_created",
        user_id=current_admin.id,
        target_type="resource",
        target_id=str(res.id),
        details={"title": res.title, "url": res.url},
    )

    return res


@router.put(
    "/{resource_id}",
    response_model=ResourceResponse,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
async def update_resource(
    resource_id: uuid.UUID,
    payload: ResourceUpdate,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    res = db.query(Resource).filter(Resource.id == resource_id).first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )

    update_data = payload.model_dump(exclude_unset=True)
    # Active (non-archived) published resources cannot be silently rewritten outside
    # the draft approval workflow; soft-archiving via is_archived=True remains allowed.
    if not res.is_archived and not (
        set(update_data.keys()) == {"is_archived"} and update_data.get("is_archived") is True
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active published resources are immutable; create an approved content draft revision to modify.",
        )

    if "url" in update_data and update_data["url"] != res.url:
        if not validate_url_format(update_data["url"]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid URL format",
            )
        status_code, code = await check_url_availability(update_data["url"])
        res.availability_status = status_code
        res.status_code = code
        res.last_checked_at = datetime.now(timezone.utc)

    for field, val in update_data.items():
        setattr(res, field, val)

    db.commit()
    db.refresh(res)

    return res


@router.delete(
    "/{resource_id}",
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def delete_or_archive_resource(
    resource_id: uuid.UUID,
    hard_delete: bool = False,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    res = db.query(Resource).filter(Resource.id == resource_id).first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )

    if hard_delete:
        if current_admin.role != UserRole.TECH_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only tech_admin can permanently delete archived resources",
            )
        if not res.is_archived:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Archive a published resource before permanent deletion",
            )
        db.delete(res)
        action = "resource_deleted"
    else:
        res.is_archived = True
        action = "resource_archived"

    db.commit()

    log_audit_event(
        db=db,
        action=action,
        user_id=current_admin.id,
        target_type="resource",
        target_id=str(resource_id),
    )

    return {"message": f"Resource {action} successfully", "id": str(resource_id)}


@router.post(
    "/{resource_id}/check-availability",
    response_model=ResourceCheckResult,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
async def check_single_resource_availability(
    resource_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    res = db.query(Resource).filter(Resource.id == resource_id).first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )

    status_code, code = await check_url_availability(res.url)
    now = datetime.now(timezone.utc)
    res.availability_status = status_code
    res.status_code = code
    res.last_checked_at = now
    db.commit()

    return ResourceCheckResult(
        resource_id=res.id,
        url=res.url,
        availability_status=res.availability_status,
        status_code=res.status_code,
        checked_at=now,
    )


@router.post(
    "/check-all-availability",
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
async def check_all_resources_availability(
    limit: int = Query(default=50, ge=1, le=50), db: Session = Depends(get_db)
):
    from app.services.scheduler import job_runner

    results = await job_runner.run_resource_availability_scan(db, limit=limit)
    return {"message": "Availability scan completed", "summary": results}
