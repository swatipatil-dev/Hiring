"""
Interviewer workflow tests — list interviews, submit feedback, RBAC checks.
"""
import pytest
import uuid


# ── helpers ────────────────────────────────────────────────────────────────

def _make_interview(db, interviewer_email: str, hr_user_email: str = None):
    """Insert a minimal Interview row directly and return its id."""
    from src.models.models import (
        Interview, JobPosting, Candidate, Company, Department, User
    )
    from datetime import datetime, timedelta

    # Need a company + job + candidate chain so FKs are satisfied
    company = Company(
        id=str(uuid.uuid4()), name="Iv-Co", slug=f"iv-co-{uuid.uuid4().hex[:6]}"
    )
    db.add(company)
    db.flush()

    # Link the interviewer to this company so the tenant filter on
    # /api/v1/interviewer/interviews matches.
    iv_user = db.query(User).filter(User.email == interviewer_email).first()
    if iv_user and iv_user.company_id != company.id:
        iv_user.company_id = company.id
        db.flush()

    dept = Department(id=str(uuid.uuid4()), name="Engineering", company_id=company.id)
    db.add(dept)
    db.flush()

    job = JobPosting(
        id=str(uuid.uuid4()),
        title="Test Role",
        description="desc",
        requirements="Python",
        required_skills=["python"],
        location="Remote",
        employment_type="full_time",
        experience_years=1,
        status="active",
        company_id=company.id,
        # JobPosting.created_by is a relationship (FK column is created_by_id,
        # nullable). Leave unset — these tests don't exercise the creator path.
    )
    db.add(job)
    db.flush()

    candidate = Candidate(
        id=str(uuid.uuid4()),
        first_name="Cand",
        last_name="Test",
        email=f"cand-{uuid.uuid4().hex[:6]}@test.com",
        company_id=company.id,
        job_id=job.id,
    )
    db.add(candidate)
    db.flush()

    # Interview.id is an autoincrement Integer (not UUID), so don't pre-set it.
    interview = Interview(
        candidate_id=candidate.id,
        job_id=job.id,
        company_id=company.id,
        interview_type="technical",
        scheduled_datetime=datetime.utcnow() + timedelta(days=1),
        duration_minutes=60,
        status="scheduled",
        interviewer_email=interviewer_email,
        created_by=interviewer_email,
    )
    db.add(interview)
    db.flush()
    return str(interview.id)


# ── tests ──────────────────────────────────────────────────────────────────

@pytest.mark.interviewer
def test_interviewer_list_interviews_empty(client, interviewer_headers):
    """Interviewer with no assigned interviews gets 200 and an empty list."""
    resp = client.get("/api/v1/interviewer/interviews", headers=interviewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    interviews = body if isinstance(body, list) else body.get("interviews", body.get("data", []))
    assert isinstance(interviews, list)


@pytest.mark.interviewer
def test_interviewer_sees_assigned_interview(client, interviewer_headers, db):
    """Interviewer must see interviews assigned to their email."""
    from src.models.models import User
    iv_user = db.query(User).filter(User.email == "interviewer_test@test.com").first()
    if not iv_user:
        pytest.skip("Interviewer user not found")

    iid = _make_interview(db, interviewer_email=iv_user.email)

    resp = client.get("/api/v1/interviewer/interviews", headers=interviewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    interviews = body if isinstance(body, list) else body.get("interviews", body.get("data", []))
    ids = [i.get("interview_id") or i.get("id") for i in interviews]
    assert iid in ids, f"Created interview {iid} not visible in list: {ids}"


@pytest.mark.interviewer
def test_interviewer_get_nonexistent_interview(client, interviewer_headers):
    """Getting an interview that doesn't exist returns 404."""
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/interviewer/interviews/{fake_id}", headers=interviewer_headers)
    assert resp.status_code == 404


@pytest.mark.interviewer
@pytest.mark.security
def test_candidate_cannot_access_interviewer_endpoint(client, candidate_headers):
    """Candidates must be forbidden from interviewer endpoints."""
    resp = client.get("/api/v1/interviewer/interviews", headers=candidate_headers)
    assert resp.status_code in (401, 403)


@pytest.mark.interviewer
@pytest.mark.security
def test_hr_cannot_access_interviewer_endpoint(client, hr_headers):
    """HR role must be forbidden from interviewer-only endpoints."""
    resp = client.get("/api/v1/interviewer/interviews", headers=hr_headers)
    assert resp.status_code in (401, 403)


@pytest.mark.interviewer
@pytest.mark.security
def test_unauthenticated_cannot_access_interviewer_endpoint(client):
    """No token means 401/403 on interviewer endpoints."""
    resp = client.get("/api/v1/interviewer/interviews")
    assert resp.status_code in (401, 403)


@pytest.mark.interviewer
def test_submit_feedback_for_assigned_interview(client, interviewer_headers, db):
    """Interviewer can submit feedback for an interview assigned to them."""
    from src.models.models import User
    iv_user = db.query(User).filter(User.email == "interviewer_test@test.com").first()
    if not iv_user:
        pytest.skip("Interviewer user not found")

    iid = _make_interview(db, interviewer_email=iv_user.email)

    resp = client.post(
        f"/api/v1/interviewer/interviews/{iid}/feedback",
        headers=interviewer_headers,
        json={
            "technical_score": 80,
            "communication_score": 75,
            "problem_solving_score": 85,
            "cultural_fit_score": 70,
            "strengths": ["Python", "FastAPI"],
            "weaknesses": ["Kubernetes"],
            "recommendations": "Good candidate",
            "hire_recommendation": "hire",
            "additional_notes": "Strong backend skills",
        },
    )
    assert resp.status_code in (200, 201), f"Feedback submission failed: {resp.text}"


@pytest.mark.interviewer
def test_submit_feedback_for_unassigned_interview_is_rejected(client, interviewer_headers, db):
    """Interviewer cannot submit feedback for someone else's interview."""
    iid = _make_interview(db, interviewer_email="other_interviewer@test.com")

    resp = client.post(
        f"/api/v1/interviewer/interviews/{iid}/feedback",
        headers=interviewer_headers,
        json={
            "technical_score": 80,
            "communication_score": 75,
            "problem_solving_score": 85,
            "cultural_fit_score": 70,
            "strengths": [],
            "weaknesses": [],
            "recommendations": "Good",
            "hire_recommendation": "hire",
        },
    )
    assert resp.status_code in (403, 404), (
        "Expected 403 or 404 when accessing unassigned interview"
    )


@pytest.mark.interviewer
def test_interviewer_profile_accessible(client, interviewer_headers):
    """Interviewer can access their own profile."""
    resp = client.get("/api/v1/interviewer/profile", headers=interviewer_headers)
    assert resp.status_code == 200


# ── Feedback history ───────────────────────────────────────────────────────

@pytest.mark.interviewer
def test_interview_history_is_reachable(client, interviewer_headers):
    """/interviews/history must not be swallowed by /interviews/{interview_id}.

    Starlette matches routes in registration order. While the catch-all was declared
    first, this path resolved to get_interview_details with interview_id="history"
    and 404'd — the endpoint was unreachable from the day it was written. Asserting
    the envelope here is what stops the ordering regressing.
    """
    resp = client.get("/api/v1/interviewer/interviews/history", headers=interviewer_headers)
    assert resp.status_code == 200, resp.text
    assert "interviews" in resp.json()


@pytest.mark.interviewer
def test_interview_history_returns_the_feedback_that_was_submitted(
        client, interviewer_headers, db):
    """A scored interview comes back with its scores, candidate and job."""
    from src.models.models import User, Interview

    iv_user = db.query(User).filter(User.email == "interviewer_test@test.com").first()
    if not iv_user:
        pytest.skip("Interviewer user not found")

    iid = _make_interview(db, interviewer_email=iv_user.email)
    submitted = client.post(
        f"/api/v1/interviewer/interviews/{iid}/feedback",
        headers=interviewer_headers,
        json={
            "technical_score": 80,
            "communication_score": 75,
            "problem_solving_score": 85,
            "cultural_fit_score": 70,
            "strengths": ["Python", "FastAPI"],
            "weaknesses": ["Kubernetes"],
            "recommendations": "Good candidate",
            "hire_recommendation": "hire",
            "additional_notes": "Strong backend skills",
        },
    )
    assert submitted.status_code in (200, 201), submitted.text

    # Only completed interviews appear in history.
    db.query(Interview).filter(Interview.id == int(iid)).update({"status": "COMPLETED"})
    db.flush()

    resp = client.get("/api/v1/interviewer/interviews/history", headers=interviewer_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["interviews"]

    row = next((r for r in rows if r["interview_id"] == iid), None)
    assert row is not None, f"scored interview {iid} missing from history: {rows}"
    # The per-competency scores are the point of the screen — an overall alone
    # cannot tell an interviewer whether they score consistently.
    assert row["technical_score"] == 80
    assert row["communication_score"] == 75
    assert row["hire_recommendation"] == "hire"
    assert row["candidate_name"].startswith("Cand")
    assert row["job_title"] == "Test Role"
