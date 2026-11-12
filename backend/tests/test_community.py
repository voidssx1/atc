from app.models.community import CommunityItem, CommunityKind


def test_public_community_feed_only_returns_published_items(client, db_session):
    hidden = CommunityItem(kind=CommunityKind.EVENT, title="Hidden event", slug="hidden-event", is_published=False)
    visible = CommunityItem(kind=CommunityKind.EVENT, title="Public event", slug="public-event", is_published=True)
    db_session.add_all([hidden, visible])
    db_session.commit()
    response = client.get("/api/v1/community/events")
    assert response.status_code == 200
    assert [item["slug"] for item in response.json()] == ["public-event"]


def test_community_content_requires_admin_and_can_be_published(client, tech_admin_headers):
    payload = {"kind":"projects","title":"ATC Open Source","slug":"atc-open-source","description":"A community project","is_published":False}
    assert client.post("/api/v1/community/", json=payload).status_code == 401
    created = client.post("/api/v1/community/", json=payload, headers=tech_admin_headers)
    assert created.status_code == 201
    item_id = created.json()["id"]
    assert client.get("/api/v1/community/projects").json() == []
    published = client.patch(f"/api/v1/community/{item_id}", json={"is_published":True}, headers=tech_admin_headers)
    assert published.status_code == 200
    assert published.json()["is_published"] is True
    assert [x["slug"] for x in client.get("/api/v1/community/projects").json()] == ["atc-open-source"]


def test_community_content_cannot_be_created_by_students(client, student_headers):
    response = client.post("/api/v1/community/", json={"kind":"gallery","title":"Photo","slug":"photo"}, headers=student_headers)
    assert response.status_code == 403


def test_authenticated_user_endpoint_returns_role(client, tech_admin_headers):
    response = client.get("/api/v1/auth/me", headers=tech_admin_headers)
    assert response.status_code == 200
    assert response.json()["role"] == "tech_admin"
