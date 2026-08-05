"""
Candidate in-app notification tests.

Covers the notification_service helper, the three candidate notification
endpoints (list / mark-read / mark-all-read), per-user isolation, and that an
application emits a notification. Pure DB + API — no Ollama, runs in CI.
"""
import pytest


def _create_active_job(client, hr_headers):
    """Create and approve a job, return its id. Mirrors test_candidate_workflow."""
    resp = client.post("/api/v1/hr/jobs", headers=hr_headers, json={
        "title": "Software Engineer",
        "description": "Build great software",
        "requirements": "Python, FastAPI",
        "location": "Remote",
        "employment_type": "full_time",
        "experience_years": 2,
        "required_skills": ["Python", "FastAPI"],
        "salary_min": 50000,
        "salary_max": 90000,
    })
    assert resp.status_code in (200, 201), f"Job creation failed: {resp.text}"
    job = resp.json()
    job_id = job.get("id") or job.get("job_id")
    client.put(f"/api/v1/hr/jobs/{job_id}/approve", headers=hr_headers, json={
        "salary_min": 50000, "salary_max": 90000, "location": "Remote",
    })
    return job_id


def _apply(client, headers, job_id, email="candidate_test@test.com"):
    import io
    return client.post(
        f"/api/v1/candidate/jobs/{job_id}/apply",
        headers=headers,
        files={"resume": ("resume.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        data={"first_name": "Test", "last_name": "Candidate", "email": email,
              "phone": "1234567890", "years_experience": "2"},
    )


# ── notification_service unit behaviour ──────────────────────────────────────

@pytest.mark.candidate
def test_create_notification_persists(client, candidate_headers, db):
    """create_notification writes a row keyed on the recipient User.id."""
    from src.services.notification_service import create_notification
    from src.models.models import User, Notification

    user = db.query(User).filter(User.email == "candidate_test@test.com").first()
    notif = create_notification(
        db, user_id=user.id, title="Hello", message="World", type="info", commit=False,
    )
    assert notif is not None
    db.flush()
    row = db.query(Notification).filter(Notification.user_id == user.id).first()
    assert row is not None and row.title == "Hello" and row.is_read is False


@pytest.mark.candidate
def test_create_notification_no_user_returns_none(client, db):
    """No user_id (anonymous/legacy application) is a no-op, never an error."""
    from src.services.notification_service import create_notification
    assert create_notification(db, user_id=None, title="x", message="y") is None


# ── endpoints ────────────────────────────────────────────────────────────────

@pytest.mark.candidate
def test_apply_emits_notification(client, hr_headers, candidate_headers):
    """Applying to a job creates an 'application_received' notification."""
    job_id = _create_active_job(client, hr_headers)
    assert _apply(client, candidate_headers, job_id).status_code in (200, 201)

    resp = client.get("/api/v1/candidate/notifications", headers=candidate_headers)
    assert resp.status_code == 200
    notifs = resp.json()
    assert any(n["type"] == "application_received" for n in notifs), \
        f"No application_received notification after apply: {notifs}"


@pytest.mark.candidate
def test_notifications_are_per_user(client, hr_headers, candidate_headers, candidate2_headers):
    """Candidate B must not see Candidate A's notifications."""
    job_id = _create_active_job(client, hr_headers)
    _apply(client, candidate_headers, job_id, email="candidate_test@test.com")

    resp = client.get("/api/v1/candidate/notifications", headers=candidate2_headers)
    assert resp.status_code == 200
    assert resp.json() == [] or all(
        n["type"] != "application_received" for n in resp.json()
    ), "Candidate 2 sees Candidate 1's notifications — leak!"


@pytest.mark.candidate
def test_mark_single_notification_read(client, hr_headers, candidate_headers):
    job_id = _create_active_job(client, hr_headers)
    _apply(client, candidate_headers, job_id)

    notif_id = client.get("/api/v1/candidate/notifications", headers=candidate_headers).json()[0]["id"]
    resp = client.put(f"/api/v1/candidate/notifications/{notif_id}/read", headers=candidate_headers)
    assert resp.status_code == 200

    after = client.get("/api/v1/candidate/notifications", headers=candidate_headers).json()
    assert next(n for n in after if n["id"] == notif_id)["is_read"] is True


@pytest.mark.candidate
def test_mark_all_notifications_read(client, hr_headers, candidate_headers):
    job_id = _create_active_job(client, hr_headers)
    _apply(client, candidate_headers, job_id)

    resp = client.put("/api/v1/candidate/notifications/read-all", headers=candidate_headers)
    assert resp.status_code == 200

    after = client.get("/api/v1/candidate/notifications", headers=candidate_headers).json()
    assert all(n["is_read"] for n in after)


@pytest.mark.candidate
def test_mark_other_users_notification_forbidden(client, hr_headers, candidate_headers, candidate2_headers):
    """Marking a notification you don't own returns 404 (not found for you)."""
    job_id = _create_active_job(client, hr_headers)
    _apply(client, candidate_headers, job_id)
    notif_id = client.get("/api/v1/candidate/notifications", headers=candidate_headers).json()[0]["id"]

    resp = client.put(f"/api/v1/candidate/notifications/{notif_id}/read", headers=candidate2_headers)
    assert resp.status_code == 404


@pytest.mark.candidate
def test_notifications_require_auth(client):
    assert client.get("/api/v1/candidate/notifications").status_code in (401, 403)
