import uuid
from sqlalchemy import event

from app.models.clerk import ClerkOutbox
from app.models.resource import AvailabilityStatus, Resource, ResourceType
from app.models.roadmap import RoadmapSubtopic, RoadmapTopic


def test_import_roadmap_json_preserves_parent_subtopic_and_rejects_invalid(
    client, db_session, tech_admin_headers
):
    parent_old_id = str(uuid.uuid4())
    child_old_id = str(uuid.uuid4())
    grandchild_old_id = str(uuid.uuid4())

    payload = [
        {
            "title": "Nested Import Topic",
            "slug": "nested-import-topic",
            "description": "Imported hierarchy",
            "order_index": 10,
            "is_published": True,
            "subtopics": [
                {
                    "id": parent_old_id,
                    "title": "Root Subtopic",
                    "slug": "root-subtopic",
                    "order_index": 1,
                    "difficulty": "easy",
                    "parent_subtopic_id": None,
                },
                {
                    "id": child_old_id,
                    "title": "Child Subtopic",
                    "slug": "child-subtopic",
                    "order_index": 2,
                    "difficulty": "medium",
                    "parent_subtopic_id": parent_old_id,
                },
                {
                    "id": grandchild_old_id,
                    "title": "Grandchild Subtopic",
                    "slug": "grandchild-subtopic",
                    "order_index": 3,
                    "difficulty": "hard",
                    "parent_subtopic_id": child_old_id,
                },
            ],
        }
    ]

    res = client.post(
        "/api/v1/admin/data/import/roadmap/json",
        json=payload,
        headers=tech_admin_headers,
    )
    assert res.status_code == 200
    assert res.json()["imported_topics"] == 1

    topic = (
        db_session.query(RoadmapTopic)
        .filter(RoadmapTopic.slug == "nested-import-topic")
        .first()
    )
    assert topic is not None
    subs = {
        s.slug: s
        for s in db_session.query(RoadmapSubtopic)
        .filter(RoadmapSubtopic.topic_id == topic.id)
        .all()
    }
    assert subs["root-subtopic"].parent_subtopic_id is None
    assert subs["child-subtopic"].parent_subtopic_id == subs["root-subtopic"].id
    assert subs["grandchild-subtopic"].parent_subtopic_id == subs["child-subtopic"].id

    # Invalid parent UUID must return 400
    bad_uuid_payload = [
        {
            "title": "Bad Parent UUID Topic",
            "slug": "bad-parent-uuid-topic",
            "subtopics": [
                {
                    "title": "Broken Child",
                    "slug": "broken-child",
                    "parent_subtopic_id": "not-a-uuid",
                }
            ],
        }
    ]
    bad_res = client.post(
        "/api/v1/admin/data/import/roadmap/json",
        json=bad_uuid_payload,
        headers=tech_admin_headers,
    )
    assert bad_res.status_code == 400
    assert (
        db_session.query(RoadmapTopic)
        .filter(RoadmapTopic.slug == "bad-parent-uuid-topic")
        .first()
        is None
    )

    # Nonexistent parent UUID must return 400
    missing_parent_payload = [
        {
            "title": "Missing Parent Topic",
            "slug": "missing-parent-topic",
            "subtopics": [
                {
                    "title": "Orphan Child",
                    "slug": "orphan-child",
                    "parent_subtopic_id": str(uuid.uuid4()),
                }
            ],
        }
    ]
    missing_res = client.post(
        "/api/v1/admin/data/import/roadmap/json",
        json=missing_parent_payload,
        headers=tech_admin_headers,
    )
    assert missing_res.status_code == 400


def test_import_resources_csv_validates_urls_and_subtopic_uuids(
    client, db_session, tech_admin_headers
):
    topic = RoadmapTopic(title="CSV Topic", slug="csv-topic", order_index=1)
    db_session.add(topic)
    db_session.flush()
    sub = RoadmapSubtopic(
        topic_id=topic.id, title="CSV Subtopic", slug="csv-subtopic", order_index=1
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

    # Invalid URL must return 400 and rollback row
    bad_url_csv = (
        "title,url,resource_type,difficulty,subtopic_id\n"
        f"Bad URL Res,javascript:alert(1),article,easy,{sub.id}\n"
    )
    bad_url_res = client.post(
        "/api/v1/admin/data/import/resources/csv",
        json={"csv_content": bad_url_csv},
        headers=tech_admin_headers,
    )
    assert bad_url_res.status_code == 400
    assert (
        db_session.query(Resource).filter(Resource.title == "Bad URL Res").first()
        is None
    )

    # Invalid subtopic_id UUID must return 400 instead of silently becoming None
    bad_sub_csv = (
        "title,url,resource_type,difficulty,subtopic_id\n"
        "Bad Subtopic UUID,https://example.com/valid,article,easy,not-a-valid-uuid\n"
    )
    bad_sub_res = client.post(
        "/api/v1/admin/data/import/resources/csv",
        json={"csv_content": bad_sub_csv},
        headers=tech_admin_headers,
    )
    assert bad_sub_res.status_code == 400
    assert (
        db_session.query(Resource).filter(Resource.title == "Bad Subtopic UUID").first()
        is None
    )

    # Unknown subtopic_id UUID must return 400
    unknown_sub_csv = (
        "title,url,resource_type,difficulty,subtopic_id\n"
        f"Missing Subtopic Res,https://example.com/valid2,article,easy,{uuid.uuid4()}\n"
    )
    unknown_sub_res = client.post(
        "/api/v1/admin/data/import/resources/csv",
        json={"csv_content": unknown_sub_csv},
        headers=tech_admin_headers,
    )
    assert unknown_sub_res.status_code == 400

    # Valid CSV with valid URL & subtopic_id UUID succeeds
    valid_csv = (
        "title,url,resource_type,difficulty,subtopic_id\n"
        f"Valid CSV Res,https://example.com/dsa-notes,article,medium,{sub.id}\n"
    )
    ok_res = client.post(
        "/api/v1/admin/data/import/resources/csv",
        json={"csv_content": valid_csv},
        headers=tech_admin_headers,
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["imported_count"] == 1
    imported = db_session.query(Resource).filter(Resource.title == "Valid CSV Res").first()
    assert imported is not None
    assert imported.subtopic_id == sub.id


def test_analytics_click_404_and_single_query_summary(
    client, db_session, student_headers, tech_admin_headers
):
    missing_id = str(uuid.uuid4())
    assert (
        client.post("/api/v1/analytics/click", json={"resource_id": missing_id}).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/analytics/event",
            json={"resource_id": missing_id},
            headers=student_headers,
        ).status_code
        == 404
    )

    archived_res = Resource(
        title="Archived Analytics Resource",
        url="https://example.com/arch-analytics",
        resource_type=ResourceType.ARTICLE,
        availability_status=AvailabilityStatus.AVAILABLE,
        is_archived=True,
    )
    resources = [
        Resource(
            title=f"Analytics Item {i}",
            url=f"https://example.com/analytics-{i}",
            resource_type=ResourceType.ARTICLE,
            availability_status=AvailabilityStatus.AVAILABLE,
            order_index=i,
        )
        for i in range(4)
    ]
    db_session.add(archived_res)
    db_session.add_all(resources)
    db_session.commit()
    for r in resources:
        db_session.refresh(r)
    db_session.refresh(archived_res)

    # Archived resource click returns 404
    assert (
        client.post(
            "/api/v1/analytics/click", json={"resource_id": str(archived_res.id)}
        ).status_code
        == 404
    )

    # 1 anonymous click + 2 authenticated student clicks on resources[0]
    target_id = str(resources[0].id)
    assert (
        client.post("/api/v1/analytics/click", json={"resource_id": target_id}).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/analytics/event",
            json={"resource_id": target_id},
            headers=student_headers,
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/analytics/click",
            json={"resource_id": target_id},
            headers=student_headers,
        ).status_code
        == 201
    )

    # Count SQL statements specifically inside GET /api/v1/analytics/resources (no 2N+1)
    executed_sql = []

    def _before_execute(conn, cursor, statement, parameters, context, executemany):
        executed_sql.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _before_execute)
    try:
        summary_res = client.get(
            "/api/v1/analytics/resources?limit=50", headers=tech_admin_headers
        )
    finally:
        event.remove(engine, "before_cursor_execute", _before_execute)

    assert summary_res.status_code == 200
    analytics_selects = [s for s in executed_sql if "resource_analytics" in s.lower()]
    assert len(analytics_selects) == 1

    data = summary_res.json()
    item0 = next(x for x in data if x["resource_id"] == target_id)
    assert item0["total_clicks"] == 3
    assert item0["unique_students"] == 1
    assert all(x["resource_id"] != str(archived_res.id) for x in data)


def test_admin_create_user_duplicate_username_returns_409(
    client, tech_admin_headers
):
    first = client.post(
        "/api/v1/admin/users/",
        json={
            "email": "first.viewer@atc.bennett.edu.in",
            "password": "Password123!",
            "username": "shared_username",
            "role": "viewer",
        },
        headers=tech_admin_headers,
    )
    assert first.status_code == 201

    dup = client.post(
        "/api/v1/admin/users/",
        json={
            "email": "second.viewer@atc.bennett.edu.in",
            "password": "Password123!",
            "username": "shared_username",
            "role": "viewer",
        },
        headers=tech_admin_headers,
    )
    assert dup.status_code == 409


def test_admin_clerk_outbox_status_filter_validation(
    client, db_session, tech_admin_headers
):
    db_session.add_all(
        [
            ClerkOutbox(
                clerk_user_id="user_pending_1",
                audit_target_id=str(uuid.uuid4()),
                status="pending",
            ),
            ClerkOutbox(
                clerk_user_id="user_dead_1",
                audit_target_id=str(uuid.uuid4()),
                status="dead_letter",
            ),
        ]
    )
    db_session.commit()

    ok_pending = client.get(
        "/api/v1/admin/jobs/clerk-outbox?status=pending", headers=tech_admin_headers
    )
    assert ok_pending.status_code == 200
    assert all(i["status"] == "pending" for i in ok_pending.json()["items"])

    ok_dead = client.get(
        "/api/v1/admin/jobs/clerk-outbox?status=dead_letter", headers=tech_admin_headers
    )
    assert ok_dead.status_code == 200
    assert all(i["status"] == "dead_letter" for i in ok_dead.json()["items"])

    bad_status = client.get(
        "/api/v1/admin/jobs/clerk-outbox?status=invalid_status",
        headers=tech_admin_headers,
    )
    assert bad_status.status_code == 422
