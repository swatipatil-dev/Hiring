"""
Tests for scripts/purge_numeric_questions.py.

The script exists to remove questions whose text is a row number, left behind by
the CSV importer bug. The two things that matter:

  1. It must clear question_set_assignments first. Four tables hold foreign keys
     to interview_questions.id with no ON DELETE clause, so MySQL refuses the
     parent delete while a child row survives — and a bulk Query.delete()
     bypasses the ORM cascade that would otherwise handle it. Junk questions are
     pool questions, so they acquire assignment rows as soon as HR assembles
     test sets: the common case, not an edge one.

  2. It must NOT delete a question a candidate has interacted with, even when the
     text is junk. Those are reported and left alone.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest

# Loaded by path: site-packages ships a regular `scripts` package that shadows
# backend/scripts (which has no __init__.py), so `from scripts import ...`
# resolves to the wrong module.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "purge_numeric_questions.py"
_spec = importlib.util.spec_from_file_location("purge_numeric_questions", _SCRIPT)
purge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(purge)


def _interview(db):
    from src.models.models import Interview

    iv = Interview(
        job_id=None, candidate_id=None, interview_type="TECHNICAL",
        status="draft", interviewer_email="iv@test.com",
    )
    db.add(iv)
    db.flush()
    return iv


def _question(db, interview_id, text):
    from src.models.question_models import Question

    q = Question(
        id=str(uuid.uuid4()), interview_id=interview_id, question_text=text,
        question_type="multiple_choice", difficulty_level="Medium",
        skill_name="MS office", is_pool_question=True, status="approved",
        is_approved=True,
    )
    db.add(q)
    db.flush()
    return q


def _assignment(db, interview_id, question_id):
    from src.models.question_models import QuestionSetAssignment

    a = QuestionSetAssignment(
        id=str(uuid.uuid4()), interview_id=interview_id,
        question_id=question_id, set_number=1, question_order=0,
    )
    db.add(a)
    db.flush()
    return a


# ── is_serial_like: the rule shared with the importer ─────────────────────────

@pytest.mark.parametrize("text", ["1", "30", "1.", "01.", "1.0", "Q1", "#3", " 2 "])
def test_serial_like_texts_are_junk(text):
    assert purge.is_serial_like(text) is True


@pytest.mark.parametrize("text", [
    "What is 2 + 2?", "Which is 1.5 times 2?", "Q. What is onboarding?", "", None,
    "2026 hiring targets: which is right?",
])
def test_real_questions_are_not_junk(text):
    assert purge.is_serial_like(text) is False


def test_rule_matches_the_importer():
    """If these drift, the parser refuses rows this script cannot find."""
    from generator_router import is_serial_like as parser_rule

    for text in ["1", "1.", "1.0", "Q1", "What is 2 + 2?", "Which is 1.5 times 2?"]:
        assert purge.is_serial_like(text) == parser_rule(text), text


# ── the FK behaviour ──────────────────────────────────────────────────────────

def test_assigned_junk_is_deleted_with_its_assignment(db):
    """The production case: junk questions were pooled, so assembling test sets
    gave them question_set_assignments rows. A bare DELETE on the parent raises
    IntegrityError 1451 and removes nothing."""
    from src.models.question_models import Question, QuestionSetAssignment

    iv = _interview(db)
    junk = _question(db, iv.id, "7")
    _assignment(db, iv.id, junk.id)

    assert db.query(QuestionSetAssignment).filter_by(question_id=junk.id).count() == 1

    deletable, protected = purge.classify(db, interview_id=iv.id)
    assert [r.id for r in deletable] == [junk.id]
    assert protected == []

    purge.delete_questions(db, [r.id for r in deletable])

    assert db.query(Question).filter_by(id=junk.id).count() == 0
    assert db.query(QuestionSetAssignment).filter_by(question_id=junk.id).count() == 0


def test_real_questions_are_left_alone(db):
    from src.models.question_models import Question

    iv = _interview(db)
    junk = _question(db, iv.id, "3")
    real = _question(db, iv.id, "What is the purpose of onboarding?")

    deletable, _ = purge.classify(db, interview_id=iv.id)
    purge.delete_questions(db, [r.id for r in deletable])

    assert db.query(Question).filter_by(id=real.id).count() == 1
    assert db.query(Question).filter_by(id=junk.id).count() == 0


def test_candidate_touched_junk_is_reported_not_deleted(db):
    """Even junk text is not safe to remove once a candidate has been assigned
    it — deleting would orphan their history."""
    from src.models.models import Candidate, JobPosting
    from src.models.question_models import CandidateQuestionHistory, Question

    iv = _interview(db)
    junk = _question(db, iv.id, "9")

    job = JobPosting(id=str(uuid.uuid4()), title="T", description="d",
                     requirements="r", status="active")
    db.add(job)
    db.flush()
    cand = Candidate(id=str(uuid.uuid4()), job_id=job.id, email="c@test.com",
                     first_name="A", last_name="B", current_stage="applied")
    db.add(cand)
    db.flush()
    db.add(CandidateQuestionHistory(
        id=str(uuid.uuid4()), candidate_id=cand.id,
        source_question_id=junk.id, interview_id=iv.id,
    ))
    db.flush()

    deletable, protected = purge.classify(db, interview_id=iv.id)
    assert deletable == []
    assert [r.id for r in protected] == [junk.id]

    # And nothing is removed when the caller honours the split.
    purge.delete_questions(db, [r.id for r in deletable])
    assert db.query(Question).filter_by(id=junk.id).count() == 1


def test_nothing_to_do_on_a_clean_interview(db):
    iv = _interview(db)
    _question(db, iv.id, "A perfectly normal question?")

    deletable, protected = purge.classify(db, interview_id=iv.id)
    assert deletable == []
    assert protected == []
