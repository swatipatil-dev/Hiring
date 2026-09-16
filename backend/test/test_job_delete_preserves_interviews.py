"""Permanently deleting a job must not erase who conducted its interviews.

`DELETE /api/v1/hr/jobs/{id}/permanent` already went out of its way to preserve the
PEOPLE — every candidate is copied back to the talent pool before anything is destroyed.
What it did not preserve was the record of which colleague interviewed them and what that
colleague concluded: `Interview` and `InterviewFeedback` are the only place that lives,
and both were deleted with the job.

The snapshot written at delete time now carries it, and `GET /hr/talent-pool` replays it,
so a person's completed interview history survives the job it happened under.
"""
import json
import uuid

import pytest

from test.test_resume_pool import (  # reuse the established pool fixtures/helpers
    _job_and_candidate,
    _pool_rows,
    _uid,
    hr_company,      # noqa: F401 - imported so pytest resolves it as a fixture here
    stub_scorer,     # noqa: F401 - autouse; keeps MiniLM/spaCy out of this module too
)


def _completed_interview(db, company_id, job_id, candidate_id, *, panel, score=82.0,
                         recommendation="hire"):
    """A finished panel interview with submitted feedback."""
    from datetime import datetime, timedelta

    from src.models.models import Interview, InterviewFeedback

    iv = Interview(
        company_id=company_id,
        title="Technical Round",
        job_id=job_id,
        candidate_id=candidate_id,
        interview_type="TECHNICAL",
        status="COMPLETED",
        is_scheduled_interview=True,
        scheduled_datetime=datetime.utcnow() - timedelta(days=5),
        duration_minutes=60,
        interviewer_name=panel[0]["name"],
        interviewer_email=panel[0]["email"],
        panel_members=json.dumps(panel),
    )
    db.add(iv)
    db.flush()

    fb = InterviewFeedback(
        id=_uid(),
        company_id=company_id,
        interview_id=iv.id,
        overall_score=score,
        hire_recommendation=recommendation,
        additional_notes="Strong on system design.",
    )
    db.add(fb)
    db.flush()
    return iv


PANEL = [
    {"id": None, "name": "Asha Rao", "email": "asha@example.com", "user_id": None},
    {"id": None, "name": "Ben Park", "email": "ben@example.com", "user_id": None},
]


def test_deleting_a_job_preserves_who_interviewed_the_candidate(client, db, hr_headers,
                                                                hr_company):
    from src.models.models import Interview, InterviewFeedback

    job, cand = _job_and_candidate(db, hr_company, stage="interview_completed")
    iv = _completed_interview(db, hr_company.id, job.id, cand.id, panel=PANEL)
    db.commit()
    email, job_id, iv_id = cand.email, job.id, iv.id

    resp = client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_interviews"] == 1

    db.expire_all()
    # The live rows really are gone — this is a hard delete, not a soft one.
    assert db.query(Interview).filter(Interview.id == iv_id).first() is None
    assert db.query(InterviewFeedback).filter(
        InterviewFeedback.interview_id == iv_id).first() is None

    # ...but the record of who ran it survives on the pool entry.
    rows = _pool_rows(db, hr_company, email)
    assert len(rows) == 1
    archived = json.loads(rows[0].archived_applications)
    assert len(archived) == 1
    interviews = archived[0]["interviews"]
    assert len(interviews) == 1

    snap = interviews[0]
    assert snap["interviewers"] == ["Asha Rao", "Ben Park"]
    assert snap["hire_recommendation"] == "hire"
    assert snap["overall_score"] == 82.0
    assert snap["interview_type"] == "TECHNICAL"
    assert snap["held_at"]


def test_talent_pool_replays_the_preserved_interview(client, db, hr_headers, hr_company):
    """The snapshot is only useful if HR can actually see it."""
    job, cand = _job_and_candidate(db, hr_company, stage="interview_completed")
    _completed_interview(db, hr_company.id, job.id, cand.id, panel=PANEL,
                         score=45.0, recommendation="no_hire")
    db.commit()
    email, job_id = cand.email, job.id

    client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)

    resp = client.get("/api/v1/hr/resume-pool", params={"per_page": 100},
                      headers=hr_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = body.get("results") or body.get("items") or body
    person = next(r for r in rows if r.get("email") == email)

    app = next(a for a in person["applications"] if a["archived"])
    assert app["interviews"], "the preserved interview did not reach the API"
    snap = app["interviews"][0]
    assert snap["interviewers"] == ["Asha Rao", "Ben Park"]
    assert snap["hire_recommendation"] == "no_hire"


def test_a_scheduled_but_unheld_interview_is_not_archived(client, db, hr_headers,
                                                          hr_company):
    """Only work that actually happened is worth preserving; a future booking has no
    outcome and its panel is not a record of anything."""
    from src.models.models import Interview

    job, cand = _job_and_candidate(db, hr_company, stage="interview")
    iv = _completed_interview(db, hr_company.id, job.id, cand.id, panel=PANEL)
    iv.status = "SCHEDULED"
    db.commit()
    email, job_id = cand.email, job.id

    resp = client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_interviews"] == 0

    db.expire_all()
    archived = json.loads(_pool_rows(db, hr_company, email)[0].archived_applications)
    assert archived[0]["interviews"] == []


def test_lowercase_completed_status_is_still_archived(client, db, hr_headers, hr_company):
    """Both casings exist in older rows; a plain == would drop the lowercase ones."""
    job, cand = _job_and_candidate(db, hr_company, stage="interview_completed")
    iv = _completed_interview(db, hr_company.id, job.id, cand.id, panel=PANEL)
    iv.status = "completed"
    db.commit()
    email, job_id = cand.email, job.id

    client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)

    db.expire_all()
    archived = json.loads(_pool_rows(db, hr_company, email)[0].archived_applications)
    assert len(archived[0]["interviews"]) == 1, "lowercase-status interview was dropped"


def test_deleting_a_job_with_no_interviews_still_works(client, db, hr_headers, hr_company):
    """The common case must not regress: no interviews, no snapshot, no error."""
    job, cand = _job_and_candidate(db, hr_company)
    db.commit()
    email, job_id = cand.email, job.id

    resp = client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_interviews"] == 0

    db.expire_all()
    archived = json.loads(_pool_rows(db, hr_company, email)[0].archived_applications)
    assert archived[0]["job_title"] == "Backend Engineer"
    assert archived[0]["interviews"] == []


def _template(db, company_id, job_id, *, author="Asha Rao",
              author_email="asha@example.com", title="MCQ Round", status="READY"):
    """A test written for a job: no candidate, not a scheduled meeting."""
    from src.models.models import Interview

    iv = Interview(
        company_id=company_id,
        title=title,
        job_id=job_id,
        candidate_id=None,
        interview_type="TECHNICAL",
        status=status,
        is_scheduled_interview=False,
        interviewer_name=author,
        interviewer_email=author_email,
        created_by=author_email,
    )
    db.add(iv)
    db.flush()
    return iv


def test_deleting_a_job_records_who_authored_its_tests(client, db, hr_headers, hr_company):
    """A template has no candidate, so it cannot ride along on a pool entry — without the
    archive row its author is erased outright."""
    from src.models.models import Interview, JobDeletionArchive

    job, _cand = _job_and_candidate(db, hr_company)
    tpl = _template(db, hr_company.id, job.id)
    db.commit()
    job_id, tpl_id = job.id, tpl.id

    resp = client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_tests"] == 1

    db.expire_all()
    assert db.query(Interview).filter(Interview.id == tpl_id).first() is None

    row = db.query(JobDeletionArchive).filter(
        JobDeletionArchive.job_id == str(job_id)).first()
    assert row is not None, "no archive row was written"
    assert row.job_title == "Backend Engineer"
    tests = json.loads(row.tests)
    assert len(tests) == 1
    assert tests[0]["author_name"] == "Asha Rao"
    assert tests[0]["author_email"] == "asha@example.com"
    assert tests[0]["title"] == "MCQ Round"


def test_deleted_jobs_endpoint_lists_the_authors(client, db, hr_headers, hr_company):
    """The record is only worth keeping if HR can read it back."""
    job, _cand = _job_and_candidate(db, hr_company)
    _template(db, hr_company.id, job.id, author="Ben Park", author_email="ben@example.com")
    db.commit()
    job_id = job.id

    client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)

    resp = client.get("/api/v1/hr/deleted-jobs", headers=hr_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    entry = next(r for r in body["results"] if r["job_id"] == str(job_id))
    assert entry["job_title"] == "Backend Engineer"
    assert entry["deleted_by"]
    assert [t["author_name"] for t in entry["tests"]] == ["Ben Park"]


def test_archive_counts_candidates_and_interviews_too(client, db, hr_headers, hr_company):
    from src.models.models import JobDeletionArchive

    job, cand = _job_and_candidate(db, hr_company, stage="interview_completed")
    _completed_interview(db, hr_company.id, job.id, cand.id, panel=PANEL)
    _template(db, hr_company.id, job.id)
    db.commit()
    job_id = job.id

    client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)

    db.expire_all()
    row = db.query(JobDeletionArchive).filter(
        JobDeletionArchive.job_id == str(job_id)).first()
    assert row.candidates_archived == 1
    assert row.interviews_archived == 1


def test_a_job_with_no_tests_writes_an_archive_row_anyway(client, db, hr_headers, hr_company):
    """The row also records WHO deleted the job, which is worth having on its own."""
    from src.models.models import JobDeletionArchive

    job, _cand = _job_and_candidate(db, hr_company)
    db.commit()
    job_id = job.id

    resp = client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)
    assert resp.json()["archived_tests"] == 0

    db.expire_all()
    row = db.query(JobDeletionArchive).filter(
        JobDeletionArchive.job_id == str(job_id)).first()
    assert row is not None
    assert row.tests is None


@pytest.mark.security
def test_deleted_jobs_are_scoped_to_the_callers_company(client, db, hr_headers, hr_company):
    from src.models.models import Company, JobDeletionArchive

    # A real second tenant — company_id is an FK, so a fabricated id cannot be inserted.
    other_co = Company(id=_uid(), name="Other Co", slug=f"other-{_uid()[:8]}")
    db.add(other_co)
    db.flush()

    other = JobDeletionArchive(
        id=_uid(),
        company_id=other_co.id,
        job_id=_uid(),
        job_title="Someone Else's Job",
        tests=json.dumps([{"author_name": "Not Yours"}]),
    )
    db.add(other)
    db.commit()

    resp = client.get("/api/v1/hr/deleted-jobs", headers=hr_headers)
    assert resp.status_code == 200, resp.text
    titles = [r["job_title"] for r in resp.json()["results"]]
    assert "Someone Else's Job" not in titles
