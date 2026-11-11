import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.models.progress import Bookmark
from app.models.resource import Resource
from app.models.user import User
from app.schemas.progress import BookmarkCreateRequest, BookmarkResponse

router = APIRouter(prefix="/bookmarks", tags=["Bookmarks"])


@router.get("/", response_model=List[BookmarkResponse])
def get_user_bookmarks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bookmarks = (
        db.query(Bookmark)
        .options(joinedload(Bookmark.resource))
        .filter(Bookmark.user_id == current_user.id)
        .order_by(Bookmark.created_at.desc())
        .all()
    )
    return bookmarks


@router.post(
    "/",
    response_model=BookmarkResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_bookmark(
    payload: BookmarkCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resource = (
        db.query(Resource)
        .filter(Resource.id == payload.resource_id)
        .first()
    )
    if not resource or resource.is_archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )

    bookmark = (
        db.query(Bookmark)
        .filter(
            Bookmark.user_id == current_user.id,
            Bookmark.resource_id == payload.resource_id,
        )
        .first()
    )
    if bookmark:
        bookmark.notes = payload.notes
    else:
        bookmark = Bookmark(
            user_id=current_user.id,
            resource_id=payload.resource_id,
            notes=payload.notes,
        )
        db.add(bookmark)

    db.commit()
    db.refresh(bookmark)
    return bookmark


@router.delete("/{resource_id}")
def delete_bookmark(
    resource_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bookmark = (
        db.query(Bookmark)
        .filter(
            Bookmark.user_id == current_user.id,
            Bookmark.resource_id == resource_id,
        )
        .first()
    )
    if not bookmark:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bookmark not found"
        )

    db.delete(bookmark)
    db.commit()
    return {"message": "Bookmark removed", "resource_id": str(resource_id)}
