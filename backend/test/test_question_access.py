"""Answer keys and hidden test cases never reach candidates.

- The question-generator routes (/interviews/*) are staff-only and company-scoped:
  they return correct answers, reference solutions and hidden test cases.
- A candidate's paper (/api/v1/mcq/interviews/{id}/questions) is their own interview
  only, approved questions only, with ``correct_answer`` blank and sample cases only.
- Endpoints that return or change answer keys (/mcq/questions*, /mcq/sample,
  /evaluation/*/submit) are staff-only.
- Only the candidate who owns an interview can submit it; an account with no
  Candidate row owns nothing.
- The score's denominator is the paper that was served, not the answers sent.
"""
import json
import uuid
from types import SimpleNamespace

import pytest

from conftest import _register_and_login
from src.models.models import Candidate, Company, Interview, JobPosting, User
from src.models.question_models import Question

HIDDEN_INPUT = "hidden-input-7f3a"
HIDDEN_OUTPUT = "hidden-output-7f3a"


def _user(db, email):
    return db.query(User).filter(User.email == email).first()


@pytest.fixture
def paper(db, hr_headers, candidate_headers, candidate2_headers):
    hr = _user(db, "hr_test@test.com")
    cand_user = _user(db, "candidate_test@test.com")
    cand2_user = _user(db, "candidate2_test@test.com")
    # Candidates sit in the company whose job they applied to (as in production).
    cand_user.company_id = hr.company_id
    cand2_user.company_id = hr.company_id
    job = JobPosting(id=str(uuid.uuid4()), company_id=hr.company_id, title="Dev",
                     description="d", requirements="r", status="active")
    db.add(job)
    db.flush()
    cand = Candidate(id=str(uuid.uuid4()), company_id=hr.company_id, user_id=cand_user.id,
                     job_id=job.id, email=cand_user.email, first_name="C", last_name="One",
                     current_stage="test_invited")
    db.add(cand)
    db.flush()
    iv = Interview(company_id=hr.company_id, title="Dev test", job_id=job.id,
                   candidate_id=cand.id, status="READY")
    db.add(iv)
    db.flush()
    mcqs = []
    for i in range(3):
        q = Question(interview_id=iv.id, question_text=f"MCQ {i}", question_type="multiple_choice",
                     difficulty_level="Easy", options=json.dumps(["A1", "B1", "C1", "D1"]),
                     correct_answer="B", is_approved=True)
        db.add(q)
        mcqs.append(q)
    unapproved = Question(interview_id=iv.id, question_text="Draft", question_type="multiple_choice",
                          difficulty_level="Easy", options=json.dumps(["x", "y"]),
                          correct_answer="A", is_approved=False)
    coding = Question(interview_id=iv.id, question_text="Sum", question_type="coding",
                      difficulty_level="Easy", language="python", points=10, is_approved=True,
                      sample_answer="print(sum(map(int, input().split())))",
                      test_cases=json.dumps([
                          {"input": "1 2", "expected_output": "3", "is_sample": True},
                          {"input": HIDDEN_INPUT, "expected_output": HIDDEN_OUTPUT, "is_sample": False},
                      ]))
    db.add_all([unapproved, coding])
    db.flush()
    return SimpleNamespace(hr=hr, job=job, cand=cand, iv=iv, mcqs=mcqs,
                           unapproved=unapproved, coding=coding)


@pytest.fixture
def other_company_hr(client, db):
    headers = _register_and_login(client, "hr_other@test.com", "Pass@1234", "hr", db=db)
    other = Company(id=str(uuid.uuid4()), name="Other Co", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    _user(db, "hr_other@test.com").company_id = other.id
    db.flush()
    return headers


# ── /interviews/* (question generator) ─────────────────────────────────────

def _generator_calls(p):
    iv, qid = p.iv.id, p.mcqs[0].id
    return [
        ("get", f"/interviews/{iv}/questions", None),
        ("get", f"/interviews/{iv}/test-sets", None),
        ("get", "/interviews/questions/search?skill_name=a", None),
        ("put", f"/interviews/{iv}/questions/{qid}", {"correct_answer": "C"}),
        ("delete", f"/interviews/{iv}/questions", None),
        ("get", "/interviews/health", None),
        ("get", "/interviews/test-ollama", None),
    ]


def test_generator_routes_refuse_anonymous_and_candidates(client, paper, candidate_headers):
    for method, url, body in _generator_calls(paper):
        anon = getattr(client, method)(url, **({"json": body} if body else {}))
        assert anon.status_code in (401, 403), (url, anon.status_code)
        cand = getattr(client, method)(url, headers=candidate_headers, **({"json": body} if body else {}))
        assert cand.status_code == 403, (url, cand.status_code)
    assert paper.mcqs[0].correct_answer == "B"       # the PUT changed nothing


def test_generator_routes_are_company_scoped(client, paper, hr_headers, other_company_hr):
    own = client.get(f"/interviews/{paper.iv.id}/questions", headers=hr_headers)
    assert own.status_code == 200, own.text
    assert any(q.get("correct_answer") == "B" for q in own.json()["questions"])

    url = f"/interviews/{paper.iv.id}/questions"
    assert client.get(url, headers=other_company_hr).status_code == 404
    assert client.put(f"{url}/{paper.mcqs[0].id}", headers=other_company_hr,
                      json={"correct_answer": "C"}).status_code == 404
    assert client.delete(url, headers=other_company_hr).status_code == 404


def test_question_search_only_returns_own_company(client, db, paper, hr_headers, other_company_hr):
    q = paper.mcqs[0]
    q.skill_name, q.is_pool_question = "python-secret-skill", True
    db.flush()
    mine = client.get("/interviews/questions/search?skill_name=python-secret-skill", headers=hr_headers)
    assert [r["id"] for r in mine.json()] == [q.id]
    theirs = client.get("/interviews/questions/search?skill_name=python-secret-skill",
                        headers=other_company_hr)
    assert theirs.status_code == 200 and theirs.json() == []


# ── the candidate's paper ──────────────────────────────────────────────────

def test_candidate_paper_has_no_answers_or_hidden_cases(client, paper, candidate_headers):
    r = client.get(f"/api/v1/mcq/interviews/{paper.iv.id}/questions", headers=candidate_headers)
    assert r.status_code == 200, r.text
    qs = r.json()["questions"]
    assert {q["id"] for q in qs} == {q.id for q in paper.mcqs} | {paper.coding.id}   # approved only
    assert all(q["correct_answer"] == "" for q in qs)
    coding = next(q for q in qs if q["id"] == paper.coding.id)
    assert coding["sample_test_cases"] == [{"input": "1 2", "expected_output": "3"}]
    raw = r.text
    assert HIDDEN_INPUT not in raw and HIDDEN_OUTPUT not in raw
    assert "sum(map(int" not in raw                                     # no reference solution


def test_staff_still_see_the_answer_key(client, paper, hr_headers):
    r = client.get(f"/api/v1/mcq/interviews/{paper.iv.id}/questions", headers=hr_headers)
    assert r.status_code == 200, r.text
    assert {q["correct_answer"] for q in r.json()["questions"] if q["question_type"] != "coding"} >= {"B"}


def test_candidate_cannot_read_another_candidates_paper(client, paper, candidate2_headers):
    r = client.get(f"/api/v1/mcq/interviews/{paper.iv.id}/questions", headers=candidate2_headers)
    assert r.status_code == 404


def test_answer_key_endpoints_are_staff_only(client, paper, candidate_headers):
    qid = paper.mcqs[0].id
    assert client.get(f"/api/v1/mcq/questions/{qid}", headers=candidate_headers).status_code == 403
    assert client.get("/api/v1/mcq/sample", headers=candidate_headers).status_code == 403
    assert client.delete(f"/api/v1/mcq/questions/{qid}", headers=candidate_headers).status_code == 403
    assert client.post("/api/v1/mcq/questions", headers=candidate_headers, json={
        "interview_id": str(paper.iv.id), "question_text": "x", "options": ["a", "b"],
        "correct_answer": "a"}).status_code == 403
    for path, body in [("/api/v1/evaluation/mcq/submit",
                        {"interview_id": paper.iv.id, "candidate_name": "C",
                         "candidate_email": "c@t.com", "answers": []}),
                       ("/api/v1/evaluation/coding/submit",
                        {"interview_id": paper.iv.id, "question_id": paper.coding.id, "answer": "x"})]:
        r = client.post(path, headers=candidate_headers, json=body)
        assert r.status_code == 403, (path, r.status_code, r.text)


# ── submitting ─────────────────────────────────────────────────────────────

def _submit(client, headers, iv_id, answers):
    return client.post(f"/api/v1/candidate/interviews/{iv_id}/submit", headers=headers,
                       json={"answers": answers})


def test_only_the_owner_can_submit(client, paper, hr_headers, candidate2_headers):
    # HR/interviewer accounts have no Candidate row: they used to pass the ownership
    # check for any interview in their company.
    assert _submit(client, hr_headers, paper.iv.id, []).status_code == 403
    # Same company, but not their interview and no Candidate row of their own.
    assert _submit(client, candidate2_headers, paper.iv.id, []).status_code == 404


def test_score_is_out_of_the_served_paper(client, paper, candidate_headers, monkeypatch):
    import src.judge.interview_grading as grading
    monkeypatch.setattr(grading, "grade_interview_question",
                        lambda *a, **k: pytest.fail("the judge should not run"))
    right = {"question_id": paper.mcqs[0].id, "selected_option": "B1"}
    # Only the one answer we're sure of, sent three times.
    r = _submit(client, candidate_headers, paper.iv.id, [right, right, right])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["score"] == 1
    # 3 MCQs at 1 mark + the coding question's 10; the unapproved draft doesn't count.
    assert body["total_marks"] == 13
