from datetime import datetime, timedelta, timezone

from app.core.security import generate_session_token, hash_password_sync, hash_session_token
from app.models.analytics import AuditLog
from app.models.user import StudentProfile, User, UserRole, UserSession


def _make_admin_headers(db_session, *, email: str, username: str, role: UserRole):
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


def test_content_draft_approval_workflow(client, db_session, tech_admin_headers):
    _, content_admin_headers = _make_admin_headers(
        db_session,
        email="author.workflow@atc.bennett.edu.in",
        username="authorwf",
        role=UserRole.CONTENT_ADMIN,
    )

    # 1. Content admin creates draft
    create_res = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "topic",
            "title": "Dynamic Programming",
            "payload": {
                "title": "Dynamic Programming Masterclass",
                "slug": "dynamic-programming",
                "description": "Memoization, tabulation and state optimization",
                "order_index": 5,
            },
        },
        headers=content_admin_headers,
    )
    assert create_res.status_code == 201
    draft_id = create_res.json()["id"]
    assert create_res.json()["status"] == "draft"

    # Approving before submit must fail with 400
    pre_submit_app = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={},
        headers=tech_admin_headers,
    )
    assert pre_submit_app.status_code == 400

    # 2. Submit draft
    submit_res = client.post(
        f"/api/v1/drafts/{draft_id}/submit", headers=content_admin_headers
    )
    assert submit_res.status_code == 200
    assert submit_res.json()["status"] == "pending_approval"

    # Publishing unapproved draft fails (HTTP 400)
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/publish", headers=tech_admin_headers
        ).status_code
        == 400
    )

    # 3. Independent tech_admin approves draft
    approve_res = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"feedback": "Looks comprehensive!"},
        headers=tech_admin_headers,
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "approved"

    # 4. Independent tech_admin publishes draft into live entity
    pub_res = client.post(
        f"/api/v1/drafts/{draft_id}/publish", headers=tech_admin_headers
    )
    assert pub_res.status_code == 200
    published_draft = pub_res.json()
    assert published_draft["status"] == "published"
    assert published_draft["entity_id"] is not None
    topic_id = published_draft["entity_id"]

    # Published draft cannot be archived, restored, updated, or re-approved
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/archive", headers=tech_admin_headers
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/v1/drafts/{draft_id}/restore", headers=tech_admin_headers
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/api/v1/drafts/{draft_id}",
            json={"title": "Mutate"},
            headers=tech_admin_headers,
        ).status_code
        == 400
    )

    # 5. Live entity exists and cannot be directly mutated outside revision workflow
    live_topic_res = client.get("/api/v1/roadmap/topics/dynamic-programming")
    assert live_topic_res.status_code == 200
    assert live_topic_res.json()["title"] == "Dynamic Programming Masterclass"
    assert (
        client.put(
            f"/api/v1/roadmap/topics/{topic_id}",
            json={"title": "Bypass"},
            headers=tech_admin_headers,
        ).status_code
        == 409
    )

    # 6. Revision draft updates published entity while preserving prior published revision snapshot
    rev_draft = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "topic",
            "entity_id": topic_id,
            "title": "Dynamic Programming v2",
            "payload": {"title": "Dynamic Programming Masterclass v2"},
        },
        headers=content_admin_headers,
    ).json()
    assert (
        client.post(
            f"/api/v1/drafts/{rev_draft['id']}/submit", headers=content_admin_headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/drafts/{rev_draft['id']}/approve",
            json={},
            headers=tech_admin_headers,
        ).status_code
        == 200
    )
    rev_pub = client.post(
        f"/api/v1/drafts/{rev_draft['id']}/publish", headers=tech_admin_headers
    )
    assert rev_pub.status_code == 200
    assert rev_pub.json()["version_number"] == 2

    history = client.get(
        f"/api/v1/drafts/?entity_id={topic_id}&status=published",
        headers=content_admin_headers,
    )
    assert history.status_code == 200
    assert [d["version_number"] for d in history.json()] == [2, 1]

    pub_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action == "draft_published",
            AuditLog.target_id == str(rev_draft["id"]),
        )
        .first()
    )
    assert pub_audit is not None
    assert pub_audit.details["previous_snapshot"]["title"] == "Dynamic Programming Masterclass"
    assert pub_audit.details["published_snapshot"]["title"] == "Dynamic Programming Masterclass v2"


def test_creator_cannot_self_approve_or_self_reject_draft(client, db_session, tech_admin_headers):
    """Even a TECH_ADMIN creator must not approve, reject, or publish their own draft."""
    _, other_tech_headers = _make_admin_headers(
        db_session,
        email="second.tech@atc.bennett.edu.in",
        username="secondtech",
        role=UserRole.TECH_ADMIN,
    )

    own_draft = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "topic",
            "title": "Self Approval Test",
            "payload": {"title": "Self Approval", "slug": "self-approval"},
        },
        headers=other_tech_headers,
    ).json()
    did = own_draft["id"]

    assert (
        client.post(f"/api/v1/drafts/{did}/submit", headers=other_tech_headers).status_code
        == 200
    )
    # Creator attempts self-approve and self-reject -> 403 Forbidden
    assert (
        client.post(
            f"/api/v1/drafts/{did}/approve", json={}, headers=other_tech_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/drafts/{did}/reject",
            json={"feedback": "Self reject"},
            headers=other_tech_headers,
        ).status_code
        == 403
    )

    # Independent tech_admin can approve, and creator still cannot self-publish
    assert (
        client.post(
            f"/api/v1/drafts/{did}/approve", json={}, headers=tech_admin_headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/drafts/{did}/publish", headers=other_tech_headers
        ).status_code
        == 403
    )
