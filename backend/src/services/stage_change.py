"""
🔀 STAGE CHANGE — the one write for "move this candidate to a new stage"

Writes the stage, the immutable history row, and the candidate's in-app notice.
The caller has already authorised the candidate and commits.
"""

from datetime import datetime

from src.models.models import CandidateStageHistory

# Stages that get a richer dedicated notification elsewhere (interview scheduled,
# offer sent) are skipped here to avoid double-notifying.
CANDIDATE_STAGE_MESSAGES = {
    "hr_review": "Your application is now under HR review.",
    "shortlisted": "Good news — you've been shortlisted!",
    "test_scheduled": "A test has been assigned to you. Check your Interviews tab.",
    "test_completed": "Your test has been recorded.",
    "interview_scheduled": "An interview is being arranged for you.",
    "hired": "Congratulations — you've been hired! 🎉",
    "onboarding": "Welcome aboard — onboarding has begun.",
    "rejected": "There's an update on your application.",
    "withdrawn": "Your application has been withdrawn.",
}
STAGE_SKIP_NOTIFY = {"technical_interview", "interview_scheduled", "offer_sent", "applied",
                     "ats_screening", "merged"}


def apply_stage_change(db, candidate, new_stage, changed_by_id=None, notes=None, notify=True,
                       record_unchanged=False):
    """Move ``candidate`` to ``new_stage``. Returns the history row, or None when the
    candidate is already there and ``record_unchanged`` is off (nothing written).
    The candidate is never notified of a no-op."""
    old_stage = candidate.current_stage
    if old_stage == new_stage and not record_unchanged:
        return None
    candidate.current_stage = new_stage
    candidate.updated_at = datetime.utcnow()

    row = CandidateStageHistory(
        company_id=candidate.company_id,
        candidate_id=candidate.id,
        from_stage=old_stage,
        to_stage=new_stage,
        changed_by_id=changed_by_id,
        notes=notes,
    )
    db.add(row)

    if (notify and old_stage != new_stage and new_stage not in STAGE_SKIP_NOTIFY
            and getattr(candidate, "user_id", None)):
        from src.services.notification_service import create_notification
        from src.services.candidate_status import candidate_label
        msg = CANDIDATE_STAGE_MESSAGES.get(
            new_stage, f"Your application status changed to {candidate_label(new_stage)}."
        )
        create_notification(
            db,
            user_id=candidate.user_id,
            company_id=candidate.company_id,
            title="Application update",
            message=msg,
            type="stage_change",
            reference_type="application",
            reference_id=candidate.id,
            commit=False,
        )
    return row
