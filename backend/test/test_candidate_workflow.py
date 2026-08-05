"""
Candidate workflow tests — browse jobs, apply, view applications, accept offer.
Includes regression test for BUG-02 (candidate.company_id is NULL after applying).
"""
import pytest
import io


def _create_active_job(client, hr_headers):
    """Helper: create and approve a job, return job dict."""
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

    # Approve to make it active
    client.put(f"/api/v1/hr/jobs/{job_id}/approve", headers=hr_headers, json={
        "salary_min": 50000,
        "salary_max": 90000,
        "location": "Remote",
    })
    return job_id


def _make_resume():
    """Return a minimal PDF-like bytes object for upload."""
    return io.BytesIO(b"%PDF-1.4 fake pdf content for testing")


@pytest.mark.candidate
def test_browse_active_jobs_only(client, hr_headers, candidate_headers):
    """Candidates should only see active (not draft) jobs."""
    _create_active_job(client, hr_headers)

    resp = client.get("/api/v1/candidate/jobs", headers=candidate_headers)
    assert resp.status_code == 200
    body = resp.json()
    jobs = body if isinstance(body, list) else body.get("jobs", body.get("data", []))
    for j in jobs:
        assert j.get("status") == "active", f"Non-active job visible: {j}"


@pytest.mark.candidate
def test_apply_creates_application(client, hr_headers, candidate_headers):
    """Applying to a job must create an Application row."""
    job_id = _create_active_job(client, hr_headers)

    resp = client.post(
        f"/api/v1/candidate/jobs/{job_id}/apply",
        headers=candidate_headers,
        files={"resume": ("resume.pdf", _make_resume(), "application/pdf")},
        data={
            "first_name": "Test",
            "last_name": "Candidate",
            "email": "candidate_test@test.com",
            "phone": "1234567890",
            "years_experience": "2",
        },
    )
    assert resp.status_code in (200, 201), f"Apply failed: {resp.text}"


@pytest.mark.candidate
@pytest.mark.security
def test_apply_sets_company_id(client, hr_headers, candidate_headers, db):
    """
    BUG-02 REGRESSION: After a candidate applies, candidate.company_id
    must be set to the job's company_id (not NULL).
    This test FAILS until the bug is fixed in routes/candidate_routes.py.
    """
    from src.models.models import Candidate
    job_id = _create_active_job(client, hr_headers)

    client.post(
        f"/api/v1/candidate/jobs/{job_id}/apply",
        headers=candidate_headers,
        files={"resume": ("resume.pdf", _make_resume(), "application/pdf")},
        data={
            "first_name": "Test",
            "last_name": "Candidate",
            "email": "candidate_test@test.com",
            "phone": "1234567890",
            "years_experience": "2",
        },
    )

    # A Candidate row is auto-created at registration (no job_id); the apply
    # adds a second row tied to the job. Filter by job_id to get the right one.
    cand = db.query(Candidate).filter(
        Candidate.email == "candidate_test@test.com",
        Candidate.job_id == job_id,
    ).first()

    assert cand is not None, "Candidate row not created after apply"
    assert cand.company_id is not None, (
        "BUG-02: candidate.company_id is NULL after applying — "
        "fix: add company_id=job.company_id in routes/candidate_routes.py"
    )


@pytest.mark.candidate
def test_view_own_applications_only(client, hr_headers, candidate_headers, candidate2_headers):
    """Candidate A must not see Candidate B's applications."""
    job_id = _create_active_job(client, hr_headers)

    # Candidate 1 applies
    client.post(
        f"/api/v1/candidate/jobs/{job_id}/apply",
        headers=candidate_headers,
        files={"resume": ("r.pdf", _make_resume(), "application/pdf")},
        data={"first_name": "C1", "last_name": "Test", "email": "candidate_test@test.com",
              "phone": "111", "years_experience": "1"},
    )

    # Candidate 2 checks their own applications — should be empty or only their own
    resp = client.get("/api/v1/candidate/applications", headers=candidate2_headers)
    assert resp.status_code == 200
    body = resp.json()
    apps = body if isinstance(body, list) else body.get("applications", body.get("data", []))
    for app in apps:
        assert app.get("candidate_email") != "candidate_test@test.com", \
            "Candidate 2 can see Candidate 1's application — data leak!"
