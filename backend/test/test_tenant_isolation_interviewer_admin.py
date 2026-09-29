"""Cross-tenant and role checks for the interviewer, evaluation, MCQ and admin routes.

Each test pins one hole found in the tenant-isolation audit. Company A's users attack
Company B's rows by id; a NULL-company row (legacy data) must be reachable by nobody.
Every one of these returned 200 before the fix.
"""
import json
import uuid

import pytest

from test.test_tenant_isolation import (
    _make_company,
    _make_user,
    _login,
    _make_job,
    _make_interview,
)

pytestmark = pytest.mark.security


@pytest.fixture()
def co(client, db):
    """Two companies, each with an admin, an HR (with profile), an interviewer, a
    candidate, a job and a draft test with one approved question."""
    from src.models.models import HRProfile, Department
    from src.models.question_models import Question

    out = {}
    for key in ("a", "b"):
        cid = _make_company(db, name=f"IV {key.upper()}")
        tag = uuid.uuid4().hex[:6]
        emails = {r: f"{r}_{key}_{tag}@t.com" for r in ("admin", "hr", "interviewer", "candidate")}
        ids = {r: _make_user(db, e, r, company_id=cid) for r, e in emails.items()}
        dept = Department(id=str(uuid.uuid4()), company_id=cid, name=f"Eng {key}")
        db.add(dept)
        hrp = HRProfile(id=str(uuid.uuid4()), company_id=cid, user_id=ids["hr"],
                        department_id=dept.id, employee_id=f"HR-{tag}{key}")
        db.add(hrp)
        job_id = _make_job(db, cid, title=f"Role {key}")
        iv_id = _make_interview(db, cid, job_id=job_id)
        db.add(Question(interview_id=iv_id, company_id=cid, question_text="Q?",
                        question_type="multiple_choice", difficulty_level="Easy",
                        correct_answer="A", is_approved=True))
        # Committed, not just flushed: several handlers call db.rollback() on their 404
        # path, which would otherwise discard these rows mid-test. conftest's savepoint
        # recipe still rolls everything back at teardown.
        db.commit()
        out[key] = {
            "company_id": cid, "job_id": job_id, "interview_id": iv_id,
            "dept_id": dept.id, "hr_profile_id": hrp.id, "emails": emails, "ids": ids,
            **{r: _login(client, e) for r, e in emails.items()},
        }
    return out


def _null_company_draft(db, job_id=None):
    from src.models.models import Interview
    iv = Interview(company_id=None, title="Legacy draft", status="DRAFT", job_id=job_id)
    db.add(iv)
    db.commit()
    return iv.id


def _token_for(db, user_id):
    """Headers for a specific user. _login() resolves by email alone, so for a user
    whose address also exists in another company it can sign in the wrong account."""
    from src.auth import create_token_response
    from src.models.models import User
    from src.tenancy import tenant_bypass
    with tenant_bypass():
        user = db.get(User, user_id)
    return {"Authorization": f"Bearer {create_token_response(user, db).access_token}"}


# ── enhanced_interviewer_routes ─────────────────────────────────────────────

def test_publish_blocks_other_company_and_candidates(client, co):
    a, b = co["a"], co["b"]
    url = f"/api/v1/interviewer/interviews/{a['interview_id']}/publish"
    body = {"hr_id": b["hr_profile_id"]}
    assert client.post(url, headers=b["hr"], json=body).status_code == 404
    assert client.post(url, headers=a["candidate"], json={"hr_id": a["hr_profile_id"]}).status_code == 403


def test_publish_does_not_reassign_other_company_job(client, db, co):
    from src.models.models import JobPosting
    a, b = co["a"], co["b"]
    client.post(f"/api/v1/interviewer/interviews/{a['interview_id']}/publish",
                headers=b["hr"], json={"hr_id": b["hr_profile_id"]})
    db.expire_all()
    from src.tenancy import tenant_bypass
    with tenant_bypass():
        assert db.get(JobPosting, a["job_id"]).hr_id != b["hr_profile_id"]


def test_null_company_draft_is_nobodys(client, db, co):
    """A legacy draft with no company used to be editable and deletable by every tenant."""
    iv_id = _null_company_draft(db)
    for key in ("a", "b"):
        headers = co[key]["hr"]
        assert client.put(f"/api/v1/interviewer/interviews/{iv_id}", headers=headers,
                          json={"title": "hijack"}).status_code == 404
        assert client.delete(f"/api/v1/interviewer/interviews/{iv_id}",
                             headers=headers).status_code == 404


def test_update_cannot_attach_other_company_job(client, co):
    a, b = co["a"], co["b"]
    resp = client.put(f"/api/v1/interviewer/interviews/{a['interview_id']}", headers=a["hr"],
                      json={"job_id": b["job_id"]})
    assert resp.status_code == 404, resp.text


def test_draft_ignores_other_company_job(client, co):
    a, b = co["a"], co["b"]
    resp = client.post("/api/v1/interviewer/interviews/draft", headers=a["interviewer"],
                       json={"title": "Probe", "job_id": b["job_id"]})
    assert resp.status_code == 200, resp.text
    # Not B's existing draft handed back, and not attached to B's job.
    assert resp.json()["id"] != b["interview_id"]


def test_draft_and_create_require_staff(client, co):
    a = co["a"]
    assert client.post("/api/v1/interviewer/interviews/draft", headers=a["candidate"],
                       json={"title": "x"}).status_code == 403
    assert client.post("/api/v1/interviewer/auto-create-job", headers=a["candidate"],
                       json={"title": "x", "description": "d", "requirements": "r"}).status_code == 403


def test_draft_is_stamped_with_company(client, db, co):
    from src.models.models import Interview
    a = co["a"]
    resp = client.post("/api/v1/interviewer/interviews/draft", headers=a["interviewer"],
                       json={"title": "Stamped"})
    assert resp.status_code == 200, resp.text
    from src.tenancy import tenant_bypass
    with tenant_bypass():
        assert db.get(Interview, resp.json()["id"]).company_id == a["company_id"]


def test_my_meetings_do_not_match_same_email_in_other_company(client, db, co):
    """Email is unique per company only: B's user with A's interviewer address must not
    see A's meetings."""
    from datetime import datetime, timedelta
    from src.models.models import Interview, Candidate
    a, b = co["a"], co["b"]
    cand = Candidate(id=str(uuid.uuid4()), company_id=a["company_id"], job_id=a["job_id"],
                     email="c@a.com", first_name="Ann", last_name="A")
    db.add(cand)
    db.add(Interview(company_id=a["company_id"], title="A meeting", status="SCHEDULED",
                     job_id=a["job_id"], candidate_id=cand.id, is_scheduled_interview=True,
                     scheduled_datetime=datetime.utcnow() + timedelta(days=1),
                     interviewer_email=a["emails"]["interviewer"]))
    twin = a["emails"]["interviewer"]
    twin_id = _make_user(db, twin, "interviewer", company_id=b["company_id"])
    db.commit()
    twin_headers = _token_for(db, twin_id)
    resp = client.get("/api/v1/interviewer/my-meetings", headers=twin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    own = client.get("/api/v1/interviewer/my-meetings", headers=a["interviewer"]).json()
    assert [m["title"] for m in own] == ["A meeting"]


def test_bank_question_of_other_or_no_company_is_unreachable(client, db, co):
    from src.models.question_models import Question
    a, b = co["a"], co["b"]
    null_iv = _null_company_draft(db)
    q_null = Question(interview_id=null_iv, question_text="orphan", question_type="multiple_choice",
                      difficulty_level="Easy", correct_answer="A")
    db.add(q_null)
    db.flush()
    from src.tenancy import tenant_bypass
    with tenant_bypass():
        q_b = db.query(Question).filter(Question.interview_id == b["interview_id"]).first()
    for qid in (q_null.id, q_b.id):
        assert client.get(f"/api/v1/interviewer/questions/{qid}/usage",
                          headers=a["hr"]).status_code == 404
        assert client.put(f"/api/v1/interviewer/questions/{qid}", headers=a["hr"],
                          json={"question_text": "hijack"}).status_code == 404


def test_templates_builtins_visible_foreign_ones_locked(client, db, co):
    from src.models.models import JDTemplate
    a, b = co["a"], co["b"]
    builtin = JDTemplate(id=str(uuid.uuid4()), company_id=None, name="Builtin",
                         is_builtin=True, source="builtin")
    orphan = JDTemplate(id=str(uuid.uuid4()), company_id=None, name="Orphan", is_builtin=False)
    theirs = JDTemplate(id=str(uuid.uuid4()), company_id=b["company_id"], name="B's")
    db.add_all([builtin, orphan, theirs])
    db.flush()

    names = {t["name"] for t in client.get("/api/v1/interviewer/templates", headers=a["hr"]).json()}
    assert "Builtin" in names
    assert "B's" not in names and "Orphan" not in names
    for t in (orphan, theirs):
        assert client.put(f"/api/v1/interviewer/templates/{t.id}", headers=a["hr"],
                          json={"name": "x"}).status_code == 404
        assert client.delete(f"/api/v1/interviewer/templates/{t.id}",
                             headers=a["hr"]).status_code == 404


# ── question-bank tenancy ──────────────────────────────────────────────────

def test_question_rows_inherit_interview_company(db, co):
    """Created with no tenant bound (a worker, a script), a question still gets its
    interview's company — otherwise the tenant filter would hide it from its owner."""
    from src.models.question_models import Question, QuestionSetAssignment, CommunicationAnswer
    from src.tenancy import set_current_company
    set_current_company(None)
    a = co["a"]
    q = Question(interview_id=a["interview_id"], question_text="new", question_type="coding",
                 difficulty_level="Hard")
    db.add(q)
    db.flush()
    qsa = QuestionSetAssignment(interview_id=a["interview_id"], question_id=q.id)
    ca = CommunicationAnswer(interview_id=a["interview_id"], question_id=q.id)
    db.add_all([qsa, ca])
    db.flush()
    assert q.company_id == qsa.company_id == ca.company_id == a["company_id"]


def test_question_filter_scopes_answer_keys(db, co):
    from src.models.question_models import Question
    from src.tenancy import set_current_company
    a, b = co["a"], co["b"]
    set_current_company(a["company_id"])
    try:
        seen = {q.interview_id for q in db.query(Question).all()}
        assert a["interview_id"] in seen and b["interview_id"] not in seen
    finally:
        set_current_company(None)


# ── evaluation_routes ──────────────────────────────────────────────────────

def test_mcq_submit_needs_assignment_for_interviewers(client, co):
    a = co["a"]
    resp = client.post("/api/v1/evaluation/mcq/submit", headers=a["interviewer"], json={
        "interview_id": a["interview_id"], "candidate_name": "x", "candidate_email": "x@x.com",
        "answers": [],
    })
    assert resp.status_code == 404, resp.text
    # HR in the company may.
    resp = client.post("/api/v1/evaluation/mcq/submit", headers=a["hr"], json={
        "interview_id": a["interview_id"], "candidate_name": "x", "candidate_email": "x@x.com",
        "answers": [],
    })
    assert resp.status_code == 200, resp.text


def test_null_company_interview_refused_by_evaluation_and_mcq(client, db, co):
    iv_id = _null_company_draft(db)
    a = co["a"]
    assert client.get(f"/api/v1/evaluation/marksheet/{iv_id}", headers=a["hr"]).status_code == 404
    assert client.get(f"/api/v1/mcq/interviews/{iv_id}/questions", headers=a["hr"]).status_code == 404


def _offer_for(db, company, email):
    from datetime import date, timedelta
    from src.models.models import Candidate, Offer
    cand = Candidate(id=str(uuid.uuid4()), company_id=company["company_id"], job_id=company["job_id"],
                     user_id=company["ids"]["candidate"], email=email, first_name="C", last_name="D")
    db.add(cand)
    db.flush()
    offer = Offer(id=str(uuid.uuid4()), company_id=company["company_id"], candidate_id=cand.id,
                  job_id=company["job_id"], hr_id=company["hr_profile_id"], salary=100,
                  start_date=date.today() + timedelta(days=30),
                  expiry_date=date.today() + timedelta(days=7), status="sent")
    db.add(offer)
    db.flush()
    return offer.id


def test_offer_respond_is_owner_only(client, db, co):
    """Same email in another company, or a staff user, must not answer someone's offer."""
    a, b = co["a"], co["b"]
    offer_id = _offer_for(db, a, a["emails"]["candidate"])
    twin = a["emails"]["candidate"]
    twin_id = _make_user(db, twin, "candidate", company_id=b["company_id"])
    db.commit()
    url = f"/api/v1/evaluation/offers/{offer_id}/respond"
    assert client.post(url, headers=_token_for(db, twin_id),
                       json={"decision": "accept"}).status_code == 404
    assert client.post(url, headers=a["hr"], json={"decision": "accept"}).status_code == 403


# ── admin_routes ───────────────────────────────────────────────────────────

def test_admin_cannot_touch_platform_config(client, co):
    a = co["a"]
    assert client.get("/api/v1/admin/config", headers=a["admin"]).status_code == 403
    assert client.put("/api/v1/admin/config/maintenance_mode", headers=a["admin"],
                      json={"on": True}).status_code == 403


def test_admin_job_rejects_other_company_department(client, co):
    a, b = co["a"], co["b"]
    resp = client.post("/api/v1/admin/jobs", headers=a["admin"], json={
        "title": "Hijack", "description": "d", "requirements": "r",
        "department_id": b["dept_id"], "location": "Remote",
        "employment_type": "full_time", "experience_years": 0, "required_skills": [],
    })
    assert resp.status_code == 404, resp.text


def test_admin_overview_counts_only_own_company(client, co):
    a = co["a"]
    reply = client.post("/api/v1/admin/department-chat", headers=a["admin"],
                        json={"message": "show system overview stats"}).json()["reply"]
    # A has admin, hr, interviewer, candidate = 4 users and 1 job; B's are not counted.
    assert "- Users: 4" in reply and "- Job Postings: 1" in reply

    health = client.get("/api/v1/admin/system/health", headers=a["admin"]).json()
    assert health["statistics"]["total_users"] == 4
