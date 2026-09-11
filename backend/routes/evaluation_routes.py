"""
📊 EVALUATION & WORKFLOW ROUTES
- MCQ test submission + auto-grading (mark sheet)
- Coding/pseudocode submission + evaluation
- Offer accept / reject by candidate
- Email notifications at each stage
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
from sqlalchemy import text
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import uuid

from src.config.database_config_mysql import get_db
from src.auth import get_current_user
from src.models.models import User
from src.services.email_service import (
    send_mcq_result, send_offer_accepted, send_offer_rejected, resolve_sender
)
from src.models.models import Candidate as _CandidateModel, Interview as _InterviewModel

router = APIRouter(prefix="/api/v1/evaluation", tags=["Evaluation & Workflow"])


# ==================== SCHEMAS ====================

class MCQAnswer(BaseModel):
    question_id: str
    selected_answer: str   # The answer text or option label chosen

class MCQSubmission(BaseModel):
    interview_id: int
    candidate_name: str
    candidate_email: str
    answers: List[MCQAnswer]

class CodingSubmission(BaseModel):
    interview_id: int
    question_id: str
    candidate_name: str
    candidate_email: str
    language: str = "python"
    code: str
    pseudocode: Optional[str] = None

class OfferResponse(BaseModel):
    decision: str   # "accept" or "reject"
    notes: Optional[str] = None


# ==================== MCQ SUBMISSION & MARK SHEET ====================

@router.post("/mcq/submit")
def submit_mcq_test(
    submission: MCQSubmission,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit MCQ answers and get instant mark sheet."""

    # Tenant guard: the raw SQL below bypasses the before_compile auto-filter,
    # so we explicitly verify the target Interview belongs to the caller's
    # company before touching anything.  This blocks IDOR where a Company-A
    # candidate posts an interview_id from Company B.
    interview = db.query(_InterviewModel).filter(
        _InterviewModel.id == submission.interview_id
    ).first()
    if not interview:
        raise HTTPException(404, "Interview not found")
    if current_user.role != "superadmin" and interview.company_id and \
       interview.company_id != current_user.company_id:
        raise HTTPException(404, "Interview not found")

    # Who this submission is *about* is decided by the database, never by the
    # request. submission.candidate_name/candidate_email are still accepted for
    # wire compatibility but are deliberately ignored: the submitter controls
    # them, and trusting them made this endpoint an authenticated open relay —
    # any signed-in user could name an arbitrary recipient and have the tenant's
    # SMTP identity deliver to it, with company branding and aligned SPF/DKIM.
    candidate = None
    if interview.candidate_id:
        candidate = db.query(_CandidateModel).filter(
            _CandidateModel.id == interview.candidate_id
        ).first()
    candidate_display_name = (
        f"{candidate.first_name} {candidate.last_name}".strip()
        if candidate is not None else "Candidate"
    )

    # Load questions
    rows = db.execute(text(
        "SELECT id, question_text, correct_answer, points, question_type, difficulty_level, approval_notes "
        "FROM interview_questions WHERE interview_id = :iid"
    ), {"iid": submission.interview_id}).fetchall()

    if not rows:
        raise HTTPException(404, "No questions found for this interview")

    questions = {str(r[0]): {
        "text": r[1], "correct": str(r[2]).strip().lower() if r[2] else "",
        "points": r[3] or 10, "type": r[4], "difficulty": r[5], "explanation": r[6]
    } for r in rows}

    answer_map = {a.question_id: a.selected_answer.strip().lower() for a in submission.answers}

    total_points = 0
    earned_points = 0
    results = []

    for qid, q in questions.items():
        given = answer_map.get(qid, "")
        correct = q["correct"]
        is_correct = given == correct or (given and correct and given in correct)
        pts = q["points"] if is_correct else 0
        total_points += q["points"]
        earned_points += pts
        results.append({
            "question_id": qid,
            "question_text": q["text"],
            "question_type": q["type"],
            "difficulty": q["difficulty"],
            "your_answer": answer_map.get(qid, "Not answered"),
            "correct_answer": q["correct"],
            "is_correct": is_correct,
            "points_earned": pts,
            "max_points": q["points"],
            "explanation": q["explanation"]
        })

    percentage = (earned_points / total_points * 100) if total_points > 0 else 0
    passed = percentage >= 50

    # Breakdown by difficulty
    by_difficulty = {}
    by_type = {}
    for r in results:
        d = r["difficulty"] or "Unknown"
        t = r["question_type"] or "Unknown"
        by_difficulty.setdefault(d, {"correct": 0, "total": 0})
        by_difficulty[d]["total"] += 1
        if r["is_correct"]:
            by_difficulty[d]["correct"] += 1
        by_type.setdefault(t, {"correct": 0, "total": 0})
        by_type[t]["total"] += 1
        if r["is_correct"]:
            by_type[t]["correct"] += 1

    # Advance candidate stage to test_submitted.
    # Keyed on the interview's own candidate id, not the submitted email. Matching
    # on email here was both spoofable and unscoped — the raw SQL bypasses the
    # before_compile tenant filter, so one address could move candidate rows in
    # every company that happened to share it.
    if candidate is not None:
        try:
            db.execute(text(
                "UPDATE candidates SET current_stage='test_submitted', updated_at=NOW() "
                "WHERE id=:cid AND current_stage='shortlisted'"
            ), {"cid": candidate.id})
            db.execute(text(
                "INSERT INTO candidate_stage_history (id, candidate_id, from_stage, to_stage, notes, created_at) "
                "SELECT :hid, id, 'shortlisted', 'test_submitted', :notes, NOW() "
                "FROM candidates WHERE id=:cid AND current_stage='test_submitted' LIMIT 1"
            ), {
                "hid": str(uuid.uuid4()),
                "cid": candidate.id,
                "notes": f"Test submitted — Score: {round(percentage, 1)}% ({'PASS' if passed else 'FAIL'})"
            })
            db.commit()
        except Exception:
            db.rollback()

    # Send result email to the interview's own candidate, using that candidate's
    # company to resolve the sender. Both the recipient and the company come from
    # the database — see the note above the candidate lookup.
    email_status = "skipped"
    if candidate is None or not candidate.email:
        logger.warning(
            "MCQ result email skipped: interview %s has no candidate to notify",
            submission.interview_id,
        )
    elif not candidate.company_id:
        # Never resolve a sender with company_id=None. resolve_sender falls through
        # to the platform's env SMTP identity, which would let a tenant-less row
        # borrow the platform's sending reputation.
        logger.warning(
            "MCQ result email skipped: candidate %s has no company", candidate.id
        )
    else:
        try:
            from src.services.pipeline_notifications import resolve_step_dispatch
            from src.services.email_service import delivery_outcome
            disp = resolve_step_dispatch(db, candidate.company_id, "mcq_result")
            smtp_config = disp["smtp_config"] or resolve_sender(db, candidate.company_id, "candidate")
            _result = send_mcq_result(
                candidate.email, candidate_display_name,
                f"Interview #{submission.interview_id}",
                earned_points, total_points, passed,
                smtp_config=smtp_config,
                cc=disp["cc"]
            )
            email_status = delivery_outcome(_result)["status"]
        except Exception as _e:
            logger.warning(f"MCQ result email to {candidate.email} failed: {_e}")
            email_status = "failed"

    return {
        "mark_sheet": {
            "candidate_name": candidate_display_name,
            "interview_id": submission.interview_id,
            "submitted_at": datetime.now().isoformat(),
            "total_questions": len(questions),
            "answered": len(answer_map),
            "correct": sum(1 for r in results if r["is_correct"]),
            "incorrect": sum(1 for r in results if not r["is_correct"]),
            "score": earned_points,
            "max_score": total_points,
            "percentage": round(percentage, 2),
            "result": "PASS" if passed else "FAIL",
            "grade": (
                "A+" if percentage >= 90 else "A" if percentage >= 80 else
                "B" if percentage >= 70 else "C" if percentage >= 60 else
                "D" if percentage >= 50 else "F"
            ),
        },
        "performance_breakdown": {
            "by_difficulty": by_difficulty,
            "by_question_type": by_type
        },
        "detailed_results": results,
        # Was hardcoded True. "sent" and "queued" both mean the message was
        # accepted for delivery; anything else means it was not.
        "email_sent": email_status in ("sent", "queued"),
        "email_status": email_status,
    }


# ==================== CODING / PSEUDOCODE EVALUATION ====================

@router.post("/coding/submit")
def submit_coding_answer(
    submission: CodingSubmission,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit coding or pseudocode answer for a question. Returns structured evaluation."""

    # Tenant guard before raw SQL.
    interview = db.query(_InterviewModel).filter(
        _InterviewModel.id == submission.interview_id
    ).first()
    if not interview:
        raise HTTPException(404, "Interview not found")
    if current_user.role != "superadmin" and interview.company_id and \
       interview.company_id != current_user.company_id:
        raise HTTPException(404, "Interview not found")

    row = db.execute(text(
        "SELECT question_text, sample_answer, correct_answer, points, difficulty_level "
        "FROM interview_questions WHERE id = :qid AND interview_id = :iid"
    ), {"qid": submission.question_id, "iid": submission.interview_id}).fetchone()

    if not row:
        raise HTTPException(404, "Question not found")

    question_text, sample_answer, correct_answer, points, difficulty = row
    code = submission.code.strip()
    pseudo = submission.pseudocode or ""

    # Heuristic evaluation (keywords, length, structure)
    keywords_found = []
    score = 0
    feedback = []

    sample_lower = (sample_answer or "").lower()
    code_lower = code.lower()

    # Check for key programming constructs
    constructs = {
        "loop": ["for ", "while ", "loop"],
        "condition": ["if ", "else", "elif", "switch"],
        "function": ["def ", "function", "return"],
        "data_structure": ["list", "dict", "array", "map", "set"],
        "error_handling": ["try", "except", "catch", "error"],
    }
    for construct, patterns in constructs.items():
        if any(p in code_lower for p in patterns):
            keywords_found.append(construct)
            score += 10

    # Length adequacy
    if len(code.split('\n')) >= 5:
        score += 10
        feedback.append("✅ Adequate code length")
    else:
        feedback.append("⚠️ Code seems too short")

    # Has comments?
    if "#" in code or "//" in code or "/*" in code:
        score += 5
        feedback.append("✅ Code is commented")
    else:
        feedback.append("💡 Add comments to explain logic")

    # Pseudocode bonus
    if pseudo and len(pseudo) > 30:
        score += 10
        feedback.append("✅ Pseudocode provided — shows clear thinking")

    # Cap at max points
    max_points = points or 20
    earned = min(score, max_points)
    pct = (earned / max_points) * 100

    return {
        "evaluation": {
            "question": question_text,
            "language": submission.language,
            "difficulty": difficulty,
            "max_points": max_points,
            "earned_points": earned,
            "percentage": round(pct, 1),
            "result": "PASS" if pct >= 50 else "NEEDS_IMPROVEMENT",
        },
        "code_analysis": {
            "constructs_used": keywords_found,
            "lines_of_code": len(code.split('\n')),
            "has_pseudocode": bool(pseudo),
            "feedback": feedback,
        },
        "sample_solution": sample_answer or "No sample solution provided",
        "note": "This is an automated heuristic evaluation. Final score subject to interviewer review."
    }


# ==================== OFFER ACCEPT / REJECT ====================

@router.post("/offers/{offer_id}/respond")
def respond_to_offer(
    offer_id: str,
    response: OfferResponse,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Candidate accepts or rejects an offer."""

    if response.decision.lower() not in ("accept", "reject"):
        raise HTTPException(400, "decision must be 'accept' or 'reject'")

    row = db.execute(text(
        """SELECT o.id, o.status, o.salary, o.start_date, o.expiry_date,
                  c.first_name, c.last_name, c.email,
                  j.title as job_title,
                  u.email as hr_email
           FROM offers o
           JOIN candidates c ON o.candidate_id = c.id
           JOIN job_postings j ON o.job_id = j.id
           LEFT JOIN hr_profiles hp ON o.hr_id = hp.id
           LEFT JOIN users u ON hp.user_id = u.id
           WHERE o.id = :oid"""
    ), {"oid": offer_id}).fetchone()

    if not row:
        raise HTTPException(404, "Offer not found")

    (oid, status, salary, start_date, expiry_date,
     fname, lname, c_email, job_title, hr_email) = row

    # Verify the current user owns this offer (must be the candidate)
    if current_user.email != c_email:
        raise HTTPException(403, "You are not authorized to respond to this offer")

    if status != "sent":
        raise HTTPException(400, f"Offer is already '{status}' and cannot be responded to")

    # Check expiry
    from datetime import date as _date
    if expiry_date and _date.today() > expiry_date:
        raise HTTPException(400, "This offer has expired and can no longer be responded to")

    new_status = "accepted" if response.decision.lower() == "accept" else "rejected"

    candidate_name = f"{fname} {lname}"

    # Update offer and candidate stage fully via ORM
    from src.models.models import Offer, Candidate as CandidateModel, CandidateStageHistory
    cand_obj = None
    offer_obj = db.query(Offer).filter(Offer.id == offer_id).first()
    if offer_obj:
        offer_obj.status = new_status
        offer_obj.responded_at = datetime.now()
        cand_obj = db.query(CandidateModel).filter(CandidateModel.id == offer_obj.candidate_id).first()
        if cand_obj:
            prev = cand_obj.current_stage
            cand_obj.current_stage = "hired" if new_status == "accepted" else "offer_rejected"
            db.add(CandidateStageHistory(
                candidate_id=cand_obj.id,
                from_stage=prev,
                to_stage=cand_obj.current_stage,
                changed_by_id=current_user.id,
                notes=f"Offer {new_status} by candidate"
            ))

    from src.services.pipeline_notifications import resolve_step_dispatch
    _cid = getattr(cand_obj, "company_id", None) if cand_obj else None
    _step = "offer_accepted" if new_status == "accepted" else "offer_rejected"
    hr_email_status = "skipped"
    if not _cid:
        # Same guard as the MCQ path: resolve_sender(db, None, ...) falls through
        # to the platform's env SMTP identity, letting a tenant-less row borrow
        # the platform's sending reputation.
        logger.warning("offer-response email skipped: offer %s has no company", offer_id)
    else:
        from src.services.email_service import delivery_outcome
        disp = resolve_step_dispatch(db, _cid, _step, offer=offer_obj)
        smtp_config = disp["smtp_config"] or resolve_sender(db, _cid, "candidate")
        try:
            if new_status == "accepted":
                _res = send_offer_accepted(hr_email or "hr@company.com", candidate_name,
                                           job_title, str(start_date),
                                           smtp_config=smtp_config, cc=disp["cc"])
            else:
                _res = send_offer_rejected(hr_email or "hr@company.com", candidate_name,
                                           job_title, smtp_config=smtp_config,
                                           cc=disp["cc"])
            outcome = delivery_outcome(_res)
            hr_email_status = outcome["status"]
            if not outcome["ok"]:
                logger.warning(
                    f"offer-{new_status} email to HR ({hr_email}) not delivered: "
                    f"{outcome['error_type']} — {outcome['error']}"
                )
        except Exception as _e:
            hr_email_status = "failed"
            logger.warning(f"offer-{new_status} email to HR ({hr_email}) failed: {_e}")

    db.commit()

    return {
        "offer_id": offer_id,
        "decision": response.decision.upper(),
        "new_status": new_status,
        "candidate": candidate_name,
        "job_title": job_title,
        "notes": response.notes,
        "responded_at": datetime.now().isoformat(),
        "message": f"Offer {new_status} successfully." + {
            "sent": " HR has been notified via email.",
            "queued": " An email to HR is queued for delivery.",
            "failed": " WARNING: HR could not be notified by email.",
            "skipped": " No HR notification was sent.",
        }.get(hr_email_status, ""),
        "hr_email_status": hr_email_status,
    }


# ==================== MARK SHEET BY INTERVIEW ====================

@router.get("/marksheet/{interview_id}")
def get_marksheet(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get question statistics / mark sheet for an interview."""

    # Tenant guard before raw SQL.
    interview = db.query(_InterviewModel).filter(
        _InterviewModel.id == interview_id
    ).first()
    if not interview:
        raise HTTPException(404, "Interview not found")
    if current_user.role != "superadmin" and interview.company_id and \
       interview.company_id != current_user.company_id:
        raise HTTPException(404, "Interview not found")

    rows = db.execute(text(
        """SELECT question_type, difficulty_level, points,
                  COUNT(*) as total, SUM(is_approved) as approved
           FROM interview_questions
           WHERE interview_id = :iid
           GROUP BY question_type, difficulty_level, points"""
    ), {"iid": interview_id}).fetchall()

    if not rows:
        raise HTTPException(404, "No questions found for this interview")

    total_questions = 0
    total_marks = 0
    breakdown = []
    by_type: Dict[str, Any] = {}

    for qtype, diff, pts, total, approved in rows:
        total_questions += total
        total_marks += (pts or 10) * total
        breakdown.append({
            "question_type": qtype,
            "difficulty": diff,
            "points_each": pts or 10,
            "question_count": total,
            "subtotal_marks": (pts or 10) * total,
            "approved": approved or 0
        })
        by_type.setdefault(qtype, {"count": 0, "marks": 0})
        by_type[qtype]["count"] += total
        by_type[qtype]["marks"] += (pts or 10) * total

    return {
        "interview_id": interview_id,
        "mark_sheet_summary": {
            "total_questions": total_questions,
            "total_marks": total_marks,
            "passing_marks": int(total_marks * 0.5),
            "question_breakdown": breakdown,
            "by_type": by_type
        }
    }
