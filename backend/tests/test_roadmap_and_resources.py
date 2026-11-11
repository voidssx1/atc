def test_roadmap_hierarchy_and_reorder(client, tech_admin_headers):
    # 1. Create Topic
    topic_res = client.post(
        "/api/v1/roadmap/topics",
        json={
            "title": "Arrays & Strings",
            "slug": "arrays-and-strings",
            "description": "Fundamental array operations and two-pointer techniques",
            "order_index": 1,
        },
        headers=tech_admin_headers,
    )
    assert topic_res.status_code == 201
    topic = topic_res.json()
    topic_id = topic["id"]

    # 2. Create Parent Subtopic
    sub_res = client.post(
        "/api/v1/roadmap/subtopics",
        json={
            "topic_id": topic_id,
            "title": "Two Pointer Technique",
            "slug": "two-pointer-technique",
            "description": "Opposite ends and fast/slow pointer methods",
            "order_index": 1,
            "difficulty": "medium",
        },
        headers=tech_admin_headers,
    )
    assert sub_res.status_code == 201
    parent_sub = sub_res.json()

    # 3. Create Nested Child Subtopic
    child_sub_res = client.post(
        "/api/v1/roadmap/subtopics",
        json={
            "topic_id": topic_id,
            "parent_subtopic_id": parent_sub["id"],
            "title": "Fast and Slow Pointers",
            "slug": "fast-slow-pointers",
            "description": "Cycle detection and middle element lookup",
            "order_index": 1,
            "difficulty": "medium",
        },
        headers=tech_admin_headers,
    )
    assert child_sub_res.status_code == 201
    child_sub = child_sub_res.json()
    assert child_sub["parent_subtopic_id"] == parent_sub["id"]

    # 4. Fetch roadmap topics and verify nested tree
    roadmap_res = client.get("/api/v1/roadmap/topics")
    assert roadmap_res.status_code == 200
    topics_list = roadmap_res.json()
    current_topic = next((t for t in topics_list if t["id"] == topic_id), None)
    assert current_topic is not None
    assert len(current_topic["subtopics"]) >= 1
    # Check parent subtopic has child in children list
    parent_in_tree = next(
        (s for s in current_topic["subtopics"] if s["id"] == parent_sub["id"]),
        None,
    )
    assert parent_in_tree is not None
    assert len(parent_in_tree["children"]) >= 1
    assert parent_in_tree["children"][0]["id"] == child_sub["id"]


def test_resource_creation_and_url_validation(client, tech_admin_headers):
    # Invalid URL format should fail
    bad_res = client.post(
        "/api/v1/resources/",
        json={
            "title": "Invalid URL Problem",
            "url": "not-a-valid-url",
            "resource_type": "article",
        },
        headers=tech_admin_headers,
    )
    assert bad_res.status_code == 400

    # Valid resource creation
    valid_res = client.post(
        "/api/v1/resources/",
        json={
            "title": "FastAPI Documentation",
            "url": "https://fastapi.tiangolo.com",
            "resource_type": "article",
            "difficulty": "beginner",
        },
        headers=tech_admin_headers,
    )
    assert valid_res.status_code == 201
    resource_data = valid_res.json()
    assert resource_data["title"] == "FastAPI Documentation"
    assert "availability_status" in resource_data

    # Check resource list
    list_res = client.get("/api/v1/resources/")
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1
