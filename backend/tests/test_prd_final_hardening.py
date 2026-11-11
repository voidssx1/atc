import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.models.analytics import AuditLog, ResourceAnalytics
from app.models.resource import AvailabilityStatus, Resource, ResourceType
from app.models.roadmap import RoadmapTopic
from app.models.user import UserRole
from app.services.scheduler import JobRunnerService


def test_duplicate_canonical_resource_url_rejected_on_create_and_publish(
    client, db_session, tech_admin_headers, monkeypatch
):
    """Canonical URL duplicate prevention must reject active duplicates with 409 on direct create and draft publish."""
    from tests.test_drafts_and_workflow import _make_admin_headers

    monkeypatch.setattr(
        "app.api.v1.resources.check_url_availability",
        AsyncMock(return_value=(AvailabilityStatus.AVAILABLE, 200)),
    )

    _, ca_headers = _make_admin_headers(
        db_session,
        email="dup.ca@atc.bennett.edu.in",
        username="dupca",
        role=UserRole.CONTENT_ADMIN,
    )

    # 1. Create initial active resource
    r1 = client.post(
        "/api/v1/resources/",
        json={
            "title": "CP Algorithms",
            "url": "https://cp-algorithms.com/graph/breadth-first-search.html",
            "resource_type": "article",
        },
        headers=ca_headers,
    )
    assert r1.status_code == 201
    r1_id = r1.json()["id"]

    # 2. Direct create with canonical-equivalent URL (case/trailing slash) -> 409 Conflict
    r_dup = client.post(
        "/api/v1/resources/",
        json={
            "title": "CP Algorithms Duplicate",
            "url": "https://CP-ALGORITHMS.com/graph/breadth-first-search.html/",
            "resource_type": "article",
        },
        headers=ca_headers,
    )
    assert r_dup.status_code == 409

    # 3. Draft creation & publish targeting the same canonical URL -> 409 Conflict
    draft = client.post(
        "/api/v1/drafts/",
        json={
            "entity_type": "resource",
            "title": "Dup Resource Draft",
            "payload": {
                "title": "Dup Draft",
                "url": "https://cp-algorithms.com/graph/breadth-first-search.html/",
                "resource_type": "article",
            },
        },
        headers=ca_headers,
    ).json()
    assert (
        client.post(f"/api/v1/drafts/{draft['id']}/submit", headers=ca_headers).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/drafts/{draft['id']}/approve",
            json={},
            headers=tech_admin_headers,
        ).status_code
        == 200
    )
    pub_dup = client.post(
        f"/api/v1/drafts/{draft['id']}/publish", headers=tech_admin_headers
    )
    assert pub_dup.status_code == 409

    # 4. After archiving the original resource, recreating the URL is permitted
    assert (
        client.delete(f"/api/v1/resources/{r1_id}", headers=ca_headers).status_code
        == 200
    )
    r_recreate = client.post(
        "/api/v1/resources/",
        json={
            "title": "CP Algorithms Re-added",
            "url": "https://cp-algorithms.com/graph/breadth-first-search.html",
            "resource_type": "article",
        },
        headers=ca_headers,
    )
    assert r_recreate.status_code == 201


def test_tech_admin_cannot_self_demote_or_self_deactivate(
    client, tech_admin_user, tech_admin_headers
):
    """Prevent accidental self-lockout via role downgrade or status deactivation."""
    r_demote = client.patch(
        f"/api/v1/admin/users/{tech_admin_user.id}/role",
        json={"role": "viewer"},
        headers=tech_admin_headers,
    )
    assert r_demote.status_code == 400

    # Keeping role=tech_admin remains valid
    r_same = client.patch(
        f"/api/v1/admin/users/{tech_admin_user.id}/role",
        json={"role": "tech_admin"},
        headers=tech_admin_headers,
    )
    assert r_same.status_code == 200

    r_deact = client.patch(
        f"/api/v1/admin/users/{tech_admin_user.id}/status",
        json={"is_active": False},
        headers=tech_admin_headers,
    )
    assert r_deact.status_code == 400


def test_analytics_bounded_date_filters_and_reorder_audit(
    client, db_session, tech_admin_headers
):
    """Verify analytics date bounds (max 365 days, start<=end) and reorder audit log entry."""
    res = Resource(
        title="Bounded Analytics Link",
        url="https://example.com/bounded-analytics",
        resource_type=ResourceType.ARTICLE,
    )
    topic = RoadmapTopic(title="Audit Reorder Topic", slug="audit-reorder-topic", order_index=1)
    db_session.add_all([res, topic])
    db_session.flush()

    now = datetime.now(timezone.utc)
    old_click = ResourceAnalytics(
        resource_id=res.id, user_id=None, created_at=now - timedelta(days=20)
    )
    recent_click = ResourceAnalytics(
        resource_id=res.id, user_id=None, created_at=now - timedelta(days=1)
    )
    db_session.add_all([old_click, recent_click])
    db_session.commit()

    # Invalid range: start > end -> 400
    bad_order = client.get(
        "/api/v1/analytics/resources",
        params={
            "start_date": (now + timedelta(days=1)).isoformat(),
            "end_date": now.isoformat(),
        },
        headers=tech_admin_headers,
    )
    assert bad_order.status_code == 400

    # Oversized range > 365 days -> 400
    too_wide = client.get(
        "/api/v1/analytics/resources",
        params={
            "start_date": (now - timedelta(days=400)).isoformat(),
            "end_date": now.isoformat(),
        },
        headers=tech_admin_headers,
    )
    assert too_wide.status_code == 400

    # Recent 5-day window counts only the recent click while keeping resource row present
    window_res = client.get(
        "/api/v1/analytics/resources",
        params={
            "start_date": (now - timedelta(days=5)).isoformat(),
            "end_date": now.isoformat(),
        },
        headers=tech_admin_headers,
    )
    assert window_res.status_code == 200
    row = next(r for r in window_res.json() if r["resource_id"] == str(res.id))
    assert row["total_clicks"] == 1

    # Reorder emits AuditLog entry
    reord = client.post(
        "/api/v1/roadmap/reorder",
        json={"entity_type": "topic", "items": [{"id": str(topic.id), "order_index": 3}]},
        headers=tech_admin_headers,
    )
    assert reord.status_code == 200
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "roadmap_reordered")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert audit is not None
    assert audit.details["count"] == 1


def test_availability_scan_claims_timestamp_before_external_http_probe(db_session):
    """Ensure scan commits batch claim before slow HTTP probing so DB locks are not held across I/O."""
    res = Resource(
        title="Claim Order Resource",
        url="https://example.com/claim-order",
        resource_type=ResourceType.ARTICLE,
        last_checked_at=None,
    )
    db_session.add(res)
    db_session.commit()

    seen_pre_probe_claim = []

    async def inspect_during_probe(url: str):
        db_session.expire_all()
        cur = db_session.query(Resource).filter(Resource.id == res.id).first()
        seen_pre_probe_claim.append(cur.last_checked_at is not None)
        return AvailabilityStatus.AVAILABLE, 200

    with patch(
        "app.services.scheduler.check_url_availability",
        side_effect=inspect_during_probe,
    ):
        stats = asyncio.run(JobRunnerService.run_resource_availability_scan(db_session, limit=5))

    assert stats["total_checked"] == 1
    assert stats["available"] == 1
    assert seen_pre_probe_claim == [True]


def test_create_async_http_client_reuses_shared_ssl_context():
    """Repeated outbound HTTP client creation must reuse one SSLContext rather than reloading CA bundles."""
    from app.core import http as http_mod

    http_mod._SHARED_SSL_CONTEXT = None
    with patch("app.core.http.ssl.create_default_context") as mock_ssl:
        sentinel_ctx = object()
        mock_ssl.return_value = sentinel_ctx
        with patch("app.core.http.httpx.AsyncClient") as mock_client:
            http_mod.create_async_http_client(timeout=5.0)
            http_mod.create_async_http_client(timeout=5.0)
            http_mod.create_async_http_client(timeout=5.0)
        assert mock_ssl.call_count == 1
        assert mock_client.call_count == 3
        for call in mock_client.call_args_list:
            assert call.kwargs["verify"] is sentinel_ctx
    http_mod._SHARED_SSL_CONTEXT = None
