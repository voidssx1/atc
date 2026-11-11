def test_student_progress_and_dashboard(
    client, student_headers, tech_admin_headers
):
    # 1. Create Topic and Subtopic
    topic_res = client.post(
        "/api/v1/roadmap/topics",
        json={"title": "Trees & Graphs", "slug": "trees-graphs", "order_index": 2},
        headers=tech_admin_headers,
    )
    topic_id = topic_res.json()["id"]

    sub_res = client.post(
        "/api/v1/roadmap/subtopics",
        json={
            "topic_id": topic_id,
            "title": "Binary Search Trees",
            "slug": "bst",
            "difficulty": "medium",
        },
        headers=tech_admin_headers,
    )
    subtopic_id = sub_res.json()["id"]

    # 2. Mark progress as completed
    prog_res = client.post(
        f"/api/v1/progress/{subtopic_id}",
        json={"status": "completed", "notes": "Solved 10 LeetCode problems"},
        headers=student_headers,
    )
    assert prog_res.status_code == 200
    assert prog_res.json()["status"] == "completed"

    # 3. Create Resource & Bookmark it
    res_res = client.post(
        "/api/v1/resources/",
        json={
            "title": "BST Visualizer",
            "url": "https://visualgo.net/en/bst",
            "subtopic_id": subtopic_id,
        },
        headers=tech_admin_headers,
    )
    resource_id = res_res.json()["id"]

    bm_res = client.post(
        "/api/v1/bookmarks/",
        json={"resource_id": resource_id, "notes": "Great interactive visualizer"},
        headers=student_headers,
    )
    assert bm_res.status_code == 201

    # 4. Check Student Dashboard
    dash_res = client.get("/api/v1/dashboard/student", headers=student_headers)
    assert dash_res.status_code == 200
    dash_data = dash_res.json()
    assert dash_data["completed_subtopics"] >= 1
    assert dash_data["total_bookmarks"] >= 1

    # 5. Check Admin Dashboard
    admin_dash_res = client.get(
        "/api/v1/dashboard/admin", headers=tech_admin_headers
    )
    assert admin_dash_res.status_code == 200
    admin_data = admin_dash_res.json()
    assert admin_data["total_students"] >= 1
    assert admin_data["total_topics"] >= 1
