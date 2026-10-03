"""Who may see or act on an interview's questions.

Two rules, used by every route that serves questions or runs a candidate's code:

- A candidate reaches an interview only through their own Candidate rows (linked by
  user id, or by email for rows created before the account existed). There is no
  fallback: an account with no Candidate row owns nothing. The old fallback ("any
  interview in the company that has a candidate") let HR and interviewer accounts,
  which never have Candidate rows, submit and grade any candidate's test.
- Staff (HR, admin, interviewer) reach interviews in their own company. A superadmin
  reaches another company's interview only while that company has granted support
  access (src/services/audit_log.py), and every such read is audited.

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


def staff_can_access_interview(user: User, interview: Optional[Interview],
                               db: Optional[Session] = None, request=None) -> bool:
    """Staff reach interviews in their own company.

    A superadmin needs an active support-access grant from the interview's company,
    checked (and audited) through `db`; without a session there is nothing to check
    the grant against, so the answer is no. Pass the request so the audit row carries
    the method, path and IP. An access that cannot be audited is refused (503).
    """
    if interview is None or not is_staff(user):
        return False
    if user.role == "superadmin":
        if db is None or not interview.company_id:
            return False
        from src.services.audit_log import _audit_or_deny, _is_write, active_grant, record_audit
        grant = active_grant(db, interview.company_id)
        resource = f"interview:{interview.id}"
        if grant is None:
            record_audit(db, action="support_access.denied", actor=user,
                         company_id=interview.company_id, resource=resource, request=request)
            return False
        _audit_or_deny(db, action="superadmin.write" if _is_write(request) else "superadmin.read",
                       actor=user, company_id=interview.company_id, resource=resource,
                       request=request, detail={"grant_id": grant.id})
        return True
    return bool(interview.company_id) and interview.company_id == user.company_id
