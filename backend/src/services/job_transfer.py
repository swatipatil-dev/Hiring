"""
🔀 JOB TRANSFER

Hands a job from one HR to another. Ownership is ``job_postings.hr_id`` and every HR
list filters on it, but two other tables carry the owning HR as well:

* ``offers.hr_id`` — ``GET /hr/offers`` and the offers dashboard count filter on it, and
  the candidate's accept/decline email is addressed to it. Left alone, the previous HR
  keeps the job's offers and keeps receiving the replies.
* ``interviews.assigned_hr_id`` — written at publish time; moved so the row agrees with
  the job it belongs to.

Everything else (candidates, ATS results, stage history, feedback, candidate requests)
hangs off ``job_id`` and follows the job automatically.

``created_by_id`` is NOT touched: it records who wrote the job, which a transfer does not
change.
"""

import logging
from datetime import datetime

from src.models.models import HRProfile, Interview, JobTransferHistory, Offer
from src.services.job_access import job_hr_user_ids, remove_collaborator_if_owner
from src.services.notification_service import create_notification

logger = logging.getLogger(__name__)


class JobTransferError(ValueError):
    """A transfer that makes no sense, e.g. to the HR who already owns the job."""


def hr_display_name(profile):
    """"First Last" for an HRProfile, or None when there is no profile/user."""
    if not profile or not profile.user:
        return None
    u = profile.user
    return f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email


def transfer_job(db, *, job, to_hr, actor, reason=None, bulk=False, notify=True):
    """Move ``job`` (and its offers and interviews) to ``to_hr``.

    Does NOT commit — the caller owns the transaction, so a bulk transfer lands as one
    unit and a failure part-way leaves nothing half-moved.

    ``notify=False`` suppresses the per-job notifications; the bulk route sends one
    summary instead of flooding the recipient with one bell entry per job.
    """
    if job.hr_id and str(job.hr_id) == str(to_hr.id):
        raise JobTransferError("This job is already owned by that HR.")

    from_hr = db.query(HRProfile).filter(HRProfile.id == job.hr_id).first() if job.hr_id else None
    from_name = hr_display_name(from_hr)
    to_name = hr_display_name(to_hr)
    reason = (reason or "").strip() or None

    job.hr_id = to_hr.id
    job.updated_at = datetime.utcnow()
    # A co-HR who becomes the owner is now the owner only — never both.
    remove_collaborator_if_owner(db, job)

    offers_moved = db.query(Offer).filter(Offer.job_id == job.id).update(
        {Offer.hr_id: to_hr.id}, synchronize_session=False)
    interviews_moved = db.query(Interview).filter(Interview.job_id == job.id).update(
        {Interview.assigned_hr_id: to_hr.id}, synchronize_session=False)

    db.add(JobTransferHistory(
        company_id=job.company_id or getattr(actor, "company_id", None),
        job_id=str(job.id),
        job_title=job.title,
        from_hr_id=from_hr.id if from_hr else None,
        from_hr_name=from_name,
        to_hr_id=to_hr.id,
        to_hr_name=to_name,
        transferred_by_id=actor.id,
        transferred_by_email=actor.email,
        reason=reason,
        offers_moved=offers_moved or 0,
        interviews_moved=interviews_moved or 0,
        bulk=bulk,
    ))

    if notify:
        actor_name = f"{actor.first_name or ''} {actor.last_name or ''}".strip() or actor.email
        suffix = f" Reason: {reason}" if reason else ""
        # Don't tell someone they transferred a job to themselves.
        if to_hr.user_id != actor.id:
            create_notification(
                db, user_id=to_hr.user_id, company_id=job.company_id,
                title="Job transferred to you",
                message=f"{actor_name} transferred '{job.title}' to you.{suffix}",
                type="job_transferred", reference_type="job", reference_id=job.id,
                commit=False,
            )
        if from_hr and from_hr.user_id != actor.id:
            create_notification(
                db, user_id=from_hr.user_id, company_id=job.company_id,
                title="Job transferred away",
                message=f"{actor_name} transferred '{job.title}' to {to_name}.{suffix}",
                type="job_transferred", reference_type="job", reference_id=job.id,
                commit=False,
            )
        # Co-HRs keep their access, but should know who owns the job now.
        told = {to_hr.user_id, actor.id, from_hr.user_id if from_hr else None}
        for uid in job_hr_user_ids(db, job):
            if uid not in told:
                create_notification(
                    db, user_id=uid, company_id=job.company_id,
                    title="Shared job has a new owner",
                    message=f"{actor_name} transferred '{job.title}' to {to_name}. You are still a co-HR on it.",
                    type="job_transferred", reference_type="job", reference_id=job.id,
                    commit=False,
                )

    return {
        "job_id": str(job.id),
        "job_title": job.title,
        "from_hr_id": str(from_hr.id) if from_hr else None,
        "from_hr_name": from_name,
        "to_hr_id": str(to_hr.id),
        "to_hr_name": to_name,
        "offers_moved": offers_moved or 0,
        "interviews_moved": interviews_moved or 0,
    }
