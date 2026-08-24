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


# ── Department logos ────────────────────────────────────────────────────────
# Logos used to be base64 blobs in the uploading admin's localStorage, so they
# were invisible to every other admin. They are now uploaded to the shared file
# storage and returned on the department row.

# Smallest valid PNG (1x1), so these tests do not carry a binary fixture.
_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005a3fa8d0b0000000049454e44ae426082"
)


def _make_department(client, admin_headers, name="Logo Dept"):
    resp = client.post("/api/v1/admin/departments", headers=admin_headers,
                       json={"name": name, "description": "for logo tests"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


@pytest.mark.admin
def test_upload_and_clear_department_logo(client, admin_headers):
    dept_id = _make_department(client, admin_headers, "Upload Dept")

    up = client.post(f"/api/v1/admin/departments/{dept_id}/logo",
                     headers=admin_headers,
                     files={"file": ("logo.png", _PNG_1PX, "image/png")})
    assert up.status_code == 200, up.text
    assert up.json()["logo_url"].startswith("/logos/")

    # It must come back on the list — adding the column without widening the
    # response is the classic half-done version of this change.
    listed = client.get("/api/v1/admin/departments", headers=admin_headers).json()
    row = next(d for d in listed if d["id"] == dept_id)
    assert row["logo_url"] == up.json()["logo_url"]

    cleared = client.delete(f"/api/v1/admin/departments/{dept_id}/logo",
                            headers=admin_headers)
    assert cleared.status_code == 200
    assert cleared.json()["logo_url"] is None


@pytest.mark.admin
def test_department_list_includes_logo_url_when_unset(client, admin_headers):
    _make_department(client, admin_headers, "No Logo Dept")
    listed = client.get("/api/v1/admin/departments", headers=admin_headers).json()
    assert all("logo_url" in d for d in listed)


@pytest.mark.admin
def test_non_image_upload_rejected(client, admin_headers):
    dept_id = _make_department(client, admin_headers, "Bad Type Dept")
    resp = client.post(f"/api/v1/admin/departments/{dept_id}/logo",
                       headers=admin_headers,
                       files={"file": ("payload.pdf", b"%PDF-1.4", "application/pdf")})
    assert resp.status_code == 400


@pytest.mark.admin
def test_oversized_upload_rejected(client, admin_headers):
    dept_id = _make_department(client, admin_headers, "Big Dept")
    oversized = _PNG_1PX + b"\0" * (2 * 1024 * 1024 + 1)
    resp = client.post(f"/api/v1/admin/departments/{dept_id}/logo",
                       headers=admin_headers,
                       files={"file": ("huge.png", oversized, "image/png")})
    assert resp.status_code == 400


@pytest.mark.admin
def test_empty_upload_rejected(client, admin_headers):
    dept_id = _make_department(client, admin_headers, "Empty Dept")
    resp = client.post(f"/api/v1/admin/departments/{dept_id}/logo",
                       headers=admin_headers,
                       files={"file": ("empty.png", b"", "image/png")})
    assert resp.status_code == 400


@pytest.mark.admin
def test_unknown_department_returns_404(client, admin_headers):
    resp = client.post("/api/v1/admin/departments/does-not-exist/logo",
                       headers=admin_headers,
                       files={"file": ("logo.png", _PNG_1PX, "image/png")})
    assert resp.status_code == 404


@pytest.mark.admin
def test_non_admin_cannot_upload_logo(client, admin_headers, hr_headers):
    dept_id = _make_department(client, admin_headers, "Guarded Dept")
    resp = client.post(f"/api/v1/admin/departments/{dept_id}/logo",
                       headers=hr_headers,
                       files={"file": ("logo.png", _PNG_1PX, "image/png")})
    assert resp.status_code in (401, 403, 404)
