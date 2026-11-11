import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.core.security import generate_session_token, hash_password_sync, hash_session_token
from app.models.resource import AvailabilityStatus, Resource, ResourceType
from app.models.roadmap import RoadmapSubtopic, RoadmapTopic
from app.models.user import StudentProfile, User, UserRole, UserSession


def _create_role_headers(db_session, *, email: str, username: str, role: UserRole):
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
    db_session.add(StudentProfile(user_id=user.id, full_name=f"{username} Name"))
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


def test_admin_data_export_csv_and_import_rbac(
    client, db_session, student_headers, tech_admin_headers
):
    _, content_admin_headers = _create_role_headers(
        db_session,
        email="ca.data@atc.bennett.edu.in",
        username="cadata",
        role=UserRole.CONTENT_ADMIN,
    )
    topic = RoadmapTopic(title="Export CSV Topic", slug="export-csv-topic", order_index=1)
    db_session.add(topic)
    db_session.flush()
    db_session.add(
        RoadmapSubtopic(
            topic_id=topic.id,
            title="Export CSV Sub",
            slug="export-csv-sub",
            order_index=1,
        )
    )
    db_session.commit()

    # Auth & RBAC on export/import
    assert client.get("/api/v1/admin/data/export/roadmap/csv").status_code == 401
    assert (
        client.get(
            "/api/v1/admin/data/export/roadmap/csv", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/admin/data/import/roadmap/json",
            json=[],
            headers=content_admin_headers,
        ).status_code
        == 403
    )

    csv_res = client.get(
        "/api/v1/admin/data/export/roadmap/csv", headers=content_admin_headers
    )
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers.get("content-type", "")
    assert "export-csv-topic" in csv_res.text
    assert "export-csv-sub" in csv_res.text

    # Authorized Tech Admin import
    imp = client.post(
        "/api/v1/admin/data/import/roadmap/json",
        json=[{"title": "Imported Topic", "slug": "imported-topic"}],
        headers=tech_admin_headers,
    )
    assert imp.status_code == 200
    assert imp.json()["imported_topics"] == 1


def test_admin_jobs_cleanup_availability_scan_and_test_email(
    client, db_session, student_headers, tech_admin_headers, student_user, monkeypatch
):
    _, content_admin_headers = _create_role_headers(
        db_session,
        email="ca.jobs@atc.bennett.edu.in",
        username="cajobs",
        role=UserRole.CONTENT_ADMIN,
    )

    # Unauthenticated / insufficient role checks
    for path, body in [
        ("/api/v1/admin/jobs/cleanup-expired-sessions", None),
        ("/api/v1/admin/jobs/run-availability-scan", None),
        ("/api/v1/admin/jobs/test-email", {"to_email": "user@bennett.edu.in"}),
    ]:
        kwargs = {"json": body} if body is not None else {}
        assert client.post(path, **kwargs).status_code == 401
        assert client.post(path, headers=student_headers, **kwargs).status_code == 403
        assert (
            client.post(path, headers=content_admin_headers, **kwargs).status_code
            == 403
        )

    # Seed expired session + active resource
    db_session.add(
        UserSession(
            user_id=student_user.id,
            token=hash_session_token(generate_session_token(48)),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            is_revoked=False,
        )
    )
    db_session.add(
        Resource(
            title="Job Scan Resource",
            url="https://example.com/job-scan",
            resource_type=ResourceType.ARTICLE,
        )
    )
    db_session.commit()

    clean_res = client.post(
        "/api/v1/admin/jobs/cleanup-expired-sessions", headers=tech_admin_headers
    )
    assert clean_res.status_code == 200
    assert clean_res.json()["deleted_count"] >= 1

    monkeypatch.setattr(
        "app.services.scheduler.check_url_availability",
        AsyncMock(return_value=(AvailabilityStatus.AVAILABLE, 200)),
    )
    scan_res = client.post(
        "/api/v1/admin/jobs/run-availability-scan?limit=10",
        headers=tech_admin_headers,
    )
    assert scan_res.status_code == 200
    assert scan_res.json()["results"]["total_checked"] >= 1

    # Invalid email payload -> 422
    assert (
        client.post(
            "/api/v1/admin/jobs/test-email",
            json={"to_email": "not-an-email"},
            headers=tech_admin_headers,
        ).status_code
        == 422
    )

    # Valid email dispatch (isolated dev mock mode) -> 200
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "RESEND_API_KEY", None)
    monkeypatch.setattr(settings, "BREVO_API_KEY", None)
    ok_mail = client.post(
        "/api/v1/admin/jobs/test-email",
        json={"to_email": "student@bennett.edu.in"},
        headers=tech_admin_headers,
    )
    assert ok_mail.status_code == 200

    # Simulated dispatch failure -> 500
    monkeypatch.setattr(
        "app.api.v1.admin_jobs.email_service.send_email",
        AsyncMock(return_value=False),
    )
    fail_mail = client.post(
        "/api/v1/admin/jobs/test-email",
        json={"to_email": "student@bennett.edu.in"},
        headers=tech_admin_headers,
    )
    assert fail_mail.status_code == 500


def test_admin_users_detail_and_delete(
    client, db_session, student_headers, tech_admin_user, tech_admin_headers
):
    target, _ = _create_role_headers(
        db_session,
        email="target.user@bennett.edu.in",
        username="targetuser",
        role=UserRole.STUDENT,
    )
    _, content_admin_headers = _create_role_headers(
        db_session,
        email="ca.users@atc.bennett.edu.in",
        username="causers",
        role=UserRole.CONTENT_ADMIN,
    )
    missing_id = uuid.uuid4()

    # GET /admin/users/{user_id}
    assert client.get(f"/api/v1/admin/users/{target.id}").status_code == 401
    assert (
        client.get(
            f"/api/v1/admin/users/{target.id}", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/admin/users/{missing_id}", headers=tech_admin_headers
        ).status_code
        == 404
    )
    detail = client.get(
        f"/api/v1/admin/users/{target.id}", headers=content_admin_headers
    )
    assert detail.status_code == 200
    assert detail.json()["email"] == target.email

    # DELETE /admin/users/{user_id}
    assert client.delete(f"/api/v1/admin/users/{target.id}").status_code == 401
    assert (
        client.delete(
            f"/api/v1/admin/users/{target.id}", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/admin/users/{target.id}", headers=content_admin_headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/admin/users/{tech_admin_user.id}", headers=tech_admin_headers
        ).status_code
        == 400
    )
    assert (
        client.delete(
            f"/api/v1/admin/users/{missing_id}", headers=tech_admin_headers
        ).status_code
        == 404
    )
    del_ok = client.delete(
        f"/api/v1/admin/users/{target.id}", headers=tech_admin_headers
    )
    assert del_ok.status_code == 200


def test_bookmarks_and_progress_full_crud(
    client, db_session, student_headers
):
    topic = RoadmapTopic(title="Prog Topic", slug="prog-topic", order_index=1)
    db_session.add(topic)
    db_session.flush()
    sub = RoadmapSubtopic(
        topic_id=topic.id, title="Prog Sub", slug="prog-sub", order_index=1
    )
    db_session.add(sub)
    res = Resource(
        title="Bookmark Target",
        url="https://example.com/bm-target",
        resource_type=ResourceType.ARTICLE,
    )
    db_session.add(res)
    db_session.commit()
    db_session.refresh(sub)
    db_session.refresh(res)
    missing_id = uuid.uuid4()

    # Bookmarks unauth + 404 + GET + DELETE
    assert client.get("/api/v1/bookmarks/").status_code == 401
    assert client.delete(f"/api/v1/bookmarks/{res.id}").status_code == 401
    assert (
        client.post(
            "/api/v1/bookmarks/",
            json={"resource_id": str(missing_id)},
            headers=student_headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/bookmarks/",
            json={"resource_id": str(res.id), "notes": "Save me"},
            headers=student_headers,
        ).status_code
        == 201
    )
    bm_list = client.get("/api/v1/bookmarks/", headers=student_headers)
    assert bm_list.status_code == 200
    assert any(b["resource_id"] == str(res.id) for b in bm_list.json())

    assert (
        client.delete(
            f"/api/v1/bookmarks/{missing_id}", headers=student_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/bookmarks/{res.id}", headers=student_headers
        ).status_code
        == 200
    )
    bm_after = client.get("/api/v1/bookmarks/", headers=student_headers)
    assert all(b["resource_id"] != str(res.id) for b in bm_after.json())

    # Progress unauth + 404 + GET/ + GET/{id} + DELETE/{id}
    assert client.get("/api/v1/progress/").status_code == 401
    assert client.get(f"/api/v1/progress/{sub.id}").status_code == 401
    assert client.delete(f"/api/v1/progress/{sub.id}").status_code == 401
    assert (
        client.get(
            f"/api/v1/progress/{missing_id}", headers=student_headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/progress/{missing_id}",
            json={"status": "completed"},
            headers=student_headers,
        ).status_code
        == 404
    )

    assert (
        client.post(
            f"/api/v1/progress/{sub.id}",
            json={"status": "in_progress", "notes": "Halfway"},
            headers=student_headers,
        ).status_code
        == 200
    )
    prog_all = client.get("/api/v1/progress/", headers=student_headers)
    assert prog_all.status_code == 200
    assert any(p["subtopic_id"] == str(sub.id) for p in prog_all.json())

    prog_one = client.get(f"/api/v1/progress/{sub.id}", headers=student_headers)
    assert prog_one.status_code == 200
    assert prog_one.json()["status"] == "in_progress"

    assert (
        client.delete(f"/api/v1/progress/{sub.id}", headers=student_headers).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/progress/{sub.id}", headers=student_headers).status_code
        == 404
    )


def test_drafts_detail_update_reject_archive_and_restore(
    client, db_session, student_headers, tech_admin_headers
):
    _, content_admin_headers = _create_role_headers(
        db_session,
        email="ca.draft@atc.bennett.edu.in",
        username="cadraft",
        role=UserRole.CONTENT_ADMIN,
    )
    missing_id = uuid.uuid4()

    created = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "topic",
            "title": "Draft v1",
            "payload": {"title": "Draft v1", "slug": "draft-v1"},
        },
        headers=content_admin_headers,
    ).json()
    draft_id = created["id"]

    # GET /drafts/{id}
    assert client.get(f"/api/v1/drafts/{draft_id}").status_code == 401
    assert (
        client.get(
            f"/api/v1/drafts/{draft_id}", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/drafts/{missing_id}", headers=content_admin_headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/drafts/{draft_id}", headers=content_admin_headers
        ).status_code
        == 200
    )

    # PUT /drafts/{id}
    assert (
        client.put(f"/api/v1/drafts/{draft_id}", json={"title": "x"}).status_code
        == 401
    )
    assert (
        client.put(
            f"/api/v1/drafts/{draft_id}",
            json={"title": "x"},
            headers=student_headers,
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/drafts/{missing_id}",
            json={"title": "x"},
            headers=content_admin_headers,
        ).status_code
        == 404
    )
    upd = client.put(
        f"/api/v1/drafts/{draft_id}",
        json={
            "title": "Draft v2",
            "payload": {"title": "Draft v2", "slug": "draft-v1"},
        },
        headers=content_admin_headers,
    )
    assert upd.status_code == 200
    assert upd.json()["version_number"] == 2

    # Submit then Reject (Tech Admin only)
    client.post(f"/api/v1/drafts/{draft_id}/submit", headers=content_admin_headers)
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/reject",
            json={"feedback": "No"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/reject",
            json={"feedback": "No"},
            headers=student_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/reject",
            json={"feedback": "No"},
            headers=content_admin_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/drafts/{missing_id}/reject",
            json={"feedback": "Missing"},
            headers=tech_admin_headers,
        ).status_code
        == 404
    )
    rej = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"feedback": "Needs more examples"},
        headers=tech_admin_headers,
    )
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"
    assert rej.json()["review_feedback"] == "Needs more examples"

    # Archive & Restore
    assert (
        client.post(f"/api/v1/drafts/{draft_id}/archive").status_code == 401
    )
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/archive", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/drafts/{missing_id}/archive", headers=content_admin_headers
        ).status_code
        == 404
    )
    arch = client.post(
        f"/api/v1/drafts/{draft_id}/archive", headers=content_admin_headers
    )
    assert arch.status_code == 200
    assert arch.json()["is_archived"] is True
    assert arch.json()["status"] == "archived"

    # Editing an archived draft fails cleanly with 400
    assert (
        client.put(
            f"/api/v1/drafts/{draft_id}",
            json={"title": "Cannot edit"},
            headers=content_admin_headers,
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/reject",
            json={"feedback": "Too late"},
            headers=tech_admin_headers,
        ).status_code
        == 400
    )

    assert client.post(f"/api/v1/drafts/{draft_id}/restore").status_code == 401
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/restore", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/drafts/{missing_id}/restore", headers=content_admin_headers
        ).status_code
        == 404
    )
    restored = client.post(
        f"/api/v1/drafts/{draft_id}/restore", headers=content_admin_headers
    )
    assert restored.status_code == 200
    assert restored.json()["is_archived"] is False
    assert restored.json()["status"] == "draft"


def test_resources_put_delete_and_availability_checks(
    client, db_session, student_headers, tech_admin_headers, monkeypatch
):
    _, content_admin_headers = _create_role_headers(
        db_session,
        email="ca.res2@atc.bennett.edu.in",
        username="cares2",
        role=UserRole.CONTENT_ADMIN,
    )
    monkeypatch.setattr(
        "app.api.v1.resources.check_url_availability",
        AsyncMock(return_value=(AvailabilityStatus.AVAILABLE, 200)),
    )
    monkeypatch.setattr(
        "app.services.scheduler.check_url_availability",
        AsyncMock(return_value=(AvailabilityStatus.AVAILABLE, 200)),
    )

    res_obj = client.post(
        "/api/v1/resources/",
        json={
            "title": "Initial Resource",
            "url": "https://example.com/initial",
            "resource_type": "article",
        },
        headers=content_admin_headers,
    ).json()
    res_id = res_obj["id"]
    missing_id = uuid.uuid4()

    # PUT /resources/{id} enforces immutability on active published resources
    assert client.put(f"/api/v1/resources/{res_id}", json={"title": "x"}).status_code == 401
    assert (
        client.put(
            f"/api/v1/resources/{res_id}",
            json={"title": "x"},
            headers=student_headers,
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/resources/{missing_id}",
            json={"title": "x"},
            headers=content_admin_headers,
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/v1/resources/{res_id}",
            json={"title": "Bypass Direct Edit"},
            headers=content_admin_headers,
        ).status_code
        == 409
    )

    # Single availability check
    assert (
        client.post(f"/api/v1/resources/{res_id}/check-availability").status_code
        == 401
    )
    assert (
        client.post(
            f"/api/v1/resources/{res_id}/check-availability",
            headers=student_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/resources/{missing_id}/check-availability",
            headers=content_admin_headers,
        ).status_code
        == 404
    )
    chk_one = client.post(
        f"/api/v1/resources/{res_id}/check-availability",
        headers=content_admin_headers,
    )
    assert chk_one.status_code == 200
    assert chk_one.json()["availability_status"] == "available"

    # Bulk availability check
    assert client.post("/api/v1/resources/check-all-availability").status_code == 401
    assert (
        client.post(
            "/api/v1/resources/check-all-availability", headers=student_headers
        ).status_code
        == 403
    )
    chk_all = client.post(
        "/api/v1/resources/check-all-availability?limit=10",
        headers=content_admin_headers,
    )
    assert chk_all.status_code == 200
    assert chk_all.json()["summary"]["total_checked"] >= 1

    # DELETE /resources/{id} (soft archive, update archived metadata, then tech_admin hard delete)
    assert client.delete(f"/api/v1/resources/{res_id}").status_code == 401
    assert (
        client.delete(
            f"/api/v1/resources/{res_id}", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/resources/{missing_id}", headers=content_admin_headers
        ).status_code
        == 404
    )
    # Hard deleting active resource is blocked (403 for content_admin, 409 for tech_admin)
    assert (
        client.delete(
            f"/api/v1/resources/{res_id}?hard_delete=true",
            headers=content_admin_headers,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/resources/{res_id}?hard_delete=true",
            headers=tech_admin_headers,
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"/api/v1/resources/{res_id}", headers=content_admin_headers
        ).status_code
        == 200
    )
    assert client.get(f"/api/v1/resources/{res_id}").status_code == 404

    # Updating archived resource validates URLs and succeeds
    assert (
        client.put(
            f"/api/v1/resources/{res_id}",
            json={"url": "invalid-url"},
            headers=content_admin_headers,
        ).status_code
        == 400
    )
    upd = client.put(
        f"/api/v1/resources/{res_id}",
        json={"title": "Updated Resource", "url": "https://example.com/updated"},
        headers=content_admin_headers,
    )
    assert upd.status_code == 200
    assert upd.json()["title"] == "Updated Resource"

    assert (
        client.delete(
            f"/api/v1/resources/{res_id}?hard_delete=true",
            headers=tech_admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/resources/{res_id}?include_archived=true",
            headers=content_admin_headers,
        ).status_code
        == 404
    )


def test_roadmap_topic_subtopic_put_delete_and_reorder(
    client, db_session, student_headers
):
    _, content_admin_headers = _create_role_headers(
        db_session,
        email="ca.rm@atc.bennett.edu.in",
        username="carm",
        role=UserRole.CONTENT_ADMIN,
    )
    missing_id = uuid.uuid4()

    # Staging (unpublished) items support direct CRUD; published items are immutable (409)
    t1 = client.post(
        "/api/v1/roadmap/topics",
        json={"title": "Topic One", "slug": "topic-one", "order_index": 1, "is_published": False},
        headers=content_admin_headers,
    ).json()
    t2 = client.post(
        "/api/v1/roadmap/topics",
        json={"title": "Topic Two", "slug": "topic-two", "order_index": 2, "is_published": False},
        headers=content_admin_headers,
    ).json()
    t_pub = client.post(
        "/api/v1/roadmap/topics",
        json={"title": "Published Topic", "slug": "published-topic-imm", "order_index": 3, "is_published": True},
        headers=content_admin_headers,
    ).json()
    s1 = client.post(
        "/api/v1/roadmap/subtopics",
        json={
            "topic_id": t1["id"],
            "title": "Sub One",
            "slug": "sub-one",
            "order_index": 1,
            "is_published": False,
        },
        headers=content_admin_headers,
    ).json()
    s_pub = client.post(
        "/api/v1/roadmap/subtopics",
        json={
            "topic_id": t_pub["id"],
            "title": "Published Sub",
            "slug": "published-sub-imm",
            "order_index": 1,
            "is_published": True,
        },
        headers=content_admin_headers,
    ).json()

    # Published topic/subtopic immutability guards
    assert (
        client.put(
            f"/api/v1/roadmap/topics/{t_pub['id']}",
            json={"title": "Bypass"},
            headers=content_admin_headers,
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"/api/v1/roadmap/topics/{t_pub['id']}",
            headers=content_admin_headers,
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/v1/roadmap/subtopics/{s_pub['id']}",
            json={"title": "Bypass"},
            headers=content_admin_headers,
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"/api/v1/roadmap/subtopics/{s_pub['id']}",
            headers=content_admin_headers,
        ).status_code
        == 409
    )

    # PUT /topics/{id}
    assert client.put(f"/api/v1/roadmap/topics/{t1['id']}", json={"title": "X"}).status_code == 401
    assert (
        client.put(
            f"/api/v1/roadmap/topics/{t1['id']}",
            json={"title": "X"},
            headers=student_headers,
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/roadmap/topics/{missing_id}",
            json={"title": "X"},
            headers=content_admin_headers,
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/v1/roadmap/topics/{t1['id']}",
            json={"slug": "topic-two"},
            headers=content_admin_headers,
        ).status_code
        == 400
    )
    upd_t = client.put(
        f"/api/v1/roadmap/topics/{t1['id']}",
        json={"title": "Topic One Updated"},
        headers=content_admin_headers,
    )
    assert upd_t.status_code == 200
    assert upd_t.json()["title"] == "Topic One Updated"

    # PUT /subtopics/{id}
    assert (
        client.put(f"/api/v1/roadmap/subtopics/{s1['id']}", json={"title": "Y"}).status_code
        == 401
    )
    assert (
        client.put(
            f"/api/v1/roadmap/subtopics/{s1['id']}",
            json={"title": "Y"},
            headers=student_headers,
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/roadmap/subtopics/{missing_id}",
            json={"title": "Y"},
            headers=content_admin_headers,
        ).status_code
        == 404
    )
    upd_s = client.put(
        f"/api/v1/roadmap/subtopics/{s1['id']}",
        json={"title": "Sub One Updated", "difficulty": "hard"},
        headers=content_admin_headers,
    )
    assert upd_s.status_code == 200
    assert upd_s.json()["title"] == "Sub One Updated"

    # POST /roadmap/reorder
    assert (
        client.post(
            "/api/v1/roadmap/reorder",
            json={"entity_type": "topic", "items": []},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/roadmap/reorder",
            json={"entity_type": "topic", "items": []},
            headers=student_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/roadmap/reorder",
            json={"entity_type": "invalid", "items": []},
            headers=content_admin_headers,
        ).status_code
        == 400
    )
    reorder_topics = client.post(
        "/api/v1/roadmap/reorder",
        json={
            "entity_type": "topic",
            "items": [
                {"id": t1["id"], "order_index": 20},
                {"id": t2["id"], "order_index": 10},
            ],
        },
        headers=content_admin_headers,
    )
    assert reorder_topics.status_code == 200
    reorder_subs = client.post(
        "/api/v1/roadmap/reorder",
        json={
            "entity_type": "subtopic",
            "items": [{"id": s1["id"], "order_index": 99}],
        },
        headers=content_admin_headers,
    )
    assert reorder_subs.status_code == 200

    # DELETE /subtopics/{id} & DELETE /topics/{id}
    assert client.delete(f"/api/v1/roadmap/subtopics/{s1['id']}").status_code == 401
    assert (
        client.delete(
            f"/api/v1/roadmap/subtopics/{s1['id']}", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/roadmap/subtopics/{missing_id}", headers=content_admin_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/roadmap/subtopics/{s1['id']}", headers=content_admin_headers
        ).status_code
        == 200
    )

    assert client.delete(f"/api/v1/roadmap/topics/{t1['id']}").status_code == 401
    assert (
        client.delete(
            f"/api/v1/roadmap/topics/{t1['id']}", headers=student_headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/roadmap/topics/{missing_id}", headers=content_admin_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/roadmap/topics/{t1['id']}", headers=content_admin_headers
        ).status_code
        == 200
    )
