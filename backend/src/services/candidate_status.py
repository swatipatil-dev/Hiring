"""
📣 CANDIDATE STATUS — what a candidate is told, and withdrawing an application

``current_stage`` is the company's internal pipeline vocabulary (ats_screening,
hr_review, test_submitted…). Candidates see a small, stable set of plain-language
statuses instead, so an internal rename or a custom stage never leaks as jargon.
"""

from datetime import datetime

from fastapi import HTTPException

from src.models.models import Interview, Notification, Offer

# Stage → (status shown to the candidate, one-line explanation).
_CANDIDATE_STATUS = {
    "registered":          ("Received", "We have your application."),
    "applied":             ("Received", "We have your application."),
    "ats_screening":       ("Under review", "Your application is being reviewed."),
    "screening":           ("Under review", "Your application is being reviewed."),
    "hr_review":           ("Under review", "A recruiter is reviewing your application."),
    "shortlisted":         ("Shortlisted", "You have been shortlisted for the next step."),
    "test_scheduled":      ("Assessment", "An assessment is waiting for you in the Interviews tab."),
    "assessment":          ("Assessment", "An assessment is waiting for you in the Interviews tab."),
    "test_submitted":      ("Assessment completed", "Your assessment has been received."),
    "test_completed":      ("Assessment completed", "Your assessment has been received."),
    "interview":           ("Interviewing", "You are in the interview stage."),
    "interview_scheduled": ("Interviewing", "An interview has been arranged — see the Interviews tab."),
    "technical_interview": ("Interviewing", "An interview has been arranged — see the Interviews tab."),
    "interview_completed": ("Interview completed", "Your interview is complete; the team is deciding next steps."),
    "offer":               ("Offer", "An offer is being prepared for you."),
    "offer_sent":          ("Offer", "You have an offer to review."),
    "hired":               ("Hired", "Congratulations — you have been hired."),
    "onboarding":          ("Hired", "Congratulations — onboarding has begun."),
    "rejected":            ("Not selected", "The team has decided not to move forward with this application."),
    "withdrawn":           ("Withdrawn", "You withdrew this application."),
    "merged":              ("Combined", "This application was combined with your other application for the same job."),
}

# Stages a candidate can no longer withdraw from: the outcome is already decided,
# or the row is not a live application.
WITHDRAW_BLOCKED = {"hired", "onboarding", "rejected", "withdrawn", "merged"}

# Stages no recruiter action applies to any more.
TERMINAL_STAGES = {"hired", "onboarding", "rejected", "withdrawn", "merged"}


def candidate_label(stage):
    if stage in _CANDIDATE_STATUS:
        return _CANDIDATE_STATUS[stage][0]
    return (stage or "in progress").replace("_", " ").capitalize()


def candidate_status(candidate):
    """The candidate-facing view of an application's state."""
    stage = candidate.current_stage or "ats_screening"
    label, message = _CANDIDATE_STATUS.get(stage, ("In progress", "Your application is in progress."))
    return {
        "status_label": label,
        "status_message": message,
        "can_withdraw": can_withdraw(candidate),
        "withdrawn_at": candidate.withdrawn_at.isoformat() if candidate.withdrawn_at else None,
    }


def can_withdraw(candidate):
    return (candidate.current_stage or "") not in WITHDRAW_BLOCKED and not candidate.merged_into_id


def withdraw_application(db, candidate, user, reason=None):
    """Withdraw ``candidate`` (already confirmed to belong to ``user``).

    Upcoming interviews are cancelled and an outstanding offer is marked withdrawn,
    so nobody turns up to a meeting the candidate has pulled out of. Every HR on the
    job is told in-app. The caller commits.
    """
    from src.services.stage_change import apply_stage_change

    if not can_withdraw(candidate):
        raise HTTPException(409, "This application can no longer be withdrawn.")

    reason = (reason or "").strip()[:2000] or None
    now = datetime.utcnow()
    candidate.withdrawn_at = now
    candidate.withdrawal_reason = reason
    apply_stage_change(db, candidate, "withdrawn", changed_by_id=user.id,
                       notes="Withdrawn by the candidate" + (f": {reason}" if reason else ""),
                       notify=False)

    cancelled = []
    for iv in db.query(Interview).filter(
        Interview.candidate_id == candidate.id,
        Interview.company_id == candidate.company_id,
    ).all():
        status = (iv.status or "").upper()
        if status in ("COMPLETED", "CANCELLED"):
            continue
        if iv.scheduled_datetime and iv.scheduled_datetime < now and status != "SCHEDULED":
            continue
        iv.status = "CANCELLED"
        cancelled.append(iv.id)

    withdrawn_offers = []
    for offer in db.query(Offer).filter(
        Offer.candidate_id == candidate.id,
        Offer.company_id == candidate.company_id,
    ).all():
        if (offer.status or "").lower() in ("draft", "sent", "pending"):
            offer.status = "withdrawn"
            withdrawn_offers.append(offer.id)

    if candidate.job is not None:
        from src.services.job_access import job_hr_user_ids
        name = f"{candidate.first_name} {candidate.last_name}".strip()
        for uid in job_hr_user_ids(db, candidate.job):
            db.add(Notification(
                user_id=uid, company_id=candidate.company_id,
                title="Application withdrawn",
                message=f"{name} withdrew their application for {candidate.job.title}."
                        + (f" Reason: {reason}" if reason else ""),
                type="application_withdrawn",
                reference_type="candidate", reference_id=candidate.id,
            ))

    return {"cancelled_interviews": cancelled, "withdrawn_offers": withdrawn_offers}
