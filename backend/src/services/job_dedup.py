"""One definition of "we are already hiring for this".

Three endpoints can bring a role into existence — an interviewer's candidate request, the
interviewer's auto-create-job, and HR's job posting — and until now each had its own idea
of what a duplicate was: two had none at all, and the third matched any job with that
title in **any tenant**, including ones closed years ago. That is how the same opening
ended up in the system several times over.

The rules live here rather than in the route files so a change to what "the same role"
means reaches all three callers, instead of fixing one and letting the others drift.

On the two status sets below: they are asymmetric on purpose, and the asymmetry is the
point. `JobPosting.status` is a closed vocabulary written only by our own code, so it is
safe to name the open states. `CandidateRequest.status` is written straight from an
unvalidated dict in HR's update endpoint (`routes/hr_routes.py`), so any string at all can
land there — which means naming the *finished* states and treating everything else as
still open. Get that backwards and a typo in HR's payload silently switches the guard off.
"""

from typing import Optional

from sqlalchemy import func

# JobPosting.status is draft | pending_approval | active | closed (models.py:334).
# Everything but closed is a role someone is still working.
OPEN_JOB_STATUSES = ("draft", "pending_approval", "active")

# CandidateRequest.status defaults to "pending" but is otherwise open-ended — see the
# module docstring for why this is a blocklist and not an allowlist.
DONE_REQUEST_STATUSES = ("rejected", "fulfilled", "closed", "cancelled", "declined")


def _same_text(column, value: str):
    """Case- and whitespace-insensitive equality for a free-text column.

    Deliberately not `ilike`: a title containing `%` or `_` would be read as a wildcard,
    so "50% Analyst" would match half the board and "Data_Engineer" would match
    "DataXEngineer". Equality on the normalised text has no such hole.
    """
    return func.lower(func.trim(column)) == value.strip().lower()


def find_open_duplicate_job(db, company_id: Optional[str], title: str,
                            department_id: Optional[str] = None):
    """An open job for this role in this company, or None.

    `company_id` is always applied explicitly. The `before_compile` tenant auto-filter
    would normally add it, but it is bound from a sync dependency that FastAPI runs in a
    threadpool, so the contextvar it writes does not reach the async handler — relying on
    it here is what let this query return another tenant's job.
    """
    from src.models.models import JobPosting

    if not title or not title.strip():
        return None

    q = db.query(JobPosting).filter(
        JobPosting.company_id == company_id,
        _same_text(JobPosting.title, title),
        JobPosting.status.in_(OPEN_JOB_STATUSES),
    )
    # A role with no department set is only a duplicate of another with none — otherwise
    # one unfiled job would block every department from hiring that title.
    q = (q.filter(JobPosting.department_id.is_(None)) if department_id is None
         else q.filter(JobPosting.department_id == department_id))
    return q.order_by(JobPosting.created_at.desc()).first()


def find_open_duplicate_request(db, company_id: Optional[str], title: str,
                                department: Optional[str] = None):
    """An unfinished candidate request for this role in this company, or None.

    `CandidateRequest.department` is free text, not a foreign key, so it is compared the
    same forgiving way the title is.
    """
    from src.models.models import CandidateRequest

    if not title or not title.strip():
        return None

    q = db.query(CandidateRequest).filter(
        CandidateRequest.company_id == company_id,
        _same_text(CandidateRequest.job_title, title),
        # NOT IN is null-hostile in SQL, so a NULL status would fall out of the result
        # entirely and read as "no duplicate". A request with no status is still open.
        func.coalesce(func.lower(CandidateRequest.status), "pending").notin_(DONE_REQUEST_STATUSES),
    )
    if department is None or not str(department).strip():
        q = q.filter(func.coalesce(func.trim(CandidateRequest.department), "") == "")
    else:
        q = q.filter(_same_text(CandidateRequest.department, department))
    return q.order_by(CandidateRequest.created_at.desc()).first()


def duplicate_request_detail(existing, requester=None) -> str:
    """Why the request was refused, and where to go instead.

    A bare "duplicate" tells someone they are blocked but not what to do about it. Naming
    the existing request, its state and who raised it means they can go and look at it —
    which is usually the actual answer, since the role they want is already in flight.
    """
    who = ""
    if requester is not None:
        name = f"{requester.first_name or ''} {requester.last_name or ''}".strip()
        who = f" by {name or requester.email}"
    when = existing.created_at.strftime("%d %b") if existing.created_at else "earlier"
    return (
        f"A request for '{existing.job_title}' was already raised{who} on {when} and is "
        f"still {existing.status or 'pending'}. Ask HR about that one, or change the "
        f"title if this is a different role."
    )


def duplicate_job_detail(existing) -> str:
    """Why the posting was refused, and which job is in the way."""
    when = existing.created_at.strftime("%d %b") if existing.created_at else "earlier"
    return (
        f"A job titled '{existing.title}' already exists for this department "
        f"(created {when}, currently {existing.status}). Edit or close that one instead "
        f"of posting a second copy."
    )
