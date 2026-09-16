"""Import registered portal candidates into the HR Resume Matcher pool.

The Resume Matcher only ever showed resumes HR uploaded through it, so everyone who
signed up on the candidate portal was invisible there. This imports them as real
`ResumePoolEntry` rows, which means they inherit the pool's existing search, filters,
ranking and "Move to ATS" action with no changes to any of it.

Three things drive the design:

* **The unit is a User, not a Candidate.** ``src.auth.create_user`` writes a
  ``Candidate(job_id=NULL, current_stage="registered")`` at signup, and each
  application writes *another* Candidate row. Enumerating candidates would import
  an applicant once per application.

* **The structured profile beats the resume.** ``candidate_routes`` scores an
  applicant with ``profile_text or resume_text``; the import uses the same
  precedence so an imported person scores the same as one who applied.

* **Someone can be registered with nothing at all.** No resume, no profile. They
  are still imported — as a scoreless row — because HR asked to see everyone.
  ``content_hash`` therefore cannot be the dedupe key (there is no text to hash);
  ``source_user_id`` is.

The imported row points at a resume file the CANDIDATE owns. It is never copied,
which is why deleting a pool row only deletes files under ``RESUME_POOL_DIR``.
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.models import (
    Candidate,
    CandidateProfileExt,
    ResumePoolEntry,
    User,
)

logger = logging.getLogger(__name__)

# Hard ceiling on one import call. Scoring is N people x M jobs of sentence-transformer
# work, so a big batch is a slow request, not a cheap one. The caller repeats.
IMPORT_BATCH_MAX = 50
IMPORT_BATCH_DEFAULT = 25

# Scoring costs one scorer call per person PER ACTIVE JOB. At sixteen open roles that is
# ~5-16s for a single person, so a batch of ten would be a three-minute request holding
# one of only two gunicorn workers. Small batches, and the caller loops.
SCORE_BATCH_DEFAULT = 3
SCORE_BATCH_MAX = 10


def _best_education(profile: Optional[CandidateProfileExt]):
    """The education row that should fill college / year of passing / degree.

    Most recent finished qualification wins; still-studying and undated rows sort
    last so a completed B.Tech beats an in-progress certificate.
    """
    if profile is None:
        return None
    educations = [e for e in (profile.educations or []) if e.institution_name]
    if not educations:
        return None
    return sorted(
        educations,
        key=lambda e: (
            0 if e.currently_pursuing else 1,   # finished first
            e.end_year or 0,
        ),
        reverse=True,
    )[0]


def _current_role(profile: Optional[CandidateProfileExt]) -> Optional[str]:
    """Their headline, else the job title they are currently working in, else the
    most recent one. ``start_date`` is a "YYYY-MM" string, which sorts correctly."""
    if profile is None:
        return None
    if profile.headline:
        return profile.headline[:255]
    experiences = list(profile.work_experiences or [])
    if not experiences:
        return None
    current = next((w for w in experiences if w.currently_working and w.job_title), None)
    if current:
        return current.job_title[:255]
    dated = sorted(experiences, key=lambda w: (w.start_date or ""), reverse=True)
    return dated[0].job_title[:255] if dated and dated[0].job_title else None


def _resolve_scoring_text(profile, candidates) -> tuple:
    """Pick the text to score, mirroring ``candidate_routes``' precedence.

    Returns ``(text, came_from_raw_resume)``. The flag matters because the resume
    parser must never run on ``build_profile_text`` output — that document opens
    with the literal heading "Professional Summary", which the name/college
    heuristics would happily return as the candidate's college.
    """
    from routes.candidate_profile_routes import build_profile_text
    from src.utils import extract_text_from_file

    profile_text = build_profile_text(profile) if profile else ""
    if profile_text and profile_text.strip():
        return profile_text, False

    stored = next((c.resume_text for c in candidates if c.resume_text and c.resume_text.strip()), None)
    if stored:
        return stored, True

    path = (profile.resume_path if profile else None) or next(
        (c.resume_path for c in candidates if c.resume_path), None
    )
    if path:
        try:
            text, _ = extract_text_from_file(path)
            if text and text.strip():
                return text, True
        except Exception as e:
            logger.warning(f"Resume pool import: could not read {path}: {e}")

    return "", False


def _apply_profile_fields(entry: ResumePoolEntry, user: User, profile, candidates,
                          scoring_text: str, text_is_raw_resume: bool,
                          extracted_skills, extracted_experience) -> None:
    """Fill the pool's flat columns, structured profile data first.

    The resume parser is a fallback for gaps only — when someone has filled in their
    education properly there is no reason to guess at it from prose.
    """
    from routes.hr_routes import _clamp_experience
    from src.services.resume_profile_parser import parse_resume_profile

    source_candidate = candidates[0] if candidates else None

    entry.full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email
    entry.email = user.email
    entry.phone = (
        user.phone
        or (profile.alternate_phone if profile else None)
        or (source_candidate.phone if source_candidate else None)
    )

    education = _best_education(profile)
    if education:
        entry.college = education.institution_name
        entry.year_of_passing = education.end_year
        entry.highest_degree = education.degree or education.education_level
    else:
        entry.college = None
        entry.year_of_passing = None
        entry.highest_degree = None

    entry.job_role = _current_role(profile)

    skills = [s.skill_name for s in (profile.skills or [])] if profile else []
    if not skills:
        skills = list(extracted_skills or [])
    entry.extracted_skills = json.dumps(skills) if skills else None

    experience = profile.total_experience_years if profile else None
    if experience is None:
        experience = extracted_experience
    if experience is None and source_candidate is not None:
        experience = source_candidate.experience_years
    entry.experience_years = _clamp_experience(experience)

    # Only consult the parser when we are looking at a real resume AND the structured
    # data left a hole. Never on profile text — see _resolve_scoring_text.
    needs_parse = text_is_raw_resume and not all(
        [entry.college, entry.year_of_passing, entry.highest_degree, entry.job_role]
    )
    if needs_parse and scoring_text:
        try:
            parsed = parse_resume_profile(scoring_text)
        except Exception as e:
            logger.warning(f"Resume pool import: parse failed for {user.email}: {e}")
            parsed = {}
        entry.college = entry.college or parsed.get("college")
        entry.year_of_passing = entry.year_of_passing or parsed.get("year_of_passing")
        entry.highest_degree = entry.highest_degree or parsed.get("highest_degree")
        entry.job_role = entry.job_role or parsed.get("job_role")
        entry.phone = entry.phone or parsed.get("phone")
        _llm_fill_gaps(entry, scoring_text, label=user.email)


def _llm_fill_gaps(entry, resume_text: Optional[str], label: str = "") -> None:
    """Fill profile fields still blank after parsing, using Gemini, and record it.

    A no-op when the feature is off, credentials are absent, or nothing is missing —
    so the common case costs nothing. Synchronous because this module is: it runs from
    scripts and background jobs, not a request coroutine.

    Never raises. An import that has already done real work must not fail because the
    model was unreachable.
    """
    if not resume_text:
        return
    try:
        from src.services.llm_resume_extractor import (
            PROFILE_FIELDS, missing_fields, extract_gaps_sync, profile_source_map,
        )
        current = {f: getattr(entry, f, None) for f in PROFILE_FIELDS}
        if not missing_fields(current):
            return
        filled = extract_gaps_sync(resume_text, current)
        if not filled:
            return
        # Drop attributions for fields that are now empty; the parser never overwrites a
        # populated field, so a surviving "llm" label still describes its value.
        src_map = {k: v for k, v in profile_source_map(entry).items()
                   if getattr(entry, k, None)}
        for field, value in filled.items():
            setattr(entry, field, value)
            src_map[field] = "llm"
        entry.profile_source = json.dumps(src_map)
    except Exception as e:
        logger.warning(f"Resume pool import: LLM gap-fill failed for {label}: {e}")


def import_registered_users(
    db: Session,
    *,
    company_id: Optional[str],
    actor_user_id: Optional[str],
    limit: int = IMPORT_BATCH_DEFAULT,
    refresh_existing: bool = False,
    only_user_id: Optional[str] = None,
    prepared_jobs: Optional[List[Dict[str, Any]]] = None,
    score: bool = True,
) -> Dict[str, Any]:
    """Import registered portal users into this company's resume pool.

    Idempotent: re-running imports only people who aren't in the pool yet, unless
    ``refresh_existing`` is set. Returns per-run counters plus ``remaining``, so the
    caller can drive a "import the next batch" affordance.
    """
    # A NULL company_id would write rows the tenant filter hides forever
    # (docs/DASHBOARD_DATA_PITFALLS.md).
    if not company_id:
        raise ValueError("company_id is required to import registered candidates")

    from routes.hr_routes import _prepare_active_jobs, _score_against_jobs

    if prepared_jobs is None:
        prepared_jobs = _prepare_active_jobs(db, company_id)

    query = db.query(User).filter(
        User.company_id == company_id,
        User.role == "candidate",
    )
    if only_user_id:
        query = query.filter(User.id == only_user_id)
    elif not refresh_existing:
        # NOT EXISTS rather than a Python-side id set, so a tenant with thousands of
        # registered users doesn't pull them all into memory to diff.
        query = query.filter(~db.query(ResumePoolEntry.id).filter(
            ResumePoolEntry.company_id == company_id,
            ResumePoolEntry.source_user_id == User.id,
        ).exists())

    remaining_before = query.count()
    batch = min(max(int(limit or 0), 1), IMPORT_BATCH_MAX)
    users = query.order_by(User.created_at.asc(), User.id.asc()).limit(batch).all()

    counts = {"imported": 0, "refreshed": 0, "skipped": 0, "failed": 0,
              "scored": 0, "scoreless": 0}

    for user in users:
        try:
            profile = db.query(CandidateProfileExt).filter(
                CandidateProfileExt.user_id == user.id,
                # Explicit company predicate also rescues legacy profile rows whose
                # company_id migration f6a7b8c9d0e1 missed. Safe: user_id is unique
                # and the user is already company-scoped.
                or_(CandidateProfileExt.company_id == company_id,
                    CandidateProfileExt.company_id.is_(None)),
            ).first()

            candidates = db.query(Candidate).filter(
                Candidate.user_id == user.id,
                Candidate.company_id == company_id,
            ).order_by(Candidate.created_at.desc()).all()

            # Prefer a real application over the placeholder row signup creates.
            applied = [c for c in candidates if c.job_id]
            source_candidate = applied[0] if applied else (candidates[0] if candidates else None)

            scoring_text, text_is_raw_resume = _resolve_scoring_text(profile, candidates)
            resume_path = (profile.resume_path if profile else None) or next(
                (c.resume_path for c in candidates if c.resume_path), None
            )

            ranked, extracted_skills, extracted_experience = [], [], None
            if score and scoring_text and prepared_jobs:
                ranked, extracted_skills, extracted_experience = _score_against_jobs(
                    scoring_text, prepared_jobs, label=user.email
                )

            entry = db.query(ResumePoolEntry).filter(
                ResumePoolEntry.company_id == company_id,
                ResumePoolEntry.source_user_id == user.id,
            ).first()
            is_new = entry is None

            if entry is None:
                # HR may already have uploaded this person's CV by hand. Adopt that row
                # rather than showing the same human twice — their uploaded file is a
                # real artifact the pool owns, so it is kept.
                entry = db.query(ResumePoolEntry).filter(
                    ResumePoolEntry.company_id == company_id,
                    ResumePoolEntry.source_user_id.is_(None),
                    ResumePoolEntry.email.ilike(user.email),
                ).first()
                if entry is not None:
                    resume_path = entry.resume_path or resume_path
                    is_new = False

            if entry is None:
                entry = ResumePoolEntry(
                    company_id=company_id,          # never rely on the before_flush stamp
                    uploaded_by_id=actor_user_id,
                    uploaded_at=user.created_at or datetime.utcnow(),
                )
                db.add(entry)

            entry.source = "registered"
            entry.source_user_id = user.id
            entry.source_candidate_id = source_candidate.id if source_candidate else None
            entry.imported_at = datetime.utcnow()
            entry.resume_path = resume_path
            entry.resume_text = scoring_text or None
            entry.content_hash = (
                hashlib.sha256(scoring_text.encode("utf-8", "ignore")).hexdigest()
                if scoring_text else None
            )
            entry.filename = (
                resume_path.rsplit("/", 1)[-1] if resume_path
                else (f"{user.first_name} {user.last_name}".strip() or user.email)
            )

            _apply_profile_fields(entry, user, profile, candidates, scoring_text,
                                  text_is_raw_resume, extracted_skills, extracted_experience)

            # Only overwrite a stored ranking when we actually produced one, so a
            # re-import while every job is closed doesn't blank a score HR is reading.
            if ranked:
                entry.best_job_id = ranked[0]["job_id"]
                entry.best_score = ranked[0]["ats_score"]
                entry.match_results = json.dumps(ranked)

            # status / moved_candidate_id are deliberately untouched: an imported row
            # starts un-promoted so HR can still move it onto a different job.

            db.commit()
            counts["imported" if is_new else "refreshed"] += 1
            counts["scored" if (ranked or entry.best_score is not None) else "scoreless"] += 1

        except IntegrityError:
            # uq_resume_pool_company_source_user — a concurrent import won the race.
            db.rollback()
            counts["skipped"] += 1
        except Exception as e:
            db.rollback()
            logger.error(f"Resume pool import failed for user {user.id}: {e}")
            counts["failed"] += 1

    counts["remaining"] = max(0, remaining_before - len(users))
    counts["active_job_count"] = len(prepared_jobs)
    return counts


def _merge_ranked(existing: List[Dict[str, Any]], fresh: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fold one job's score into an existing ranking, best-first.

    Overwriting `match_results` outright would throw away a multi-job ranking a prior
    import produced, so the pool would forget every other role this person suits the
    moment they applied to one.
    """
    merged = [m for m in (existing or [])
              if isinstance(m, dict) and m.get("job_id") != fresh["job_id"]]
    merged.append(fresh)
    merged.sort(key=lambda m: m.get("ats_score") if m.get("ats_score") is not None else -1,
                reverse=True)
    return merged


def _ranked_dict_from_ats(job, department: Optional[str], ats_result) -> Dict[str, Any]:
    """Shape an EnhancedATSScoreResult like `_score_against_jobs` does, so a pool row
    written at apply time is indistinguishable from one written by the matcher."""
    def _num(value):
        return round(float(value), 1) if value is not None else None

    return {
        "job_id": str(job.id),
        "job_title": job.title,
        "department": department,
        "ats_score": round(float(ats_result.ats_score or 0), 1),
        "rating": getattr(ats_result, "rating", None) or None,
        "matched_skills": getattr(ats_result, "matched_skills", None) or [],
        "missing_skills": getattr(ats_result, "missing_skills", None) or [],
        "skills_match_score": _num(getattr(ats_result, "skills_match_score", None)),
        "semantic_score": _num(getattr(ats_result, "semantic_score", None)),
        "experience_score": _num(getattr(ats_result, "experience_score", None)),
        "keyword_score": _num(getattr(ats_result, "keyword_score", None)),
        "education_score": _num(getattr(ats_result, "education_score", None)),
    }


def sync_pool_on_apply(db: Session, *, candidate, job, ats_result,
                       profile=None, resume_text: Optional[str] = None,
                       actor_user_id: Optional[str] = None) -> None:
    """Fold a fresh application into the talent pool. Never raises.

    Called right after an application commits. The apply flow has already scored this
    resume against this job, and an EnhancedATSScoreResult carries every field the pool
    stores — so this costs one SELECT plus one INSERT/UPDATE and re-runs no scoring.

    A pool write must never be able to fail an application, so every exception is
    swallowed. The person can always be picked up later by the import.
    """
    try:
        company_id = job.company_id or candidate.company_id
        if not company_id:
            return

        entry = None
        if candidate.user_id:
            entry = db.query(ResumePoolEntry).filter(
                ResumePoolEntry.company_id == company_id,
                ResumePoolEntry.source_user_id == candidate.user_id,
            ).first()
        if entry is None and candidate.email:
            entry = db.query(ResumePoolEntry).filter(
                ResumePoolEntry.company_id == company_id,
                ResumePoolEntry.source_user_id.is_(None),
                ResumePoolEntry.email == candidate.email,
            ).first()

        if entry is None:
            entry = ResumePoolEntry(
                company_id=company_id,
                uploaded_by_id=actor_user_id,
                uploaded_at=datetime.utcnow(),
                source="registered" if candidate.user_id else "applied",
            )
            db.add(entry)

        if candidate.user_id and not entry.source_user_id:
            entry.source_user_id = candidate.user_id
        entry.source_candidate_id = candidate.id
        entry.imported_at = datetime.utcnow()
        entry.full_name = (f"{candidate.first_name or ''} {candidate.last_name or ''}".strip()
                           or entry.full_name or candidate.email)
        entry.email = candidate.email
        entry.phone = candidate.phone or entry.phone
        entry.resume_path = candidate.resume_path or entry.resume_path
        text = resume_text or candidate.resume_text
        if text:
            entry.resume_text = text
            entry.content_hash = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
        if candidate.resume_path and not entry.filename:
            entry.filename = candidate.resume_path.rsplit("/", 1)[-1]
        entry.filename = entry.filename or entry.full_name or candidate.email

        if profile is not None:
            education = _best_education(profile)
            if education:
                entry.college = education.institution_name
                entry.year_of_passing = education.end_year
                entry.highest_degree = education.degree or education.education_level
            entry.job_role = _current_role(profile) or entry.job_role
            skills = [s.skill_name for s in (profile.skills or [])]
            if skills:
                entry.extracted_skills = json.dumps(skills)
            if profile.total_experience_years is not None:
                entry.experience_years = _clamp_experience_local(profile.total_experience_years)

        if not entry.extracted_skills and candidate.extracted_skills:
            entry.extracted_skills = candidate.extracted_skills
        if entry.experience_years is None and candidate.experience_years is not None:
            entry.experience_years = _clamp_experience_local(candidate.experience_years)

        department = job.department.name if getattr(job, "department", None) else None
        fresh = _ranked_dict_from_ats(job, department, ats_result)
        try:
            existing = json.loads(entry.match_results) if entry.match_results else []
        except (json.JSONDecodeError, TypeError, ValueError):
            existing = []
        merged = _merge_ranked(existing, fresh)
        entry.match_results = json.dumps(merged)
        entry.best_job_id = merged[0]["job_id"]
        entry.best_score = merged[0]["ats_score"]

        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Talent pool sync skipped for candidate {getattr(candidate, 'id', '?')}: {e}")


def _interview_snapshots(db: Session, cand_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Who interviewed these candidates, and what they concluded.

    A hard job delete destroys the Interview and InterviewFeedback rows, and those are
    the only record of which colleague conducted an interview and what they recommended.
    Snapshotting it here — before anything is deleted — is what keeps a person's
    completed work attributed to them after the job is gone.

    Keyed by candidate_id. Only interviews that actually happened are kept; a scheduled
    interview that never took place has no outcome worth preserving.
    """
    from src.models.models import Interview, InterviewFeedback

    if not cand_ids:
        return {}

    rows = (
        db.query(Interview, InterviewFeedback)
        .outerjoin(InterviewFeedback, InterviewFeedback.interview_id == Interview.id)
        .filter(
            Interview.candidate_id.in_(cand_ids),
            # UPPER() because both casings exist in older rows.
            func.upper(Interview.status) == "COMPLETED",
        )
        .all()
    )

    out: Dict[str, List[Dict[str, Any]]] = {}
    for iv, fb in rows:
        try:
            panel = json.loads(iv.panel_members) if iv.panel_members else []
        except (json.JSONDecodeError, TypeError, ValueError):
            panel = []
        panel_names = [p.get("name") for p in panel if isinstance(p, dict) and p.get("name")]
        if not panel_names and iv.interviewer_name:
            panel_names = [iv.interviewer_name]

        out.setdefault(iv.candidate_id, []).append({
            "interview_id": iv.id,
            "title": iv.title,
            "interview_type": iv.interview_type,
            "held_at": iv.scheduled_datetime.isoformat() if iv.scheduled_datetime else None,
            "is_scheduled_interview": bool(iv.is_scheduled_interview),
            "interviewers": panel_names,
            "interviewer_email": iv.interviewer_email or None,
            "test_score": float(iv.test_score) if iv.test_score is not None else None,
            "overall_score": float(fb.overall_score) if fb is not None and fb.overall_score is not None else None,
            "hire_recommendation": fb.hire_recommendation if fb is not None else None,
            "feedback_notes": fb.additional_notes if fb is not None else None,
        })
    return out


def return_candidates_to_pool(db: Session, *, job, candidates,
                              actor_user_id: Optional[str] = None,
                              archive_applications: bool = False) -> int:
    """Put a job's candidates back into the talent pool, so closing or hard-deleting the
    job does not strand them. Returns the number of entries created or updated.

    **This one raises.** `sync_pool_on_apply` swallows everything because a pool write
    must never be able to fail a candidate's application. Here the pool write *is* the
    point: on the hard-delete path a failure has to roll the transaction back and leave
    the job intact, rather than destroy candidates it failed to preserve. The close path
    catches it at the call site, where a pool problem must not fail the close.

    Does not commit — it runs inside the caller's transaction, which on the delete path
    is the same one that removes the candidates.

    `archive_applications` snapshots each application onto the entry. Only the hard-delete
    path needs it, because the Candidate row and the job title are both about to be
    destroyed; on close both still exist and the live history already names the job.
    """
    from src.models.models import ATSResult

    company_id = job.company_id
    if not company_id:
        return 0

    scores: Dict[str, Any] = {}
    interviews: Dict[str, List[Dict[str, Any]]] = {}
    if archive_applications:
        cand_ids = [c.id for c in candidates]
        if cand_ids:
            scores = {r.candidate_id: r.ats_score for r in
                      db.query(ATSResult.candidate_id, ATSResult.ats_score)
                        .filter(ATSResult.candidate_id.in_(cand_ids)).all()}
            interviews = _interview_snapshots(db, cand_ids)

    touched = 0
    for candidate in candidates:
        # Find the person before creating anything. This lookup is the ONLY thing
        # preventing a duplicate: a candidate promoted via Move to ATS carries
        # user_id=None and leaves both source_* columns NULL on its pool row, so a blind
        # insert violates neither unique constraint and silently splits one human in two.
        # Order mirrors _person_key: the account when there is one, then the explicit
        # links, then the email of an account-less row.
        entry = None
        if candidate.user_id:
            entry = db.query(ResumePoolEntry).filter(
                ResumePoolEntry.company_id == company_id,
                ResumePoolEntry.source_user_id == candidate.user_id,
            ).first()
        if entry is None:
            entry = db.query(ResumePoolEntry).filter(
                ResumePoolEntry.company_id == company_id,
                or_(ResumePoolEntry.moved_candidate_id == candidate.id,
                    ResumePoolEntry.source_candidate_id == candidate.id),
            ).first()
        if entry is None and candidate.email:
            entry = db.query(ResumePoolEntry).filter(
                ResumePoolEntry.company_id == company_id,
                ResumePoolEntry.source_user_id.is_(None),
                ResumePoolEntry.email == candidate.email,
            ).first()

        if entry is None:
            entry = ResumePoolEntry(
                company_id=company_id,
                uploaded_by_id=actor_user_id,
                uploaded_at=candidate.created_at or datetime.utcnow(),
                source="registered" if candidate.user_id else "applied",
            )
            db.add(entry)

        # Gap-fill only, same as sync_pool_on_apply: never overwrite a value HR curated
        # by hand with one scraped off a Candidate row.
        if candidate.user_id and not entry.source_user_id:
            entry.source_user_id = candidate.user_id
        entry.source_candidate_id = candidate.id
        entry.imported_at = datetime.utcnow()
        entry.full_name = (f"{candidate.first_name or ''} {candidate.last_name or ''}".strip()
                           or entry.full_name or candidate.email)
        entry.email = entry.email or candidate.email
        entry.phone = entry.phone or candidate.phone
        entry.resume_path = entry.resume_path or candidate.resume_path
        if not entry.resume_text and candidate.resume_text:
            entry.resume_text = candidate.resume_text
            entry.content_hash = hashlib.sha256(
                candidate.resume_text.encode("utf-8", "ignore")).hexdigest()
        if not entry.filename:
            entry.filename = (candidate.resume_path.rsplit("/", 1)[-1]
                              if candidate.resume_path
                              else entry.full_name or candidate.email)
        if not entry.extracted_skills and candidate.extracted_skills:
            entry.extracted_skills = candidate.extracted_skills
        if entry.experience_years is None and candidate.experience_years is not None:
            entry.experience_years = _clamp_experience_local(candidate.experience_years)

        if archive_applications:
            try:
                archived = json.loads(entry.archived_applications) if entry.archived_applications else []
            except (json.JSONDecodeError, TypeError, ValueError):
                archived = []
            if not isinstance(archived, list):
                archived = []
            # Keyed on candidate_id, so a retried delete does not list the same
            # application twice.
            archived = [a for a in archived
                        if isinstance(a, dict) and a.get("candidate_id") != candidate.id]
            score = scores.get(candidate.id)
            if score is None:
                score = candidate.ats_score
            archived.append({
                "candidate_id": candidate.id,
                "job_id": str(job.id),
                "job_title": job.title,
                "stage": candidate.current_stage or "applied",
                "applied_at": candidate.created_at.isoformat() if candidate.created_at else None,
                "ats_score": float(score) if score is not None else None,
                # Who conducted their interviews and what was concluded. The Interview
                # and InterviewFeedback rows are about to be destroyed with the job;
                # this is the only surviving copy.
                "interviews": interviews.get(candidate.id, []),
            })
            entry.archived_applications = json.dumps(archived)

        touched += 1

    db.flush()
    return touched


def _uncovered_candidate_query(db: Session, company_id: str):
    """Candidates whose person has no pool row yet.

    Two NOT EXISTS: by user account, then by email. `NULL = x` is never true, so a
    candidate with `user_id IS NULL` automatically fails the first check and falls
    through to the email one — no special-casing needed.
    """
    covered_by_user = db.query(ResumePoolEntry.id).filter(
        ResumePoolEntry.company_id == company_id,
        ResumePoolEntry.source_user_id.isnot(None),
        ResumePoolEntry.source_user_id == Candidate.user_id,
    ).exists()

    covered_by_email = db.query(ResumePoolEntry.id).filter(
        ResumePoolEntry.company_id == company_id,
        ResumePoolEntry.email.isnot(None),
        # utf8mb4_unicode_ci already compares case-insensitively; LOWER() would
        # discard ix_candidates_email.
        ResumePoolEntry.email == Candidate.email,
    ).exists()

    return db.query(Candidate).filter(
        Candidate.company_id == company_id,
        ~covered_by_user,
        ~covered_by_email,
    )


def import_orphan_candidates(
    db: Session,
    *,
    company_id: Optional[str],
    actor_user_id: Optional[str],
    limit: int = IMPORT_BATCH_DEFAULT,
    prepared_jobs: Optional[List[Dict[str, Any]]] = None,
    score: bool = False,
) -> Dict[str, Any]:
    """Import candidates who have no portal user account into the pool.

    These are people the registered-user pass cannot see: candidates HR created by hand
    through Move to ATS whose pool row was later deleted, candidates whose account was
    removed, and rows whose company differs from their user's. Without this pass the
    talent pool quietly omits them even though they are in the pipeline.

    Enumerates distinct PEOPLE, not candidate rows — someone who applied three times is
    one person. Defaults to `score=False` so the import returns quickly; HR ranks them
    afterwards with Re-match, or a scored batch import.
    """
    if not company_id:
        raise ValueError("company_id is required to import candidates")

    from routes.hr_routes import _prepare_active_jobs, _score_against_jobs

    if prepared_jobs is None:
        prepared_jobs = _prepare_active_jobs(db, company_id)

    base = _uncovered_candidate_query(db, company_id)
    person_key = func.coalesce(Candidate.user_id, func.lower(Candidate.email))

    remaining_before = base.with_entities(person_key).distinct().count()
    batch = min(max(int(limit or 0), 1), IMPORT_BATCH_MAX)
    people = (base
              .with_entities(person_key.label("pkey"),
                             func.min(Candidate.id).label("seed_id"),
                             func.min(Candidate.created_at).label("first_seen"))
              .group_by(person_key)
              .order_by(func.max(Candidate.created_at).desc())
              .limit(batch).all())

    counts = {"imported": 0, "refreshed": 0, "skipped": 0, "failed": 0,
              "scored": 0, "scoreless": 0}

    for person in people:
        try:
            seed = db.query(Candidate).filter(Candidate.id == person.seed_id).first()
            if seed is None:
                counts["skipped"] += 1
                continue

            # Every row belonging to this same human.
            if seed.user_id:
                rows = db.query(Candidate).filter(
                    Candidate.company_id == company_id,
                    Candidate.user_id == seed.user_id,
                ).order_by(Candidate.created_at.desc()).all()
            else:
                rows = db.query(Candidate).filter(
                    Candidate.company_id == company_id,
                    Candidate.user_id.is_(None),
                    Candidate.email == seed.email,
                ).order_by(Candidate.created_at.desc()).all()
            rows = rows or [seed]

            applied = [c for c in rows if c.job_id]
            source_candidate = applied[0] if applied else rows[0]

            resume_text = next(
                (c.resume_text for c in rows if c.resume_text and c.resume_text.strip()), None)
            resume_path = next((c.resume_path for c in rows if c.resume_path), None)
            if not resume_text and resume_path:
                try:
                    from src.utils import extract_text_from_file
                    text, _ = extract_text_from_file(resume_path)
                    resume_text = text if text and text.strip() else None
                except Exception as e:
                    logger.warning(f"Resume pool import: could not read {resume_path}: {e}")

            ranked, extracted_skills, extracted_experience = [], [], None
            if score and resume_text and prepared_jobs:
                ranked, extracted_skills, extracted_experience = _score_against_jobs(
                    resume_text, prepared_jobs, label=seed.email or seed.id)

            entry = db.query(ResumePoolEntry).filter(
                ResumePoolEntry.company_id == company_id,
                ResumePoolEntry.source_candidate_id.in_([c.id for c in rows]),
            ).first()
            is_new = entry is None
            if entry is None:
                entry = ResumePoolEntry(
                    company_id=company_id,
                    uploaded_by_id=actor_user_id,
                    # "How long we have known them", so the date filters read sensibly.
                    uploaded_at=person.first_seen or datetime.utcnow(),
                )
                db.add(entry)

            entry.source = "applied"
            entry.source_user_id = seed.user_id
            entry.source_candidate_id = source_candidate.id
            entry.imported_at = datetime.utcnow()
            entry.full_name = f"{seed.first_name or ''} {seed.last_name or ''}".strip() or seed.email
            entry.email = seed.email
            entry.phone = next((c.phone for c in rows if c.phone), None)
            entry.resume_path = resume_path
            entry.resume_text = resume_text
            entry.content_hash = (
                hashlib.sha256(resume_text.encode("utf-8", "ignore")).hexdigest()
                if resume_text else None
            )
            entry.filename = (
                resume_path.rsplit("/", 1)[-1] if resume_path else (entry.full_name or seed.email)
            )

            skills = _json_list_or_none(next((c.extracted_skills for c in rows if c.extracted_skills), None))
            if not skills:
                skills = list(extracted_skills or [])
            entry.extracted_skills = json.dumps(skills) if skills else None

            experience = next((c.experience_years for c in rows if c.experience_years is not None), None)
            if experience is None:
                experience = extracted_experience
            entry.experience_years = _clamp_experience_local(experience)

            # This is a real resume, so the parser is safe here — unlike profile text,
            # which would yield college="Professional Summary".
            if resume_text and not all([entry.college, entry.year_of_passing,
                                        entry.highest_degree, entry.job_role]):
                try:
                    from src.services.resume_profile_parser import parse_resume_profile
                    parsed = parse_resume_profile(resume_text)
                except Exception as e:
                    logger.warning(f"Resume pool import: parse failed for {seed.email}: {e}")
                    parsed = {}
                entry.college = entry.college or parsed.get("college")
                entry.year_of_passing = entry.year_of_passing or parsed.get("year_of_passing")
                entry.highest_degree = entry.highest_degree or parsed.get("highest_degree")
                entry.job_role = entry.job_role or parsed.get("job_role")
                _llm_fill_gaps(entry, resume_text, label=seed.email)

            if ranked:
                entry.best_job_id = ranked[0]["job_id"]
                entry.best_score = ranked[0]["ats_score"]
                entry.match_results = json.dumps(ranked)

            db.commit()
            counts["imported" if is_new else "refreshed"] += 1
            counts["scored" if entry.best_score is not None else "scoreless"] += 1

        except IntegrityError:
            db.rollback()
            counts["skipped"] += 1
        except Exception as e:
            db.rollback()
            logger.error(f"Resume pool orphan import failed for {person.pkey}: {e}")
            counts["failed"] += 1

    counts["remaining"] = max(0, remaining_before - len(people))
    counts["active_job_count"] = len(prepared_jobs)
    return counts


def _json_list_or_none(raw):
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _clamp_experience_local(value):
    from routes.hr_routes import _clamp_experience
    return _clamp_experience(value)


def count_orphan_candidates(db: Session, company_id: Optional[str]) -> int:
    """Distinct people with candidate rows but no pool row. Drives the import label."""
    if not company_id:
        return 0
    person_key = func.coalesce(Candidate.user_id, func.lower(Candidate.email))
    return (_uncovered_candidate_query(db, company_id)
            .with_entities(person_key).distinct().count())


def _unscored_query(db: Session, company_id: str):
    """Pool rows that have never been ranked against the open roles."""
    return db.query(ResumePoolEntry).filter(
        ResumePoolEntry.company_id == company_id,
        ResumePoolEntry.best_score.is_(None),
    )


def count_unscored(db: Session, company_id: Optional[str]):
    """(scoreable, unscoreable) counts of unranked people.

    Split deliberately. A row with no stored text can never be scored, so counting it as
    outstanding work would leave the caller looping against a number that never falls.
    The UI shows the second figure separately and explains it.
    """
    if not company_id:
        return 0, 0
    base = _unscored_query(db, company_id)
    scoreable = base.filter(ResumePoolEntry.resume_text.isnot(None)).count()
    unscoreable = base.filter(ResumePoolEntry.resume_text.is_(None)).count()
    return scoreable, unscoreable


def score_backlog(db: Session, *, company_id: Optional[str],
                  limit: int = SCORE_BATCH_DEFAULT) -> Dict[str, Any]:
    """Rank a bounded batch of unscored people against the currently open roles.

    Bounded on purpose: this runs inside the web request. Redis is not provisioned, so
    an RQ queue would fall back to running inline anyway (see src/judge/queue.py) — and
    doing unbounded ML work in a gunicorn worker is what exhausted the connection pool
    before. The caller repeats until `remaining` reaches zero.
    """
    if not company_id:
        raise ValueError("company_id is required to score the backlog")

    from routes.hr_routes import _prepare_active_jobs, _score_against_jobs

    # Prepared once for the whole batch — re-parsing every job description per person
    # would double the cost of the slowest thing on the screen.
    prepared_jobs = _prepare_active_jobs(db, company_id)
    if not prepared_jobs:
        scoreable, unscoreable = count_unscored(db, company_id)
        return {"scored": 0, "failed": 0, "remaining": scoreable,
                "unscoreable": unscoreable, "active_job_count": 0}

    batch = min(max(int(limit or 0), 1), SCORE_BATCH_MAX)
    entries = (_unscored_query(db, company_id)
               .filter(ResumePoolEntry.resume_text.isnot(None))
               # Oldest first, so repeated clicks chew through the backlog from a stable
               # end rather than reshuffling what is left.
               .order_by(ResumePoolEntry.uploaded_at.asc())
               .limit(batch).all())

    scored = failed = 0
    for entry in entries:
        try:
            ranked, extracted_skills, extracted_experience = _score_against_jobs(
                entry.resume_text, prepared_jobs, label=entry.email or entry.id
            )
            if not ranked:
                failed += 1
                continue

            entry.best_job_id = ranked[0]["job_id"]
            entry.best_score = ranked[0]["ats_score"]
            entry.match_results = json.dumps(ranked)

            # Only fill gaps. HR may have corrected these by hand on the card, and the
            # parser's guess must not overwrite a human's correction.
            if not entry.extracted_skills and extracted_skills:
                entry.extracted_skills = json.dumps(extracted_skills)
            if entry.experience_years is None and extracted_experience is not None:
                entry.experience_years = _clamp_experience_local(extracted_experience)

            db.commit()
            scored += 1
        except Exception as e:
            db.rollback()
            logger.error(f"Backlog scoring failed for pool entry {entry.id}: {e}")
            failed += 1

    remaining, unscoreable = count_unscored(db, company_id)
    return {"scored": scored, "failed": failed, "remaining": remaining,
            "unscoreable": unscoreable, "active_job_count": len(prepared_jobs)}


def count_importable_users(db: Session, company_id: Optional[str]) -> int:
    """Registered users not yet in the pool. Drives the import button's label."""
    if not company_id:
        return 0
    return db.query(User).filter(
        User.company_id == company_id,
        User.role == "candidate",
        ~db.query(ResumePoolEntry.id).filter(
            ResumePoolEntry.company_id == company_id,
            ResumePoolEntry.source_user_id == User.id,
        ).exists(),
    ).count()
