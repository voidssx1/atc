import uuid
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import (
    ensure_admin_for_flag,
    get_db,
    get_optional_current_user,
    require_role,
)
from app.models.roadmap import RoadmapSubtopic, RoadmapTopic
from app.models.user import User, UserRole
from app.schemas.roadmap import (
    BulkReorderRequest,
    RoadmapSubtopicCreate,
    RoadmapSubtopicResponse,
    RoadmapSubtopicUpdate,
    RoadmapTopicCreate,
    RoadmapTopicDetailResponse,
    RoadmapTopicSummaryResponse,
    RoadmapTopicUpdate,
)
from app.services.audit import log_audit_event

router = APIRouter(prefix="/roadmap", tags=["DSA Roadmap"])

_TOPIC_COLUMNS = (
    RoadmapTopic.id,
    RoadmapTopic.title,
    RoadmapTopic.slug,
    RoadmapTopic.description,
    RoadmapTopic.order_index,
    RoadmapTopic.is_published,
    RoadmapTopic.created_at,
    RoadmapTopic.updated_at,
)
_SUBTOPIC_COLUMNS = (
    RoadmapSubtopic.id,
    RoadmapSubtopic.topic_id,
    RoadmapSubtopic.parent_subtopic_id,
    RoadmapSubtopic.title,
    RoadmapSubtopic.slug,
    RoadmapSubtopic.description,
    RoadmapSubtopic.order_index,
    RoadmapSubtopic.difficulty,
    RoadmapSubtopic.is_published,
    RoadmapSubtopic.created_at,
    RoadmapSubtopic.updated_at,
)


def _build_subtopic_mapping_tree(
    by_parent: dict[Any, List[dict[str, Any]]],
    topic_id: uuid.UUID,
    parent_id: Optional[uuid.UUID] = None,
) -> List[dict[str, Any]]:
    """Build nested subtopic dictionaries in O(n) from pre-ordered parent buckets."""
    nodes = by_parent.get((topic_id, parent_id))
    if not nodes:
        return []
    result: List[dict[str, Any]] = []
    for row in nodes:
        node = dict(row)
        node["children"] = _build_subtopic_mapping_tree(
            by_parent, topic_id, row["id"]
        )
        result.append(node)
    return result


def build_subtopic_tree(
    subtopics: List[RoadmapSubtopic], parent_id: Optional[uuid.UUID] = None
) -> List[RoadmapSubtopicResponse]:
    """Recursively builds the nested subtopic hierarchy."""
    by_parent: dict[Optional[uuid.UUID], List[RoadmapSubtopic]] = {}
    for s in sorted(subtopics, key=lambda x: x.order_index):
        by_parent.setdefault(s.parent_subtopic_id, []).append(s)

    def _build(pid: Optional[uuid.UUID]) -> List[RoadmapSubtopicResponse]:
        result = []
        for s in by_parent.get(pid, ()):
            item = RoadmapSubtopicResponse(
                id=s.id,
                topic_id=s.topic_id,
                parent_subtopic_id=s.parent_subtopic_id,
                title=s.title,
                slug=s.slug,
                description=s.description,
                order_index=s.order_index,
                difficulty=s.difficulty,
                is_published=s.is_published,
                created_at=s.created_at,
                updated_at=s.updated_at,
                children=_build(s.id),
            )
            result.append(item)
        return result

    return _build(parent_id)


@router.get("/topics", response_model=List[RoadmapTopicDetailResponse])
def get_roadmap_topics(
    include_unpublished: bool = False,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin_for_flag(
        include_unpublished,
        current_user,
        "Admin privileges required to view unpublished roadmap content",
    )

    topic_stmt = select(*_TOPIC_COLUMNS).order_by(RoadmapTopic.order_index.asc())
    if not include_unpublished:
        topic_stmt = topic_stmt.where(RoadmapTopic.is_published == True)  # noqa: E712

    topics = db.execute(topic_stmt).mappings().all()
    if not topics:
        return []

    topic_ids = [t["id"] for t in topics]
    sub_stmt = (
        select(*_SUBTOPIC_COLUMNS)
        .where(RoadmapSubtopic.topic_id.in_(topic_ids))
        .order_by(RoadmapSubtopic.order_index.asc())
    )
    if not include_unpublished:
        sub_stmt = sub_stmt.where(RoadmapSubtopic.is_published == True)  # noqa: E712

    subtopics_by_parent: dict[Any, List[dict[str, Any]]] = {}
    for sub in db.execute(sub_stmt).mappings().all():
        subtopics_by_parent.setdefault(
            (sub["topic_id"], sub["parent_subtopic_id"]), []
        ).append(sub)

    results = []
    for t in topics:
        topic_dict = dict(t)
        topic_dict["subtopics"] = _build_subtopic_mapping_tree(
            subtopics_by_parent, t["id"], parent_id=None
        )
        results.append(topic_dict)
    return results


@router.get("/topics/{topic_slug}", response_model=RoadmapTopicDetailResponse)
def get_topic_by_slug(
    topic_slug: str,
    include_unpublished: bool = False,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin_for_flag(
        include_unpublished,
        current_user,
        "Admin privileges required to view unpublished roadmap content",
    )

    topic = (
        db.execute(select(*_TOPIC_COLUMNS).where(RoadmapTopic.slug == topic_slug))
        .mappings()
        .first()
    )
    if not topic or (not include_unpublished and not topic["is_published"]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found"
        )

    sub_stmt = (
        select(*_SUBTOPIC_COLUMNS)
        .where(RoadmapSubtopic.topic_id == topic["id"])
        .order_by(RoadmapSubtopic.order_index.asc())
    )
    if not include_unpublished:
        sub_stmt = sub_stmt.where(RoadmapSubtopic.is_published == True)  # noqa: E712

    subtopics_by_parent: dict[Any, List[dict[str, Any]]] = {}
    for sub in db.execute(sub_stmt).mappings().all():
        subtopics_by_parent.setdefault(
            (sub["topic_id"], sub["parent_subtopic_id"]), []
        ).append(sub)

    topic_dict = dict(topic)
    topic_dict["subtopics"] = _build_subtopic_mapping_tree(
        subtopics_by_parent, topic["id"], parent_id=None
    )
    return topic_dict


@router.post(
    "/topics",
    response_model=RoadmapTopicSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def create_topic(
    payload: RoadmapTopicCreate,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    if (
        db.query(RoadmapTopic)
        .filter(RoadmapTopic.slug == payload.slug)
        .first()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Topic slug already exists",
        )

    topic = RoadmapTopic(**payload.model_dump())
    db.add(topic)
    db.commit()
    db.refresh(topic)

    log_audit_event(
        db=db,
        action="topic_created",
        user_id=current_admin.id,
        target_type="topic",
        target_id=str(topic.id),
        details={"slug": topic.slug, "title": topic.title},
    )

    return RoadmapTopicSummaryResponse(
        id=topic.id,
        title=topic.title,
        slug=topic.slug,
        description=topic.description,
        order_index=topic.order_index,
        is_published=topic.is_published,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        subtopics_count=0,
    )


@router.put(
    "/topics/{topic_id}",
    response_model=RoadmapTopicSummaryResponse,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def update_topic(
    topic_id: uuid.UUID,
    payload: RoadmapTopicUpdate,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    topic = db.query(RoadmapTopic).filter(RoadmapTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found"
        )
    if topic.is_published:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published roadmap topics are immutable; create an approved content draft revision to modify or unpublish.",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if "slug" in update_data and update_data["slug"] != topic.slug:
        if (
            db.query(RoadmapTopic)
            .filter(RoadmapTopic.slug == update_data["slug"])
            .first()
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Topic slug already exists",
            )

    for field, val in update_data.items():
        setattr(topic, field, val)

    db.commit()
    db.refresh(topic)

    return RoadmapTopicSummaryResponse(
        id=topic.id,
        title=topic.title,
        slug=topic.slug,
        description=topic.description,
        order_index=topic.order_index,
        is_published=topic.is_published,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        subtopics_count=len(topic.subtopics),
    )


@router.delete(
    "/topics/{topic_id}",
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def delete_topic(
    topic_id: uuid.UUID,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    topic = db.query(RoadmapTopic).filter(RoadmapTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found"
        )
    if topic.is_published:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published roadmap topics cannot be directly deleted; unpublish via an approved content draft first.",
        )

    db.delete(topic)
    db.commit()

    log_audit_event(
        db=db,
        action="topic_deleted",
        user_id=current_admin.id,
        target_type="topic",
        target_id=str(topic_id),
    )

    return {"message": "Topic deleted successfully", "id": str(topic_id)}


@router.post(
    "/subtopics",
    response_model=RoadmapSubtopicResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def create_subtopic(
    payload: RoadmapSubtopicCreate,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    topic = (
        db.query(RoadmapTopic)
        .filter(RoadmapTopic.id == payload.topic_id)
        .first()
    )
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parent topic not found"
        )

    if payload.parent_subtopic_id:
        parent_sub = (
            db.query(RoadmapSubtopic)
            .filter(RoadmapSubtopic.id == payload.parent_subtopic_id)
            .first()
        )
        if not parent_sub:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent subtopic not found",
            )
        if parent_sub.topic_id != payload.topic_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent subtopic must belong to the same topic",
            )

    subtopic = RoadmapSubtopic(**payload.model_dump())
    db.add(subtopic)
    db.commit()
    db.refresh(subtopic)

    log_audit_event(
        db=db,
        action="subtopic_created",
        user_id=current_admin.id,
        target_type="subtopic",
        target_id=str(subtopic.id),
        details={"slug": subtopic.slug, "title": subtopic.title},
    )

    return RoadmapSubtopicResponse(
        id=subtopic.id,
        topic_id=subtopic.topic_id,
        parent_subtopic_id=subtopic.parent_subtopic_id,
        title=subtopic.title,
        slug=subtopic.slug,
        description=subtopic.description,
        order_index=subtopic.order_index,
        difficulty=subtopic.difficulty,
        is_published=subtopic.is_published,
        created_at=subtopic.created_at,
        updated_at=subtopic.updated_at,
        children=[],
    )


@router.put(
    "/subtopics/{subtopic_id}",
    response_model=RoadmapSubtopicResponse,
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def update_subtopic(
    subtopic_id: uuid.UUID,
    payload: RoadmapSubtopicUpdate,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    subtopic = (
        db.query(RoadmapSubtopic)
        .filter(RoadmapSubtopic.id == subtopic_id)
        .first()
    )
    if not subtopic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subtopic not found"
        )
    if subtopic.is_published:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published roadmap subtopics are immutable; create an approved content draft revision to modify or unpublish.",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if "parent_subtopic_id" in update_data and update_data["parent_subtopic_id"] is not None:
        curr_id: Optional[uuid.UUID] = update_data["parent_subtopic_id"]
        visited = {subtopic.id}
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
            if depth == 0 and parent_row.topic_id != subtopic.topic_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Parent subtopic must belong to the same topic",
                )
            curr_id = parent_row.parent_subtopic_id
            depth += 1
            if depth > 64:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Subtopic hierarchy exceeds maximum nesting depth",
                )

    for field, val in update_data.items():
        setattr(subtopic, field, val)

    db.commit()
    db.refresh(subtopic)

    return RoadmapSubtopicResponse(
        id=subtopic.id,
        topic_id=subtopic.topic_id,
        parent_subtopic_id=subtopic.parent_subtopic_id,
        title=subtopic.title,
        slug=subtopic.slug,
        description=subtopic.description,
        order_index=subtopic.order_index,
        difficulty=subtopic.difficulty,
        is_published=subtopic.is_published,
        created_at=subtopic.created_at,
        updated_at=subtopic.updated_at,
        children=[],
    )


@router.delete(
    "/subtopics/{subtopic_id}",
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def delete_subtopic(
    subtopic_id: uuid.UUID,
    current_admin: User = Depends(
        require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN])
    ),
    db: Session = Depends(get_db),
):
    subtopic = (
        db.query(RoadmapSubtopic)
        .filter(RoadmapSubtopic.id == subtopic_id)
        .first()
    )
    if not subtopic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subtopic not found"
        )
    if subtopic.is_published:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published roadmap subtopics cannot be directly deleted; unpublish via an approved content draft first.",
        )

    db.delete(subtopic)
    db.commit()

    log_audit_event(
        db=db,
        action="subtopic_deleted",
        user_id=current_admin.id,
        target_type="subtopic",
        target_id=str(subtopic_id),
    )

    return {"message": "Subtopic deleted successfully", "id": str(subtopic_id)}


@router.post(
    "/reorder",
    dependencies=[
        Depends(require_role([UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN]))
    ],
)
def bulk_reorder(payload: BulkReorderRequest, db: Session = Depends(get_db)):
    if payload.entity_type == "topic":
        model = RoadmapTopic
    elif payload.entity_type == "subtopic":
        model = RoadmapSubtopic
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_type must be 'topic' or 'subtopic'",
        )

    for item in payload.items:
        db.query(model).filter(model.id == item.id).update(
            {"order_index": item.order_index}
        )
    db.commit()

    return {"message": f"Successfully reordered {len(payload.items)} items"}
