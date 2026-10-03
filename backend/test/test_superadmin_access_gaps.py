"""Support (superadmin) access to client data: restricted to a grant, and on the record.

The grant model is pinned in test_auth_hardening.py. These cover the routes that used to
sit outside it — a superadmin could create a working admin inside any client, read and
delete answer keys by id, list every tenant's question bank, coding problems and
submissions, watch any live proctoring session, and edit interviews — and the audit trail
itself: rows the client can see, with the request on them, writes named as writes, the
SQL console's query text, and no access at all when the row cannot be written.
"""
import json
import uuid

import pytest

from test.test_auth_hardening import _company, _headers, _login, _superadmin, _user


def _grant(client, admin):
    h = _headers(_login(client, admin.email).json()["access_token"])
    r = client.post("/api/v1/support/access-grants", headers=h, json={"reason": "Ticket 7"})
    assert r.status_code == 201, r.text
    return h


def _audit(db, action, company_id=None):
    from src.models.models import AuditLog
    q = db.query(AuditLog).filter(AuditLog.action == action)
    if company_id:
        q = q.filter(AuditLog.company_id == company_id)
    return q.order_by(AuditLog.created_at.desc()).all()


def _interview_with_question(db, company_id):
    from src.models.models import Interview
    from src.models.question_models import Question
    iv = Interview(company_id=company_id, title="T", status="DRAFT")
    db.add(iv)
    db.flush()
    q = Question(id=str(uuid.uuid4()), interview_id=iv.id, company_id=company_id,
                 question_text="2+2?", question_type="multiple_choice", difficulty_level="EASY",
                 options='["3", "4"]', correct_answer="B", is_pool_question=True,
                 is_approved=True, skill_name="math")
    db.add(q)
    db.flush()
    return iv, q


# ── creating an admin inside a client ────────────────────────────────────────

def _new_admin(email):
    return {"email": email, "password": "Str0ng!Passw0rd", "first_name": "New", "last_name": "Admin"}


def test_superadmin_can_seed_the_first_admin_of_an_admin_less_company(client, db):
    co = _company(db)
    sa, h = _superadmin(client, db)
    r = client.post(f"/api/v1/superadmin/companies/{co.id}/admin", headers=h,
                    json=_new_admin(f"first_{uuid.uuid4().hex[:6]}@t.com"))
    assert r.status_code == 201, r.text
    row = _audit(db, "superadmin.create_admin", co.id)[0]
    detail = json.loads(row.detail)
    assert detail["company_had_admin"] is False and detail["grant_id"] is None
    assert row.actor_user_id == sa.id and row.path.endswith("/admin")


def test_adding_an_admin_to_a_company_with_one_needs_its_grant_and_tells_it(client, db):
    from src.models.models import Notification, User
    co = _company(db)
    existing = _user(db, "admin", co.id)
    _, h = _superadmin(client, db)
    email = f"extra_{uuid.uuid4().hex[:6]}@t.com"

    r = client.post(f"/api/v1/superadmin/companies/{co.id}/admin", headers=h, json=_new_admin(email))
    assert r.status_code == 403
    assert db.query(User).filter(User.email == email).first() is None

    _grant(client, existing)
    r = client.post(f"/api/v1/superadmin/companies/{co.id}/admin", headers=h, json=_new_admin(email))
    assert r.status_code == 201, r.text
    assert json.loads(_audit(db, "superadmin.create_admin", co.id)[0].detail)["grant_id"]
    note = db.query(Notification).filter(Notification.user_id == existing.id,
                                         Notification.type == "security").one()
    assert email in note.message


# ── answer keys and question banks ───────────────────────────────────────────

def test_superadmin_question_read_and_delete_need_a_grant(client, db):
    from src.models.question_models import Question
    co = _company(db)
    admin = _user(db, "admin", co.id)
    _, q = _interview_with_question(db, co.id)
    _, h = _superadmin(client, db)

    assert client.get(f"/api/v1/mcq/questions/{q.id}", headers=h).status_code == 403
    assert client.delete(f"/api/v1/mcq/questions/{q.id}", headers=h).status_code == 403
    db.expire_all()
    assert db.query(Question).filter(Question.id == q.id).first() is not None

    _grant(client, admin)
    r = client.get(f"/api/v1/mcq/questions/{q.id}", headers=h)
    assert r.status_code == 200, r.text
    read = _audit(db, "superadmin.read", co.id)[0]
    assert read.method == "GET" and read.path == f"/api/v1/mcq/questions/{q.id}"   # request recorded

    assert client.delete(f"/api/v1/mcq/questions/{q.id}", headers=h).status_code == 200
    write = _audit(db, "superadmin.write", co.id)[0]
    assert write.method == "DELETE"


def test_superadmin_cannot_sample_or_search_every_tenants_questions(client, db):
    co = _company(db)
    _interview_with_question(db, co.id)
    _, h = _superadmin(client, db)
    assert client.get("/api/v1/mcq/sample", headers=h).status_code == 403
    assert client.get("/interviews/questions/search?skill_name=math", headers=h).status_code == 403


# ── coding judge ─────────────────────────────────────────────────────────────

def _problem_and_submission(db, company_id, user_id):
    from src.models.judge_models import CodeSubmission, CodingProblem
    p = CodingProblem(id=str(uuid.uuid4()), company_id=company_id, title="Sum",
                      reference_solution="print(3)", status="published")
    db.add(p)
    db.flush()
    s = CodeSubmission(id=str(uuid.uuid4()), company_id=company_id, problem_id=p.id,
                       user_id=user_id, language="python", source_code="print(3)", status="done")
    db.add(s)
    db.flush()
    return p, s


def test_superadmin_cannot_list_every_tenants_problems_or_submissions(client, db):
    co = _company(db)
    hr = _user(db, "hr", co.id)
    _problem_and_submission(db, co.id, hr.id)
    _, h = _superadmin(client, db)
    assert client.get("/api/v1/judge/problems", headers=h).status_code == 403
    assert client.get("/api/v1/judge/submissions", headers=h).status_code == 403


def test_live_submission_socket_needs_a_grant_for_superadmin(client, db, monkeypatch):
    import routes.judge_routes as judge

    class _Borrowed:
        """The test's session, lent to code that closes what it opens."""
        def __getattr__(self, name):
            return getattr(db, name)

        def close(self):
            pass

    monkeypatch.setattr(judge.db_manager, "get_db_session", lambda: _Borrowed())
    co = _company(db)
    admin, hr = _user(db, "admin", co.id), _user(db, "hr", co.id)
    _, sub = _problem_and_submission(db, co.id, hr.id)
    _, h = _superadmin(client, db)
    token = h["Authorization"].split(" ", 1)[1]

    with pytest.raises(ValueError, match="forbidden"):
        judge._ws_authorize(token, sub.id)
    _grant(client, admin)
    assert judge._ws_authorize(token, sub.id)[1] == "superadmin"


def test_superadmin_regrade_sweep_is_audited(client, db):
    sa, h = _superadmin(client, db)
    client.post("/api/v1/judge/regrade-pending", headers=h)   # 503 without a judge is fine
    assert any(r.actor_user_id == sa.id for r in _audit(db, "superadmin.regrade"))


# ── interviews ───────────────────────────────────────────────────────────────

def test_superadmin_cannot_edit_or_delete_a_clients_interview(client, db):
    from src.models.models import Interview
    co = _company(db)
    iv = Interview(company_id=co.id, title="Draft", status="DRAFT")
    db.add(iv)
    db.flush()
    _, h = _superadmin(client, db)
    assert client.put(f"/api/v1/interviewer/interviews/{iv.id}", headers=h,
                      json={"title": "hijacked"}).status_code == 403
    assert client.delete(f"/api/v1/interviewer/interviews/{iv.id}", headers=h).status_code == 403
    db.expire_all()
    assert db.get(Interview, iv.id).title == "Draft"


# ── the audit trail itself ───────────────────────────────────────────────────

def test_ticket_requests_are_logged_against_the_clients_company(client, db):
    from src.models.models import SupportRequest
    co = _company(db)
    admin = _user(db, "admin", co.id)
    ticket = SupportRequest(company_id=co.id, created_by=admin.id, subject="Help")
    db.add(ticket)
    db.flush()
    _, h = _superadmin(client, db)
    assert client.get(f"/api/v1/support/admin/requests/{ticket.id}", headers=h).status_code == 200

    admin_h = _headers(_login(client, admin.email).json()["access_token"])
    log = client.get("/api/v1/support/access-log", headers=admin_h).json()["entries"]
    assert any(e["action"] == "superadmin.request" and ticket.id in (e.get("path") or e.get("resource") or "")
               for e in log), log


def test_sql_console_records_the_query_text(client, db, monkeypatch):
    monkeypatch.setenv("ENABLE_RAW_SQL_CONSOLE", "true")
    sa, h = _superadmin(client, db)
    client.post("/api/v1/database/query", headers=h, json={"query": "SELECT 1 AS one"})
    row = next(r for r in _audit(db, "superadmin.sql_query") if r.actor_user_id == sa.id)
    assert json.loads(row.detail)["query"] == "SELECT 1 AS one"


def test_support_access_fails_closed_when_it_cannot_be_audited(client, db, monkeypatch):
    import src.services.audit_log as audit_log
    co = _company(db)
    admin = _user(db, "admin", co.id)
    iv, _ = _interview_with_question(db, co.id)
    _, h = _superadmin(client, db)
    _grant(client, admin)

    class _Broken:
        def __init__(self, **kw):
            raise RuntimeError("audit store down")

    monkeypatch.setattr(audit_log, "AuditLog", _Broken)
    assert client.get(f"/api/v1/mcq/interviews/{iv.id}/questions", headers=h).status_code == 503
    assert client.get("/api/v1/superadmin/companies", headers=h).status_code == 503


def test_record_audit_default_still_never_raises(db, monkeypatch):
    import src.services.audit_log as audit_log

    class _Broken:
        def __init__(self, **kw):
            raise RuntimeError("down")

    monkeypatch.setattr(audit_log, "AuditLog", _Broken)
    audit_log.record_audit(db, action="x.y")   # logged, not raised
    with pytest.raises(audit_log.AuditUnavailable):
        audit_log.record_audit(db, action="x.y", required=True)


# ── proctoring (CI stage 2: needs the proctoring extras) ─────────────────────

@pytest.mark.proctor
def test_superadmin_reads_a_live_proctoring_session_only_under_a_grant(client, db, monkeypatch):
    import contextlib
    pytest.importorskip("cv2", reason="proctoring extras not installed")
    import proctoring.routes as routes
    from fastapi import HTTPException

    monkeypatch.setattr(routes, "_db_session", contextlib.contextmanager(lambda: (yield db)))
    co = _company(db)
    admin = _user(db, "admin", co.id)
    sa, _ = _superadmin(client, db)
    sid = str(uuid.uuid4())
    monkeypatch.setitem(routes._sessions, sid, {"owner_user_id": "someone-else", "company_id": co.id})

    with pytest.raises(HTTPException) as denied:
        routes._readable_entry(sid, sa)
    assert denied.value.status_code == 403
    _grant(client, admin)
    assert routes._readable_entry(sid, sa)["company_id"] == co.id
