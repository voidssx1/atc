import csv
import io
import json
import uuid
from typing import Any, Dict, List, Tuple
from sqlalchemy.orm import Session

from app.models.roadmap import RoadmapTopic, RoadmapSubtopic
from app.models.resource import Resource, ResourceType, AvailabilityStatus
from app.services.url_checker import validate_url_format


class DataTransferService:
    @staticmethod
    def export_roadmap_json(db: Session) -> List[Dict[str, Any]]:
        topics = db.query(RoadmapTopic).order_by(RoadmapTopic.order_index).all()
        result = []
        for topic in topics:
            topic_data = {
                "id": str(topic.id),
                "title": topic.title,
                "slug": topic.slug,
                "description": topic.description,
                "order_index": topic.order_index,
                "is_published": topic.is_published,
                "subtopics": [
                    {
                        "id": str(sub.id),
                        "title": sub.title,
                        "slug": sub.slug,
                        "description": sub.description,
                        "order_index": sub.order_index,
                        "difficulty": sub.difficulty,
                        "is_published": sub.is_published,
                        "parent_subtopic_id": str(sub.parent_subtopic_id)
                        if sub.parent_subtopic_id
                        else None,
                    }
                    for sub in topic.subtopics
                ],
            }
            result.append(topic_data)
        return result

    @staticmethod
    def export_roadmap_csv(db: Session) -> str:
        topics = db.query(RoadmapTopic).order_by(RoadmapTopic.order_index).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "topic_title",
                "topic_slug",
                "topic_order",
                "subtopic_title",
                "subtopic_slug",
                "subtopic_order",
                "difficulty",
            ]
        )
        for t in topics:
            if not t.subtopics:
                writer.writerow([t.title, t.slug, t.order_index, "", "", "", ""])
            for s in t.subtopics:
                writer.writerow(
                    [
                        t.title,
                        t.slug,
                        t.order_index,
                        s.title,
                        s.slug,
                        s.order_index,
                        s.difficulty,
                    ]
                )
        return output.getvalue()

    @staticmethod
    def import_roadmap_json(db: Session, data: List[Dict[str, Any]]) -> int:
        imported_count = 0
        with db.begin_nested():
            for item in data:
                raw_slug = item.get("slug")
                raw_title = item.get("title")
                if not isinstance(raw_slug, str) or not raw_slug.strip():
                    raise ValueError("Topic entry is missing a valid 'slug'")
                if not isinstance(raw_title, str) or not raw_title.strip():
                    raise ValueError("Topic entry is missing a valid 'title'")

                topic_slug = raw_slug.strip()
                topic = (
                    db.query(RoadmapTopic)
                    .filter(RoadmapTopic.slug == topic_slug)
                    .first()
                )
                if not topic:
                    topic = RoadmapTopic(
                        title=raw_title.strip(),
                        slug=topic_slug,
                        description=item.get("description"),
                        order_index=item.get("order_index", 0),
                        is_published=item.get("is_published", True),
                    )
                    db.add(topic)
                    db.flush()
                    imported_count += 1

                # Pass 1: create/collect subtopics and map exported IDs & slugs -> persisted IDs
                old_id_to_new_id: Dict[str, uuid.UUID] = {}
                pending_parent_links: List[Tuple[RoadmapSubtopic, Dict[str, Any]]] = []

                for sub_data in item.get("subtopics", []):
                    sub_slug = sub_data.get("slug")
                    sub_title = sub_data.get("title")
                    if not isinstance(sub_slug, str) or not sub_slug.strip():
                        raise ValueError("Subtopic entry is missing a valid 'slug'")
                    if not isinstance(sub_title, str) or not sub_title.strip():
                        raise ValueError("Subtopic entry is missing a valid 'title'")

                    sub_slug_clean = sub_slug.strip()
                    subtopic = (
                        db.query(RoadmapSubtopic)
                        .filter(
                            RoadmapSubtopic.topic_id == topic.id,
                            RoadmapSubtopic.slug == sub_slug_clean,
                        )
                        .first()
                    )
                    if not subtopic:
                        subtopic = RoadmapSubtopic(
                            topic_id=topic.id,
                            title=sub_title.strip(),
                            slug=sub_slug_clean,
                            description=sub_data.get("description"),
                            order_index=sub_data.get("order_index", 0),
                            difficulty=sub_data.get("difficulty", "medium"),
                            is_published=sub_data.get("is_published", True),
                        )
                        db.add(subtopic)
                        db.flush()

                    if sub_data.get("id") is not None:
                        old_id_to_new_id[str(sub_data["id"]).strip()] = subtopic.id
                    pending_parent_links.append((subtopic, sub_data))

                # Pass 2: resolve and preserve parent_subtopic_id hierarchy
                for subtopic, sub_data in pending_parent_links:
                    raw_parent = sub_data.get("parent_subtopic_id")
                    if raw_parent is None or str(raw_parent).strip() == "":
                        continue

                    raw_parent_str = str(raw_parent).strip()
                    try:
                        parsed_parent_uuid = uuid.UUID(raw_parent_str)
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid parent_subtopic_id UUID '{raw_parent_str}' for subtopic '{subtopic.slug}'"
                        ) from exc

                    resolved_parent_id = old_id_to_new_id.get(str(parsed_parent_uuid))
                    if resolved_parent_id is None:
                        existing_parent = (
                            db.query(RoadmapSubtopic)
                            .filter(
                                RoadmapSubtopic.id == parsed_parent_uuid,
                                RoadmapSubtopic.topic_id == topic.id,
                            )
                            .first()
                        )
                        if not existing_parent:
                            raise ValueError(
                                f"Parent subtopic '{parsed_parent_uuid}' not found in topic '{topic.slug}'"
                            )
                        resolved_parent_id = existing_parent.id

                    if resolved_parent_id == subtopic.id:
                        raise ValueError(
                            f"Subtopic '{subtopic.slug}' cannot reference itself as parent"
                        )
                    subtopic.parent_subtopic_id = resolved_parent_id

        db.commit()
        return imported_count

    @staticmethod
    def export_resources_csv(db: Session) -> str:
        resources = (
            db.query(Resource)
            .filter(Resource.is_archived == False)  # noqa: E712
            .order_by(Resource.order_index)
            .all()
        )
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "title",
                "url",
                "resource_type",
                "difficulty",
                "availability_status",
                "subtopic_id",
            ]
        )
        for r in resources:
            writer.writerow(
                [
                    str(r.id),
                    r.title,
                    r.url,
                    r.resource_type.value
                    if hasattr(r.resource_type, "value")
                    else r.resource_type,
                    r.difficulty,
                    r.availability_status.value
                    if hasattr(r.availability_status, "value")
                    else r.availability_status,
                    str(r.subtopic_id) if r.subtopic_id else "",
                ]
            )
        return output.getvalue()

    @staticmethod
    def import_resources_csv(db: Session, csv_content: str) -> int:
        reader = csv.DictReader(io.StringIO(csv_content))
        imported_count = 0
        with db.begin_nested():
            for row_idx, row in enumerate(reader, start=1):
                raw_title = (row.get("title") or "").strip()
                raw_url = (row.get("url") or "").strip()
                if not raw_title or not raw_url:
                    raise ValueError(
                        f"CSV row {row_idx} is missing required 'title' or 'url'"
                    )

                if not validate_url_format(raw_url):
                    raise ValueError(
                        f"Invalid resource URL at CSV row {row_idx}: '{raw_url}'"
                    )

                sub_id = None
                raw_sub_id = (row.get("subtopic_id") or "").strip()
                if raw_sub_id:
                    try:
                        sub_id = uuid.UUID(raw_sub_id)
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid subtopic_id UUID at CSV row {row_idx}: '{raw_sub_id}'"
                        ) from exc
                    if (
                        not db.query(RoadmapSubtopic)
                        .filter(RoadmapSubtopic.id == sub_id)
                        .first()
                    ):
                        raise ValueError(
                            f"Referenced subtopic_id not found at CSV row {row_idx}: '{sub_id}'"
                        )

                raw_type = (row.get("resource_type") or "article").strip().lower()
                try:
                    res_type = ResourceType(raw_type)
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid resource_type at CSV row {row_idx}: '{raw_type}'"
                    ) from exc

                res = Resource(
                    title=raw_title,
                    url=raw_url,
                    resource_type=res_type,
                    difficulty=(row.get("difficulty") or "medium").strip() or "medium",
                    subtopic_id=sub_id,
                )
                db.add(res)
                imported_count += 1
        db.commit()
        return imported_count


data_transfer_service = DataTransferService()
