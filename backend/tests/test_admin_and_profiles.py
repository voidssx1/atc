def test_admin_user_management(client, tech_admin_headers):
    # 1. Create a content admin
    create_res = client.post(
        "/api/v1/admin/users/",
        json={
            "email": "contentadmin@atc.bennett.edu.in",
            "password": "Password123!",
            "username": "contentadmin",
            "role": "content_admin",
        },
        headers=tech_admin_headers,
    )
    assert create_res.status_code == 201
    created_user = create_res.json()
    assert created_user["role"] == "content_admin"
    user_id = created_user["id"]

    # 2. List users
    list_res = client.get("/api/v1/admin/users/", headers=tech_admin_headers)
    assert list_res.status_code == 200
    users = list_res.json()
    assert any(u["id"] == user_id for u in users)

    # 3. Update role to viewer
    role_res = client.patch(
        f"/api/v1/admin/users/{user_id}/role",
        json={"role": "viewer"},
        headers=tech_admin_headers,
    )
    assert role_res.status_code == 200
    assert role_res.json()["role"] == "viewer"

    # 4. Deactivate user
    status_res = client.patch(
        f"/api/v1/admin/users/{user_id}/status",
        json={"is_active": False},
        headers=tech_admin_headers,
    )
    assert status_res.status_code == 200
    assert status_res.json()["is_active"] is False


def test_student_profile_management(client, student_headers):
    # Get profile
    me_res = client.get("/api/v1/profile/me", headers=student_headers)
    assert me_res.status_code == 200
    profile = me_res.json()
    assert profile["full_name"] == "Student Tester"
    assert profile["email"] == "student@bennett.edu.in"

    # Update profile (only full_name is supported)
    update_res = client.put(
        "/api/v1/profile/me",
        json={"full_name": "Updated Student Name"},
        headers=student_headers,
    )
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["full_name"] == "Updated Student Name"
    assert updated["email"] == "student@bennett.edu.in"
