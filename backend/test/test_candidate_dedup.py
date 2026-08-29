"""One candidate row per person per job (uq_candidates_company_job_email).

Five code paths create a `Candidate` and each checked `(job_id, email)` first, which is
what produces a readable error. Those checks cannot be the whole story: two concurrent
applications both read "not present" before either writes, and the loser then meets the
constraint. This covers the database half of that, and the translation of a raced
insert into a 409 rather than a 500 on the candidate's apply button.

The NULL behaviour is pinned too, because it is a deliberate limit rather than an
oversight: registration writes a placeholder row per user with `job_id = NULL`, and MySQL
treats NULLs as distinct in a unique index, so those rows are outside the constraint.
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from src.models.models import Candidate, Company, Department, JobPosting, User
from src.services.job_dedup import is_duplicate_key


def _uid():
    return str(uuid.uuid4())


@pytest.fixture()
def company(db, hr_headers):
    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    return db.query(Company).filter(Company.id == hr.company_id).first()


@pytest.fixture()
def job(db, company):
    dept = db.query(Department).filter(Department.company_id == company.id).first()
    j = JobPosting(
        id=_uid(), company_id=company.id, title=f"Role {_uid()[:8]}", status="active",
        description="d", requirements="r",
        department_id=dept.id if dept else None,
    )
    db.add(j)
    db.flush()
    return j


def _candidate(db, company, job_id, email, **kw):
    c = Candidate(
        id=_uid(), company_id=company.id, job_id=job_id, email=email,
        first_name="A", last_name="B", **kw,
    )
    db.add(c)
    return c


# ── The constraint itself ────────────────────────────────────────────────────

def test_the_same_person_cannot_be_entered_twice_for_one_job(db, company, job):
    """The database refuses it, whichever code path forgot to check."""
    _candidate(db, company, job.id, "anjali@example.com")
    db.flush()

    _candidate(db, company, job.id, "anjali@example.com")
    with pytest.raises(IntegrityError) as e:
        db.flush()
    assert is_duplicate_key(e.value), "must be a unique violation, not some other integrity error"
    db.rollback()


def test_case_alone_does_not_make_a_second_row(db, company, job):
    """`candidates.email` is utf8mb4_unicode_ci, so the index compares case-insensitively.
    Pinned because a future collation change would silently open this back up."""
    _candidate(db, company, job.id, "anjali@example.com")
    db.flush()

    _candidate(db, company, job.id, "ANJALI@example.com")
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_the_same_person_may_apply_to_a_different_job(db, company, job):
    """The guard is per opening, not per person — applying to two roles is normal."""
    other = JobPosting(id=_uid(), company_id=company.id, title="Another Role",
                       status="active", description="d", requirements="r")
    db.add(other)
    db.flush()

    _candidate(db, company, job.id, "anjali@example.com")
    _candidate(db, company, other.id, "anjali@example.com")
    db.flush()   # must not raise


def test_a_different_person_may_apply_to_the_same_job(db, company, job):
    _candidate(db, company, job.id, "anjali@example.com")
    _candidate(db, company, job.id, "ravi@example.com")
    db.flush()   # must not raise


def test_signup_placeholders_are_outside_the_constraint(db, company):
    """Documented limit, not a hole to be surprised by later.

    Registration writes one row per user with job_id = NULL, and MySQL treats NULLs as
    distinct in a unique index — the same property uq_resume_pool_company_source_user
    relies on. One placeholder per person is guaranteed upstream by
    uq_users_company_email instead.
    """
    _candidate(db, company, None, "anjali@example.com", current_stage="registered")
    _candidate(db, company, None, "anjali@example.com", current_stage="registered")
    db.flush()   # allowed, by design


# ── The translation to a 409 ─────────────────────────────────────────────────

def test_is_duplicate_key_ignores_other_integrity_errors(db, company):
    """A foreign-key violation is also an IntegrityError. Reporting one as "already
    exists" would send whoever is debugging it in exactly the wrong direction."""
    _candidate(db, company, "no-such-job-id", "anjali@example.com")
    with pytest.raises(IntegrityError) as e:
        db.flush()
    assert not is_duplicate_key(e.value)
    db.rollback()


def test_applying_twice_returns_a_conflict_not_a_server_error(client, db, hr_headers,
                                                              company, job):
    """End to end through the apply endpoint. The app check catches this one before the
    constraint does — what matters is that the candidate gets a clear answer and only one
    row exists either way."""
    _candidate(db, company, job.id, "anjali@example.com")
    db.commit()

    r = client.post(f"/api/v1/candidate/jobs/{job.id}/apply", json={
        "first_name": "Anjali", "last_name": "Rao",
        "email": "anjali@example.com", "phone": "9999999999",
    })
    assert r.status_code != 500, f"a repeat application must never 500: {r.text}"
    if r.status_code in (400, 409):
        detail = r.json().get("detail")
        assert isinstance(detail, str), "several UIs put detail straight into React state"

    n = db.query(Candidate).filter(
        Candidate.job_id == job.id, Candidate.email == "anjali@example.com").count()
    assert n == 1, "still exactly one row for this person on this job"
