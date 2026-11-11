import uuid
from datetime import datetime, timedelta, timezone

from app.core.security import generate_session_token, hash_password_sync, hash_session_token
from app.models.resource import Resource, ResourceType
from app.models.roadmap import RoadmapSubtopic, RoadmapTopic
from app.models.user import StudentProfile, User, UserRole, UserSession
from app.services.data_transfer import MAX_RESOURCE_IMPORT_ROWS, MAX_ROADMAP_IMPORT_TOPICS


def _mk_user(db_session, *, email: str, username: str, role: UserRole):
    user = User(
        email=email,
        username=username,
        hashed_password=hash_password_sync("Password123!"),
        role=role,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(StudentProfile(user_id=user.id, full_name=username))
    raw_tok = generate_session_token(48)
    db_session.add(
        UserSession(
            user_id=user.id,
            token=hash_session_token(raw_tok),
            enforce_single_session=(role == UserRole.TECH_ADMIN),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            is_revoked=False,
        )
    )
    db_session.commit()
    return user, {"Authorization": f"Bearer {raw_tok}"}


def test_viewer_rbac_read_only_and_write_denials(
    client, db_session, student_headers, tech_admin_headers
):
    """Prove VIEWER can read permitted views/exports and receives 403 on all mutations."""
    _, viewer_headers = _mk_user(
        db_session,
        email="viewer.rbac@atc.bennett.edu.in",
        username="viewerrbac",
        role=UserRole.VIEWER,
    )
    _, content_headers = _mk_user(
        db_session,
        email="content.rbac@atc.bennett.edu.in",
        username="contentrbac",
        role=UserRole.CONTENT_ADMIN,
    )

    topic = RoadmapTopic(title="Viewer T", slug="viewer-t", is_published=False)
    db_session.add(topic)
    db_session.flush()
    sub = RoadmapSubtopic(
        topic_id=topic.id, title="Viewer S", slug="viewer-s", is_published=False
    )
    res = Resource(
        title="Viewer R",
        url="https://example.com/viewer-r",
        resource_type=ResourceType.ARTICLE,
        is_archived=True,
    )
    db_session.add_all([sub, res])
    db_session.commit()

    # VIEWER permitted read endpoints -> 200
    for path in (
        "/api/v1/dashboard/admin",
        "/api/v1/analytics/resources",
        "/api/v1/admin/audit-logs",
        "/api/v1/admin/users/",
        "/api/v1/drafts/",
        "/api/v1/roadmap/topics?include_unpublished=true",
        "/api/v1/resources/?include_archived=true",
        "/api/v1/admin/data/export/roadmap/json",
        "/api/v1/admin/data/export/roadmap/csv",
        "/api/v1/admin/data/export/resources/csv",
    ):
        r = client.get(path, headers=viewer_headers)
        assert r.status_code == 200, f"VIEWER GET {path} failed with {r.status_code}"

    # VIEWER mutation/privileged endpoints -> 403
    mutations = [
        ("POST", "/api/v1/roadmap/topics", {"title": "X", "slug": "x-v"}),
        ("PUT", f"/api/v1/roadmap/topics/{topic.id}", {"title": "Y"}),
        ("DELETE", f"/api/v1/roadmap/topics/{topic.id}", None),
        (
            "POST",
            "/api/v1/roadmap/subtopics",
            {"topic_id": str(topic.id), "title": "S", "slug": "s-v"},
        ),
        ("POST", "/api/v1/roadmap/reorder", {"entity_type": "topic", "items": []}),
        (
            "POST",
            "/api/v1/resources/",
            {"title": "R", "url": "https://example.com/v", "resource_type": "article"},
        ),
        ("PUT", f"/api/v1/resources/{res.id}", {"title": "R2"}),
        ("DELETE", f"/api/v1/resources/{res.id}", None),
        ("POST", f"/api/v1/resources/{res.id}/check-availability", None),
        ("POST", "/api/v1/resources/check-all-availability", None),
        (
            "POST",
            "/api/v1/drafts/",
            {"entity_type": "topic", "title": "D", "payload": {"title": "D", "slug": "d-v"}},
        ),
        (
            "POST",
            "/api/v1/admin/users/",
            {"email": "new@bennett.edu.in", "role": "student", "password": "Password123!"},
        ),
        ("POST", "/api/v1/admin/data/import/roadmap/json", []),
        ("POST", "/api/v1/admin/data/import/resources/csv", {"csv_content": "title,url\n"}),
        ("POST", "/api/v1/admin/jobs/cleanup-expired-sessions", None),
        ("GET", "/api/v1/admin/jobs/clerk-outbox", None),
    ]
    for method, path, body in mutations:
        kwargs = {"headers": viewer_headers}
        if body is not None:
            kwargs["json"] = body
        r = client.request(method, path, **kwargs)
        assert r.status_code == 403, f"VIEWER {method} {path} returned {r.status_code} (expected 403)"

    # CONTENT_ADMIN denied TECH_ADMIN-only operations -> 403
    for method, path, body in (
        ("GET", "/api/v1/admin/jobs/clerk-outbox", None),
        ("POST", "/api/v1/admin/data/import/roadmap/json", []),
        (
            "POST",
            "/api/v1/admin/users/",
            {"email": "ca.test@bennett.edu.in", "role": "student", "password": "Password123!"},
        ),
    ):
        kwargs = {"headers": content_headers}
        if body is not None:
            kwargs["json"] = body
        assert client.request(method, path, **kwargs).status_code == 403

    # STUDENT denied admin views -> 403
    for path in (
        "/api/v1/dashboard/admin",
        "/api/v1/analytics/resources",
        "/api/v1/admin/audit-logs",
        "/api/v1/admin/users/",
        "/api/v1/drafts/",
        "/api/v1/admin/data/export/roadmap/json",
    ):
        assert client.get(path, headers=student_headers).status_code == 403

    # TECH_ADMIN permitted -> 200
    assert client.get("/api/v1/admin/jobs/clerk-outbox", headers=tech_admin_headers).status_code == 200


def test_student_isolation_and_unpublished_archived_guards(client, db_session, student_user, student_headers):
    """Ensure students stay strictly isolated to their own progress/bookmarks/profiles and published items."""
    other_student, other_headers = _mk_user(
        db_session,
        email="other.student@bennett.edu.in",
        username="otherstudent",
        role=UserRole.STUDENT,
    )

    pub_topic = RoadmapTopic(title="Public T", slug="iso-pub-t", is_published=True)
    unpub_topic = RoadmapTopic(title="Unpub T", slug="iso-unpub-t", is_published=False)
    db_session.add_all([pub_topic, unpub_topic])
    db_session.flush()
    pub_sub = RoadmapSubtopic(
        topic_id=pub_topic.id, title="Pub S", slug="iso-pub-s", is_published=True
    )
    unpub_sub = RoadmapSubtopic(
        topic_id=pub_topic.id, title="Unpub S", slug="iso-unpub-s", is_published=False
    )
    arch_res = Resource(
        title="Archived R",
        url="https://example.com/iso-arch",
        resource_type=ResourceType.ARTICLE,
        is_archived=True,
    )
    pub_res = Resource(
        title="Pub R",
        url="https://example.com/iso-pub",
        resource_type=ResourceType.ARTICLE,
        is_archived=False,
    )
    db_session.add_all([pub_sub, unpub_sub, arch_res, pub_res])
    db_session.commit()

    # Student A cannot read Student B's profile
    assert client.get(f"/api/v1/profile/{other_student.id}", headers=student_headers).status_code == 403
    assert client.get(f"/api/v1/profile/{student_user.id}", headers=student_headers).status_code == 200

    # Student cannot mark progress on unpublished subtopic or bookmark archived resource
    assert (
        client.post(
            f"/api/v1/progress/{unpub_sub.id}",
            json={"status": "completed"},
            headers=student_headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/bookmarks/",
            json={"resource_id": str(arch_res.id)},
            headers=student_headers,
        ).status_code
        == 404
    )

    # Progress and bookmarks saved by Student A remain invisible to Student B
    assert (
        client.post(
            f"/api/v1/progress/{pub_sub.id}",
            json={"status": "completed"},
            headers=student_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/bookmarks/",
            json={"resource_id": str(pub_res.id)},
            headers=student_headers,
        ).status_code
        == 201
    )
    assert client.get("/api/v1/progress/", headers=other_headers).json() == []
    assert client.get("/api/v1/bookmarks/", headers=other_headers).json() == []
    assert client.delete(f"/api/v1/bookmarks/{pub_res.id}", headers=other_headers).status_code == 404


def test_bounded_duplicate_safe_imports_and_cycle_rejection(client, db_session, tech_admin_headers):
    """Prove CSV/JSON imports enforce row bounds, detect cycles, and skip duplicates safely."""
    # 1. Duplicate CSV import skips canonical URL matches cleanly
    csv_first = (
        "title,url,resource_type,difficulty\n"
        "Guide One,https://Example.COM/dsa-guide/,article,easy\n"
        "Guide One Dup,https://example.com/dsa-guide,article,easy\n"
    )
    res1 = client.post(
        "/api/v1/admin/data/import/resources/csv",
        json={"csv_content": csv_first},
        headers=tech_admin_headers,
    )
    assert res1.status_code == 200
    assert res1.json()["imported_count"] == 1
    assert res1.json()["skipped_count"] == 1

    # Re-importing same CSV skips without duplicating
    res2 = client.post(
        "/api/v1/admin/data/import/resources/csv",
        json={"csv_content": csv_first},
        headers=tech_admin_headers,
    )
    assert res2.status_code == 200
    assert res2.json()["imported_count"] == 0
    assert res2.json()["skipped_count"] == 2

    # 2. Exceeding max CSV rows returns 400
    over_csv = "title,url\n" + "\n".join(
        f"Res {i},https://example.com/r{i}" for i in range(MAX_RESOURCE_IMPORT_ROWS + 1)
    )
    assert (
        client.post(
            "/api/v1/admin/data/import/resources/csv",
            json={"csv_content": over_csv},
            headers=tech_admin_headers,
        ).status_code
        == 400
    )

    # 3. Exceeding max roadmap topics returns 400
    over_topics = [
        {"title": f"T{i}", "slug": f"over-topic-{i}"}
        for i in range(MAX_ROADMAP_IMPORT_TOPICS + 1)
    ]
    assert (
        client.post(
            "/api/v1/admin/data/import/roadmap/json",
            json=over_topics,
            headers=tech_admin_headers,
        ).status_code
        == 400
    )

    # 4. Circular subtopic parent chain in roadmap JSON returns 400
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    cycle_payload = [
        {
            "title": "Cycle Topic",
            "slug": "cycle-topic",
            "subtopics": [
                {"id": id_a, "title": "A", "slug": "sub-a", "parent_subtopic_id": id_b},
                {"id": id_b, "title": "B", "slug": "sub-b", "parent_subtopic_id": id_a},
            ],
        }
    ]
    assert (
        client.post(
            "/api/v1/admin/data/import/roadmap/json",
            json=cycle_payload,
            headers=tech_admin_headers,
        ).status_code
        == 400
    )

    # 5. Bounded availability scan limit (>50 -> 422)
    assert (
        client.post(
            "/api/v1/admin/jobs/run-availability-scan?limit=51",
            headers=tech_admin_headers,
        ).status_code
        == 422
    )
