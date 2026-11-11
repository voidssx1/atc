def test_content_draft_approval_workflow(client, tech_admin_headers):
    # 1. Create content draft
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
        headers=tech_admin_headers,
    )
    assert create_res.status_code == 201
    draft = create_res.json()
    draft_id = draft["id"]
    assert draft["status"] == "draft"

    # 2. Submit draft
    submit_res = client.post(
        f"/api/v1/drafts/{draft_id}/submit", headers=tech_admin_headers
    )
    assert submit_res.status_code == 200
    assert submit_res.json()["status"] == "pending_approval"

    # Attempt to publish unapproved draft should be blocked (HTTP 400)
    unapproved_pub_res = client.post(
        f"/api/v1/drafts/{draft_id}/publish", headers=tech_admin_headers
    )
    assert unapproved_pub_res.status_code == 400

    # 3. Approve draft
    approve_res = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"feedback": "Looks comprehensive!"},
        headers=tech_admin_headers,
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "approved"

    # 4. Publish draft directly into live entity
    pub_res = client.post(
        f"/api/v1/drafts/{draft_id}/publish", headers=tech_admin_headers
    )
    assert pub_res.status_code == 200
    published_draft = pub_res.json()
    assert published_draft["status"] == "published"
    assert published_draft["entity_id"] is not None

    # 5. Verify the live entity exists in topics
    live_topic_res = client.get("/api/v1/roadmap/topics/dynamic-programming")
    assert live_topic_res.status_code == 200
    assert live_topic_res.json()["title"] == "Dynamic Programming Masterclass"
