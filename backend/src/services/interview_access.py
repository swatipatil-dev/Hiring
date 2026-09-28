"""Who may see or act on an interview's questions.

Two rules, used by every route that serves questions or runs a candidate's code:

- A candidate reaches an interview only through their own Candidate rows (linked by
  user id, or by email for rows created before the account existed). There is no
  fallback: an account with no Candidate row owns nothing. The old fallback ("any
  interview in the company that has a candidate") let HR and interviewer accounts,
  which never have Candidate rows, submit and grade any candidate's test.
- Staff (HR, admin, interviewer) reach interviews in their own company; superadmin
  reaches all of them.

Answer keys (``correct_answer``, ``sample_answer``) and hidden test cases are for
staff only. ``is_staff`` is the single place that decides who counts as staff.
"""
from typing import List, Optional

from sqlalchemy.orm import Session

from src.models.models import Candidate, Interview, User

STAFF_ROLES = ("hr", "admin", "interviewer", "superadmin")


def is_staff(user: User) -> bool:
    return str(getattr(user, "role", "") or "").lower() in STAFF_ROLES


def is_candidate(user: User) -> bool:
    return str(getattr(user, "role", "") or "").lower() == "candidate"


def candidate_ids_for(db: Session, user: User) -> List[str]:
    """The Candidate rows that belong to this user account (none for non-candidates)."""
    if not is_candidate(user):
        return []
    rows = db.query(Candidate.id).filter(Candidate.user_id == user.id).all()
    if not rows and user.email:
        rows = db.query(Candidate.id).filter(Candidate.email == user.email).all()
    return [r[0] for r in rows]


def candidate_owns_interview(db: Session, user: User, interview_id) -> Optional[Interview]:
    """The interview if it is this candidate's own, else None."""
    try:
        iv_id = int(interview_id)
    except (TypeError, ValueError):
        return None
    cand_ids = candidate_ids_for(db, user)
    if not cand_ids:
        return None
    return db.query(Interview).filter(
        Interview.id == iv_id,
        Interview.company_id == user.company_id,
        Interview.candidate_id.in_(cand_ids),
    ).first()


def staff_can_access_interview(user: User, interview: Optional[Interview]) -> bool:
    """Staff reach interviews in their own company; superadmin reaches every company."""
    if interview is None or not is_staff(user):
        return False
    if user.role == "superadmin":
        return True
    return bool(interview.company_id) and interview.company_id == user.company_id
