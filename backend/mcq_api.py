"""
📝 MCQ QUESTIONS API
API endpoints for managing MCQ questions.

Tenant isolation: every endpoint requires authentication, and any lookup by
interview_id or question_id verifies the target row belongs to the caller's
company before touching the underlying ``interview_questions`` table (raw
SQL — the SQLAlchemy auto-filter does not apply).

Answer keys: a candidate only ever gets their own interview's paper, with
``correct_answer`` blanked and only sample test cases. Every other endpoint that
returns or changes answer keys is staff-only.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
import json

from src.config.database_config_mysql import get_db
from src.auth import get_current_user, require_role
from src.models.models import User, Interview
from src.judge.starter_sanitizer import sanitize_starter
from src.mcq_options import normalize_mcq_options
from src.services.interview_access import STAFF_ROLES, candidate_owns_interview, is_candidate
from sqlalchemy import text

router = APIRouter(prefix="/mcq", tags=["MCQ Questions"])
_staff_user = require_role(list(STAFF_ROLES))

# Pydantic Models
class MCQQuestion(BaseModel):
    id: str
    interview_id: str
    question_text: str
    question_type: str
    difficulty_level: str
    options: List[str]
    correct_answer: str
    explanation: Optional[str] = None
    time_limit_minutes: Optional[int] = None
    points: Optional[int] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Coding questions: starter code + SAMPLE-only test cases (hidden cases never sent).
    code_template: Optional[str] = None
    sample_test_cases: Optional[List[dict]] = None
    # Target coding language (python/java/cpp/javascript/sql); None = candidate's choice.
    language: Optional[str] = None

    class Config:
        from_attributes = True

class MCQQuestionList(BaseModel):
    questions: List[MCQQuestion]
    total_count: int
    interview_id: str

class MCQQuestionCreate(BaseModel):
    interview_id: str
    question_text: str
    question_type: str = "multiple_choice"
    difficulty_level: str = "Medium"
    options: List[str]
    correct_answer: str
    explanation: Optional[str] = None
    time_limit_minutes: int = 5
    points: int = 10


def _assert_interview_in_tenant(db: Session, interview_id, current_user: User,
                                request: Optional[Request] = None) -> Interview:
    """Resolve an interview by id and raise 404 if it doesn't belong to the
    caller's company.  A superadmin reaches it only under the owning company's
    support-access grant, audited (require_support_access).  An interview with
    no company belongs to no tenant and is refused to everyone else.
    """
    try:
        iv_id = int(interview_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Interview not found")

    interview = db.query(Interview).filter(Interview.id == iv_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if current_user.role != "superadmin" and (
        not interview.company_id or interview.company_id != current_user.company_id
    ):
        raise HTTPException(status_code=404, detail="Interview not found")
    # Superadmin: only under the company's support-access grant, and audited.
    from src.services.audit_log import require_support_access
    require_support_access(db, request, current_user, interview.company_id, f"interview {iv_id} (mcq)")
    return interview


def _assert_question_in_tenant(db: Session, question_id: str, current_user: User,
                               request: Optional[Request] = None) -> None:
    """Block cross-tenant question lookup/mutation by id."""
    row = db.execute(
        text("""
            SELECT q.company_id, i.company_id
            FROM interview_questions q
            LEFT JOIN interviews i ON q.interview_id = i.id
            WHERE q.id = :qid
        """),
        {"qid": question_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Question not found")
    q_company = row[0]
    iv_company = row[1]
    # The owning interview is authoritative; the question's own column is a copy of it.
    # No owner at all means no tenant, which is not the caller's.
    owner = iv_company or q_company
    if current_user.role == "superadmin":
        # Answer keys are client data: only under that company's grant, and audited.
        if not owner:
            raise HTTPException(status_code=404, detail="Question not found")
        from src.services.audit_log import require_support_access
        require_support_access(db, request, current_user, owner, f"question {question_id} (mcq)")
        return
    if not owner or owner != current_user.company_id:
        raise HTTPException(status_code=404, detail="Question not found")


@router.get("/interviews/{interview_id}/questions", response_model=MCQQuestionList)
def get_interview_mcq_questions(
    interview_id: str,
    request: Request,
    difficulty: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all MCQ questions for a specific interview (tenant-scoped).

    Candidates: only their own interview, only approved questions, and never the
    answer key — ``correct_answer`` is always empty for them.
    """
    _assert_interview_in_tenant(db, interview_id, current_user, request)
    candidate_view = is_candidate(current_user)
    if candidate_view and not candidate_owns_interview(db, current_user, interview_id):
        raise HTTPException(status_code=404, detail="Interview not found")

    # A candidate must not be able to pull the paper again after submitting. The sibling
    # endpoint (candidate_routes /interviews/{id}/questions) already blocks this, but the
    # client tries THIS one first, so without the same guard a completed test still served
    # its questions and the retake was only stopped at submission.
    #
    # Candidates only: HR, interviewers and admins legitimately read the questions of a
    # completed interview when reviewing it.
    if str(getattr(current_user, "role", "")).lower() == "candidate":
        completed = db.execute(
            text("SELECT status FROM interviews WHERE id = :id"),
            {"id": interview_id},
        ).scalar()
        if str(completed or "").upper() == "COMPLETED":
            raise HTTPException(
                status_code=403,
                detail="Test already submitted. You cannot retake this test.",
            )

    try:
        query = "SELECT id, interview_id, question_text, question_type, difficulty_level, category, options, correct_answer, time_limit_minutes, points, status, created_at, updated_at, code_template, test_cases, language FROM interview_questions WHERE interview_id = :interview_id"
        params = {"interview_id": interview_id}
        if candidate_view:
            query += " AND is_approved = 1"

        if difficulty:
            query += " AND difficulty_level = :difficulty"
            params["difficulty"] = difficulty

        query += " ORDER BY CASE WHEN question_type = 'coding' THEN 1 ELSE 0 END, question_type, difficulty_level, id"

        result = db.execute(text(query), params)
        questions_data = result.fetchall()

        # Fallback: if no questions assigned directly, pull from the source template interview
        # (same job_id, no candidate_id) — but only if that template is in the same company.
        if not questions_data:
            try:
                iv_res = db.execute(
                    text("SELECT job_id, candidate_id, question_format_data, company_id FROM interviews WHERE id = :id"),
                    {"id": interview_id}
                )
                iv_row = iv_res.fetchone()
                if iv_row and iv_row[1]:  # candidate_id set → this is a candidate test
                    job_id = iv_row[0]
                    own_fmt = json.loads(iv_row[2]) if iv_row[2] else {}
                    limit = int(own_fmt.get("total_questions", 0)) or None

                    # Template lookup MUST stay within the same company.
                    src_res = db.execute(
                        text("SELECT id FROM interviews WHERE job_id = :job_id AND candidate_id IS NULL "
                             "AND (company_id <=> :cid) ORDER BY id DESC LIMIT 1"),
                        {"job_id": job_id, "cid": iv_row[3]}
                    )
                    src_row = src_res.fetchone()
                    if src_row:
                        src_id = src_row[0]
                        fq = (
                            "SELECT id, interview_id, question_text, question_type, difficulty_level, "
                            "category, options, correct_answer, time_limit_minutes, points, status, "
                            "created_at, updated_at, code_template, test_cases, language FROM interview_questions "
                            "WHERE interview_id = :interview_id AND is_approved = 1 ORDER BY RAND()"
                        )
                        if limit:
                            fq += f" LIMIT {int(limit)}"
                        fallback_result = db.execute(text(fq), {"interview_id": src_id})
                        questions_data = fallback_result.fetchall()
            except Exception as fb_err:
                print(f"[MCQ] Fallback failed: {fb_err}")

        questions = []
        for q in questions_data:
            raw_opts = q[6]
            # Split any merged "A) .. B) .. C) .." blob back into separate options.
            options = normalize_mcq_options(raw_opts)

            # Coding: expose starter code + SAMPLE test cases only (never hidden ones).
            raw_tc = q[14] if len(q) > 14 else None
            sample_cases = []
            try:
                all_tc = json.loads(raw_tc) if raw_tc and str(raw_tc).strip() else []
                sample_cases = [
                    {"input": t.get("input", ""), "expected_output": t.get("expected_output", "")}
                    for t in all_tc if isinstance(t, dict) and t.get("is_sample")
                ]
            except (json.JSONDecodeError, TypeError):
                sample_cases = []

            # The generator sometimes leaks the full solution into code_template; strip
            # it to a starter stub so candidates never see the answer in the editor.
            qtype = str(q[3]) if q[3] else 'multiple_choice'
            raw_template = str(q[13]) if len(q) > 13 and q[13] else None
            safe_template = (
                sanitize_starter(raw_template, category=(str(q[5]) if q[5] else None))
                if qtype == 'coding' else raw_template
            )

            question = MCQQuestion(
                id=str(q[0]),
                interview_id=str(q[1]),
                question_text=str(q[2]),
                question_type=qtype,
                difficulty_level=str(q[4]) if q[4] else 'Medium',
                options=options,
                correct_answer='' if candidate_view else (str(q[7]) if q[7] else ''),
                explanation=None,
                time_limit_minutes=int(q[8]) if q[8] else 5,
                points=int(q[9]) if q[9] else 1,
                status=str(q[10]) if q[10] else 'approved',
                created_at=str(q[11]) if q[11] else None,
                updated_at=str(q[12]) if q[12] else None,
                code_template=safe_template,
                sample_test_cases=sample_cases,
                language=(str(q[15]) if len(q) > 15 and q[15] else None),
            )
            questions.append(question)

        return MCQQuestionList(
            questions=questions,
            total_count=len(questions),
            interview_id=interview_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving questions: {str(e)}"
        )

@router.get("/questions/{question_id}", response_model=MCQQuestion)
def get_mcq_question(
    question_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(_staff_user),
):
    """Get a specific MCQ question by ID (tenant-scoped)."""
    _assert_question_in_tenant(db, question_id, current_user, request)

    try:
        result = db.execute(
            text("""SELECT id, interview_id, question_text, question_type,
                    difficulty_level, options, correct_answer,
                    time_limit_minutes, points, status, created_at, updated_at
                    FROM interview_questions WHERE id = :question_id"""),
            {"question_id": question_id}
        )
        row = result.mappings().fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Question not found"
            )

        # Parse options JSON (and split any merged single-blob option set).
        options = normalize_mcq_options(row['options'])

        return MCQQuestion(
            id=str(row['id']),
            interview_id=str(row['interview_id']),
            question_text=str(row['question_text']),
            question_type=str(row['question_type']) if row['question_type'] else 'multiple_choice',
            difficulty_level=str(row['difficulty_level']) if row['difficulty_level'] else 'Medium',
            options=options,
            correct_answer=str(row['correct_answer']) if row['correct_answer'] else '',
            explanation=None,
            time_limit_minutes=int(row['time_limit_minutes']) if row['time_limit_minutes'] else 5,
            points=int(row['points']) if row['points'] else 1,
            status=str(row['status']) if row['status'] else 'approved',
            created_at=str(row['created_at']) if row['created_at'] else None,
            updated_at=str(row['updated_at']) if row['updated_at'] else None
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving question: {str(e)}"
        )

@router.post("/questions", response_model=MCQQuestion, status_code=status.HTTP_201_CREATED)
def create_mcq_question(
    question: MCQQuestionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(_staff_user),
):
    """Create a new MCQ question (tenant-scoped — only on caller's interviews)."""
    interview = _assert_interview_in_tenant(db, question.interview_id, current_user, request)

    try:
        import uuid
        question_id = f"mcq_{str(uuid.uuid4())[:8]}"

        # Stamp the row with the interview's company_id so the auto-filter and
        # raw-SQL guards both see the correct owner.
        insert_query = """
            INSERT INTO interview_questions (
                id, interview_id, company_id, question_text, question_type,
                difficulty_level, options, correct_answer,
                time_limit_minutes, points,
                status, created_at, updated_at
            ) VALUES (
                :id, :interview_id, :company_id, :question_text, :question_type,
                :difficulty_level, :options, :correct_answer,
                :time_limit_minutes, :points,
                :status, NOW(), NOW()
            )
        """

        db.execute(text(insert_query), {
            'id': question_id,
            'interview_id': question.interview_id,
            'company_id': interview.company_id,
            'question_text': question.question_text,
            'question_type': question.question_type,
            'difficulty_level': question.difficulty_level,
            'options': json.dumps(question.options),
            'correct_answer': question.correct_answer,
            'time_limit_minutes': question.time_limit_minutes,
            'points': question.points,
            'status': 'generated'
        })

        db.commit()

        # Retrieve the created question (auth re-checked via dep above)
        return get_mcq_question(question_id, db, current_user)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating question: {str(e)}"
        )

@router.get("/categories")
def get_mcq_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """MCQ question categories and statistics, scoped to the caller's company."""
    try:
        cid = current_user.company_id
        is_super = current_user.role == "superadmin"

        # Difficulty distribution
        if is_super:
            result = db.execute(text(
                "SELECT difficulty_level, COUNT(*) AS count FROM interview_questions GROUP BY difficulty_level"
            ))
        else:
            result = db.execute(text(
                "SELECT difficulty_level, COUNT(*) AS count FROM interview_questions "
                "WHERE company_id = :cid GROUP BY difficulty_level"
            ), {"cid": cid})
        difficulty_stats = result.fetchall()

        # Type distribution
        if is_super:
            result = db.execute(text(
                "SELECT question_type, COUNT(*) AS count FROM interview_questions GROUP BY question_type"
            ))
        else:
            result = db.execute(text(
                "SELECT question_type, COUNT(*) AS count FROM interview_questions "
                "WHERE company_id = :cid GROUP BY question_type"
            ), {"cid": cid})
        type_stats = result.fetchall()

        if is_super:
            result = db.execute(text("SELECT COUNT(*) FROM interview_questions"))
        else:
            result = db.execute(text(
                "SELECT COUNT(*) FROM interview_questions WHERE company_id = :cid"
            ), {"cid": cid})
        total_questions = result.fetchone()[0]

        return {
            "total_questions": total_questions,
            "difficulty_distribution": [
                {"level": row[0], "count": row[1]} for row in difficulty_stats
            ],
            "type_distribution": [
                {"type": row[0], "count": row[1]} for row in type_stats
            ],
            "available_difficulties": ["Easy", "Medium", "Hard"],
            "available_types": ["multiple_choice", "short_answer", "coding", "behavioral", "technical", "situational"]
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving categories: {str(e)}"
        )

@router.get("/sample")
def get_sample_mcq_questions(
    db: Session = Depends(get_db),
    current_user: User = Depends(_staff_user),
):
    """Sample MCQ questions — limited to the caller's company."""
    # A superadmin has no company of their own; a random sample across every tenant
    # (answer keys included) is not support access to any one of them.
    from src.services.audit_log import refuse_superadmin_listing
    refuse_superadmin_listing(current_user, "Sample questions")
    try:
        result = db.execute(text(
            "SELECT id, question_text, difficulty_level, options, correct_answer "
            "FROM interview_questions WHERE company_id = :cid ORDER BY RAND() LIMIT 5"
        ), {"cid": current_user.company_id})
        sample_questions = result.fetchall()

        questions = []
        for q in sample_questions:
            options = normalize_mcq_options(q[3])
            questions.append({
                "id": q[0],
                "question_text": q[1],
                "difficulty_level": q[2],
                "options": options,
                "correct_answer": q[4]
            })

        return {
            "sample_questions": questions,
            "total_available": len(questions)
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving sample questions: {str(e)}"
        )

@router.delete("/questions/{question_id}")
def delete_mcq_question(
    question_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(_staff_user),
):
    """Delete a MCQ question (tenant-scoped)."""
    _assert_question_in_tenant(db, question_id, current_user, request)

    try:
        result = db.execute(
            text("DELETE FROM interview_questions WHERE id = :question_id"),
            {"question_id": question_id}
        )

        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Question not found"
            )

        db.commit()

        return {"message": "Question deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting question: {str(e)}"
        )
