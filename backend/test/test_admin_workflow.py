"""
Admin workflow tests — user management, departments, jobs.
"""
import pytest


@pytest.mark.admin
def test_create_hr_user(client, admin_headers):
    resp = client.post("/api/v1/admin/users/hr", headers=admin_headers, json={
        "email": "hr_new@test.com",
        "password": "Pass@1234",
        "first_name": "HR",
        "last_name": "Person",
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data.get("role") == "hr" or data.get("email") == "hr_new@test.com"


@pytest.mark.admin
def test_create_interviewer_user(client, admin_headers):
    resp = client.post("/api/v1/admin/users/interviewer", headers=admin_headers, json={
        "email": "interviewer_new@test.com",
        "password": "Pass@1234",
        "first_name": "Int",
        "last_name": "Viewer",
    })
    assert resp.status_code in (200, 201)


@pytest.mark.admin
def test_create_department(client, admin_headers):
    resp = client.post("/api/v1/admin/departments", headers=admin_headers, json={
        "name": "Engineering",
        "description": "Software team",
    })
    assert resp.status_code in (200, 201)
    assert resp.json().get("name") == "Engineering"


@pytest.mark.admin
def test_list_users(client, admin_headers):
    resp = client.get("/api/v1/admin/users", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    # Accept list or paginated wrapper
    users = body if isinstance(body, list) else body.get("users", body.get("data", []))
    assert isinstance(users, list)


@pytest.mark.admin
def test_toggle_user_active(client, admin_headers, db):
    from src.models.models import User
    # Create a user to toggle
    client.post("/api/v1/auth/register", json={
        "email": "toggle_user@test.com",
        "password": "Pass@1234",
        "role": "candidate",
        "first_name": "T",
        "last_name": "U",
    })
    user = db.query(User).filter(User.email == "toggle_user@test.com").first()
    original_status = user.is_active

    # Endpoint takes is_active as a query parameter (not a JSON body)
    resp = client.put(
        f"/api/v1/admin/users/{user.id}/status",
        headers=admin_headers,
        params={"is_active": str(not original_status).lower()},
    )
    assert resp.status_code in (200, 204)

    db.refresh(user)
    assert user.is_active != original_status


@pytest.mark.admin
def test_non_admin_cannot_create_department(client, hr_headers):
    resp = client.post("/api/v1/admin/departments", headers=hr_headers, json={
        "name": "Unauthorized Dept",
    })
    assert resp.status_code in (401, 403)
