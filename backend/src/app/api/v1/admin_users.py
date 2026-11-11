import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.api.v1.auth import _hard_revoke_sessions
from app.core.security import hash_password
from app.models.clerk import ClerkOutbox
from app.models.user import StudentProfile, User, UserRole
from app.schemas.user import (
    AdminCreateUserRequest,
    AdminUpdateRoleRequest,
    AdminUpdateStatusRequest,
    UserResponse,
)
from app.services.audit import log_audit_event
from app.services.clerk import ClerkOutboxService

router = APIRouter(prefix="/admin/users", tags=["Admin User Management"])


@router.get(
    "/",
    response_model=List[UserResponse],
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def list_users(
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.filter(
            (User.email.ilike(search_pattern))
            | (User.username.ilike(search_pattern))
        )
    return query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
async def create_admin_or_user(
    payload: AdminCreateUserRequest,
    current_admin: User = Depends(require_role([UserRole.TECH_ADMIN])),
    db: Session = Depends(get_db),
):
    email_clean = payload.email.lower().strip()
    if db.query(User).filter(User.email == email_clean).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists",
        )

    username_clean = (
        payload.username.strip()
        if payload.username and payload.username.strip()
        else email_clean.split("@")[0]
    )
    if db.query(User).filter(User.username == username_clean).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already taken",
        )

    hashed_pw = await hash_password(payload.password) if payload.password else None
    user = User(
        email=email_clean,
        username=username_clean,
        hashed_password=hashed_pw,
        role=payload.role,
        is_active=True,
        is_verified=True,  # Admins created directly are verified
    )
    db.add(user)
    try:
        db.flush()
        profile = StudentProfile(user_id=user.id, full_name=user.username)
        db.add(profile)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email or username already exists",
        ) from exc
    db.refresh(user)

    log_audit_event(
        db=db,
        action="admin_created_user",
        user_id=current_admin.id,
        target_type="user",
        target_id=str(user.id),
        details={"email": user.email, "role": user.role.value},
    )

    return user


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def get_user_detail(user_id: uuid.UUID, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return user


@router.patch(
    "/{user_id}/role",
    response_model=UserResponse,
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def update_user_role(
    user_id: uuid.UUID,
    payload: AdminUpdateRoleRequest,
    current_admin: User = Depends(require_role([UserRole.TECH_ADMIN])),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if user.id == current_admin.id and payload.role != UserRole.TECH_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own tech_admin role",
        )

    old_role = user.role.value
    role_changed = payload.role != user.role
    user.role = payload.role
    revoked_count = 0
    if role_changed:
        revoked_count = _hard_revoke_sessions(db, user_id=user.id)
    db.commit()
    db.refresh(user)

    log_audit_event(
        db=db,
        action="admin_updated_role",
        user_id=current_admin.id,
        target_type="user",
        target_id=str(user.id),
        details={
            "old_role": old_role,
            "new_role": user.role.value,
            "sessions_revoked": revoked_count,
        },
    )

    return user


@router.patch(
    "/{user_id}/status",
    response_model=UserResponse,
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def update_user_status(
    user_id: uuid.UUID,
    payload: AdminUpdateStatusRequest,
    current_admin: User = Depends(require_role([UserRole.TECH_ADMIN])),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if user.id == current_admin.id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own admin account",
        )

    user.is_active = payload.is_active
    if not payload.is_active:
        _hard_revoke_sessions(db, user_id=user.id)
    db.commit()
    db.refresh(user)

    log_audit_event(
        db=db,
        action="admin_updated_user_status",
        user_id=current_admin.id,
        target_type="user",
        target_id=str(user.id),
        details={"is_active": user.is_active},
    )

    return user


@router.delete(
    "/{user_id}",
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
async def delete_user(
    user_id: uuid.UUID,
    current_admin: User = Depends(require_role([UserRole.TECH_ADMIN])),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own admin account",
        )

    clerk_user_id = user.clerk_user_id
    outbox_item = None
    if clerk_user_id:
        outbox_item = ClerkOutbox(
            clerk_user_id=clerk_user_id,
            audit_target_id=str(user_id),
            operation="delete_user",
            status="pending",
            attempts=0,
            max_attempts=5,
            next_retry_at=datetime.now(timezone.utc),
        )
        db.add(outbox_item)

    db.delete(user)
    db.commit()

    if outbox_item is not None:
        db.refresh(outbox_item)
        await ClerkOutboxService.dispatch_item(db, outbox_item)

    log_audit_event(
        db=db,
        action="admin_deleted_user",
        user_id=current_admin.id,
        target_type="user",
        target_id=str(user_id),
    )

    return {"message": "User deleted successfully", "user_id": str(user_id)}
