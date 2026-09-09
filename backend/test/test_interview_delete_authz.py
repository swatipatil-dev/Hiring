"""DELETE /api/v1/interviewer/interviews/{id} — who may delete what.

This endpoint took only ``get_current_user``. Tenant scoping came free from Interview being
TenantScoped, but nothing checked the caller's ROLE — so any authenticated user in the company,
a CANDIDATE included, could delete any interview: a published test, or another candidate's
in-flight attempt along with its answers.

It also relied on SQLAlchemy's default for a parent delete, which is to NULL the child FK.
That orphaned ``interview_questions`` rows into the ``interview_id IS NULL`` state that has no
derivable tenant owner, and outright failed on ``question_set_assignments``, whose
``interview_id`` is NOT NULL.

The Exam Drafts UI puts a Delete button on this endpoint, which is why these are pinned.
"""
import uuid
import pytest

from test.test_tenant_isolation import (  # reuse the established fixtures/helpers
    _make_company,
    _make_user,
    _login,
    _make_job,
    _make_interview,
)


def _make_question(db, interview_id, company_id, *, pool=True):
    from src.models.question_models import Question
    q = Question(
        id=str(uuid.uuid4()),
        interview_id=interview_id,
        question_text=f"Q {uuid.uuid4().hex[:6]}?",
        question_type="multiple_choice",
        difficulty_level="MEDIUM",
        options='["a", "b", "c", "d"]',
        correct_answer="A",
        is_pool_question=pool,
        is_approved=True,
    )
    db.add(q)
    db.flush()
    return q.id


def _make_set_assignment(db, interview_id, question_id):
    from src.models.question_models import QuestionSetAssignment
    a = QuestionSetAssignment(
        id=str(uuid.uuid4()),
        interview_id=interview_id,
        question_id=question_id,
        set_number=1,
        question_order=0,
    )
    db.add(a)
    db.flush()
    return a.id


@pytest.fixture()
def company_with_draft(client, db):
    """One company, an HR user, a candidate user, and a DRAFT test with questions."""
    cid = _make_company(db, "Delete Co")
    _make_user(db, "hr-del@example.com", "hr", cid)
    _make_user(db, "cand-del@example.com", "candidate", cid)
    job_id = _make_job(db, cid, title="Backend Dev")
    iv_id = _make_interview(db, cid, job_id=job_id)
    q_id = _make_question(db, iv_id, cid)
    db.commit()
    return {
        "company_id": cid,
        "job_id": job_id,
        "interview_id": iv_id,
        "question_id": q_id,
        "hr": _login(client, "hr-del@example.com"),
        "candidate": _login(client, "cand-del@example.com"),
    }


@pytest.mark.security
def test_delete_interview_rejects_anonymous(client, company_with_draft):
    iv = company_with_draft["interview_id"]
    assert client.delete(f"/api/v1/interviewer/interviews/{iv}").status_code in (401, 403)


@pytest.mark.security
def test_delete_interview_rejects_candidate(client, db, company_with_draft):
    """A candidate must not be able to delete a test — this was the open hole."""
    from src.models.models import Interview

    iv = company_with_draft["interview_id"]
    resp = client.delete(
        f"/api/v1/interviewer/interviews/{iv}",
        headers=company_with_draft["candidate"],
    )
    assert resp.status_code == 403, resp.text

    db.expire_all()
    assert db.query(Interview).filter(Interview.id == iv).first() is not None, \
        "candidate's delete was rejected but the row is gone anyway"


@pytest.mark.security
def test_hr_can_delete_a_draft_and_its_questions(client, db, company_with_draft):
    from src.models.models import Interview
    from src.models.question_models import Question

    iv = company_with_draft["interview_id"]
    resp = client.delete(f"/api/v1/interviewer/interviews/{iv}", headers=company_with_draft["hr"])
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.query(Interview).filter(Interview.id == iv).first() is None
    # Children must be gone, NOT left behind with interview_id NULL — an orphan there has no
    # derivable owner, so no tenant query can ever see or clean it up again.
    assert db.query(Question).filter(Question.interview_id == iv).count() == 0
    assert db.query(Question).filter(
        Question.id == company_with_draft["question_id"]
    ).first() is None


@pytest.mark.security
def test_deleting_a_draft_with_assembled_sets_does_not_500(client, db, company_with_draft):
    """question_set_assignments.interview_id is NOT NULL, so the old nullify-on-delete
    behaviour raised an IntegrityError and surfaced as a 500."""
    from src.models.question_models import QuestionSetAssignment

    iv = company_with_draft["interview_id"]
    _make_set_assignment(db, iv, company_with_draft["question_id"])
    db.commit()

    resp = client.delete(f"/api/v1/interviewer/interviews/{iv}", headers=company_with_draft["hr"])
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.query(QuestionSetAssignment).filter(
        QuestionSetAssignment.interview_id == iv
    ).count() == 0


@pytest.mark.security
def test_cannot_delete_a_published_test(client, db, company_with_draft):
    """Only DRAFTs are one-click deletable. A READY test is somebody's live assessment."""
    from src.models.models import Interview

    iv_id = company_with_draft["interview_id"]
    db.query(Interview).filter(Interview.id == iv_id).update({"status": "READY"})
    db.commit()

    resp = client.delete(f"/api/v1/interviewer/interviews/{iv_id}", headers=company_with_draft["hr"])
    assert resp.status_code == 409, resp.text
    assert "DRAFT" in resp.json()["detail"]

    db.expire_all()
    assert db.query(Interview).filter(Interview.id == iv_id).first() is not None


@pytest.mark.security
def test_cannot_delete_a_candidate_attempt(client, db, company_with_draft):
    """A per-candidate clone holds the candidate's answers. Not deletable here, even as
    DRAFT — candidate_id is what separates an attempt from a template."""
    from src.models.models import Interview, Candidate

    cid = company_with_draft["company_id"]
    cand = Candidate(
        id=str(uuid.uuid4()),
        company_id=cid,
        first_name="A",
        last_name="B",
        email=f"attempt-{uuid.uuid4().hex[:6]}@example.com",
    )
    db.add(cand)
    db.flush()

    iv_id = company_with_draft["interview_id"]
    db.query(Interview).filter(Interview.id == iv_id).update({"candidate_id": cand.id})
    db.commit()

    resp = client.delete(f"/api/v1/interviewer/interviews/{iv_id}", headers=company_with_draft["hr"])
    assert resp.status_code == 409, resp.text

    db.expire_all()
    assert db.query(Interview).filter(Interview.id == iv_id).first() is not None


@pytest.mark.security
def test_delete_blocks_cross_tenant(client, db, company_with_draft):
    """Company B's HR cannot delete Company A's draft — the auto-filter makes it a 404."""
    from src.models.models import Interview

    other = _make_company(db, "Other Co")
    _make_user(db, "hr-other@example.com", "hr", other)
    db.commit()
    headers = _login(client, "hr-other@example.com")

    iv = company_with_draft["interview_id"]
    resp = client.delete(f"/api/v1/interviewer/interviews/{iv}", headers=headers)
    assert resp.status_code == 404, resp.text

    db.expire_all()
    assert db.query(Interview).filter(Interview.id == iv).first() is not None
