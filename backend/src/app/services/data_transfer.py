import csv
import io
import json
import uuid
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy.orm import Session

from app.models.roadmap import RoadmapTopic, RoadmapSubtopic
from app.models.resource import Resource, ResourceType, AvailabilityStatus
from app.services.url_checker import validate_url_format

MAX_IMPORT_BYTES = 512 * 1024  # 512 KiB max payload size per import request
MAX_ROADMAP_IMPORT_TOPICS = 50
MAX_ROADMAP_IMPORT_SUBTOPICS = 250
MAX_RESOURCE_IMPORT_ROWS = 500
MAX_EXPORT_ROWS = 1000


def canonicalize_resource_url(raw_url: str) -> str:
    """Normalize scheme/host case and trailing slash for duplicate resource detection."""
    cleaned = raw_url.strip()
    parts = urlsplit(cleaned)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") if parts.path and parts.path != "/" else (parts.path or "")
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


class DataTransferService:
    @staticmethod
    def export_roadmap_json(db: Session) -> List[Dict[str, Any]]:
        topics = (
            db.query(RoadmapTopic)
            .order_by(RoadmapTopic.order_index)
            .limit(MAX_EXPORT_ROWS)
            .all()
        )
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
        topics = (
            db.query(RoadmapTopic)
            .order_by(RoadmapTopic.order_index)
            .limit(MAX_EXPORT_ROWS)
            .all()
        )
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
    def import_roadmap_json_with_stats(
        db: Session, data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        if len(data) > MAX_ROADMAP_IMPORT_TOPICS:
            raise ValueError(
                f"Roadmap JSON exceeds maximum topic limit of {MAX_ROADMAP_IMPORT_TOPICS}"
            )
        total_subs = sum(len((item.get("subtopics") or [])) for item in data if isinstance(item, dict))
        if total_subs > MAX_ROADMAP_IMPORT_SUBTOPICS:
            raise ValueError(
                f"Roadmap JSON exceeds maximum subtopic limit of {MAX_ROADMAP_IMPORT_SUBTOPICS}"
            )
        raw_bytes = len(json.dumps(data).encode("utf-8"))
        if raw_bytes > MAX_IMPORT_BYTES:
            raise ValueError(
                f"Roadmap JSON exceeds maximum payload size of {MAX_IMPORT_BYTES} bytes"
            )

        imported_topics = 0
        skipped_topics = 0
        imported_subtopics = 0
        skipped_subtopics = 0

        with db.begin_nested():
            for item in data:
                if not isinstance(item, dict):
                    raise ValueError("Each roadmap topic entry must be an object")
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
                    imported_topics += 1
                else:
                    skipped_topics += 1

                # Pass 1: create/collect subtopics and map exported IDs & slugs -> persisted IDs
                old_id_to_new_id: Dict[str, uuid.UUID] = {}
                pending_parent_links: List[Tuple[RoadmapSubtopic, Dict[str, Any]]] = []

                for sub_data in item.get("subtopics", []):
                    if not isinstance(sub_data, dict):
                        raise ValueError("Each subtopic entry must be an object")
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
                        imported_subtopics += 1
                    else:
                        skipped_subtopics += 1

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

                # Pass 3: detect parent cycles within topic
                db.flush()
                parent_map = {
                    s.id: s.parent_subtopic_id
                    for s in db.query(RoadmapSubtopic)
                    .filter(RoadmapSubtopic.topic_id == topic.id)
                    .all()
                }
                for sid in parent_map:
                    seen: Set[uuid.UUID] = set()
                    curr: Any = sid
                    while curr is not None:
                        if curr in seen:
                            raise ValueError(
                                f"Circular subtopic hierarchy detected in topic '{topic.slug}'"
                            )
                        seen.add(curr)
                        curr = parent_map.get(curr)

        db.commit()
        return {
            "imported_topics": imported_topics,
            "skipped_topics": skipped_topics,
            "imported_subtopics": imported_subtopics,
            "skipped_subtopics": skipped_subtopics,
            "rejected_count": 0,
        }

    @classmethod
    def import_roadmap_json(cls, db: Session, data: List[Dict[str, Any]]) -> int:
        return cls.import_roadmap_json_with_stats(db, data)["imported_topics"]

    @staticmethod
    def export_resources_csv(db: Session) -> str:
        resources = (
            db.query(Resource)
            .filter(Resource.is_archived == False)  # noqa: E712
            .order_by(Resource.order_index)
            .limit(MAX_EXPORT_ROWS)
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
    def import_resources_csv_with_stats(
        db: Session, csv_content: str
    ) -> Dict[str, int]:
        if len(csv_content.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise ValueError(
                f"Resources CSV exceeds maximum payload size of {MAX_IMPORT_BYTES} bytes"
            )
        reader = csv.DictReader(io.StringIO(csv_content))
        imported_count = 0
        skipped_count = 0

        existing_urls = {
            canonicalize_resource_url(row[0])
            for row in db.query(Resource.url)
            .filter(Resource.is_archived == False)  # noqa: E712
            .all()
        }

        with db.begin_nested():
            for row_idx, row in enumerate(reader, start=1):
                if row_idx > MAX_RESOURCE_IMPORT_ROWS:
                    raise ValueError(
                        f"Resources CSV exceeds maximum row limit of {MAX_RESOURCE_IMPORT_ROWS}"
                    )
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

                canon_url = canonicalize_resource_url(raw_url)
                if canon_url in existing_urls:
                    skipped_count += 1
                    continue

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
                existing_urls.add(canon_url)
                imported_count += 1
        db.commit()
        return {
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "rejected_count": 0,
        }

    @classmethod
    def import_resources_csv(cls, db: Session, csv_content: str) -> int:
        return cls.import_resources_csv_with_stats(db, csv_content)["imported_count"]


data_transfer_service = DataTransferService()
