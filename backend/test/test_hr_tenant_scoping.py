"""
HR routes: cross-company and job-team scoping.

Every cross-company test runs with the ORM tenant auto-filter switched OFF
(`no_autofilter`). The auto-filter is the safety net; these tests check the
explicit company scoping in each handler, which must hold on its own — the net
sat dormant for months without anyone noticing (see test_tenant_isolation).
With the handler's explicit scope reverted, each of these fails.
"""
import json
import uuid

import pytest

from test.test_tenant_isolation import _make_company, _make_user, _login, _make_job

pytestmark = pytest.mark.security


@pytest.fixture()
def no_autofilter(monkeypatch):
    """Disable the tenant auto-filter (and insert stamp) for the test."""
    import src.tenancy.events as events
    monkeypatch.setattr(events, "get_current_company", lambda: None)


def _hr(db, company_id, email=None):
    from src.models.models import HRProfile
    email = email or f"hr_{uuid.uuid4().hex[:6]}@t.com"
    uid = _make_user(db, email, "hr", company_id=company_id)
    prof = HRProfile(id=str(uuid.uuid4()), user_id=uid, company_id=company_id,
                     employee_id=f"E-{uuid.uuid4().hex[:8]}")
    db.add(prof)
    db.flush()
    return email, uid, prof.id


def _candidate(db, company_id, job_id, email=None, user_id=None, stage="test_completed"):
    from src.models.models import Candidate
    c = Candidate(id=str(uuid.uuid4()), company_id=company_id, job_id=job_id,
                  user_id=user_id, email=email or f"c_{uuid.uuid4().hex[:6]}@t.com",
                  first_name="Cand", last_name="Idate", current_stage=stage)
    db.add(c)
    db.flush()
    return c


def _interview(db, company_id, job_id, candidate_id=None, status="COMPLETED", **kw):
    from src.models.models import Interview
    iv = Interview(company_id=company_id, job_id=job_id, candidate_id=candidate_id,
                   title="Test", status=status, is_scheduled_interview=False, **kw)
    db.add(iv)
    db.flush()
    return iv


@pytest.fixture()
def world(client, db):
    """Two companies. Each has an owner HR on one job with a candidate and a
    completed test; company A also has a second HR who is on no job, and an admin
    (not logged in)."""
    a, b = _make_company(db, "HR Scope A"), _make_company(db, "HR Scope B")
    out = {}
    for key, cid in (("a", a), ("b", b)):
        email, uid, hr_id = _hr(db, cid)
        job = _make_job(db, cid, title=f"Role {key}")
        from src.models.models import JobPosting
        j = db.get(JobPosting, job)
        j.hr_id = hr_id
        j.salary_min, j.salary_max = 100, 200
        cand = _candidate(db, cid, job)
        template = _interview(db, cid, job, status="READY")
        attempt = _interview(db, cid, job, candidate_id=cand.id,
                             answers_json=json.dumps([]), test_score=5, total_marks=10)
        out[key] = {"company_id": cid, "hr_email": email, "hr_user": uid, "hr_id": hr_id,
                    "job_id": job, "candidate": cand, "template": template, "attempt": attempt}
    other_email, _, _ = _hr(db, a)
    _make_user(db, f"admin_{uuid.uuid4().hex[:6]}@t.com", "admin", company_id=a)
    db.commit()
    out["a"]["headers"] = _login(client, out["a"]["hr_email"])
    out["b"]["headers"] = _login(client, out["b"]["hr_email"])
    out["a_other_hr"] = _login(client, other_email)
    return out


# ── Cross-company ────────────────────────────────────────────────────────────

def test_test_results_list_is_company_scoped(client, world, no_autofilter):
    rows = client.get("/api/v1/hr/test-results", headers=world["a"]["headers"]).json()
    ids = {r.get("id") for r in rows}
    assert world["b"]["attempt"].id not in ids
    assert world["a"]["attempt"].id in ids


def test_test_result_answers_blocks_other_company(client, world, no_autofilter):
    r = client.get(f"/api/v1/hr/test-results/{world['b']['attempt'].id}/answers",
                   headers=world["a"]["headers"])
    assert r.status_code == 404, r.text


def test_stage_history_blocks_other_company(client, world, no_autofilter):
    r = client.get(f"/api/v1/hr/candidates/{world['b']['candidate'].id}/stage-history",
                   headers=world["a"]["headers"])
    assert r.status_code == 404, r.text


def test_schedule_interview_blocks_other_company_candidate(client, db, world, no_autofilter):
    from src.models.models import InterviewerProfile
    iv_uid = _make_user(db, f"iv_{uuid.uuid4().hex[:6]}@t.com", "interviewer",
                        company_id=world["a"]["company_id"])
    prof = InterviewerProfile(id=str(uuid.uuid4()), user_id=iv_uid,
                              company_id=world["a"]["company_id"], employee_id="IV-X")
    db.add(prof)
    db.commit()
    r = client.post("/api/v1/hr/interviews", headers=world["a"]["headers"], json={
        "candidate_id": world["b"]["candidate"].id, "interviewer_id": prof.id,
        "interview_type": "technical", "scheduled_datetime": "2030-01-01T10:00:00",
        "duration_minutes": 30,
    })
    assert r.status_code == 404, r.text
    db.refresh(world["b"]["candidate"])
    assert world["b"]["candidate"].current_stage == "test_completed"


def test_offer_blocks_other_company_candidate(client, db, world, no_autofilter):
    r = client.post("/api/v1/hr/offers", headers=world["a"]["headers"], json={
        "candidate_id": world["b"]["candidate"].id, "offer_type": "full_time",
        "salary": 1, "start_date": "2030-01-01", "expiry_date": "2030-02-01",
    })
    assert r.status_code == 404, r.text


def test_candidate_requests_are_company_scoped(client, db, world, no_autofilter):
    from src.models.models import CandidateRequest
    req = CandidateRequest(id=str(uuid.uuid4()), company_id=world["b"]["company_id"],
                           requested_by=world["b"]["hr_user"], job_title="Role b",
                           job_description="secret JD")
    db.add(req)
    db.commit()
    body = client.get("/api/v1/hr/my-candidate-requirements",
                      headers=world["a"]["headers"]).json()
    seen = {r.get("id") for r in body["unmatched_requests"]}
    for j in body["jobs"]:
        seen |= {r.get("id") for r in j["candidate_requests"]}
    assert req.id not in seen
    r = client.put(f"/api/v1/hr/candidate-requests/{req.id}",
                   headers=world["a"]["headers"], json={"status": "rejected"})
    assert r.status_code == 404, r.text


def test_send_invite_rejects_other_company_template(client, world, no_autofilter):
    r = client.post(f"/api/v1/hr/jobs/{world['a']['job_id']}/send-test-invite",
                    headers=world["a"]["headers"],
                    json={"candidate_ids": [], "test_deadline": "2030-01-01",
                          "test_id": world["b"]["template"].id})
    assert r.status_code == 404, r.text


def test_round2_rejects_other_company_template(client, world, no_autofilter):
    r = client.post(f"/api/v1/hr/jobs/{world['a']['job_id']}/send-round2-invite",
                    headers=world["a"]["headers"],
                    json={"candidate_ids": [], "source_test_id": world["b"]["template"].id})
    assert r.status_code == 404, r.text


def test_job_tests_blocks_other_company(client, world, no_autofilter):
    r = client.get(f"/api/v1/hr/jobs/{world['b']['job_id']}/tests",
                   headers=world["a"]["headers"])
    assert r.status_code == 404, r.text


def test_feedback_is_company_scoped(client, db, world, no_autofilter):
    from src.models.models import InterviewFeedback
    fb = InterviewFeedback(id=str(uuid.uuid4()), company_id=world["b"]["company_id"],
                           interview_id=world["b"]["attempt"].id, strengths="[]",
                           weaknesses="[]", overall_score=4)
    db.add(fb)
    db.commit()
    rows = client.get("/api/v1/hr/feedback", headers=world["a"]["headers"]).json()
    assert fb.id not in {r["feedback_id"] for r in rows}


# ── Within a company: the job team ───────────────────────────────────────────

def test_hr_off_the_job_cannot_edit_it(client, world):
    job_id = world["a"]["job_id"]
    payload = {"title": "Renamed", "description": "d", "requirements": "r",
               "location": "Remote", "employment_type": "full_time",
               "experience_years": 0, "required_skills": []}
    r = client.put(f"/api/v1/hr/jobs/{job_id}", headers=world["a_other_hr"], json=payload)
    assert r.status_code == 403, r.text
    assert client.delete(f"/api/v1/hr/jobs/{job_id}",
                         headers=world["a_other_hr"]).status_code == 403
    # The owner can.
    assert client.put(f"/api/v1/hr/jobs/{job_id}", headers=world["a"]["headers"],
                      json=payload).status_code == 200


def test_admin_passes_the_job_team_check(db, world):
    # Checked on the helper: admin logins go through MFA, which this suite doesn't drive.
    from routes.hr_routes import _require_job_team
    from src.models.models import JobPosting, User
    admin = db.query(User).filter(User.role == "admin",
                                  User.company_id == world["a"]["company_id"]).first()
    _require_job_team(db, db.get(JobPosting, world["a"]["job_id"]), admin)  # no raise


def test_hr_off_the_job_cannot_act_on_its_candidates(client, world):
    cand = world["a"]["candidate"]
    r = client.post("/api/v1/hr/test-results/action", headers=world["a_other_hr"],
                    json={"candidate_id": cand.id, "action": "reject"})
    assert r.status_code == 403, r.text
    r = client.get(f"/api/v1/hr/test-results/{world['a']['attempt'].id}/answers",
                   headers=world["a_other_hr"])
    assert r.status_code == 403, r.text
    r = client.get(f"/api/v1/hr/candidates/{cand.id}/stage-history",
                   headers=world["a_other_hr"])
    assert r.status_code == 403, r.text


def test_test_results_list_only_shows_hrs_own_jobs(client, world):
    rows = client.get("/api/v1/hr/test-results", headers=world["a_other_hr"]).json()
    assert world["a"]["attempt"].id not in {r.get("id") for r in rows}


def test_job_response_hides_salary_from_hr_off_the_job(db, world):
    from routes.hr_routes import create_job_response
    from src.models.models import JobPosting, User
    job = db.get(JobPosting, world["a"]["job_id"])
    outsider = db.query(User).filter(User.role == "hr",
                                     User.company_id == world["a"]["company_id"],
                                     User.id != world["a"]["hr_user"]).first()
    owner = db.get(User, world["a"]["hr_user"])
    hidden = create_job_response(job, viewer=outsider, viewer_hr_id="not-on-this-job")
    shown = create_job_response(job, viewer=owner, viewer_hr_id=world["a"]["hr_id"])
    assert hidden.salary_min is None and hidden.salary_max is None
    assert shown.salary_min == 100 and shown.salary_max == 200


# ── What a candidate sees ────────────────────────────────────────────────────

def test_candidate_never_sees_internal_notes(client, db, world):
    from src.models.models import CandidateStageHistory
    cid = world["a"]["company_id"]
    email = f"self_{uuid.uuid4().hex[:6]}@t.com"
    uid = _make_user(db, email, "candidate", company_id=cid)
    app = _candidate(db, cid, world["a"]["job_id"], email=email, user_id=uid)
    db.add_all([
        CandidateStageHistory(company_id=cid, candidate_id=app.id, from_stage="hr_review",
                              to_stage="rejected", notes="Weak communicator, bad culture fit"),
        CandidateStageHistory(company_id=cid, candidate_id=app.id, from_stage="__note__",
                              to_stage="hr", notes="Internal: salary ask too high"),
    ])
    db.commit()
    body = client.get(f"/api/v1/candidate/applications/{app.id}",
                      headers=_login(client, email)).json()
    raw = json.dumps(body)
    assert "Weak communicator" not in raw and "salary ask" not in raw
    assert all(h["from_stage"] != "__note__" for h in body["stage_history"])
    assert [h["to_stage"] for h in body["stage_history"]] == ["rejected"]
