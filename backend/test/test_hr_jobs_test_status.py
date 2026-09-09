"""GET /api/v1/hr/jobs must report whether each job already has a test.

JobPostingResponse carried no test information at all, so the HR job card could only show the
JOB's own status — "Open" / "Closed". A job holding an unfinished DRAFT test looked exactly
like a job with no test, which is what made "Create Test" silently resume a half-built one.

The fields are defaulted rather than required because create_job_response has four call sites
and only the list endpoint pays for the lookup; the single-job routes must keep working
untouched.
"""
import uuid
import pytest

from test.test_tenant_isolation import (
    _make_company,
    _make_user,
    _login,
    _make_job,
)


def _make_template(db, company_id, job_id, status="DRAFT"):
    """A template interview: candidate_id IS NULL is what distinguishes it from an attempt."""
    from src.models.models import Interview
    iv = Interview(
        company_id=company_id,
        title="Test",
        status=status,
        job_id=job_id,
    )
    db.add(iv)
    db.flush()
    return iv.id


def _attach_hr(db, company_id, user_email):
    """/hr/jobs filters by the HR's own profile for non-admins, so jobs need an hr_id."""
    from src.models.models import User, HRProfile
    user = db.query(User).filter(User.email == user_email).first()
    profile = HRProfile(
        id=str(uuid.uuid4()),
        user_id=user.id,
        company_id=company_id,
        employee_id=f"EMP-{uuid.uuid4().hex[:6]}",   # NOT NULL
    )
    db.add(profile)
    db.flush()
    return profile.id


@pytest.fixture()
def hr_with_jobs(client, db):
    cid = _make_company(db, "Jobs Co")
    _make_user(db, "hr-jobs@example.com", "hr", cid)
    hr_id = _attach_hr(db, cid, "hr-jobs@example.com")

    free = _make_job(db, cid, title="Data Engineer")
    drafted = _make_job(db, cid, title="Ai Developer")
    published = _make_job(db, cid, title="QA Engineer")
    for jid in (free, drafted, published):
        from src.models.models import JobPosting
        db.query(JobPosting).filter(JobPosting.id == jid).update({"hr_id": hr_id})

    draft_iv = _make_template(db, cid, drafted, status="DRAFT")
    ready_iv = _make_template(db, cid, published, status="READY")
    db.commit()

    return {
        "headers": _login(client, "hr-jobs@example.com"),
        "free": free,
        "drafted": drafted,
        "published": published,
        "draft_iv": draft_iv,
        "ready_iv": ready_iv,
    }


def _by_title(payload):
    return {j["title"]: j for j in payload}


@pytest.mark.hr
def test_jobs_report_an_unfinished_draft(client, hr_with_jobs):
    resp = client.get("/api/v1/hr/jobs", headers=hr_with_jobs["headers"])
    assert resp.status_code == 200, resp.text
    job = _by_title(resp.json())["Ai Developer"]

    assert job["has_test"] is True
    assert job["test_id"] == hr_with_jobs["draft_iv"]
    assert job["test_status"] == "DRAFT"
    # The job's own lifecycle is untouched and separate — that distinction is the whole point.
    assert job["status"] == "active"


@pytest.mark.hr
def test_jobs_report_a_finished_test(client, hr_with_jobs):
    resp = client.get("/api/v1/hr/jobs", headers=hr_with_jobs["headers"])
    job = _by_title(resp.json())["QA Engineer"]

    assert job["has_test"] is True
    assert job["test_id"] == hr_with_jobs["ready_iv"]
    assert job["test_status"] == "READY"


@pytest.mark.hr
def test_jobs_without_a_test_say_so(client, hr_with_jobs):
    resp = client.get("/api/v1/hr/jobs", headers=hr_with_jobs["headers"])
    job = _by_title(resp.json())["Data Engineer"]

    assert job["has_test"] is False
    assert job["test_id"] is None
    assert job["test_status"] is None


@pytest.mark.hr
def test_a_candidate_attempt_does_not_count_as_the_job_s_test(client, db, hr_with_jobs):
    """Per-candidate clones share the job_id. Only candidate_id IS NULL is the template."""
    from src.models.models import Interview, Candidate

    cand = Candidate(
        id=str(uuid.uuid4()),
        first_name="A", last_name="B",
        email=f"c-{uuid.uuid4().hex[:6]}@example.com",
    )
    db.add(cand)
    db.flush()
    clone = Interview(
        title="Clone", status="SCHEDULED",
        job_id=hr_with_jobs["free"], candidate_id=cand.id,
    )
    db.add(clone)
    db.commit()

    resp = client.get("/api/v1/hr/jobs", headers=hr_with_jobs["headers"])
    job = _by_title(resp.json())["Data Engineer"]
    assert job["has_test"] is False, "a candidate's attempt was mistaken for the job's test"


@pytest.mark.hr
def test_single_job_endpoint_still_responds(client, hr_with_jobs):
    """create_job_response's other call sites pass no test and must keep working."""
    resp = client.get(f"/api/v1/hr/jobs/{hr_with_jobs['drafted']}", headers=hr_with_jobs["headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Ai Developer"
    # Defaulted, not populated — this route does not pay for the lookup.
    assert body["has_test"] is False
