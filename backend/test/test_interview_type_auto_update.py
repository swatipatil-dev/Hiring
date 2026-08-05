"""
Tests for the auto-update of Interview.interview_type from a job's
job_category. Covers both:

  * The pure helper ``_interview_type_from_job_category`` in
    ``routes/hr_routes.py`` (used as the default when an HR endpoint
    receives no explicit interview_type).
  * The ``/api/v1/interviewer/all-jobs`` dropdown endpoint, which now
    surfaces ``job_category`` on each job so the create-test wizard can
    auto-fill the Interview Type field on the first step.
"""
import json
import uuid
import pytest


# ── 1. Pure helper: job_category → Interview.interview_type ───────────────

@pytest.mark.parametrize("category, expected", [
    ("technical", "TECHNICAL"),
    ("TECHNICAL", "TECHNICAL"),
    ("non_technical", "NON_TECHNICAL"),
    ("Non_Technical", "NON_TECHNICAL"),
    ("mixed", "MIXED"),
    (None, "TECHNICAL"),       # default when unset
    ("", "TECHNICAL"),         # falsy → default
    ("garbage", "TECHNICAL"),  # unknown → default
])
def test_interview_type_from_job_category_mapping(category, expected):
    from routes.hr_routes import _interview_type_from_job_category
    assert _interview_type_from_job_category(category) == expected


# ── 2. /interviewer/all-jobs surfaces job_category ────────────────────────

def _seed_job(db, interviewer_email: str, *, category: str, title: str = "Test Role"):
    """Seed a JobPosting tied to the interviewer's company, return its id."""
    from src.models.models import JobPosting, Company, Department, User

    iv_user = db.query(User).filter(User.email == interviewer_email).first()
    assert iv_user is not None, "Interviewer must already exist"

    if not iv_user.company_id:
        company = Company(
            id=str(uuid.uuid4()),
            name="JobCat Co",
            slug=f"jobcat-{uuid.uuid4().hex[:6]}",
        )
        db.add(company)
        db.flush()
        iv_user.company_id = company.id
        db.flush()

    dept = Department(
        id=str(uuid.uuid4()),
        name=f"Engineering-{uuid.uuid4().hex[:6]}",
        company_id=iv_user.company_id,
    )
    db.add(dept)
    db.flush()

    job = JobPosting(
        id=str(uuid.uuid4()),
        title=title,
        description="desc",
        requirements="Python",
        required_skills=json.dumps(["python"]),
        location="Remote",
        employment_type="full_time",
        experience_years=1,
        status="active",
        company_id=iv_user.company_id,
        department_id=dept.id,
        job_category=category,
    )
    db.add(job)
    db.flush()
    return str(job.id)


@pytest.mark.interviewer
def test_all_jobs_includes_job_category(client, interviewer_headers, db):
    """Every job in /interviewer/all-jobs must expose its job_category."""
    tech_id = _seed_job(
        db, "interviewer_test@test.com", category="technical", title="Backend Eng"
    )
    nontech_id = _seed_job(
        db, "interviewer_test@test.com", category="non_technical", title="HR Manager"
    )

    resp = client.get("/api/v1/interviewer/all-jobs", headers=interviewer_headers)
    assert resp.status_code == 200, resp.text

    jobs = resp.json()
    by_id = {j["job_id"]: j for j in jobs}

    assert tech_id in by_id, f"Seeded technical job missing: {jobs}"
    assert nontech_id in by_id, f"Seeded non-technical job missing: {jobs}"

    assert "job_category" in by_id[tech_id], "Response is missing job_category field"
    assert by_id[tech_id]["job_category"] == "technical"
    assert by_id[nontech_id]["job_category"] == "non_technical"


@pytest.mark.interviewer
def test_all_jobs_job_category_defaults_to_technical(client, interviewer_headers, db):
    """A job created without an explicit job_category should still surface
    a non-null value (defaulting to 'technical') so the frontend mapping
    never crashes on a missing field."""
    from src.models.models import JobPosting, Company, Department, User

    iv_user = db.query(User).filter(
        User.email == "interviewer_test@test.com"
    ).first()
    company = Company(
        id=str(uuid.uuid4()),
        name="Default Co",
        slug=f"defaultco-{uuid.uuid4().hex[:6]}",
    )
    db.add(company)
    db.flush()
    iv_user.company_id = company.id
    dept = Department(id=str(uuid.uuid4()), name="Eng", company_id=company.id)
    db.add(dept)
    db.flush()

    job = JobPosting(
        id=str(uuid.uuid4()),
        title="Unset Category Role",
        description="desc",
        requirements="-",
        required_skills=json.dumps([]),
        location="Remote",
        employment_type="full_time",
        experience_years=0,
        status="active",
        company_id=company.id,
        department_id=dept.id,
        # job_category intentionally omitted — should fall back to "technical"
    )
    db.add(job)
    db.flush()

    resp = client.get("/api/v1/interviewer/all-jobs", headers=interviewer_headers)
    assert resp.status_code == 200, resp.text
    match = next((j for j in resp.json() if j["job_id"] == str(job.id)), None)
    assert match is not None, "Seeded job not returned"
    assert match.get("job_category") in ("technical", None) or match["job_category"], (
        "job_category should never be missing from the payload"
    )
    # Endpoint normalises None → 'technical'
    assert match["job_category"] == "technical"
