"""
Cross-tenant regression tests for routes/advanced_features_routes.py and
routes/judge_routes.py.

Every route in advanced_features_routes.py once looked rows up by id alone and relied
on the tenant auto-filter, which was dormant — so any company's staff could read,
change and export any other company's candidates. judge_routes.py served any company's
reference solutions and hidden tests to any admin or HR account the same way. Each
test here uses two companies and asserts that company A's credentials get a 404 (or
an empty result) for company B's ids.
"""
import csv
import io
import json
import uuid
from types import SimpleNamespace

import pytest


# ─── helpers ────────────────────────────────────────────────────────────────

def _company(db, name):
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c.id


def _user(db, role, company_id, email=None):
    from src.models.models import User
    from src.auth import get_password_hash
    u = User(id=str(uuid.uuid4()), email=email or f"{role}_{uuid.uuid4().hex[:8]}@t.com",
             password_hash=get_password_hash("Pass@1234"), role=role,
             first_name=role.capitalize(), last_name="T", is_active=True,
             email_verified=True, company_id=company_id)
    db.add(u)
    db.flush()
    return u


def _hr_profile(db, user):
    from src.models.models import HRProfile
    p = HRProfile(id=str(uuid.uuid4()), user_id=user.id, company_id=user.company_id,
                  employee_id=f"E-{uuid.uuid4().hex[:8]}")
    db.add(p)
    db.flush()
    return p


def _headers(user):
    """A token shaped like a real signed-in session: second factor included, so the
    tests don't depend on whether MFA enforcement is switched on for admins."""
    from src.auth import create_access_token
    token = create_access_token({"sub": user.id, "type": "access", "mfa": True,
                                 "tv": getattr(user, "token_version", 0) or 0})
    return {"Authorization": f"Bearer {token}"}


def _tenant(db, name):
    """A company with an admin, an HR who owns one job, an interviewer, and a
    candidate on that job with one scheduled interview led by the interviewer."""
    from datetime import datetime, timedelta
    from src.models.models import Candidate, Interview, JobPosting

    cid = _company(db, name)
    admin = _user(db, "admin", cid)
    hr = _user(db, "hr", cid)
    hr_p = _hr_profile(db, hr)
    interviewer = _user(db, "interviewer", cid)
    job = JobPosting(id=str(uuid.uuid4()), company_id=cid, title=f"{name} Engineer",
                     description="d", requirements="r", status="active", hr_id=hr_p.id,
                     required_skills=json.dumps(["python"]))
    db.add(job)
    db.flush()
    cand = Candidate(id=str(uuid.uuid4()), company_id=cid, job_id=job.id,
                     email=f"cand_{uuid.uuid4().hex[:6]}@t.com", first_name=name,
                     last_name="Applicant", current_stage="applied",
                     expected_salary=1234567, ats_score=70)
    db.add(cand)
    db.flush()
    iv = Interview(company_id=cid, title="Tech round", job_id=job.id, candidate_id=cand.id,
                   status="SCHEDULED", interviewer_email=interviewer.email,
                   scheduled_datetime=datetime.utcnow() + timedelta(days=2),
                   duration_minutes=60)
    db.add(iv)
    db.flush()
    return SimpleNamespace(id=cid, admin=_headers(admin), hr=_headers(hr),
                           interviewer=_headers(interviewer), hr_user=hr,
                           job=job, cand=cand, iv=iv)


@pytest.fixture()
def tenants(db):
    return SimpleNamespace(a=_tenant(db, "Acme"), b=_tenant(db, "Beta"))


ADV = "/api/v1/advanced"


# ─── advanced features ─────────────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.parametrize("path", [
    "/candidates/{cand}/profile",
    "/candidates/{cand}/scorecard",
    "/candidates/{cand}/notes",
    "/jobs/{job}/funnel",
    "/jobs/{job}/timeline",
    "/jobs/{job}/skill-gap",
    "/jobs/{job}/export",
])
def test_advanced_reads_are_company_scoped(client, tenants, path):
    a, b = tenants.a, tenants.b
    own = path.format(cand=a.cand.id, job=a.job.id)
    other = path.format(cand=b.cand.id, job=b.job.id)
    assert client.get(ADV + own, headers=a.admin).status_code == 200
    assert client.get(ADV + other, headers=a.admin).status_code == 404


@pytest.mark.security
def test_advanced_search_lists_only_own_company(client, tenants):
    a, b = tenants.a, tenants.b
    body = client.get(f"{ADV}/candidates/search", headers=a.admin).json()
    ids = {c["id"] for c in body["candidates"]}
    assert a.cand.id in ids and b.cand.id not in ids


@pytest.mark.security
def test_advanced_compare_rejects_other_company(client, tenants):
    a, b = tenants.a, tenants.b
    r = client.post(f"{ADV}/candidates/compare", headers=a.admin,
                    json={"candidate_ids": [a.cand.id, b.cand.id]})
    assert r.status_code == 404


@pytest.mark.security
def test_advanced_bulk_action_cannot_move_other_company(client, db, tenants):
    a, b = tenants.a, tenants.b
    r = client.post(f"{ADV}/candidates/bulk-action", headers=a.admin,
                    json={"candidate_ids": [b.cand.id], "action": "reject"})
    assert r.status_code == 200
    assert r.json()["failed"] == [{"candidate_id": b.cand.id, "reason": "not found"}]
    db.expire_all()
    from src.models.models import Candidate
    from src.tenancy import tenant_bypass
    with tenant_bypass():
        assert db.get(Candidate, b.cand.id).current_stage == "applied"


@pytest.mark.security
def test_advanced_notes_cannot_be_written_to_other_company(client, tenants):
    a, b = tenants.a, tenants.b
    r = client.post(f"{ADV}/candidates/{b.cand.id}/notes", headers=a.admin,
                    json={"note": "planted", "visibility": "all"})
    assert r.status_code == 404


@pytest.mark.security
def test_advanced_reschedule_and_clone_are_company_scoped(client, tenants):
    a, b = tenants.a, tenants.b
    r = client.post(f"{ADV}/interviews/{b.iv.id}/reschedule", headers=a.admin,
                    json={"new_datetime": "2099-01-01T10:00:00"})
    assert r.status_code == 404
    assert client.post(f"{ADV}/jobs/{b.job.id}/clone", headers=a.admin,
                       json={}).status_code == 404


@pytest.mark.security
def test_advanced_clone_keeps_the_company(client, db, tenants):
    from src.models.models import JobPosting
    a = tenants.a
    r = client.post(f"{ADV}/jobs/{a.job.id}/clone", headers=a.admin, json={})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(JobPosting, r.json()["new_job_id"]).company_id == a.id


@pytest.mark.security
def test_advanced_calendar_and_overview_count_only_own_company(client, tenants):
    a, b = tenants.a, tenants.b
    cal = client.get(f"{ADV}/calendar", headers=a.admin).json()
    ids = {e["interview_id"] for e in cal["flat_list"]}
    assert a.iv.id in ids and b.iv.id not in ids
    overview = client.get(f"{ADV}/analytics/overview", headers=a.admin).json()
    assert overview["candidates"]["total"] == 1
    assert overview["interviews"]["total"] == 1


@pytest.mark.security
def test_advanced_hr_is_limited_to_their_own_jobs(client, db, tenants):
    """An HR in the same company who is not on the job's team can't read its pipeline."""
    a = tenants.a
    other_hr = _user(db, "hr", a.id)
    _hr_profile(db, other_hr)
    assert client.get(f"{ADV}/jobs/{a.job.id}/funnel", headers=a.hr).status_code == 200
    assert client.get(f"{ADV}/jobs/{a.job.id}/funnel",
                      headers=_headers(other_hr)).status_code == 404
    assert client.get(f"{ADV}/candidates/{a.cand.id}/profile",
                      headers=_headers(other_hr)).status_code == 404


@pytest.mark.security
def test_advanced_interviewer_sees_only_assigned_candidates_without_pay(client, db, tenants):
    from src.models.models import Candidate
    a = tenants.a
    body = client.get(f"{ADV}/candidates/{a.cand.id}/profile", headers=a.interviewer).json()
    assert "expected_salary" not in body["candidate"]
    assert "current_salary" not in body["candidate"]
    admin_body = client.get(f"{ADV}/candidates/{a.cand.id}/profile", headers=a.admin).json()
    assert admin_body["candidate"]["expected_salary"] == 1234567

    unassigned = Candidate(id=str(uuid.uuid4()), company_id=a.id, job_id=a.job.id,
                           email="u@t.com", first_name="Un", last_name="Assigned")
    db.add(unassigned)
    db.flush()
    assert client.get(f"{ADV}/candidates/{unassigned.id}/profile",
                      headers=a.interviewer).status_code == 404


@pytest.mark.security
def test_advanced_hr_only_notes_are_hidden_from_interviewers(client, tenants):
    a = tenants.a
    client.post(f"{ADV}/candidates/{a.cand.id}/notes", headers=a.admin,
                json={"note": "salary expectations too high", "visibility": "hr"})
    client.post(f"{ADV}/candidates/{a.cand.id}/notes", headers=a.admin,
                json={"note": "ask about the API project", "visibility": "interviewer"})
    notes = client.get(f"{ADV}/candidates/{a.cand.id}/notes", headers=a.interviewer).json()["notes"]
    assert [n["note"] for n in notes] == ["ask about the API project"]
    profile = client.get(f"{ADV}/candidates/{a.cand.id}/profile", headers=a.interviewer).json()
    assert all(h["from_stage"] != "__note__" for h in profile["stage_history"])


@pytest.mark.security
def test_advanced_export_neutralises_formula_injection(client, db, tenants):
    a = tenants.a
    a.cand.first_name = '=HYPERLINK("http://evil.example","x")'
    db.flush()
    r = client.get(f"{ADV}/jobs/{a.job.id}/export", headers=a.admin)
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0]["Name"].startswith("'=HYPERLINK")


@pytest.mark.security
def test_advanced_question_copy_rejects_other_companys_question(client, db, tenants):
    """add-to-interview copies the answer key, so the source must be this company's."""
    from src.models.question_models import Question
    a, b = tenants.a, tenants.b
    theirs = Question(id=str(uuid.uuid4()), interview_id=b.iv.id, question_text="Secret",
                      question_type="multiple_choice", difficulty_level="Easy",
                      correct_answer="B")
    db.add(theirs)
    db.flush()
    r = client.post(f"{ADV}/question-bank/{theirs.id}/add-to-interview/{a.iv.id}",
                    headers=a.admin)
    assert r.status_code == 404
    # ...and a question can't be copied into another company's interview either.
    mine = Question(id=str(uuid.uuid4()), interview_id=a.iv.id, question_text="Mine",
                    question_type="multiple_choice", difficulty_level="Easy")
    db.add(mine)
    db.flush()
    r = client.post(f"{ADV}/question-bank/{mine.id}/add-to-interview/{b.iv.id}",
                    headers=a.admin)
    assert r.status_code == 404


# ─── coding judge ──────────────────────────────────────────────────────────

JUDGE = "/api/v1/judge"


def _problem(db, company_id):
    from src.models.judge_models import CodingProblem, ProblemTestCase
    p = CodingProblem(id=str(uuid.uuid4()), company_id=company_id, title="Sum",
                      reference_solution="print(sum(map(int, input().split())))",
                      status="published")
    db.add(p)
    db.flush()
    db.add(ProblemTestCase(company_id=company_id, problem_id=p.id, input="1 2",
                           expected_output="3", is_hidden=True))
    db.flush()
    return p


@pytest.mark.security
def test_judge_problems_are_company_scoped(client, db, tenants):
    a, b = tenants.a, tenants.b
    theirs = _problem(db, b.id)
    mine = _problem(db, a.id)

    assert client.get(f"{JUDGE}/problems/{mine.id}", headers=a.admin).status_code == 200
    assert client.get(f"{JUDGE}/problems/{theirs.id}", headers=a.admin).status_code == 404
    assert client.get(f"{JUDGE}/problems/{theirs.id}/testcases",
                      headers=a.admin).status_code == 404
    assert client.put(f"{JUDGE}/problems/{theirs.id}", headers=a.admin,
                      json={"title": "hijacked"}).status_code == 404
    assert client.post(f"{JUDGE}/problems/{theirs.id}/testcases", headers=a.admin,
                       json=[{"input": "x", "expected_output": "y"}]).status_code == 404
    listed = {p["id"] for p in client.get(f"{JUDGE}/problems", headers=a.admin).json()["problems"]}
    assert mine.id in listed and theirs.id not in listed


@pytest.mark.security
def test_judge_new_problem_belongs_to_the_creators_company(client, db, tenants):
    from src.models.judge_models import CodingProblem
    a = tenants.a
    r = client.post(f"{JUDGE}/problems", headers=a.admin,
                    json={"title": "New", "test_cases": [{"input": "1", "expected_output": "1"}]})
    assert r.status_code == 201, r.text
    db.expire_all()
    assert db.get(CodingProblem, r.json()["id"]).company_id == a.id


@pytest.mark.security
def test_judge_submissions_are_company_scoped(client, db, tenants):
    from src.models.judge_models import CodeSubmission
    a, b = tenants.a, tenants.b
    theirs = _problem(db, b.id)
    sub = CodeSubmission(id=str(uuid.uuid4()), company_id=b.id, problem_id=theirs.id,
                         user_id=tenants.b.hr_user.id, language="python",
                         source_code="print(3)", status="done")
    db.add(sub)
    db.flush()
    assert client.get(f"{JUDGE}/submissions/{sub.id}", headers=a.admin).status_code == 404
    listed = client.get(f"{JUDGE}/submissions", headers=a.admin).json()["submissions"]
    assert sub.id not in {s["id"] for s in listed}


@pytest.mark.security
def test_judge_warmup_rejects_other_companys_interview(client, tenants):
    a, b = tenants.a, tenants.b
    assert client.post(f"{JUDGE}/warmup", headers=a.admin,
                       json={"interview_id": b.iv.id}).status_code == 404


@pytest.mark.security
def test_judge_regrade_token_not_accepted_in_query_string(client, monkeypatch):
    monkeypatch.setenv("JUDGE_REGRADE_TOKEN", "sweeper-secret")
    r = client.post(f"{JUDGE}/regrade-pending?token=sweeper-secret")
    assert r.status_code == 401
