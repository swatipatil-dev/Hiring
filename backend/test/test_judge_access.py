"""Candidates can only run code against questions in their own, unfinished test.

/judge/grade-question grades against the HIDDEN test cases. Before, a candidate could
leave out interview_id (skipping both the ownership check and the 3-run cap) and grade
any question id as often as they liked, recovering the hidden cases from the per-case
verdicts.
"""
import json
import uuid
from types import SimpleNamespace

import pytest

from conftest import _register_and_login
from src.models.models import Candidate, Company, Interview, JobPosting, User
from src.models.judge_models import InterviewCodeSubmission
from src.models.question_models import Question


def _user(db, email):
    return db.query(User).filter(User.email == email).first()


def _coding(db, iv, lang="python"):
    q = Question(interview_id=iv.id, question_text="Sum", question_type="coding",
                 difficulty_level="Easy", language=lang, points=10, is_approved=True,
                 test_cases=json.dumps([
                     {"input": "CREATE TABLE t(a); INSERT INTO t VALUES (1);" if lang == "sql" else "1 2",
                      "expected_output": "3", "is_sample": True},
                     {"input": "5 5", "expected_output": "10", "is_sample": False}]))
    db.add(q)
    db.flush()
    return q


@pytest.fixture
def exam(db, hr_headers, candidate_headers, candidate2_headers):
    hr = _user(db, "hr_test@test.com")
    job = JobPosting(id=str(uuid.uuid4()), company_id=hr.company_id, title="Dev",
                     description="d", requirements="r", status="active")
    db.add(job)
    db.flush()

    def candidate_with_test(email):
        u = _user(db, email)
        u.company_id = hr.company_id
        c = Candidate(id=str(uuid.uuid4()), company_id=hr.company_id, user_id=u.id, job_id=job.id,
                      email=email, first_name="C", last_name="X", current_stage="test_invited")
        db.add(c)
        db.flush()
        iv = Interview(company_id=hr.company_id, title="Dev test", job_id=job.id,
                       candidate_id=c.id, status="IN_PROGRESS")
        db.add(iv)
        db.flush()
        return iv

    mine = candidate_with_test("candidate_test@test.com")
    theirs = candidate_with_test("candidate2_test@test.com")
    return SimpleNamespace(hr=hr, mine=mine, theirs=theirs,
                           q=_coding(db, mine), their_q=_coding(db, theirs),
                           user=_user(db, "candidate_test@test.com"))


@pytest.fixture
def fake_grader(monkeypatch):
    import src.judge.interview_grading as grading
    calls = []

    def grade(q, lang, code, reveal_hidden=False):
        calls.append(q.id)
        return {"verdict": "WA", "passed": 1, "total": 2, "points_awarded": 5,
                "max_points": 10, "cases": []}
    monkeypatch.setattr(grading, "grade_interview_question", grade)
    return calls


def _grade(client, headers, q, interview_id=None):
    body = {"question_id": q.id, "language": "python", "source_code": "print(3)"}
    if interview_id is not None:
        body["interview_id"] = interview_id
    return client.post("/api/v1/judge/grade-question", headers=headers, json=body)


def test_candidate_must_name_their_own_test(client, exam, candidate_headers, fake_grader):
    assert _grade(client, candidate_headers, exam.q).status_code == 404               # no interview
    assert _grade(client, candidate_headers, exam.their_q, exam.theirs.id).status_code == 404
    assert _grade(client, candidate_headers, exam.their_q, exam.mine.id).status_code == 404
    assert fake_grader == []                                                          # nothing judged
    assert _grade(client, candidate_headers, exam.q, exam.mine.id).status_code == 200


def test_finished_test_cannot_be_graded(client, db, exam, candidate_headers, fake_grader):
    exam.mine.status = "COMPLETED"
    db.flush()
    assert _grade(client, candidate_headers, exam.q, exam.mine.id).status_code == 403
    assert fake_grader == []


def test_run_cap_counts_attempts_still_being_judged(client, db, exam, candidate_headers, fake_grader):
    # Two finished runs and one still running (a parallel click in flight).
    for status, verdict in [("graded", "WA"), ("graded", "AC"), ("running", None)]:
        db.add(InterviewCodeSubmission(company_id=exam.mine.company_id,
                                       interview_id=exam.mine.id, question_id=exam.q.id,
                                       user_id=exam.user.id, language="python",
                                       source_code="x", status=status, verdict=verdict))
    db.flush()
    r = _grade(client, candidate_headers, exam.q, exam.mine.id)
    assert r.status_code == 429, r.text
    assert fake_grader == []


def test_three_runs_then_stop(client, exam, candidate_headers, fake_grader):
    for left in (2, 1, 0):
        r = _grade(client, candidate_headers, exam.q, exam.mine.id)
        assert r.status_code == 200, r.text
        assert r.json()["runs_left"] == left
    assert _grade(client, candidate_headers, exam.q, exam.mine.id).status_code == 429
    assert len(fake_grader) == 3


def test_a_crash_before_judging_gives_the_run_back(client, db, exam, candidate_headers, monkeypatch):
    import src.judge.interview_grading as grading

    def boom(*a, **k):
        raise RuntimeError("worker exploded")
    monkeypatch.setattr(grading, "grade_interview_question", boom)
    assert _grade(client, candidate_headers, exam.q, exam.mine.id).status_code == 500
    rows = db.query(InterviewCodeSubmission).filter_by(interview_id=exam.mine.id).all()
    assert [(r.status, r.verdict) for r in rows] == [("pending", None)]


def test_staff_preview_is_company_scoped(client, db, exam, hr_headers, fake_grader):
    assert _grade(client, hr_headers, exam.q).status_code == 200
    other = _register_and_login(client, "hr_judge_other@test.com", "Pass@1234", "hr", db=db)
    co = Company(id=str(uuid.uuid4()), name="Other", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(co)
    db.flush()
    _user(db, "hr_judge_other@test.com").company_id = co.id
    db.flush()
    assert _grade(client, other, exam.q).status_code == 404


def test_run_code_cannot_seed_from_someone_elses_question(client, db, exam, candidate_headers,
                                                        monkeypatch):
    import src.judge.queue as jq
    monkeypatch.setattr(jq, "run_in_worker", lambda *a, **k: pytest.fail("must not run"))
    sql_q = _coding(db, exam.theirs, lang="sql")
    r = client.post("/api/v1/judge/run", headers=candidate_headers, json={
        "language": "sql", "source_code": "SELECT 1", "stdin": "",
        "question_id": sql_q.id, "interview_id": exam.mine.id})
    assert r.status_code == 404


def test_oversized_source_is_rejected_before_anything_runs(client, exam, candidate_headers, fake_grader):
    r = client.post("/api/v1/judge/grade-question", headers=candidate_headers, json={
        "question_id": exam.q.id, "language": "python", "interview_id": exam.mine.id,
        "source_code": "x" * (64 * 1024 + 1)})
    assert r.status_code == 422
    assert fake_grader == []


def test_code_execution_is_limited_per_user_not_per_network(exam, candidate_headers, candidate2_headers):
    from src.rate_limit import user_key

    def req(headers):
        return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()},
                               client=SimpleNamespace(host="10.0.0.1"))
    # Two candidates in the same office share an IP but not a bucket.
    a, b = user_key(req(candidate_headers)), user_key(req(candidate2_headers))
    assert a.startswith("user:") and b.startswith("user:") and a != b
    assert user_key(req({})) == "10.0.0.1"
    assert user_key(req({"Authorization": "Bearer forged.token.here"})) == "10.0.0.1"
