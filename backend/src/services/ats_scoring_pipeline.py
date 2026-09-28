"""One path for scoring a candidate against a job and saving the result.

Apply, quick-apply, rescore and Move-to-ATS used to each carry their own copy of this,
and none of them recorded how a score came about. Everything here:

- reads the candidate's inputs the same way every time (profile text first, the resume
  as fallback), keeping the extraction diagnostics;
- never raises: text that cannot be read, or a scorer failure, becomes a result marked
  `needs_review` rather than a silent 0;
- saves provenance with every result (rules version, job-description version, model
  versions, a hash of the text scored, when), keeps any HR override, and appends to the
  score history.
"""
from __future__ import annotations

import hashlib
import json
import logging
import types
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import func

from src.services import parse_quality
from src.services.enhanced_ats_service import (
    EnhancedATSScoringService, SCORING_RULES_VERSION, current_model_versions,
)
from src.services.jd_versions import ensure_jd_version

logger = logging.getLogger(__name__)

# At or above this a new applicant is moved to HR review automatically. Never used to
# reject: below it they simply stay in ATS screening for HR to look at.
ATS_REVIEW_THRESHOLD = 50


def parse_skills(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(s).strip() for s in parsed if str(s).strip()]
    except (TypeError, ValueError):
        pass
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def job_scoring_params(job) -> dict:
    """What the scorer reads from a job. `preferred` is None when HR never specified
    nice-to-haves (the scorer may then infer them from the JD text)."""
    raw_pref = getattr(job, "preferred_skills", None)
    return {
        "jd_text": f"{job.description or ''} {job.requirements or ''}".strip(),
        "required": parse_skills(job.required_skills),
        "preferred": None if raw_pref is None else parse_skills(raw_pref),
        "experience": job.experience_years or 0,
    }


@dataclass
class ScoringInputs:
    scoring_text: str = ""
    scoring_source: str = "resume"            # profile | resume
    resume_text: str = ""
    extraction: dict = field(default_factory=dict)
    educations: list = field(default_factory=list)
    work_experiences: list = field(default_factory=list)
    institution_names: list = field(default_factory=list)
    links_text: str = ""
    permanent_pincode: Optional[str] = None
    known_experience_years: Optional[float] = None
    profile: object = None


def build_inputs_for_candidate(db, *, user_id: Optional[str], resume_path: Optional[str] = None,
                               resume_text: Optional[str] = None) -> ScoringInputs:
    """The candidate's scoring inputs: their structured profile if they have one, with the
    resume's text as fallback (and always extracted — it is stored on the application)."""
    from src.models.models import CandidateProfileExt
    from routes.candidate_profile_routes import build_profile_text
    from src.utils import extract_text_with_diagnostics

    inputs = ScoringInputs()
    profile = db.query(CandidateProfileExt).filter(CandidateProfileExt.user_id == user_id).first() \
        if user_id else None
    inputs.profile = profile

    if resume_text is not None:
        inputs.resume_text = resume_text
        inputs.extraction = {"text": resume_text, "confidence": 0.9 if resume_text else 0.0,
                             "method": "stored", "pages": None, "error": None}
    elif resume_path:
        inputs.extraction = extract_text_with_diagnostics(resume_path)
        inputs.resume_text = inputs.extraction.get("text") or ""

    profile_text = build_profile_text(profile) if profile else ""
    if profile_text:
        inputs.scoring_text, inputs.scoring_source = profile_text, "profile"
    else:
        inputs.scoring_text, inputs.scoring_source = inputs.resume_text, "resume"

    if profile:
        inputs.educations = [
            {"education_level": e.education_level, "degree": e.degree,
             "institution_name": e.institution_name, "grade": e.grade}
            for e in (profile.educations or [])
        ]
        inputs.institution_names = [e["institution_name"] for e in inputs.educations if e["institution_name"]]
        inputs.work_experiences = [
            {"employment_type": w.employment_type, "job_title": w.job_title,
             "description": getattr(w, "description", None)}
            for w in (profile.work_experiences or [])
        ]
        bits = [profile.github_url or "", profile.linkedin_url or ""]
        bits += [p.repository_url or "" for p in (profile.projects or [])]
        bits += [p.project_url or "" for p in (profile.projects or [])]
        inputs.links_text = " ".join(b for b in bits if b)
        inputs.permanent_pincode = profile.permanent_pincode or profile.pincode
        years = getattr(profile, "total_experience_years", None)
        inputs.known_experience_years = float(years) if years is not None else None
    return inputs


@dataclass
class ScoringOutcome:
    result: object                         # EnhancedATSScoreResult, or a zero stub
    parse: dict                            # {status, confidence, issues, word_count}
    scoring_source: str
    input_hash: Optional[str]
    error: Optional[str] = None


def _stub_result():
    return types.SimpleNamespace(
        ats_score=0, extracted_skills=[], extracted_experience=0, matched_skills=[],
        missing_skills=[], recommendations=[], strengths=[], weaknesses=[],
        scoring_breakdown={}, skills_match_score=0, semantic_score=0, experience_score=0,
        keyword_score=0, rating=None, hiring_recommendation=None,
    )


def score_for_job(inputs: ScoringInputs, job, *, service=None, prepared=None) -> ScoringOutcome:
    """Score one candidate against one job. Never raises."""
    text = inputs.scoring_text or ""
    # Quality is judged on what is actually scored. When the profile is the source, the
    # resume file's own problems are informational — they did not affect the score.
    extraction = inputs.extraction if inputs.scoring_source == "resume" else {"confidence": 1.0}
    parse = parse_quality.assess(text, extraction, source=inputs.scoring_source)
    input_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    if not text.strip():
        return ScoringOutcome(_stub_result(), parse, inputs.scoring_source, input_hash)

    params = job_scoring_params(job)
    try:
        svc = service or EnhancedATSScoringService()
        result = svc.enhanced_score_candidate(
            resume_text=text,
            job_description=params["jd_text"],
            required_skills=params["required"],
            required_experience=params["experience"],
            institution_names=inputs.institution_names,
            permanent_pincode=inputs.permanent_pincode,
            educations=inputs.educations,
            work_experiences=inputs.work_experiences,
            links_text=inputs.links_text,
            preferred_skills=params["preferred"],
            prepared=prepared,
            known_experience_years=inputs.known_experience_years,
        )
        # Most of the score resting on nothing found in the resume is as unreliable as a
        # resume that could not be read: it goes to a person instead of the ranking.
        coverage = getattr(result, "evidence_coverage", None)
        if coverage is not None and coverage < parse_quality.LOW_EVIDENCE_COVERAGE:
            parse = {**parse, "status": "needs_review",
                     "issues": list(parse.get("issues", [])) + ["low_evidence"]}
        return ScoringOutcome(result, parse, inputs.scoring_source, input_hash)
    except Exception as e:
        logger.exception("ATS scoring failed for job %s", getattr(job, "id", "?"))
        parse = {**parse, "status": "needs_review",
                 "issues": list(parse.get("issues", [])) + ["scoring_error"]}
        return ScoringOutcome(_stub_result(), parse, inputs.scoring_source, input_hash, error=str(e)[:300])


def _json(v):
    if v is None:
        return None
    return json.dumps(v) if isinstance(v, (dict, list)) else v


def effective_score(ats_result) -> Optional[float]:
    if ats_result is None:
        return None
    if ats_result.override_score is not None:
        return float(ats_result.override_score)
    return float(ats_result.ats_score) if ats_result.ats_score is not None else None


def effective_score_col():
    from src.models.models import ATSResult
    return func.coalesce(ATSResult.override_score, ATSResult.ats_score)


def persist_ats_result(db, *, candidate, job, outcome: ScoringOutcome, event_type: str = "scored",
                       actor_id: Optional[str] = None):
    """Save (insert or update) the candidate's result for this job with full provenance.

    An HR override survives a rescore: the model score updates underneath it, and the
    history shows both. Candidate.ats_score mirrors the effective score.
    """
    from src.models.models import ATSResult, ATSScoreEvent

    version = ensure_jd_version(db, job, actor_id)
    r = outcome.result
    row = db.query(ATSResult).filter(
        ATSResult.candidate_id == candidate.id, ATSResult.job_id == job.id).first()
    previous_effective = effective_score(row)
    if row is None:
        row = ATSResult(candidate_id=candidate.id, job_id=job.id,
                        company_id=candidate.company_id or job.company_id)
        db.add(row)

    row.skills_match_score = r.skills_match_score
    row.semantic_score = r.semantic_score
    row.experience_score = r.experience_score
    row.keyword_score = r.keyword_score
    row.ats_score = r.ats_score
    row.matched_skills = _json(r.matched_skills or None)
    row.missing_skills = _json(r.missing_skills or None)
    row.scoring_breakdown = _json(r.scoring_breakdown) if r.scoring_breakdown else None
    row.parse_status = outcome.parse["status"]
    row.parse_confidence = outcome.parse["confidence"]
    row.parse_issues = _json(outcome.parse["issues"])
    row.scoring_source = outcome.scoring_source
    row.evidence_coverage = getattr(r, "evidence_coverage", None)
    row.mandatory_met = getattr(r, "mandatory_met", None)
    row.mandatory_total = getattr(r, "mandatory_total", None)
    explanation = getattr(r, "explanation", None)
    row.explanation = _json(explanation) if explanation else None
    row.scoring_rules_version = SCORING_RULES_VERSION
    row.jd_version_id = version.id
    row.model_version = json.dumps(current_model_versions())
    row.input_hash = outcome.input_hash
    row.scored_at = datetime.utcnow()
    db.flush()

    new_effective = effective_score(row)
    candidate.ats_score = new_effective
    db.add(ATSScoreEvent(
        company_id=row.company_id, ats_result_id=row.id, candidate_id=candidate.id, job_id=job.id,
        event_type=event_type, model_score=float(r.ats_score or 0),
        previous_effective_score=previous_effective, new_effective_score=new_effective,
        actor_user_id=actor_id, scoring_rules_version=SCORING_RULES_VERSION, jd_version_id=version.id,
        created_at=datetime.utcnow(),
    ))
    return row


def needs_review_candidate_ids(db, job_id: str) -> set:
    """Candidates whose resume could not be read reliably for this job. Every automatic
    reject or shortlist must leave them alone: they go to a person."""
    from src.models.models import ATSResult
    return {cid for (cid,) in db.query(ATSResult.candidate_id).filter(
        ATSResult.job_id == job_id, ATSResult.parse_status == "needs_review").all()}
