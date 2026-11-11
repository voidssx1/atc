def test_analytics_and_bulk_export_import(
    client, tech_admin_headers
):
    # 1. Create a resource
    res_res = client.post(
        "/api/v1/resources/",
        json={
            "title": "Algorithms Book",
            "url": "https://algs4.cs.princeton.edu",
            "resource_type": "book",
        },
        headers=tech_admin_headers,
    )
    resource_id = res_res.json()["id"]

    # 2. Track clicks by same user
    client.post(
        "/api/v1/analytics/click",
        json={"resource_id": resource_id},
        headers=tech_admin_headers,
    )
    # Repeated click by the same user
    event_res = client.post(
        "/api/v1/analytics/click",
        json={"resource_id": resource_id},
        headers=tech_admin_headers,
    )
    assert event_res.status_code == 201

    # 3. Check analytics summary (total_clicks increases, unique_students remains 1)
    summary_res = client.get(
        "/api/v1/analytics/resources", headers=tech_admin_headers
    )
    assert summary_res.status_code == 200
    summaries = summary_res.json()
    matched = next((s for s in summaries if s["resource_id"] == resource_id), None)
    assert matched is not None
    assert matched["total_clicks"] >= 2
    assert matched["unique_students"] == 1

    # 4. Check audit logs
    audit_res = client.get(
        "/api/v1/admin/audit-logs", headers=tech_admin_headers
    )
    assert audit_res.status_code == 200
    assert len(audit_res.json()) >= 1

    # 5. Export Roadmap JSON
    export_res = client.get(
        "/api/v1/admin/data/export/roadmap/json", headers=tech_admin_headers
    )
    assert export_res.status_code == 200
    assert "roadmap" in export_res.json()

    # 6. Export Resources CSV
    csv_res = client.get(
        "/api/v1/admin/data/export/resources/csv", headers=tech_admin_headers
    )
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers.get("content-type", "")

    # 7. Import Resources CSV
    sample_csv = "title,url,resource_type,difficulty\nGraph Theory Guide,https://mathworld.wolfram.com/Graph.html,article,medium\n"
    import_res = client.post(
        "/api/v1/admin/data/import/resources/csv",
        json={"csv_content": sample_csv},
        headers=tech_admin_headers,
    )
    assert import_res.status_code == 200
    assert import_res.json()["imported_count"] == 1
