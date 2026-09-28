"""
🤝 JOB ACCESS — owner HR + co-HRs

A job has one owning HR (``job_postings.hr_id``) and any number of co-HRs
(``job_hr_collaborators``). Both get the same access; only the owner (or an admin) can
manage the team, transfer the job or delete it.

Every "is this HR on this job?" question goes through here. The list filter reads the
co-HR table through a subquery on purpose: a JOIN would repeat a job once per co-HR,
duplicating it in lists and inflating every ``func.count`` built on top.
"""

from sqlalchemy import or_, select

from src.models.models import HRProfile, JobHRCollaborator, JobPosting


def job_access_filter(hr_profile_id):
    """SQL predicate: jobs this HR owns or is a co-HR on. Never duplicates rows."""
    return or_(
        JobPosting.hr_id == hr_profile_id,
        JobPosting.id.in_(
            select(JobHRCollaborator.job_id).where(JobHRCollaborator.hr_id == hr_profile_id)
        ),
    )


def is_job_owner(job, hr_profile):
    return bool(hr_profile and job.hr_id and str(job.hr_id) == str(hr_profile.id))


def is_collaborator(db, job, hr_profile):
    if not hr_profile:
        return False
    return db.query(JobHRCollaborator.id).filter(
        JobHRCollaborator.job_id == job.id,
        JobHRCollaborator.hr_id == hr_profile.id,
    ).first() is not None


def can_access_job(db, job, hr_profile):
    """Owner or co-HR."""
    return is_job_owner(job, hr_profile) or is_collaborator(db, job, hr_profile)


def _job_hr_profiles(db, job):
    """Owner first, then co-HRs — each HR once."""
    profiles = []
    if job.hr_id:
        owner = db.query(HRProfile).filter(HRProfile.id == job.hr_id).first()
        if owner:
            profiles.append(owner)
    co = (db.query(HRProfile)
          .join(JobHRCollaborator, JobHRCollaborator.hr_id == HRProfile.id)
          .filter(JobHRCollaborator.job_id == job.id)
          .order_by(JobHRCollaborator.created_at)
          .all())
    seen = {p.id for p in profiles}
    profiles.extend(p for p in co if p.id not in seen)
    return profiles


def job_hr_user_ids(db, job):
    """User ids of every HR on the job (owner + co-HRs), for notifications."""
    ids = []
    for p in _job_hr_profiles(db, job):
        if p.user_id and p.user_id not in ids:
            ids.append(p.user_id)
    return ids


def job_hr_emails(db, job):
    """Emails of every ACTIVE HR on the job, for emails such as offer responses."""
    emails = []
    for p in _job_hr_profiles(db, job):
        u = p.user
        if u and u.is_active and u.email and u.email.lower() not in {e.lower() for e in emails}:
            emails.append(u.email)
    return emails


def remove_collaborator_if_owner(db, job):
    """Drop the co-HR row of whoever now owns the job.

    Call after anything that changes ``job.hr_id`` — a co-HR who becomes owner must not
    also stay listed as a co-HR. Does not commit.
    """
    if not job.hr_id:
        return 0
    return db.query(JobHRCollaborator).filter(
        JobHRCollaborator.job_id == job.id,
        JobHRCollaborator.hr_id == job.hr_id,
    ).delete(synchronize_session=False)


def collaborator_summary(job):
    """[{hr_id, name}] from an eager-loaded ``job.collaborators``."""
    out = []
    for c in (job.collaborators or []):
        u = c.hr.user if c.hr else None
        name = (f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email) if u else None
        out.append({"hr_id": str(c.hr_id), "name": name})
    return out


__all__ = [
    "job_access_filter", "is_job_owner", "is_collaborator", "can_access_job",
    "job_hr_user_ids", "job_hr_emails", "remove_collaborator_if_owner",
    "collaborator_summary",
]
