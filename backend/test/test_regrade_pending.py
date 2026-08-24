"""Judge re-grade sweep — POST /api/v1/judge/regrade-pending.

When the judge is unreachable at submit time the coding answer is saved, flagged
`pending_regrade`, and its marks are subtracted from the denominator. This endpoint
is what puts them back. It had no caller at all until a systemd timer was added, so
the two properties that matter for an unattended sweep are covered here:

- it is idempotent — a second run must not award the same marks twice
- the shared-secret token authenticates a headless caller, and nothing else does
"""
import json
import uuid
import pytest


def _make_company(db, name="Regrade Co") -> str:
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c.id


def _create_user(db, email, role="hr", company_id=None, password="Pass@1234"):
    from src.models.models import User
    from src.auth import get_password_hash
    u = User(
        id=str(uuid.uuid4()), email=email,
        password_hash=get_password_hash(password), role=role,
        first_name=role.upper(), last_name="Tester",
        is_active=True, email_verified=True, company_id=company_id,
    )
    db.add(u)
    db.flush()
    return u.id


def _login(client, email, password="Pass@1234"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _pending_interview(db, company_id, *, score=6.0, total=10.0):
    """A COMPLETED interview whose coding answer was deferred: 4 marks were taken
    out of the denominator at submit time (10 rather than 14)."""
    from src.models.models import Interview
    from src.models.question_models import Question as InterviewQuestion

    iv = Interview(
        company_id=company_id, status="COMPLETED",
        test_score=score, total_marks=total,
    )
    db.add(iv)
    db.flush()

    q = InterviewQuestion(
        interview_id=iv.id, question_text="Sum two numbers",
        question_type="coding", difficulty_level="easy",
        language="python", points=4,
    )
    db.add(q)
    db.flush()

    iv.answers_json = json.dumps([{
        "question_id": q.id, "selected_option": "print(sum(map(int,input().split())))",
        "language": "python", "pending_regrade": True,
        "is_correct": False, "verdict": None, "points": 0, "max_points": 4,
    }])
    db.flush()
    return iv, q


@pytest.fixture()
def graded_ok(monkeypatch):
    """Stub the judge so these tests never need Docker."""
    import routes.judge_routes as jr

    monkeypatch.setattr(jr, "_engine_ready", lambda: (True, "stub"))
    import src.judge.interview_grading as ig
    monkeypatch.setattr(ig, "grade_interview_question", lambda q, lang, src: {
        "verdict": "AC", "points_awarded": 4, "max_points": 4,
        "passed": 2, "total": 2, "message": "ok",
    })
    return ig


@pytest.fixture()
def token(monkeypatch):
    monkeypatch.setenv("JUDGE_REGRADE_TOKEN", "sweeper-secret")
    return "sweeper-secret"


def test_regrade_folds_marks_back_in(client, db, graded_ok, token):
    cid = _make_company(db)
    iv, _ = _pending_interview(db, cid)

    r = client.post("/api/v1/judge/regrade-pending",
                    headers={"X-Judge-Regrade-Token": token})
    assert r.status_code == 200, r.text
    assert r.json()["answers_regraded"] >= 1

    db.refresh(iv)
    assert float(iv.test_score) == 10.0     # 6 + 4 awarded
    assert float(iv.total_marks) == 14.0    # 10 + 4 restored to the denominator
    assert "pending_regrade" not in iv.answers_json


def test_regrade_is_idempotent(client, db, graded_ok, token):
    """The timer runs every 15 minutes — a second pass must be a no-op."""
    cid = _make_company(db)
    iv, _ = _pending_interview(db, cid)

    first = client.post("/api/v1/judge/regrade-pending",
                        headers={"X-Judge-Regrade-Token": token})
    assert first.status_code == 200
    db.refresh(iv)
    score_after_first = float(iv.test_score)
    total_after_first = float(iv.total_marks)

    second = client.post("/api/v1/judge/regrade-pending",
                         headers={"X-Judge-Regrade-Token": token})
    assert second.status_code == 200, second.text
    assert second.json()["answers_regraded"] == 0

    db.refresh(iv)
    assert float(iv.test_score) == score_after_first
    assert float(iv.total_marks) == total_after_first


def test_judge_still_down_leaves_everything_pending(client, db, monkeypatch, token):
    """A sweep during an outage must not mis-score — it should change nothing."""
    import routes.judge_routes as jr
    import src.judge.interview_grading as ig
    monkeypatch.setattr(jr, "_engine_ready", lambda: (True, "stub"))
    monkeypatch.setattr(ig, "grade_interview_question",
                        lambda q, lang, src: {"unavailable": True})

    cid = _make_company(db)
    iv, _ = _pending_interview(db, cid)

    r = client.post("/api/v1/judge/regrade-pending",
                    headers={"X-Judge-Regrade-Token": token})
    assert r.status_code == 200, r.text
    assert r.json()["answers_regraded"] == 0

    db.refresh(iv)
    assert float(iv.test_score) == 6.0
    assert float(iv.total_marks) == 10.0
    assert "pending_regrade" in iv.answers_json


def test_wrong_token_rejected(client, db, graded_ok, token):
    r = client.post("/api/v1/judge/regrade-pending",
                    headers={"X-Judge-Regrade-Token": "wrong"})
    assert r.status_code == 401


def test_no_credentials_rejected(client, db, graded_ok, token):
    assert client.post("/api/v1/judge/regrade-pending").status_code == 401


def test_unset_token_does_not_authorize_empty_header(client, db, graded_ok, monkeypatch):
    """An unset JUDGE_REGRADE_TOKEN must not make the endpoint public."""
    monkeypatch.delenv("JUDGE_REGRADE_TOKEN", raising=False)
    r = client.post("/api/v1/judge/regrade-pending",
                    headers={"X-Judge-Regrade-Token": ""})
    assert r.status_code == 401


def test_hr_jwt_also_accepted(client, db, graded_ok):
    """The HR dashboard's "Re-grade now" button uses a JWT, not the token."""
    cid = _make_company(db)
    _create_user(db, "hr_regrade@test.com", "hr", cid)
    headers = _login(client, "hr_regrade@test.com")
    iv, _ = _pending_interview(db, cid)

    r = client.post(f"/api/v1/judge/regrade-pending?interview_id={iv.id}",
                    headers=headers)
    assert r.status_code == 200, r.text
    db.refresh(iv)
    assert float(iv.total_marks) == 14.0


def test_candidate_jwt_rejected(client, db, graded_ok):
    cid = _make_company(db)
    _create_user(db, "cand_regrade@test.com", "candidate", cid)
    headers = _login(client, "cand_regrade@test.com")
    assert client.post("/api/v1/judge/regrade-pending", headers=headers).status_code == 401


def test_hr_sweep_does_not_touch_another_company(client, db, graded_ok):
    """The HR button must stay company-scoped. This route has no get_current_user
    dependency to bind the tenant contextvar, so the handler binds it itself —
    without that, an HR click would re-grade every tenant on the platform."""
    mine = _make_company(db, "Sweeper Co")
    theirs = _make_company(db, "Bystander Co")
    _create_user(db, "hr_scoped@test.com", "hr", mine)
    headers = _login(client, "hr_scoped@test.com")

    my_iv, _ = _pending_interview(db, mine)
    their_iv, _ = _pending_interview(db, theirs)

    r = client.post("/api/v1/judge/regrade-pending", headers=headers)
    assert r.status_code == 200, r.text

    db.refresh(my_iv)
    db.refresh(their_iv)
    assert float(my_iv.total_marks) == 14.0        # mine was re-graded
    assert float(their_iv.total_marks) == 10.0     # theirs was left alone
    assert "pending_regrade" in their_iv.answers_json


def test_token_sweep_covers_every_tenant(client, db, graded_ok, token):
    """The platform timer is the opposite case — it must not be tenant-scoped."""
    a = _make_company(db, "Tenant A")
    b = _make_company(db, "Tenant B")
    iv_a, _ = _pending_interview(db, a)
    iv_b, _ = _pending_interview(db, b)

    r = client.post("/api/v1/judge/regrade-pending",
                    headers={"X-Judge-Regrade-Token": token})
    assert r.status_code == 200, r.text

    db.refresh(iv_a)
    db.refresh(iv_b)
    assert float(iv_a.total_marks) == 14.0
    assert float(iv_b.total_marks) == 14.0
