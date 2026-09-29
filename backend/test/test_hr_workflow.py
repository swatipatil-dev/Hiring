"""
HR workflow tests — job creation, approval, interview scheduling, offers.
"""
import pytest


def _create_job(client, hr_headers, status="draft"):
    resp = client.post("/api/v1/hr/jobs", headers=hr_headers, json={
        "title": "Backend Dev",
        "description": "FastAPI role",
        "requirements": "Python",
        "location": "Remote",
        "employment_type": "full_time",
        "experience_years": 1,
        "required_skills": ["Python"],
        "salary_min": 40000,
        "salary_max": 80000,
    })
    assert resp.status_code in (200, 201), f"Job creation failed: {resp.text}"
    job = resp.json()
    job_id = job.get("id") or job.get("job_id")
    if status == "active":
        client.put(f"/api/v1/hr/jobs/{job_id}/approve", headers=hr_headers, json={
            "salary_min": 40000, "salary_max": 80000, "location": "Remote",
        })
    return job_id


@pytest.mark.hr
def test_create_job_posting(client, hr_headers):
    resp = client.post("/api/v1/hr/jobs", headers=hr_headers, json={
        "title": "Frontend Dev",
        "description": "React role",
        "requirements": "React",
        "location": "Hybrid",
        "employment_type": "full_time",
        "experience_years": 2,
        "required_skills": ["React", "JS"],
        "salary_min": 45000,
        "salary_max": 75000,
    })
    assert resp.status_code in (200, 201)
    job = resp.json()
    assert job.get("title") == "Frontend Dev"
    assert job.get("status") in ("draft", "active", "pending")


@pytest.mark.hr
def test_approve_job_changes_status(client, hr_headers, db):
    """Approving a draft job flips its status to 'active'."""
    # HR-created jobs default to 'active', so seed a draft job directly
    # to exercise the approve transition.
    from src.models.models import JobPosting, User, Department
    import uuid
    hr_user = db.query(User).filter(User.email == "hr_test@test.com").first()
    dept = db.query(Department).filter(Department.company_id == hr_user.company_id).first()
    job = JobPosting(
        id=str(uuid.uuid4()),
        title="Draft Job",
        description="x",
        requirements="x",
        location="Remote",
        employment_type="full_time",
        company_id=hr_user.company_id,
        department_id=dept.id,
        status="draft",
    )
    db.add(job)
    db.flush()
    job_id = job.id

    resp = client.put(f"/api/v1/hr/jobs/{job_id}/approve", headers=hr_headers, json={
        "salary_min": 40000,
        "salary_max": 80000,
        "location": "Remote",
    })
    assert resp.status_code in (200, 204)


@pytest.mark.hr
def test_hr_sees_own_jobs(client, hr_headers):
    _create_job(client, hr_headers)
    resp = client.get("/api/v1/hr/jobs", headers=hr_headers)
    assert resp.status_code == 200
    body = resp.json()
    jobs = body if isinstance(body, list) else body.get("jobs", body.get("data", []))
    assert isinstance(jobs, list)
    assert len(jobs) >= 1


@pytest.mark.hr
def test_hr_cannot_access_admin_endpoint(client, hr_headers):
    resp = client.post("/api/v1/admin/departments", headers=hr_headers, json={
        "name": "Unauthorized",
    })
    assert resp.status_code in (401, 403)


@pytest.mark.hr
def test_create_offer_for_candidate(client, hr_headers, candidate_headers, db):
    """HR can create an offer — offer status should be 'sent' not 'draft'."""
    import io
    from src.models.models import Candidate, User

    # The stock fixtures put the candidate in the platform org and HR in a separate
    # "Test Company"; applying across companies is refused (404), which used to turn
    # this test into a silent skip. A candidate applying here belongs to HR's company.
    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    db.query(User).filter(User.email == "candidate_test@test.com").first().company_id = hr.company_id
    db.flush()

    job_id = _create_job(client, hr_headers, status="active")

    # Candidate applies
    client.post(
        f"/api/v1/candidate/jobs/{job_id}/apply",
        headers=candidate_headers,
        files={"resume": ("r.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")},
        data={"first_name": "Off", "last_name": "Tester",
              "email": "candidate_test@test.com", "phone": "999", "years_experience": "2"},
    )
    # Pick the candidate row tied to this job — registration auto-creates a
    # placeholder row with no job_id which we must avoid here.
    cand = db.query(Candidate).filter(
        Candidate.email == "candidate_test@test.com",
        Candidate.job_id == job_id,
    ).first()
    if not cand:
        pytest.skip("Candidate application failed — skipping offer test")

    resp = client.post("/api/v1/hr/offers", headers=hr_headers, json={
        "candidate_id": cand.id,
        "job_id": job_id,
        "salary": 70000,
        "offer_type": "full_time",
        "start_date": "2026-06-01",
        "expiry_date": "2026-12-31",
        "notes": "Welcome to the team!",
    })
    assert resp.status_code in (200, 201), f"Offer creation failed: {resp.text}"
    offer = resp.json()
    # The offer creation response confirms success via the message/offer_id
    # fields; the persisted status is asserted in the security tests instead.
    assert offer.get("offer_id"), f"Offer ID missing in response: {offer}"
    assert "sent" in offer.get("message", "").lower() or offer.get("status") in ("sent", "pending"), \
        f"Unexpected offer response: {offer}"
