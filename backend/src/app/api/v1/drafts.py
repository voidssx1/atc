import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_role
from app.models.draft import ContentDraft, DraftStatus, EntityType
from app.models.resource import AvailabilityStatus, Resource, ResourceType
from app.models.roadmap import RoadmapSubtopic, RoadmapTopic
from app.models.user import User, UserRole
from app.schemas.draft import (
    ContentDraftCreate,
    ContentDraftResponse,
    ContentDraftReview,
    ContentDraftUpdate,
)
from app.services.audit import log_audit_event
from app.services.data_transfer import canonicalize_resource_url
from app.services.url_checker import validate_url_format

_ALLOWED_TOPIC_PUBLISH_FIELDS = (
    "title",
    "slug",
    "description",
    "order_index",
    "is_published",
)
_ALLOWED_SUBTOPIC_PUBLISH_FIELDS = (
    "topic_id",
    "parent_subtopic_id",
    "title",
    "slug",
    "description",
    "order_index",
    "difficulty",
    "is_published",
)
_ALLOWED_RESOURCE_PUBLISH_FIELDS = (
    "title",
    "url",
    "resource_type",
    "difficulty",
    "subtopic_id",
    "order_index",
    "is_archived",
)


def _parse_optional_uuid(value: object, field_name: str) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID for draft payload field '{field_name}'",
        )


def _validate_publish_resource_url(raw_url: object) -> str:
    if not isinstance(raw_url, str) or not validate_url_format(raw_url.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid resource URL in draft payload. Must begin with http:// or https:// and have a valid domain.",
        )
    return raw_url.strip()


def _parse_resource_type(value: object) -> ResourceType:
    try:
        return value if isinstance(value, ResourceType) else ResourceType(str(value))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid resource_type in draft payload: {value}",
        )

def _validate_subtopic_parent(
    db: Session,
    topic_id: uuid.UUID,
    parent_id: Optional[uuid.UUID],
    subtopic_id: Optional[uuid.UUID] = None,
) -> None:
    """Prevent self-references, cross-topic orphans, and ancestor cycles."""
    if parent_id is None:
        return
    if subtopic_id is not None and parent_id == subtopic_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subtopic cannot reference itself as parent",
        )
    visited = {subtopic_id} if subtopic_id is not None else set()
    curr_id: Optional[uuid.UUID] = parent_id
    depth = 0
    while curr_id is not None:
        if curr_id in visited:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Circular parent_subtopic_id hierarchy detected",
            )
        visited.add(curr_id)
        parent_row = (
            db.query(RoadmapSubtopic)
            .filter(RoadmapSubtopic.id == curr_id)
            .first()
        )
        if not parent_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent subtopic not found",
            )
        if depth == 0 and parent_row.topic_id != topic_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent subtopic belongs to a different topic",
            )
        curr_id = parent_row.parent_subtopic_id
        depth += 1
        if depth > 64:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subtopic hierarchy exceeds maximum nesting depth",
            )


def _snapshot_entity(obj: Any, entity_type: EntityType) -> Optional[dict]:
    """Capture a JSON-serializable snapshot of a target topic, subtopic, or resource."""
    if obj is None:
        return None
    if entity_type == EntityType.TOPIC:
        return {
            "id": str(obj.id),
            "title": obj.title,
            "slug": obj.slug,
            "description": obj.description,
            "order_index": obj.order_index,
            "is_published": obj.is_published,
        }
    if entity_type == EntityType.SUBTOPIC:
        return {
            "id": str(obj.id),
            "topic_id": str(obj.topic_id),
            "parent_subtopic_id": str(obj.parent_subtopic_id)
            if obj.parent_subtopic_id
            else None,
            "title": obj.title,
            "slug": obj.slug,
            "description": obj.description,
            "order_index": obj.order_index,
            "difficulty": obj.difficulty,
            "is_published": obj.is_published,
        }
    if entity_type == EntityType.RESOURCE:
        return {
            "id": str(obj.id),
            "title": obj.title,
            "url": obj.url,
            "resource_type": obj.resource_type.value
            if hasattr(obj.resource_type, "value")
            else str(obj.resource_type),
            "difficulty": obj.difficulty,
            "subtopic_id": str(obj.subtopic_id) if obj.subtopic_id else None,
            "order_index": obj.order_index,
            "is_archived": obj.is_archived,
        }
    return None


router = APIRouter(prefix="/drafts", tags=["Content Drafts & Review Workflow"])


@router.get(
    "/",
    response_model=List[ContentDraftResponse],
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def list_drafts(
    status: Optional[DraftStatus] = None,
    entity_type: Optional[EntityType] = None,
    entity_id: Optional[uuid.UUID] = None,
    include_archived: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(ContentDraft)
    if not include_archived:
        query = query.filter(ContentDraft.is_archived == False)  # noqa: E712
    if status:
        query = query.filter(ContentDraft.status == status)
    if entity_type:
        query = query.filter(ContentDraft.entity_type == entity_type)
    if entity_id:
        query = query.filter(ContentDraft.entity_id == entity_id)

    return (
        query.order_by(
            ContentDraft.version_number.desc(), ContentDraft.updated_at.desc()
        )
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/{draft_id}",
    response_model=ContentDraftResponse,
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def get_draft(draft_id: uuid.UUID, db: Session = Depends(get_db)):
    draft = db.query(ContentDraft).filter(ContentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
        )
    return draft


@router.post(
    "/",
    response_model=ContentDraftResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def create_draft(
    payload: ContentDraftCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = ContentDraft(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        title=payload.title,
        payload=payload.payload,
        status=DraftStatus.DRAFT,
        version_number=1,
        submitted_by_id=current_user.id,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    log_audit_event(
        db=db,
        action="draft_created",
        user_id=current_user.id,
        target_type="draft",
        target_id=str(draft.id),
        details={"title": draft.title, "entity_type": draft.entity_type.value},
    )

    return draft


@router.put(
    "/{draft_id}",
    response_model=ContentDraftResponse,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def update_draft(
    draft_id: uuid.UUID,
    payload: ContentDraftUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.query(ContentDraft).filter(ContentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
        )
    if draft.is_archived or draft.status not in (
        DraftStatus.DRAFT,
        DraftStatus.REJECTED,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot edit draft in status: {draft.status.value}",
        )

    old_status = draft.status.value
    if payload.title is not None:
        draft.title = payload.title
    if payload.payload is not None:
        draft.payload = payload.payload
        draft.version_number += 1
    if draft.status == DraftStatus.REJECTED:
        draft.status = DraftStatus.DRAFT

    db.commit()
    db.refresh(draft)

    log_audit_event(
        db=db,
        action="draft_updated",
        user_id=current_user.id,
        target_type="draft",
        target_id=str(draft.id),
        details={
            "old_status": old_status,
            "new_status": draft.status.value,
            "version_number": draft.version_number,
        },
    )
    return draft


@router.post(
    "/{draft_id}/submit",
    response_model=ContentDraftResponse,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def submit_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.query(ContentDraft).filter(ContentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
        )
    if draft.is_archived or draft.status not in (
        DraftStatus.DRAFT,
        DraftStatus.REJECTED,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot submit draft in status: {draft.status.value}",
        )

    old_status = draft.status.value
    draft.status = DraftStatus.PENDING_APPROVAL
    db.commit()
    db.refresh(draft)

    log_audit_event(
        db=db,
        action="draft_submitted",
        user_id=current_user.id,
        target_type="draft",
        target_id=str(draft.id),
        details={"old_status": old_status, "new_status": draft.status.value},
    )
    return draft


@router.post(
    "/{draft_id}/approve",
    response_model=ContentDraftResponse,
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def approve_draft(
    draft_id: uuid.UUID,
    payload: Optional[ContentDraftReview] = None,
    current_admin: User = Depends(require_role([UserRole.TECH_ADMIN])),
    db: Session = Depends(get_db),
):
    draft = db.query(ContentDraft).filter(ContentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
        )

    if draft.submitted_by_id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Draft creator cannot approve their own submission",
        )

    if draft.is_archived or draft.status != DraftStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only pending_approval drafts can be approved (current status: {draft.status.value})",
        )

    old_status = draft.status.value
    draft.status = DraftStatus.APPROVED
    draft.reviewed_by_id = current_admin.id
    if payload and payload.feedback:
        draft.review_feedback = payload.feedback
    db.commit()
    db.refresh(draft)

    log_audit_event(
        db=db,
        action="draft_approved",
        user_id=current_admin.id,
        target_type="draft",
        target_id=str(draft.id),
        details={
            "old_status": old_status,
            "new_status": draft.status.value,
            "submitted_by_id": str(draft.submitted_by_id),
            "feedback": draft.review_feedback,
        },
    )

    return draft


@router.post(
    "/{draft_id}/reject",
    response_model=ContentDraftResponse,
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def reject_draft(
    draft_id: uuid.UUID,
    payload: ContentDraftReview,
    current_admin: User = Depends(require_role([UserRole.TECH_ADMIN])),
    db: Session = Depends(get_db),
):
    draft = db.query(ContentDraft).filter(ContentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
        )

    if draft.submitted_by_id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Draft creator cannot reject their own submission",
        )

    if draft.is_archived or draft.status != DraftStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only pending_approval drafts can be rejected (current status: {draft.status.value})",
        )

    old_status = draft.status.value
    draft.status = DraftStatus.REJECTED
    draft.reviewed_by_id = current_admin.id
    draft.review_feedback = payload.feedback
    db.commit()
    db.refresh(draft)

    log_audit_event(
        db=db,
        action="draft_rejected",
        user_id=current_admin.id,
        target_type="draft",
        target_id=str(draft.id),
        details={
            "old_status": old_status,
            "new_status": draft.status.value,
            "feedback": payload.feedback,
        },
    )

    return draft


@router.post(
    "/{draft_id}/publish",
    response_model=ContentDraftResponse,
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def publish_draft(
    draft_id: uuid.UUID,
    current_admin: User = Depends(require_role([UserRole.TECH_ADMIN])),
    db: Session = Depends(get_db),
):
    draft = db.query(ContentDraft).filter(ContentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
        )

    if draft.submitted_by_id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Draft creator cannot publish their own submission",
        )

    if draft.is_archived or draft.status != DraftStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only approved drafts can be published. Current draft status is '{draft.status.value}'.",
        )

    p = draft.payload or {}
    previous_snapshot = None
    target_entity = None

    if draft.entity_type == EntityType.TOPIC:
        if draft.entity_id:
            topic = (
                db.query(RoadmapTopic)
                .filter(RoadmapTopic.id == draft.entity_id)
                .first()
            )
            if not topic:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Target topic not found"
                )
            previous_snapshot = _snapshot_entity(topic, EntityType.TOPIC)
            for k in _ALLOWED_TOPIC_PUBLISH_FIELDS:
                if k in p:
                    setattr(topic, k, p[k])
            target_entity = topic
        else:
            slug = p.get("slug")
            if not isinstance(slug, str) or not slug.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Draft payload requires a non-empty 'slug' for a new topic",
                )
            topic = RoadmapTopic(
                title=p.get("title", draft.title),
                slug=slug.strip(),
                description=p.get("description"),
                order_index=p.get("order_index", 0),
                is_published=p.get("is_published", True),
            )
            db.add(topic)
            db.flush()
            draft.entity_id = topic.id
            target_entity = topic

    elif draft.entity_type == EntityType.SUBTOPIC:
        if draft.entity_id:
            sub = (
                db.query(RoadmapSubtopic)
                .filter(RoadmapSubtopic.id == draft.entity_id)
                .first()
            )
            if not sub:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Target subtopic not found"
                )
            previous_snapshot = _snapshot_entity(sub, EntityType.SUBTOPIC)
            effective_topic_id = sub.topic_id
            if "topic_id" in p:
                parsed_tid = _parse_optional_uuid(p["topic_id"], "topic_id")
                if parsed_tid is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Draft payload field 'topic_id' cannot be null",
                    )
                effective_topic_id = parsed_tid
            if "parent_subtopic_id" in p:
                parsed_pid = _parse_optional_uuid(
                    p["parent_subtopic_id"], "parent_subtopic_id"
                )
                _validate_subtopic_parent(
                    db, effective_topic_id, parsed_pid, subtopic_id=sub.id
                )
            for k in _ALLOWED_SUBTOPIC_PUBLISH_FIELDS:
                if k not in p:
                    continue
                val = p[k]
                if k in ("topic_id", "parent_subtopic_id"):
                    val = _parse_optional_uuid(val, k)
                setattr(sub, k, val)
            target_entity = sub
        else:
            topic_id = _parse_optional_uuid(p.get("topic_id"), "topic_id")
            slug = p.get("slug")
            if topic_id is None or not isinstance(slug, str) or not slug.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Draft payload requires valid 'topic_id' and 'slug' for a new subtopic",
                )
            parent_id = _parse_optional_uuid(
                p.get("parent_subtopic_id"), "parent_subtopic_id"
            )
            _validate_subtopic_parent(db, topic_id, parent_id)
            sub = RoadmapSubtopic(
                topic_id=topic_id,
                parent_subtopic_id=parent_id,
                title=p.get("title", draft.title),
                slug=slug.strip(),
                description=p.get("description"),
                order_index=p.get("order_index", 0),
                difficulty=p.get("difficulty", "medium"),
                is_published=p.get("is_published", True),
            )
            db.add(sub)
            db.flush()
            draft.entity_id = sub.id
            target_entity = sub

    elif draft.entity_type == EntityType.RESOURCE:
        if "url" in p:
            validated_url = _validate_publish_resource_url(p["url"])
        else:
            validated_url = None

        if validated_url is not None:
            canon_url = canonicalize_resource_url(validated_url)
            dup_query = db.query(Resource.id, Resource.url).filter(
                Resource.is_archived == False  # noqa: E712
            )
            if draft.entity_id:
                dup_query = dup_query.filter(Resource.id != draft.entity_id)
            if any(canonicalize_resource_url(row[1]) == canon_url for row in dup_query.all()):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An active resource with this canonical URL already exists",
                )

        if draft.entity_id:
            res = (
                db.query(Resource)
                .filter(Resource.id == draft.entity_id)
                .first()
            )
            if not res:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Target resource not found"
                )
            previous_snapshot = _snapshot_entity(res, EntityType.RESOURCE)
            for k in _ALLOWED_RESOURCE_PUBLISH_FIELDS:
                if k not in p:
                    continue
                val = p[k]
                if k == "url":
                    val = validated_url
                elif k == "subtopic_id":
                    val = _parse_optional_uuid(val, "subtopic_id")
                elif k == "resource_type":
                    val = _parse_resource_type(val)
                setattr(res, k, val)
            target_entity = res
        else:
            if validated_url is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Draft payload requires a valid 'url' for a new resource",
                )
            sub_id = _parse_optional_uuid(p.get("subtopic_id"), "subtopic_id")
            res_type = _parse_resource_type(p.get("resource_type", "article"))
            res = Resource(
                title=p.get("title", draft.title),
                url=validated_url,
                resource_type=res_type,
                difficulty=p.get("difficulty", "medium"),
                subtopic_id=sub_id,
                order_index=p.get("order_index", 0),
            )
            db.add(res)
            db.flush()
            draft.entity_id = res.id
            target_entity = res

    prior_pub_versions = (
        db.query(ContentDraft.version_number)
        .filter(
            ContentDraft.id != draft.id,
            ContentDraft.entity_type == draft.entity_type,
            ContentDraft.entity_id == draft.entity_id,
            ContentDraft.status == DraftStatus.PUBLISHED,
        )
        .all()
    )
    max_prior = max((row[0] for row in prior_pub_versions), default=0)
    if draft.version_number <= max_prior:
        draft.version_number = max_prior + 1

    old_status = draft.status.value
    draft.status = DraftStatus.PUBLISHED
    draft.reviewed_by_id = current_admin.id
    db.flush()
    published_snapshot = _snapshot_entity(target_entity, draft.entity_type)
    db.commit()
    db.refresh(draft)

    log_audit_event(
        db=db,
        action="draft_published",
        user_id=current_admin.id,
        target_type="draft",
        target_id=str(draft.id),
        details={
            "old_status": old_status,
            "new_status": draft.status.value,
            "entity_type": draft.entity_type.value,
            "entity_id": str(draft.entity_id),
            "version_number": draft.version_number,
            "submitted_by_id": str(draft.submitted_by_id),
            "previous_snapshot": previous_snapshot,
            "published_snapshot": published_snapshot,
        },
    )

    return draft


@router.post(
    "/{draft_id}/archive",
    response_model=ContentDraftResponse,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def archive_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.query(ContentDraft).filter(ContentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
        )
    if draft.status == DraftStatus.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Published drafts are immutable revision records and cannot be archived",
        )
    if draft.is_archived or draft.status == DraftStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Draft is already archived",
        )

    old_status = draft.status.value
    draft.is_archived = True
    draft.status = DraftStatus.ARCHIVED
    db.commit()
    db.refresh(draft)

    log_audit_event(
        db=db,
        action="draft_archived",
        user_id=current_user.id,
        target_type="draft",
        target_id=str(draft.id),
        details={"old_status": old_status, "new_status": draft.status.value},
    )
    return draft


@router.post(
    "/{draft_id}/restore",
    response_model=ContentDraftResponse,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def restore_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.query(ContentDraft).filter(ContentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
        )
    if draft.status == DraftStatus.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Published drafts are immutable revision records and cannot be reverted",
        )
    if not draft.is_archived and draft.status != DraftStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only archived drafts can be restored",
        )

    old_status = draft.status.value
    draft.is_archived = False
    draft.status = DraftStatus.DRAFT
    db.commit()
    db.refresh(draft)

    log_audit_event(
        db=db,
        action="draft_restored",
        user_id=current_user.id,
        target_type="draft",
        target_id=str(draft.id),
        details={"old_status": old_status, "new_status": draft.status.value},
    )
    return draft
