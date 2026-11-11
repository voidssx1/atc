import uuid
from datetime import datetime, timedelta, timezone

from app.core.security import generate_session_token, hash_password_sync, hash_session_token
from app.models.resource import AvailabilityStatus, Resource, ResourceType
from app.models.user import StudentProfile, User, UserRole, UserSession


def _make_user_and_headers(db_session, *, email: str, username: str, role: UserRole):
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
    db_session.add(StudentProfile(user_id=user.id, full_name=f"{username} Full"))

    token = generate_session_token(48)
    db_session.add(
        UserSession(
            user_id=user.id,
            token=hash_session_token(token),
            enforce_single_session=(role == UserRole.TECH_ADMIN),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            is_revoked=False,
        )
    )
    db_session.commit()
    db_session.refresh(user)
    return user, {"Authorization": f"Bearer {token}"}


def test_profile_by_id_requires_auth_and_enforces_ownership_or_admin(
    client, db_session, student_user, student_headers, tech_admin_headers
):
    other_student, other_student_headers = _make_user_and_headers(
        db_session,
        email="other.student@bennett.edu.in",
        username="otherstudent",
        role=UserRole.STUDENT,
    )
    _, content_admin_headers = _make_user_and_headers(
        db_session,
        email="content.profile@atc.bennett.edu.in",
        username="contentprofile",
        role=UserRole.CONTENT_ADMIN,
    )

    # Anonymous access must fail (no public PII/email leak)
    anon_res = client.get(f"/api/v1/profile/{student_user.id}")
    assert anon_res.status_code == 401

    # Student accessing another user's profile must fail with 403
    forbidden_res = client.get(
        f"/api/v1/profile/{student_user.id}", headers=other_student_headers
    )
    assert forbidden_res.status_code == 403

    # Owner student can read own profile
    own_res = client.get(f"/api/v1/profile/{student_user.id}", headers=student_headers)
    assert own_res.status_code == 200
    assert own_res.json()["email"] == student_user.email

    # Content Admin and Tech Admin are authorized to inspect user profiles
    ca_res = client.get(
        f"/api/v1/profile/{other_student.id}", headers=content_admin_headers
    )
    assert ca_res.status_code == 200
    assert ca_res.json()["email"] == other_student.email

    ta_res = client.get(
        f"/api/v1/profile/{other_student.id}", headers=tech_admin_headers
    )
    assert ta_res.status_code == 200
    assert ta_res.json()["email"] == other_student.email


def test_roadmap_visibility_enforced_across_roles(
    client, db_session, student_headers, tech_admin_headers
):
    _, content_admin_headers = _make_user_and_headers(
        db_session,
        email="content.roadmap@atc.bennett.edu.in",
        username="contentroadmap",
        role=UserRole.CONTENT_ADMIN,
    )

    # Create 1 published topic + 1 unpublished topic as Content Admin
    pub_topic = client.post(
        "/api/v1/roadmap/topics",
        json={
            "title": "Public Trees",
            "slug": "public-trees",
            "order_index": 1,
            "is_published": True,
        },
        headers=content_admin_headers,
    ).json()
    unpub_topic = client.post(
        "/api/v1/roadmap/topics",
        json={
            "title": "Secret Draft Topic",
            "slug": "secret-draft-topic",
            "order_index": 2,
            "is_published": False,
        },
        headers=content_admin_headers,
    ).json()

    # Under published topic, create 1 published subtopic and 1 unpublished subtopic
    pub_sub = client.post(
        "/api/v1/roadmap/subtopics",
        json={
            "topic_id": pub_topic["id"],
            "title": "Binary Tree",
            "slug": "binary-tree",
            "order_index": 1,
            "is_published": True,
        },
        headers=content_admin_headers,
    ).json()
    unpub_sub = client.post(
        "/api/v1/roadmap/subtopics",
        json={
            "topic_id": pub_topic["id"],
            "title": "Hidden Subtopic",
            "slug": "hidden-subtopic",
            "order_index": 2,
            "is_published": False,
        },
        headers=content_admin_headers,
    ).json()

    # Anonymous & Student list reads exclude unpublished topics and subtopics
    for hdrs in ({}, student_headers):
        res = client.get("/api/v1/roadmap/topics", headers=hdrs)
        assert res.status_code == 200
        topics = res.json()
        slugs = {t["slug"] for t in topics}
        assert "public-trees" in slugs
        assert "secret-draft-topic" not in slugs
        pub_t = next(t for t in topics if t["slug"] == "public-trees")
        sub_ids = {s["id"] for s in pub_t["subtopics"]}
        assert pub_sub["id"] in sub_ids
        assert unpub_sub["id"] not in sub_ids

        # Unpublished topic slug read must return 404
        slug_res = client.get(
            f"/api/v1/roadmap/topics/{unpub_topic['slug']}", headers=hdrs
        )
        assert slug_res.status_code == 404

        # Published topic slug read omits unpublished subtopic
        pub_slug_res = client.get(
            f"/api/v1/roadmap/topics/{pub_topic['slug']}", headers=hdrs
        )
        assert pub_slug_res.status_code == 200
        assert {s["id"] for s in pub_slug_res.json()["subtopics"]} == {pub_sub["id"]}

    # include_unpublished=true: 401 for anonymous, 403 for student
    assert (
        client.get("/api/v1/roadmap/topics?include_unpublished=true").status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/roadmap/topics?include_unpublished=true", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/roadmap/topics/{unpub_topic['slug']}?include_unpublished=true"
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/api/v1/roadmap/topics/{unpub_topic['slug']}?include_unpublished=true",
            headers=student_headers,
        ).status_code
        == 403
    )

    # Content Admin & Tech Admin can read unpublished topics and subtopics
    for admin_hdrs in (content_admin_headers, tech_admin_headers):
        res = client.get(
            "/api/v1/roadmap/topics?include_unpublished=true", headers=admin_hdrs
        )
        assert res.status_code == 200
        slugs = {t["slug"] for t in res.json()}
        assert {"public-trees", "secret-draft-topic"}.issubset(slugs)

        detail = client.get(
            f"/api/v1/roadmap/topics/{unpub_topic['slug']}?include_unpublished=true",
            headers=admin_hdrs,
        )
        assert detail.status_code == 200
        assert detail.json()["id"] == unpub_topic["id"]


def test_resource_archive_visibility_enforced_across_roles(
    client, db_session, student_headers, tech_admin_headers
):
    _, content_admin_headers = _make_user_and_headers(
        db_session,
        email="content.res@atc.bennett.edu.in",
        username="contentres",
        role=UserRole.CONTENT_ADMIN,
    )

    active_res = Resource(
        title="Active Resource",
        url="https://example.com/active",
        resource_type=ResourceType.ARTICLE,
        difficulty="easy",
        availability_status=AvailabilityStatus.AVAILABLE,
        is_archived=False,
    )
    archived_res = Resource(
        title="Archived Resource",
        url="https://example.com/archived",
        resource_type=ResourceType.ARTICLE,
        difficulty="medium",
        availability_status=AvailabilityStatus.AVAILABLE,
        is_archived=True,
    )
    db_session.add_all([active_res, archived_res])
    db_session.commit()
    db_session.refresh(active_res)
    db_session.refresh(archived_res)

    # Anonymous & Student list excludes archived; detail on archived returns 404
    for hdrs in ({}, student_headers):
        list_res = client.get("/api/v1/resources/", headers=hdrs)
        assert list_res.status_code == 200
        ids = {r["id"] for r in list_res.json()}
        assert str(active_res.id) in ids
        assert str(archived_res.id) not in ids

        assert (
            client.get(f"/api/v1/resources/{archived_res.id}", headers=hdrs).status_code
            == 404
        )
        assert (
            client.get(f"/api/v1/resources/{active_res.id}", headers=hdrs).status_code
            == 200
        )

    # include_archived=true: 401 for anonymous, 403 for student
    assert (
        client.get("/api/v1/resources/?include_archived=true").status_code == 401
    )
    assert (
        client.get(
            "/api/v1/resources/?include_archived=true", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/resources/{archived_res.id}?include_archived=true"
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/api/v1/resources/{archived_res.id}?include_archived=true",
            headers=student_headers,
        ).status_code
        == 403
    )

    # Content Admin & Tech Admin can inspect archived resources
    for admin_hdrs in (content_admin_headers, tech_admin_headers):
        list_res = client.get(
            "/api/v1/resources/?include_archived=true", headers=admin_hdrs
        )
        assert list_res.status_code == 200
        assert str(archived_res.id) in {r["id"] for r in list_res.json()}

        detail_res = client.get(
            f"/api/v1/resources/{archived_res.id}?include_archived=true",
            headers=admin_hdrs,
        )
        assert detail_res.status_code == 200
        assert detail_res.json()["is_archived"] is True


def test_draft_resubmit_guard_url_validation_and_allowlisted_publish(
    client, db_session, student_headers, tech_admin_headers
):
    _, content_admin_headers = _make_user_and_headers(
        db_session,
        email="content.draft@atc.bennett.edu.in",
        username="contentdraft",
        role=UserRole.CONTENT_ADMIN,
    )

    # Student cannot access drafts
    assert client.get("/api/v1/drafts/", headers=student_headers).status_code == 403
    assert client.get("/api/v1/drafts/").status_code == 401

    # 1. Invalid URL resource draft is rejected at publish time
    bad_url_draft = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "resource",
            "title": "Unsafe Resource Draft",
            "payload": {
                "title": "Unsafe Resource",
                "url": "javascript:alert(1)",
                "resource_type": "article",
            },
        },
        headers=content_admin_headers,
    ).json()

    # Content admin cannot approve or publish
    assert (
        client.post(
            f"/api/v1/drafts/{bad_url_draft['id']}/approve",
            json={},
            headers=content_admin_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/drafts/{bad_url_draft['id']}/publish",
            headers=content_admin_headers,
        ).status_code
        == 403
    )

    assert (
        client.post(
            f"/api/v1/drafts/{bad_url_draft['id']}/submit",
            headers=content_admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/drafts/{bad_url_draft['id']}/approve",
            json={},
            headers=tech_admin_headers,
        ).status_code
        == 200
    )
    bad_pub = client.post(
        f"/api/v1/drafts/{bad_url_draft['id']}/publish",
        headers=tech_admin_headers,
    )
    assert bad_pub.status_code == 400

    # 2. Archived draft cannot be submitted again
    arch_draft = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "topic",
            "title": "To Be Archived",
            "payload": {"title": "Archived Topic", "slug": "archived-topic"},
        },
        headers=content_admin_headers,
    ).json()
    assert (
        client.post(
            f"/api/v1/drafts/{arch_draft['id']}/archive",
            headers=content_admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/drafts/{arch_draft['id']}/submit",
            headers=content_admin_headers,
        ).status_code
        == 400
    )

    # 3. Valid resource creation via draft, then update draft ignoring disallowed fields
    valid_draft = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "resource",
            "title": "Valid Guide",
            "payload": {
                "title": "Binary Search Guide",
                "url": "https://cp-algorithms.com/num_methods/binary_search.html",
                "resource_type": "article",
                "difficulty": "medium",
            },
        },
        headers=content_admin_headers,
    ).json()
    assert (
        client.post(
            f"/api/v1/drafts/{valid_draft['id']}/submit",
            headers=content_admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/drafts/{valid_draft['id']}/approve",
            json={},
            headers=tech_admin_headers,
        ).status_code
        == 200
    )
    pub_res = client.post(
        f"/api/v1/drafts/{valid_draft['id']}/publish",
        headers=tech_admin_headers,
    )
    assert pub_res.status_code == 200
    target_resource_id = pub_res.json()["entity_id"]

    # Published draft cannot be re-submitted
    assert (
        client.post(
            f"/api/v1/drafts/{valid_draft['id']}/submit",
            headers=content_admin_headers,
        ).status_code
        == 400
    )

    # Update draft attempting to overwrite disallowed fields (`id`, `availability_status`, `status_code`)
    spoofed_uuid = str(uuid.uuid4())
    update_draft = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "resource",
            "entity_id": target_resource_id,
            "title": "Update Valid Guide",
            "payload": {
                "id": spoofed_uuid,
                "availability_status": "broken",
                "status_code": 999,
                "title": "Binary Search Updated Title",
                "url": "https://cp-algorithms.com/",
            },
        },
        headers=content_admin_headers,
    ).json()
    assert (
        client.post(
            f"/api/v1/drafts/{update_draft['id']}/submit",
            headers=content_admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/drafts/{update_draft['id']}/approve",
            json={},
            headers=tech_admin_headers,
        ).status_code
        == 200
    )
    upd_pub = client.post(
        f"/api/v1/drafts/{update_draft['id']}/publish",
        headers=tech_admin_headers,
    )
    assert upd_pub.status_code == 200

    live_res = db_session.query(Resource).filter(Resource.id == uuid.UUID(target_resource_id)).first()
    assert live_res is not None
    assert str(live_res.id) == target_resource_id
    assert live_res.title == "Binary Search Updated Title"
    assert live_res.url == "https://cp-algorithms.com/"
    assert live_res.availability_status != AvailabilityStatus.BROKEN
    assert live_res.status_code is None
