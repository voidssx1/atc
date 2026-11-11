import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import ADMIN_ROLES, get_current_user, get_db
from app.models.user import StudentProfile, User
from app.schemas.user import StudentProfileResponse, StudentProfileUpdate

router = APIRouter(prefix="/profile", tags=["Student Profiles"])


@router.get("/me", response_model=StudentProfileResponse)
def get_my_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = (
        db.query(StudentProfile)
        .filter(StudentProfile.user_id == current_user.id)
        .first()
    )
    if not profile:
        profile = StudentProfile(
            user_id=current_user.id, full_name=current_user.username
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

    return StudentProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        email=current_user.email,
        full_name=profile.full_name,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.put("/me", response_model=StudentProfileResponse)
def update_my_profile(
    payload: StudentProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = (
        db.query(StudentProfile)
        .filter(StudentProfile.user_id == current_user.id)
        .first()
    )
    if not profile:
        profile = StudentProfile(user_id=current_user.id)
        db.add(profile)

    if payload.full_name is not None:
        profile.full_name = payload.full_name

    db.commit()
    db.refresh(profile)

    return StudentProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        email=current_user.email,
        full_name=profile.full_name,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.get("/{user_id}", response_model=StudentProfileResponse)
def get_user_profile(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.id != user_id and current_user.role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view another user's profile",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    profile = (
        db.query(StudentProfile)
        .filter(StudentProfile.user_id == user_id)
        .first()
    )
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student profile not found",
        )

    return StudentProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        email=user.email,
        full_name=profile.full_name,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
