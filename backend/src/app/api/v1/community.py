import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_current_user, require_role
from app.models.community import CommunityItem, CommunityKind
from app.models.user import User, UserRole
from app.schemas.community import CommunityItemCreate, CommunityItemResponse, CommunityItemUpdate
from app.services.audit import log_audit_event

router = APIRouter(prefix="/community", tags=["Events, Projects & Gallery"])


@router.get("/{kind}", response_model=List[CommunityItemResponse])
def list_community_items(
    kind: CommunityKind,
    include_unpublished: bool = False,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
):
    if include_unpublished and (current_user is None or current_user.role not in (UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    query = db.query(CommunityItem).filter(CommunityItem.kind == kind, CommunityItem.is_archived == False)
    if not include_unpublished:
        query = query.filter(CommunityItem.is_published == True)
    return query.order_by(CommunityItem.order_index.asc(), CommunityItem.created_at.desc()).all()


@router.post("/", response_model=CommunityItemResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))])
def create_community_item(
    payload: CommunityItemCreate,
    current_user: User = Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])),
    db: Session = Depends(get_db),
):
    if db.query(CommunityItem).filter(CommunityItem.slug == payload.slug).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already exists")
    item = CommunityItem(
        kind=payload.kind, title=payload.title, slug=payload.slug,
        description=payload.description,
        image_url=str(payload.image_url) if payload.image_url else None,
        external_url=str(payload.external_url) if payload.external_url else None,
        location=payload.location, starts_at=payload.starts_at,
        metadata_json=payload.metadata, is_published=payload.is_published,
        order_index=payload.order_index, created_by_id=current_user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    log_audit_event(db=db, action="community_item_created", user_id=current_user.id, target_type=payload.kind.value, target_id=str(item.id), details={"slug": item.slug})
    return _serialize(item)


@router.patch("/{item_id}", response_model=CommunityItemResponse, dependencies=[Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))])
def update_community_item(
    item_id: uuid.UUID,
    payload: CommunityItemUpdate,
    current_user: User = Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])),
    db: Session = Depends(get_db),
):
    item = db.query(CommunityItem).filter(CommunityItem.id == item_id, CommunityItem.is_archived == False).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community item not found")
    values = payload.model_dump(exclude_unset=True)
    if "slug" in values and values["slug"] != item.slug and db.query(CommunityItem).filter(CommunityItem.slug == values["slug"]).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already exists")
    for key, value in values.items():
        if key in ("image_url", "external_url"):
            value = str(value) if value else None
        if key == "metadata":
            item.metadata_json = value or {}
        else:
            setattr(item, key, value)
    db.commit()
    db.refresh(item)
    log_audit_event(db=db, action="community_item_updated", user_id=current_user.id, target_type=item.kind.value, target_id=str(item.id), details={"fields": list(values)})
    return _serialize(item)


@router.delete("/{item_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))])
def archive_community_item(
    item_id: uuid.UUID,
    current_user: User = Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])),
    db: Session = Depends(get_db),
):
    item = db.query(CommunityItem).filter(CommunityItem.id == item_id, CommunityItem.is_archived == False).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community item not found")
    item.is_archived = True
    item.is_published = False
    db.commit()
    log_audit_event(db=db, action="community_item_archived", user_id=current_user.id, target_type=item.kind.value, target_id=str(item.id))
    return {"message": "Community item archived"}


def _serialize(item: CommunityItem) -> dict:
    return {
        "id": item.id, "kind": item.kind, "title": item.title, "slug": item.slug,
        "description": item.description, "image_url": item.image_url,
        "external_url": item.external_url, "location": item.location,
        "starts_at": item.starts_at, "metadata": item.metadata_json or {},
        "is_published": item.is_published, "order_index": item.order_index,
        "created_at": item.created_at, "updated_at": item.updated_at,
    }
