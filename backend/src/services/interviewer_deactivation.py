"""
🚫 INTERVIEWER DEACTIVATION

Deactivating a user flips ``users.is_active`` — and ``src/auth.py`` then refuses their
login. Without the cascade below, that leaves a black hole: the person stays the default
interviewer on their jobs, keeps being offered in HR's pickers, keeps being auto-assigned
to brand-new jobs, and keeps receiving panel invites for interviews they can never open.

Two halves, both exported:

* ``deactivate_interviewer_assignments`` — run when a user is deactivated. Detaches them
  from *active* work only.
Its counterpart lives in ``staff_directory`` — ``active_interviewer_query`` and friends,
the queries every people-picker should build on, so a deactivated user cannot be
re-assigned the moment after they are detached.

What is deliberately NOT touched: COMPLETED interviews and every ``InterviewFeedback`` row.
Those name who evaluated whom, and are the hiring audit trail — blanking them would make
past scorecards anonymous. Only current and future assignments are cleared.
"""

import json
import logging

from sqlalchemy import func

from src.models.models import (
    Interview,
    InterviewerProfile,
    JobPosting,
)
from src.services.notification_service import create_notification

logger = logging.getLogger(__name__)

# Interview.status values that still represent work in the future. Compared
# case-insensitively on purpose: the app writes "SCHEDULED" but compares lowercase in
# several places (e.g. interviewer_routes.py:148), so rows of either casing exist and a
# plain == would silently skip them — exactly the class of bug this module fixes.
_UPCOMING_STATUSES = ("SCHEDULED", "RESCHEDULED")


def _panel_without(panel, email):
    """Drop `email` from a parsed panel roster, case-insensitively."""
    target = (email or "").strip().lower()
    return [m for m in panel if (m.get("email") or "").strip().lower() != target]


def deactivate_interviewer_assignments(db, user, actor_id=None):
    """Detach a just-deactivated interviewer from all active assignments.

    Does NOT commit — the caller owns the transaction, the same contract
    ``update_candidate_stage`` (hr_routes.py:421) uses, so the cascade and the
    ``is_active`` flip land together or not at all.

    Returns a summary dict suitable for returning straight to the API caller.
    """
    summary = {
        "jobs_unassigned": 0,
        "interviews_updated": 0,
        "interviews_needing_interviewer": 0,
    }

    profile = db.query(InterviewerProfile).filter(
        InterviewerProfile.user_id == user.id
    ).first()
    if not profile:
        # HR/admin/candidate — no interviewer assignments to unwind.
        return summary

    company_id = user.company_id

    # ---- 1. Clear the job-level default -------------------------------------------
    jobs = db.query(JobPosting).filter(JobPosting.interviewer_id == profile.id).all()
    for job in jobs:
        job.interviewer_id = None
        summary["jobs_unassigned"] += 1
        _notify_hr(
            db,
            job,
            company_id,
            title="Job needs a new interviewer",
            message=(
                f"{_full_name(user)} was deactivated and has been unassigned from "
                f"'{job.title}'. Pick a replacement before scheduling more interviews."
            ),
            reference_type="job",
            reference_id=job.id,
        )

    # ---- 2. Rebuild the panel on upcoming interviews -------------------------------
    email = (user.email or "").strip()
    upcoming_q = db.query(Interview).filter(
        Interview.is_scheduled_interview == True,  # noqa: E712
        func.upper(Interview.status).in_(_UPCOMING_STATUSES),
        (Interview.interviewer_email == email)
        | (Interview.panel_members.like(f'%"{email}"%')),
    )
    # Tenant scope is load-bearing here, not decoration. Panel membership is matched on
    # EMAIL, and users are unique on (company_id, email) - so the same address legitimately
    # exists in two companies. Without this filter, one company deactivating a user blanked
    # the other company's interviewer_name/panel_members. NULL company_id is tolerated the
    # way _company_scope does it, for legacy rows and company-less callers.
    if company_id:
        upcoming_q = upcoming_q.filter(
            (Interview.company_id == company_id) | (Interview.company_id.is_(None))
        )
    upcoming = upcoming_q.all()

    for iv in upcoming:
        try:
            panel = json.loads(iv.panel_members) if iv.panel_members else []
        except (ValueError, TypeError):
            panel = []
        panel = _panel_without(panel, email)

        if panel:
            # Promote the next panelist. interviewer_name/email mirror panel[0] by
            # design (models.py:604), so single-interviewer readers keep working.
            lead = panel[0]
            iv.panel_members = json.dumps(panel)
            iv.interviewer_name = lead.get("name") or ""
            iv.interviewer_email = lead.get("email") or ""
            summary["interviews_updated"] += 1
            note = f"{_full_name(user)} was deactivated; {lead.get('name') or 'another panelist'} is now the lead."
        else:
            iv.panel_members = None
            iv.interviewer_name = ""
            iv.interviewer_email = ""
            summary["interviews_updated"] += 1
            summary["interviews_needing_interviewer"] += 1
            note = (
                f"{_full_name(user)} was deactivated and no panelist remains. "
                "Assign someone before the scheduled time."
            )

        _notify_hr(
            db,
            iv.job,
            company_id,
            title="Interview panel changed",
            message=f"{iv.title or 'An interview'} — {note}",
            reference_type="interview",
            reference_id=iv.id,
        )

    return summary


def _full_name(user):
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email


def _notify_hr(db, job, company_id, *, title, message, reference_type, reference_id):
    """Notify the job's HR owner. Silently skips a job with no HR attached."""
    hr_user_id = None
    if job is not None and job.hr is not None:
        hr_user_id = job.hr.user_id
    if not hr_user_id:
        return
    create_notification(
        db,
        user_id=hr_user_id,
        company_id=company_id or (job.company_id if job is not None else None),
        title=title,
        message=message,
        type="interviewer_unassigned",
        reference_type=reference_type,
        reference_id=reference_id,
        commit=False,
    )
