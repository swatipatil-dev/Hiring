"""Job-description versions.

Every resume score records the version of the job it was measured against. When HR edits
the parts of a job that affect scoring, a new version is created and older scores are
shown as "scored against an earlier job description" until HR rescores them. Editing
anything else (the title, the location, the salary) does not create a version: it
cannot change a score.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Optional


def _skills(raw) -> list:
    if raw is None:
        return None
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            items = [s for s in str(raw).split(",")]
    if not isinstance(items, list):
        return []
    return sorted({str(s).strip().lower() for s in items if str(s).strip()})


def jd_scoring_fingerprint(job) -> str:
    """sha256 over exactly what the scorer reads from a job."""
    payload = {
        "description": (job.description or "").strip(),
        "requirements": (job.requirements or "").strip(),
        "required_skills": _skills(job.required_skills) or [],
        "preferred_skills": _skills(getattr(job, "preferred_skills", None)),
        "experience_years": job.experience_years or 0,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def ensure_jd_version(db, job, actor_id: Optional[str] = None):
    """The job's current version, creating the next one if what scoring reads changed."""
    from src.models.models import JobDescriptionVersion

    fingerprint = jd_scoring_fingerprint(job)
    current = None
    if job.current_jd_version_id:
        current = db.query(JobDescriptionVersion).filter(
            JobDescriptionVersion.id == job.current_jd_version_id).first()
    if current and current.fingerprint == fingerprint:
        return current

    last_no = (db.query(JobDescriptionVersion.version_no)
               .filter(JobDescriptionVersion.job_id == job.id)
               .order_by(JobDescriptionVersion.version_no.desc()).first())
    version = JobDescriptionVersion(
        company_id=job.company_id,
        job_id=job.id,
        version_no=(last_no[0] if last_no else 0) + 1,
        title=job.title,
        description=job.description,
        requirements=job.requirements,
        required_skills=job.required_skills,
        preferred_skills=getattr(job, "preferred_skills", None),
        experience_years=job.experience_years,
        fingerprint=fingerprint,
        created_by_id=actor_id,
        created_at=datetime.utcnow(),
    )
    db.add(version)
    db.flush()
    job.current_jd_version_id = version.id
    return version


def staleness(ats_result, job, rules_version: str) -> Optional[str]:
    """Why a stored score may no longer reflect the job, or None if it is current.

    'legacy'       scored before versions were recorded
    'jd_changed'   the job description changed since it was scored
    'rules_updated' the scoring rules changed since it was scored
    """
    if not ats_result or not ats_result.jd_version_id:
        return "legacy"
    if job is not None and job.current_jd_version_id and ats_result.jd_version_id != job.current_jd_version_id:
        return "jd_changed"
    if ats_result.scoring_rules_version and ats_result.scoring_rules_version != rules_version:
        return "rules_updated"
    return None
