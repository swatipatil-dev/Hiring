"""
The ATS sheet lists every applicant to an open job, whatever their stage and
whether or not their ATS result can be read. It used to inner-join the result,
so an applicant whose scoring failed, or whose result still had a NULL
company_id from before tenancy, was missing; a row that failed to build was
dropped too. HR saw "2 candidates" for a job with more.
"""
import json
import uuid

import pytest

from src.models.models import ATSResult, Candidate, JobPosting, User


@pytest.fixture
def job(db, hr_headers):
    from routes.hr_routes import get_hr_profile
    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    j = JobPosting(id=str(uuid.uuid4()), company_id=hr.company_id, title="AI Engineer",
                   description="Build models", requirements="Python",
                   required_skills=json.dumps(["Python"]), experience_years=1,
                   status="active", hr_id=get_hr_profile(db, hr).id)
    db.add(j)
    db.flush()
    return j


def _candidate(db, job, name, stage, ats_score=None):
    c = Candidate(id=str(uuid.uuid4()), company_id=job.company_id, job_id=job.id,
                  email=f"{name}@t.com", first_name=name, last_name="X",
                  current_stage=stage, ats_score=ats_score)
    db.add(c)
    db.flush()
    return c


def _result(db, job, cand, score, company_id="job", **kw):
    r = ATSResult(id=str(uuid.uuid4()), candidate_id=cand.id, job_id=job.id,
                  company_id=job.company_id if company_id == "job" else company_id,
                  ats_score=score, **kw)
    db.add(r)
    db.flush()
    return r


def test_every_applicant_is_on_the_sheet(client, db, hr_headers, job):
    scored = _candidate(db, job, "scored", "shortlisted")
    _result(db, job, scored, 60)
    tested = _candidate(db, job, "tested", "test_completed")
    _result(db, job, tested, 41)
    unscored = _candidate(db, job, "unscored", "applied")
    legacy = _candidate(db, job, "legacy", "technical_interview", ats_score=36)
    _result(db, job, legacy, 36, company_id=None)       # hidden by the tenant filter
    broken = _candidate(db, job, "broken", "test_completed")
    _result(db, job, broken, 55, matched_skills=json.dumps({"not": "a list"}))

    r = client.get(f"/api/v1/hr/jobs/{job.id}/candidates", headers=hr_headers,
                   params={"per_page": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {c["candidate_id"]: c for c in body["candidates"]}

    assert set(rows) == {scored.id, tested.id, unscored.id, legacy.id, broken.id}
    assert body["total_candidates"] == 5
    # Ranked by score; the unscored applicant last, labelled rather than scored 0.
    assert [c["candidate_id"] for c in body["candidates"]][:2] == [scored.id, broken.id]
    assert body["candidates"][-1]["candidate_id"] == unscored.id
    assert rows[unscored.id]["ats_level"] == "Not scored"
    assert rows[legacy.id]["ats_score"] == 36
    assert rows[broken.id]["ats_score"] == 55
