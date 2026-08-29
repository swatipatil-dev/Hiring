"""One role, one opening (src/services/job_dedup.py).

An interviewer could raise a request for the same role repeatedly, and each one could
become another job posting — so the same opening appeared several times over, once from
the request and again from HR's posting.

Three endpoints can bring a role into existence and each had a different idea of a
duplicate: `POST /candidate-requests` and `POST /hr/jobs` had no check at all, and
`auto-create-job` had one that matched any job with that title in **any tenant**,
including ones closed long ago.

The load-bearing cases here are the ones that must still be ALLOWED. A dedup guard that
is too eager is worse than none: it blocks legitimate hiring and people route around it.
"""
import json
import uuid

import pytest

from src.models.models import Company, Department, JobPosting, User
from src.auth import get_password_hash
from src.services.job_dedup import (
    find_open_duplicate_job,
    find_open_duplicate_request,
)


def _uid():
    return str(uuid.uuid4())


@pytest.fixture()
def company(db, hr_headers):
    """The Company the hr_headers fixture attached to the HR user."""
    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    return db.query(Company).filter(Company.id == hr.company_id).first()


@pytest.fixture()
def dept(db, company):
    d = db.query(Department).filter(Department.company_id == company.id).first()
    if d is None:
        d = Department(id=_uid(), company_id=company.id, name="Engineering")
        db.add(d)
        db.flush()
    return d


def _job(db, company, title, *, status="active", department_id=None):
    j = JobPosting(
        id=_uid(), company_id=company.id, title=title, status=status,
        description="d", requirements="r", department_id=department_id,
        required_skills=json.dumps(["python"]),
    )
    db.add(j)
    db.flush()
    return j


def _request(db, company, title, *, status="pending", department=None, user=None):
    from src.models.models import CandidateRequest
    if user is None:
        user = db.query(User).filter(User.email == "hr_test@test.com").first()
    cr = CandidateRequest(
        id=_uid(), company_id=company.id, requested_by=user.id,
        job_title=title, department=department, status=status,
    )
    db.add(cr)
    db.flush()
    return cr


# ── Jobs ─────────────────────────────────────────────────────────────────────

def test_an_open_job_for_the_same_role_is_a_duplicate(db, company, dept):
    _job(db, company, "Python Developer", department_id=dept.id)
    assert find_open_duplicate_job(db, company.id, "Python Developer", dept.id) is not None


def test_case_and_padding_do_not_slip_a_duplicate_through(db, company, dept):
    _job(db, company, "Python Developer", department_id=dept.id)
    for typed in ("python developer", "  PYTHON DEVELOPER  ", "Python  Developer".replace("  ", " ")):
        assert find_open_duplicate_job(db, company.id, typed, dept.id) is not None, typed


def test_a_closed_job_does_not_block_reopening_the_role(db, company, dept):
    """The old guard matched any status, so a role closed a year ago could never be
    reopened — the interviewer silently got handed the closed job instead."""
    _job(db, company, "Python Developer", status="closed", department_id=dept.id)
    assert find_open_duplicate_job(db, company.id, "Python Developer", dept.id) is None


@pytest.mark.parametrize("status", ["draft", "pending_approval", "active"])
def test_every_unfinished_state_counts_as_still_hiring(db, company, dept, status):
    """A draft the interviewer raised must block HR posting the same role — that pair is
    exactly the duplicate this work is about."""
    _job(db, company, "QA Engineer", status=status, department_id=dept.id)
    assert find_open_duplicate_job(db, company.id, "QA Engineer", dept.id) is not None


def test_another_department_may_hire_the_same_title(db, company, dept):
    other = Department(id=_uid(), company_id=company.id, name="Support")
    db.add(other)
    db.flush()
    _job(db, company, "Analyst", department_id=dept.id)
    assert find_open_duplicate_job(db, company.id, "Analyst", other.id) is None


def test_an_unfiled_job_does_not_block_every_department(db, company, dept):
    """A job with no department is only a duplicate of another with none. Otherwise one
    unfiled posting would lock that title company-wide."""
    _job(db, company, "Analyst", department_id=None)
    assert find_open_duplicate_job(db, company.id, "Analyst", dept.id) is None
    assert find_open_duplicate_job(db, company.id, "Analyst", None) is not None


def test_a_different_role_is_not_a_duplicate(db, company, dept):
    _job(db, company, "Python Developer", department_id=dept.id)
    for other in ("Senior Python Developer", "Python Dev", "Developer"):
        assert find_open_duplicate_job(db, company.id, other, dept.id) is None, other


def test_wildcards_in_a_title_are_matched_literally(db, company, dept):
    """The old guard used ilike, so a title holding % or _ behaved as a pattern —
    '%' would have matched every job on the board."""
    _job(db, company, "Data Engineer", department_id=dept.id)
    for pattern in ("%", "_ata Engineer", "Data%Engineer", "Data_Engineer"):
        assert find_open_duplicate_job(db, company.id, pattern, dept.id) is None, pattern

    # ...and a title that genuinely contains one still matches itself.
    _job(db, company, "50% Allocation Analyst", department_id=dept.id)
    assert find_open_duplicate_job(db, company.id, "50% Allocation Analyst", dept.id) is not None


def test_another_tenant_is_never_a_duplicate(db, company, dept):
    """The regression guard for the real defect: the old query had no company filter, so
    it returned — and handed the caller — a job belonging to a different customer."""
    rival = Company(id=_uid(), name="Rival Ltd", slug=f"rival-{_uid()[:8]}")
    db.add(rival)
    db.flush()
    _job(db, rival, "Python Developer", department_id=None)

    assert find_open_duplicate_job(db, company.id, "Python Developer", dept.id) is None
    assert find_open_duplicate_job(db, company.id, "Python Developer", None) is None


# ── Candidate requests ───────────────────────────────────────────────────────

def test_a_second_open_request_for_the_same_role_is_a_duplicate(db, company):
    _request(db, company, "QA Engineer", department="Engineering")
    assert find_open_duplicate_request(db, company.id, "QA Engineer", "Engineering") is not None


@pytest.mark.parametrize("status", ["rejected", "fulfilled", "closed", "cancelled"])
def test_a_finished_request_does_not_block_asking_again(db, company, status):
    """Reopening a role must work. HR rejecting a request cannot lock the title forever."""
    _request(db, company, "QA Engineer", status=status, department="Engineering")
    assert find_open_duplicate_request(db, company.id, "QA Engineer", "Engineering") is None


def test_an_unrecognised_status_still_counts_as_open(db, company):
    """HR writes this field from an unvalidated dict, so the vocabulary is open-ended.
    An unknown value must fail safe as "still open" — an allowlist here would let a typo
    in HR's payload switch the guard off."""
    _request(db, company, "QA Engineer", status="under_review", department="Engineering")
    assert find_open_duplicate_request(db, company.id, "QA Engineer", "Engineering") is not None


def test_a_null_status_counts_as_open(db, company):
    """NOT IN is null-hostile in SQL: without a COALESCE the row drops out of the result
    entirely and reads as "no duplicate"."""
    cr = _request(db, company, "QA Engineer", department="Engineering")
    cr.status = None
    db.flush()
    assert find_open_duplicate_request(db, company.id, "QA Engineer", "Engineering") is not None


def test_requests_are_scoped_to_the_company(db, company):
    rival = Company(id=_uid(), name="Rival Ltd", slug=f"rival-{_uid()[:8]}")
    db.add(rival)
    db.flush()
    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    _request(db, rival, "QA Engineer", department="Engineering", user=hr)
    assert find_open_duplicate_request(db, company.id, "QA Engineer", "Engineering") is None


def test_department_free_text_is_compared_forgivingly(db, company):
    """CandidateRequest.department is typed by hand, not chosen from a list."""
    _request(db, company, "QA Engineer", department="Engineering")
    assert find_open_duplicate_request(db, company.id, "QA Engineer", " engineering ") is not None
    assert find_open_duplicate_request(db, company.id, "QA Engineer", "Support") is None


def test_a_blank_department_matches_only_another_blank_one(db, company):
    _request(db, company, "QA Engineer", department=None)
    assert find_open_duplicate_request(db, company.id, "QA Engineer", None) is not None
    assert find_open_duplicate_request(db, company.id, "QA Engineer", "") is not None
    assert find_open_duplicate_request(db, company.id, "QA Engineer", "Engineering") is None


def test_an_empty_title_never_matches(db, company):
    """Guard against a blank submission quietly matching the first blank row."""
    _request(db, company, "QA Engineer", department="Engineering")
    assert find_open_duplicate_request(db, company.id, "", "Engineering") is None
    assert find_open_duplicate_request(db, company.id, "   ", "Engineering") is None


# ── Through the API ──────────────────────────────────────────────────────────

def test_hr_cannot_post_a_job_an_interviewer_already_drafted(client, db, hr_headers, company, dept):
    """The exact duplicate that prompted this: the interviewer's draft and HR's posting
    for one role."""
    _job(db, company, "QA Engineer", status="draft", department_id=dept.id)
    db.commit()

    payload = {
        "title": "QA Engineer", "description": "d", "requirements": "r",
        "department_id": dept.id, "location": "Pune", "experience_years": 2,
        "required_skills": ["python"], "employment_type": "Full-time",
    }
    r = client.post("/api/v1/hr/jobs", headers=hr_headers, json=payload)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, str), "the UI renders this straight into React state"
    assert "QA Engineer" in detail

    # ...and a genuinely different role still posts fine, so the guard is not just
    # refusing everything.
    ok = client.post("/api/v1/hr/jobs", headers=hr_headers,
                     json={**payload, "title": "Site Reliability Engineer"})
    assert ok.status_code in (200, 201), ok.text
