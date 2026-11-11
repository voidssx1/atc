import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
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


def build_subtopic_tree(
    subtopics: List[RoadmapSubtopic], parent_id: Optional[uuid.UUID] = None
) -> List[RoadmapSubtopicResponse]:
    """Recursively builds the nested subtopic hierarchy."""
    result = []
    filtered = [s for s in subtopics if s.parent_subtopic_id == parent_id]
    for s in sorted(filtered, key=lambda x: x.order_index):
        children = build_subtopic_tree(subtopics, s.id)
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
            children=children,
        )
        result.append(item)
    return result


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

    query = db.query(RoadmapTopic)
    if not include_unpublished:
        query = query.filter(RoadmapTopic.is_published == True)  # noqa: E712

    topics = query.order_by(RoadmapTopic.order_index.asc()).all()
    if not topics:
        return []

    topic_ids = [t.id for t in topics]
    sub_query = db.query(RoadmapSubtopic).filter(
        RoadmapSubtopic.topic_id.in_(topic_ids)
    )
    if not include_unpublished:
        sub_query = sub_query.filter(RoadmapSubtopic.is_published == True)  # noqa: E712

    subtopics_by_topic: dict[uuid.UUID, List[RoadmapSubtopic]] = {}
    for sub in sub_query.all():
        subtopics_by_topic.setdefault(sub.topic_id, []).append(sub)

    results = []
    for t in topics:
        tree = build_subtopic_tree(subtopics_by_topic.get(t.id, []), parent_id=None)

        results.append(
            RoadmapTopicDetailResponse(
                id=t.id,
                title=t.title,
                slug=t.slug,
                description=t.description,
                order_index=t.order_index,
                is_published=t.is_published,
                created_at=t.created_at,
                updated_at=t.updated_at,
                subtopics=tree,
            )
        )
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
        db.query(RoadmapTopic)
        .filter(RoadmapTopic.slug == topic_slug)
        .first()
    )
    if not topic or (not include_unpublished and not topic.is_published):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found"
        )

    sub_query = db.query(RoadmapSubtopic).filter(
        RoadmapSubtopic.topic_id == topic.id
    )
    if not include_unpublished:
        sub_query = sub_query.filter(RoadmapSubtopic.is_published == True)  # noqa: E712
    subtopics = sub_query.all()
    tree = build_subtopic_tree(subtopics, parent_id=None)

    return RoadmapTopicDetailResponse(
        id=topic.id,
        title=topic.title,
        slug=topic.slug,
        description=topic.description,
        order_index=topic.order_index,
        is_published=topic.is_published,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        subtopics=tree,
    )


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

    update_data = payload.model_dump(exclude_unset=True)
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
