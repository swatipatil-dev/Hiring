"""
👔 HR API ROUTES
Handles job creation, ATS screening, interview scheduling, and offer management
"""

from fastapi import APIRouter, HTTPException, status, Depends, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func, desc, and_, or_
from typing import List, Optional, Dict, Any
from src.config.database_config_mysql import get_db
from src.auth import get_current_user, require_role, AuthMiddleware
from src.models.models import (
    User, JobPosting, Candidate, ATSResult, CandidateStageHistory,
    Interview, InterviewFeedback, Offer, HRProfile, Department, InterviewerProfile,
    CandidateRequest, Notification, ResumePoolEntry, ResumePoolAttachment,
    MailTemplate, JobHRCollaborator
)
from src.services.file_access import (application_file_url, content_disposition,
                                      file_headers, voice_answer_url)
from src.services.resume_pool_import import (
    import_registered_users, import_orphan_candidates,
    count_importable_users, count_orphan_candidates,
    score_backlog, count_unscored,
)
from src.services.job_dedup import find_open_duplicate_job, duplicate_job_detail
from src.services.candidate_source import SOURCES, from_pool_channel, source_view
from src.services.staff_directory import (
    active_interviewer_query, active_hr_query, active_user_by_email,
)
from src.services.interview_panel import (
    build_panel, parse_panel, apply_panel, diff_panel,
)
from src.services.llm_resume_extractor import (
    PROFILE_FIELDS, missing_fields, extract_gaps, profile_source_map as _profile_source,
    LLM_RESUME_CONCURRENCY,
)
from src.services.talent_pool import (
    derive_outcome, outcome_filter, outcome_sum_columns,
)
from src.services.job_transfer import transfer_job, hr_display_name, JobTransferError
from src.services.job_access import (
    job_access_filter, can_access_job, is_job_owner, is_collaborator, collaborator_summary,
)
from src.services.notification_service import create_notification
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime, timedelta
import asyncio
import hashlib
import logging
import uuid
import json
import os

logger = logging.getLogger(__name__)

# Shared with every other reader of these columns, so a Python-repr row parses
# the same way everywhere. See src/services/skills_parsing.py.
from src.services.skills_parsing import parse_skills as _parse_skills, dump_skills, parse_skills_or_none

# The three proctoring flags HR can see and override at send time. They live as keys
# inside Interview.security_settings_data (a JSON TEXT blob), not as real columns.
# Note copy_paste_allowed is inverted vs the other two: True means the candidate MAY
# copy/paste. Kept as-is so this matches the Create Test wizard's wording.
PROCTORING_FLAGS = (
    "enable_webcam_monitoring",
    "enable_tab_switch_detection",
    "copy_paste_allowed",
    "require_fullscreen",
)

# Per-flag defaults for a test whose security_settings_data is NULL or malformed - drafts,
# and every test created before a flag existed. These must match what the candidate client
# actually applies (routes/candidate_routes.py), or HR's summary describes a stricter or
# looser exam than the one that ran. A blanket False did not: tab-switch detection has
# always defaulted ON for the candidate.
PROCTORING_FLAG_DEFAULTS = {
    "enable_webcam_monitoring": False,
    "enable_tab_switch_detection": True,
    "copy_paste_allowed": False,
    "require_fullscreen": True,
}

def _parse_security_settings(raw):
    """Parse a security_settings_data blob into a dict. Returns {} when the column is
    NULL or holds malformed JSON, so callers can safely .get() with their own defaults."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}

def _proctoring_flags(raw):
    """The HR-visible proctoring flags from a security_settings_data blob, each coerced
    to a bool and falling back to the default the candidate client actually applies."""
    sec = _parse_security_settings(raw)
    return {flag: bool(sec.get(flag, PROCTORING_FLAG_DEFAULTS[flag]))
            for flag in PROCTORING_FLAGS}


# Appended by the server to every invitation for a proctored test, whatever HR typed in
# the body, so a candidate always learns about monitoring before opening the link. Kept in
# step with MonitoringDisclosure (frontend/src/pages/candidate/TestInstructions.jsx),
# which gives the full detail and collects consent.
MONITORING_EMAIL_NOTICE = (
    "<p style='margin:18px 0 0;padding:12px 14px;border-radius:8px;background:#f0f7f5;"
    "border:1px solid #cfe6e0;color:#274b45;font-size:0.9rem;line-height:1.6'>"
    "<b>This assessment is proctored.</b> Your camera and microphone are analysed "
    "automatically during the test, your face is compared with the photo on your "
    "application, and leaving the test tab is recorded. Flags are reviewed by a person "
    "&mdash; nothing rejects you automatically. The test page explains exactly what is "
    "collected and asks for your agreement before you start. If you need an adjustment, "
    "such as extra time, please reply to this email before you begin."
    "</p>"
)


def _test_is_proctored(test, overrides=None) -> bool:
    """Whether webcam monitoring will be on for a test sent with these overrides."""
    flags = _proctoring_flags(test.security_settings_data if test else None)
    flags.update({k: bool(v) for k, v in (overrides or {}).items() if k in flags})
    return flags["enable_webcam_monitoring"]


from src.services.jd_versions import ensure_jd_version  # noqa: E402
from src.services.ats_scoring_pipeline import (  # noqa: E402
    needs_review_candidate_ids, effective_score_col, effective_score,
    build_inputs_for_candidate, score_for_job, persist_ats_result, ATS_REVIEW_THRESHOLD,
)
from src.services.assessment_fairness import (  # noqa: E402
    approved_accommodations, parse_accommodations, describe as describe_accommodations,
)


def _preferred_json(preferred, required):
    """Nice-to-haves as stored: de-duplicated, and never repeating a mandatory skill."""
    if preferred is None:
        return None
    req = {str(r).strip().lower() for r in (required or [])}
    seen, out = set(), []
    for p in preferred:
        key = str(p).strip().lower()
        if key and key not in req and key not in seen:
            seen.add(key)
            out.append(str(p).strip())
    return json.dumps(out)


def _safe_targets(raw):
    """Parse skill_type_targets_data into a list of
    ``{"skill_name","question_type","count_per_set"}``. Returns [] when the column is NULL
    or malformed, so callers can fall back to counting questions instead."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []

# Import shared utilities
from src.utils import (
    get_ats_interpretation, calculate_ats_distribution,
    validate_pagination_params, validate_sort_params, validate_order_params,
    create_error_response, create_success_response,
    validate_file_upload, save_file_securely, extract_text_from_file
)

router = APIRouter(prefix="/api/v1/hr", tags=["HR"])

# ==================== ENHANCED PYDANTIC MODELS ====================

class ATSCandidateResponse(BaseModel):
    candidate_id: str
    name: str
    email: str
    phone: Optional[str] = None
    current_stage: str
    ats_score: float
    ats_level: str
    ats_color: str
    skills_match_score: Optional[float] = 0.0
    semantic_score: Optional[float] = 0.0
    experience_score: Optional[float] = 0.0
    keyword_score: Optional[float] = 0.0
    matched_skills: List[str] = []
    missing_skills: List[str] = []
    extracted_skills: List[str] = []
    experience_years: Optional[int] = 0
    applied_at: Optional[datetime] = None
    # Signed download links (src/services/file_access.py), never storage keys.
    resume_url: Optional[str] = None
    photo_url: Optional[str] = None
    plagiarism_flag: bool = False
    jd_overlap: Optional[float] = None
    education_score: Optional[float] = 0.0
    certification_score: Optional[float] = 0.0
    project_score: Optional[float] = 0.0
    rating: Optional[str] = None
    hiring_recommendation: Optional[str] = None
    strengths: List[str] = []
    weaknesses: List[str] = []
    missing_preferred_skills: List[str] = []
    scoring_breakdown: Optional[Dict[str, Any]] = None
    # ── Explainability and provenance. `ats_score` above is the EFFECTIVE score (HR's
    # override when there is one); `model_score` is what the scorer produced.
    model_score: Optional[float] = None
    override_score: Optional[float] = None
    is_overridden: bool = False
    parse_status: Optional[str] = None          # ok | needs_review | None (legacy)
    parse_issues: List[str] = []                # plain-language reasons
    mandatory_met: Optional[int] = None
    mandatory_total: Optional[int] = None
    evidence_coverage: Optional[float] = None
    scoring_source: Optional[str] = None        # profile | resume
    scoring_rules_version: Optional[str] = None
    jd_version_no: Optional[int] = None
    is_stale: bool = False
    stale_reason: Optional[str] = None          # jd_changed | rules_updated | legacy
    scored_at: Optional[datetime] = None
    # ── Source (src/services/candidate_source.py); NULL = not recorded.
    source: Optional[str] = None
    source_label: Optional[str] = None
    source_detail: Optional[str] = None

class JobCandidatesEnhancedResponse(BaseModel):
    job_id: str
    job_title: str
    total_candidates: int
    candidates: List[ATSCandidateResponse]
    ats_distribution: Dict[str, Any]
    current_rules_version: Optional[str] = None
    current_jd_version_no: Optional[int] = None
    rules_versions_present: List[str] = []
    needs_review_count: int = 0

class CandidateSelectionRequest(BaseModel):
    candidate_ids: List[str]
    selection_type: str = Field(..., description="Type of selection: 'top_n', 'score_threshold', 'top_percentile', 'manual'")
    top_n: Optional[int] = Field(None, description="Number of top candidates to select")
    min_score: Optional[float] = Field(None, description="Minimum ATS score threshold")
    percentile: Optional[float] = Field(None, description="Top percentile to select (e.g. 10 = top 10%)", ge=1, le=100)
    reject_others: bool = False
    rejection_reason: Optional[str] = None
    next_stage: str = Field(..., description="Next stage to move selected candidates to")

class BulkActionResponse(BaseModel):
    message: str
    selected_count: int
    rejected_count: int
    moved_to_stage: str
    job_id: str
    # Applicants whose resume could not be read reliably: never auto-selected by rank or
    # auto-rejected, because they were not really ranked.
    held_for_review: int = 0

class JobPostingCreate(BaseModel):
    title: str
    description: str
    requirements: str
    department_id: Optional[str] = None
    location: str
    employment_type: str
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    experience_years: int
    required_skills: List[str]                   # mandatory requirements
    # Nice-to-haves. None = not specified (on update: leave as is; the scorer may infer
    # them from the JD text). [] = explicitly none.
    preferred_skills: Optional[List[str]] = None
    job_category: Optional[str] = "technical"   # technical | non_technical | mixed

class JobTransferRequest(BaseModel):
    to_hr_id: str
    reason: Optional[str] = Field(None, max_length=500)

class BulkJobTransferRequest(BaseModel):
    to_hr_id: str
    # Whose jobs to move. Omitted = the caller's own jobs.
    from_hr_id: Optional[str] = None
    # Job statuses to include. Omitted = every status except "closed".
    statuses: Optional[List[str]] = None
    reason: Optional[str] = Field(None, max_length=500)

class AddJobCollaboratorRequest(BaseModel):
    hr_id: str

class JobApprovalRequest(BaseModel):
    salary_min: float = Field(..., description="Minimum salary (required)")
    salary_max: float = Field(..., description="Maximum salary (required)")
    location: Optional[str] = None
    employment_type: Optional[str] = None
    description: Optional[str] = None
    requirements: Optional[str] = None
    # The approval form shows skills carried over from the candidate request,
    # and until now had nowhere to send them — so editing them there silently
    # did nothing. Written only when present, so an approval that omits them
    # leaves the draft's own skills alone.
    required_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None

class JobPostingResponse(BaseModel):
    id: str
    title: str
    description: str
    requirements: str
    department: str
    department_id: Optional[str] = None
    location: Optional[str] = ""
    employment_type: Optional[str] = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    experience_years: Optional[int] = 0
    required_skills: List[str] = []
    preferred_skills: Optional[List[str]] = None     # None = not specified
    status: str
    created_at: datetime
    assigned_interviewer_email: Optional[str] = None
    assigned_interviewer_name: Optional[str] = None
    # The job's assessment, if one has been started. `status` above is the JOB's own
    # lifecycle and says nothing about its test — which is why a job holding an unfinished
    # draft looked identical to one with no test at all.
    # Defaulted: only the list endpoint populates these; the other create_job_response
    # call sites leave them alone.
    has_test: bool = False
    test_id: Optional[int] = None
    test_status: Optional[str] = None
    # The owning HR — what a transfer changes. Admins see every company job, so the
    # card has to say whose it is.
    hr_id: Optional[str] = None
    hr_name: Optional[str] = None
    # Co-HRs: other HRs with the same access to this same job.
    collaborators: List[Dict[str, Any]] = []
    # The viewer's relation to the job: owner | collaborator | admin. Only the list
    # endpoint computes it; the UI uses it to show owner-only actions.
    my_role: Optional[str] = None
    # The viewer's own HR profile id, so a co-HR's "Leave job" can name themselves.
    my_hr_id: Optional[str] = None

class CandidateATSResponse(BaseModel):
    candidate_id: str
    name: str
    email: str
    phone: Optional[str]
    current_stage: str
    ats_score: float
    skills_match_score: float
    semantic_score: float
    experience_score: float
    keyword_score: float
    matched_skills: List[str]
    missing_skills: List[str]
    extracted_skills: List[str]
    experience_years: int
    applied_at: datetime
    resume_path: str

class JobCandidatesResponse(BaseModel):
    job_id: str
    job_title: str
    total_candidates: int
    candidates: List[CandidateATSResponse]

class MoveToScreeningRequest(BaseModel):
    candidate_ids: List[str]
    reject_others: bool = False

class InterviewCreate(BaseModel):
    candidate_id: str
    interviewer_id: str
    interview_type: Optional[str] = None
    scheduled_datetime: datetime
    duration_minutes: int
    meeting_link: Optional[str] = None
    # "Send from" override for the interview email (see SendTestInviteRequest.mailbox_id).
    mailbox_id: Optional[str] = None

class OfferCreate(BaseModel):
    candidate_id: str
    offer_type: str
    salary: float
    bonus: Optional[float] = None
    start_date: str
    expiry_date: str
    terms: Optional[str] = ""
    # "Send from" override for the offer-letter email; omitted = the mailbox
    # configured for the offer_letter step, then the company default.
    mailbox_id: Optional[str] = None

# ==================== HELPER FUNCTIONS ====================

def get_hr_profile(db: Session, user: User) -> HRProfile:
    """Get HR profile for user, auto-creating if absent.

    Looked up by user_id outside the tenant filter: a profile is one per user
    (unique user_id), and a legacy row with a NULL company_id is invisible to
    the filter, so a scoped lookup would miss it and the insert below would hit
    the unique key. Such a row is re-homed to the user's company instead.
    """
    from src.tenancy import tenant_bypass
    with tenant_bypass():
        hr_profile = db.query(HRProfile).filter(HRProfile.user_id == user.id).first()
    if hr_profile and hr_profile.company_id != user.company_id:
        hr_profile.company_id = user.company_id
        db.commit()
    if not hr_profile:
        dept = db.query(Department).filter(Department.company_id == user.company_id).first() if user.company_id else None
        hr_profile = HRProfile(
            user_id=user.id,
            company_id=user.company_id,
            department_id=dept.id if dept else None,
            employee_id=f"HR-{str(user.id)[:8]}"
        )
        db.add(hr_profile)
        db.commit()
        db.refresh(hr_profile)
    return hr_profile

@router.get("/jobs/{job_id}/tests")
async def get_tests_for_job(
    job_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get all assignable tests/interviews created for a specific job.

    DRAFT interviews are work-in-progress (incomplete config / no questions yet) and
    must never appear as assignable tests in the HR dropdown — only finalized ones.
    """
    job = _company_job_or_404(db, job_id, current_user)
    _require_job_team(db, job, current_user)

    # A test belongs to a job ONLY by its exact job_id link. We used to also pull in
    # tests with no job_id whose job_title string matched — but that made a new test
    # bleed onto a different (e.g. old, same-titled) job. Exact link only.
    tests = db.query(Interview).filter(
        Interview.job_id == job_id,
        Interview.candidate_id.is_(None),
        Interview.status != "DRAFT",
    ).order_by(Interview.created_at.desc()).all()

    # How many questions of each type ONE candidate actually sits, so the send modal can show
    # the real exam length as HR changes the per-question minutes.
    #
    # This must be the per-set count, not the size of the generated pool: a test can hold
    # hundreds of approved questions while each candidate only receives count_per_set of them.
    # Mirrors the three routes _clone_test_for_candidate uses to pick a candidate's paper.
    from src.models.question_models import Question as _IQ, QuestionSetAssignment as _QSA
    counts_by_test = {}
    if tests:
        pooled_ids = []          # tests needing a DB count (no per-set spec on the row)
        for t in tests:
            targets = _safe_targets(t.skill_type_targets_data)
            if targets:
                # 1. The spec _assemble_for_candidate draws by: count_per_set per skill+type.
                per_type = {}
                for tgt in targets:
                    qtype = tgt.get("question_type")
                    if qtype:
                        per_type[qtype] = per_type.get(qtype, 0) + int(tgt.get("count_per_set") or 0)
                counts_by_test[t.id] = {k: v for k, v in per_type.items() if v}
            else:
                pooled_ids.append(t.id)

        for iv_id in pooled_ids:
            # 2. Rotating sets: the candidate gets exactly one set, so count a single one.
            first_set = db.query(_QSA.set_number).filter(
                _QSA.interview_id == iv_id
            ).order_by(_QSA.set_number).first()
            if first_set:
                rows = db.query(_IQ.question_type, func.count(_IQ.id)).join(
                    _QSA, _QSA.question_id == _IQ.id
                ).filter(
                    _QSA.interview_id == iv_id,
                    _QSA.set_number == first_set[0],
                ).group_by(_IQ.question_type).all()
            else:
                # 3. No sets and no targets: the clone falls back to every approved question.
                rows = db.query(_IQ.question_type, func.count(_IQ.id)).filter(
                    _IQ.interview_id == iv_id,
                    _IQ.is_approved == True,  # noqa: E712
                ).group_by(_IQ.question_type).all()
            counts_by_test[iv_id] = {qtype: n for qtype, n in rows if qtype}

    def _time_map(raw):
        try:
            cfg = json.loads(raw) if raw else {}
            return (cfg.get("time_per_question_type") or {}) if isinstance(cfg, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}

    return [
        {
            "id": t.id,
            "title": t.title,
            "interview_type": t.interview_type,
            "difficulty_level": t.difficulty_level,
            "duration_minutes": t.duration_minutes,
            "status": t.status,
            "created_by": t.created_by,
            "created_at": t.created_at,
            # Lets the ATS send-invite modal show (and override) the proctoring the
            # interviewer configured, without a second round-trip per test.
            "security_settings": _proctoring_flags(t.security_settings_data),
            # ...and likewise the question timing, which is what actually sets the clock.
            "time_per_question_type": _time_map(t.interview_config_data),
            "question_type_counts": counts_by_test.get(t.id, {}),
        }
        for t in tests
    ]


def create_job_response(job: JobPosting, test: Optional[Interview] = None,
                        viewer: Optional[User] = None,
                        viewer_hr_id: Optional[str] = None) -> JobPostingResponse:
    """Create job posting response.

    ``test`` is the job's template Interview when the caller has already looked it up in
    bulk. Left None the response reports no test, which is what every single-job caller
    wants — only the list endpoint pays for the lookup.
    """
    collaborators = collaborator_summary(job)
    my_role = None
    if viewer is not None and viewer.role == "admin":
        my_role = "admin"
    elif viewer_hr_id:
        if job.hr_id and str(job.hr_id) == str(viewer_hr_id):
            my_role = "owner"
        elif any(c["hr_id"] == str(viewer_hr_id) for c in collaborators):
            my_role = "collaborator"

    # Pay figures follow src/services/salary_visibility: an HR sees them only on a job
    # they own or co-own (or one nobody owns yet — the same exception as the job-team
    # check). Callers already gate on job access; this is the second layer.
    show_salary = not (
        viewer is not None and viewer.role == "hr" and job.hr_id
        and my_role not in ("owner", "collaborator")
    )

    interviewer_email = None
    interviewer_name = None
    if hasattr(job, 'interviewer') and job.interviewer:
        iv_user = job.interviewer.user
        if iv_user:
            interviewer_email = iv_user.email
            interviewer_name = f"{iv_user.first_name} {iv_user.last_name}"
    return JobPostingResponse(
        id=str(job.id),
        title=job.title,
        description=job.description or "",
        requirements=job.requirements or "",
        department=job.department.name if job.department else "",
        department_id=str(job.department_id) if job.department_id else None,
        location=job.location or "",
        employment_type=job.employment_type or "",
        salary_min=float(job.salary_min) if show_salary and job.salary_min else None,
        salary_max=float(job.salary_max) if show_salary and job.salary_max else None,
        experience_years=job.experience_years or 0,
        required_skills=_parse_skills(job.required_skills),
        preferred_skills=(_parse_skills(job.preferred_skills) if job.preferred_skills is not None else None),
        status=job.status,
        created_at=job.created_at,
        assigned_interviewer_email=interviewer_email,
        assigned_interviewer_name=interviewer_name,
        has_test=test is not None,
        test_id=test.id if test else None,
        test_status=test.status if test else None,
        hr_id=str(job.hr_id) if job.hr_id else None,
        hr_name=hr_display_name(job.hr) if job.hr_id else None,
        collaborators=collaborators,
        my_role=my_role,
        my_hr_id=str(viewer_hr_id) if viewer_hr_id else None,
    )

def update_candidate_stage(db: Session, candidate_id: str, new_stage: str, changed_by_id: str, notes: str = None):
    """Update candidate stage and create history record.

    Scoped to the actor's company: every caller has already authorised the candidate,
    but this is the write, so it re-checks rather than trusting an id.
    """
    actor = db.query(User).filter(User.id == changed_by_id).first()
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.company_id == (actor.company_id if actor else None),
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    from src.services.stage_change import apply_stage_change
    # record_unchanged: callers use a same-stage move to log an event (e.g. a
    # reschedule) in the history, as this always did.
    apply_stage_change(db, candidate, new_stage, changed_by_id, notes, record_unchanged=True)

    return candidate

# ==================== DEPARTMENT ENDPOINTS ====================

@router.get("/departments")
async def get_departments_for_hr(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get all departments (for job creation dropdown)"""
    dept_query = db.query(Department)
    if current_user.company_id:
        dept_query = dept_query.filter(Department.company_id == current_user.company_id)
    departments = dept_query.order_by(Department.name).all()
    return [
        {"id": str(d.id), "name": d.name, "description": d.description}
        for d in departments
    ]

# ==================== JOB MANAGEMENT ENDPOINTS ====================

@router.post("/jobs", response_model=JobPostingResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_data: JobPostingCreate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Create a new job posting (PHASE 1)
    
    - **title**: Job title
    - **description**: Job description
    - **requirements**: Job requirements
    - **department_id**: Department ID
    - **location**: Job location
    - **employment_type**: Type of employment
    - **salary_min**: Minimum salary (optional)
    - **salary_max**: Maximum salary (optional)
    - **experience_years**: Required years of experience
    - **required_skills**: List of required skills
    """
    # Get HR profile (auto-created if absent)
    hr_profile = get_hr_profile(db, current_user)

    # Resolve department_id: use provided → HR profile's dept → first company dept
    dept_id = job_data.department_id
    if not dept_id and hr_profile.department_id:
        dept_id = str(hr_profile.department_id)
    if not dept_id:
        first_dept = db.query(Department).filter(Department.company_id == current_user.company_id).first()
        if not first_dept:
            raise HTTPException(status_code=400, detail="No departments found. Create a department first.")
        dept_id = str(first_dept.id)

    department = db.query(Department).filter(
        Department.id == dept_id,
        Department.company_id == current_user.company_id
    ).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # AUTO-ASSIGN interviewer from same department. Active users only: a deactivated
    # interviewer is refused at login (src/auth.py:178), so assigning one routes the job
    # to nobody. Ordered so the pick is reproducible instead of storage-engine order.
    assigned_interviewer = active_interviewer_query(db, current_user.company_id).filter(
        InterviewerProfile.department_id == dept_id
    ).order_by(InterviewerProfile.employee_id).first()

    # Resolve company_id robustly: HR's user.company_id is canonical, but fall
    # back to the resolved department's company_id so a job is never created
    # with NULL company_id (which would break multi-tenant isolation downstream).
    job_company_id = current_user.company_id or department.company_id

    # Refuse a second posting for a role this department is already hiring for. This is
    # the path that closes the loop: an interviewer's draft for the role now blocks HR
    # posting the same title again, which is how the same opening used to appear twice —
    # once from the request and once from the posting.
    #
    # Resolved company_id, not current_user's, so the check covers the same tenant the
    # row is about to be written into.
    duplicate = find_open_duplicate_job(db, job_company_id, job_data.title, dept_id)
    if duplicate:
        raise HTTPException(status_code=409, detail=duplicate_job_detail(duplicate))

    # Create job posting
    db_job = JobPosting(
        title=job_data.title,
        description=job_data.description,
        requirements=job_data.requirements,
        department_id=dept_id,
        created_by_id=current_user.id,
        hr_id=hr_profile.id,
        interviewer_id=assigned_interviewer.id if assigned_interviewer else None,
        location=job_data.location,
        employment_type=job_data.employment_type,
        salary_min=job_data.salary_min,
        salary_max=job_data.salary_max,
        experience_years=job_data.experience_years,
        required_skills=json.dumps(job_data.required_skills) if job_data.required_skills else None,
        preferred_skills=_preferred_json(job_data.preferred_skills, job_data.required_skills),
        job_category=job_data.job_category or "technical",
        company_id=job_company_id,
        status="active"
    )
    
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    return create_job_response(db_job)

@router.get("/jobs", response_model=List[JobPostingResponse])
async def get_my_jobs(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get jobs created by current HR user"""
    query = db.query(JobPosting).filter(JobPosting.company_id == current_user.company_id)

    # Filter by HR profile for non-admin users (auto-creates profile if missing).
    # Scope to jobs the HR owns or is a co-HR on (admins fall through and see all
    # company jobs). job_access_filter uses a subquery, so a shared job is listed once.
    viewer_hr_id = None
    if current_user.role != "admin":
        hr_profile = get_hr_profile(db, current_user)
        viewer_hr_id = hr_profile.id
        query = query.filter(job_access_filter(hr_profile.id))
    
    # Closed jobs stay in the HR list. HR owns the job's lifecycle and needs to see
    # a closed posting to reopen it, so hiding it here made a close look like a
    # delete — the job simply vanished with nothing to act on. Candidates and the
    # public careers pages are filtered separately (status == "active"), so this
    # does not expose a closed job to applicants.
    if status:
        query = query.filter(JobPosting.status == status)

    # Owner name is on every card; load it with the page rather than once per job.
    jobs = (query.options(selectinload(JobPosting.hr).selectinload(HRProfile.user),
                          selectinload(JobPosting.collaborators)
                          .selectinload(JobHRCollaborator.hr).selectinload(HRProfile.user))
            .order_by(JobPosting.created_at.desc()).offset(skip).limit(limit).all())

    # Each job's template test, in ONE query rather than one per card. candidate_id IS NULL
    # is what separates a template from a per-candidate attempt; a DRAFT counts, because a
    # job holding an unfinished draft cannot take a second test either.
    tests_by_job = {}
    job_ids = [j.id for j in jobs]
    if job_ids:
        for iv in db.query(Interview).filter(
            Interview.job_id.in_(job_ids),
            Interview.candidate_id.is_(None),
        ).all():
            # Several templates for one job should not happen (create_draft_interview is
            # get-or-create), but if it does, prefer a finished test over a draft — that is
            # the one that actually blocks creating another.
            existing = tests_by_job.get(iv.job_id)
            if existing is None or (existing.status or "").upper() == "DRAFT":
                tests_by_job[iv.job_id] = iv

    return [create_job_response(job, tests_by_job.get(job.id), viewer=current_user,
                                viewer_hr_id=viewer_hr_id) for job in jobs]


# ==================== RESUME MATCHER (reverse ATS) ====================
# HR uploads a batch of resumes and, for each one, we score it against every
# ACTIVE job in the HR's company and surface the best-suitable job plus a few
# runner-up matches.
#
# Each upload is PERSISTED as a ResumePoolEntry: the file is kept, the text and
# parsed profile (name / college / year of passing / role) are stored, and the full
# ranked match list is cached as JSON. That makes the pool searchable later and lets
# HR promote an entry into a real Candidate ("Move to ATS") whenever they choose.

RESUME_MATCH_MAX_FILES = 20          # cap the batch so scoring stays responsive
RESUME_MATCH_ALTERNATES = 3          # runner-up jobs shown per resume
RESUME_POOL_DIR = "resumes/pool"     # storage prefix for pooled resumes
MAX_COLLEGE_TERMS = 200              # ceiling on the college filter's OR arm count


class JobMatch(BaseModel):
    job_id: str
    job_title: str
    department: Optional[str] = None
    ats_score: float
    rating: Optional[str] = None
    matched_skills: List[str] = []
    missing_skills: List[str] = []
    # Per-factor ATS sub-scores, so the pool shows the same breakdown as the ATS sheet.
    skills_match_score: Optional[float] = None
    semantic_score: Optional[float] = None
    experience_score: Optional[float] = None
    keyword_score: Optional[float] = None
    education_score: Optional[float] = None


class PoolAttachment(BaseModel):
    id: str
    filename: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    is_scoreable: bool = True
    uploaded_at: Optional[datetime] = None


class ArchivedInterview(BaseModel):
    """An interview that happened, replayed off a pool row after its job was deleted.

    Names the colleagues who conducted it and what they concluded. Nothing else keeps
    this once the job is hard-deleted — the Interview and InterviewFeedback rows go with it.
    """
    interview_id: Optional[int] = None
    title: Optional[str] = None
    interview_type: Optional[str] = None
    held_at: Optional[datetime] = None
    is_scheduled_interview: bool = False
    interviewers: List[str] = []
    interviewer_email: Optional[str] = None
    test_score: Optional[float] = None
    overall_score: Optional[float] = None
    hire_recommendation: Optional[str] = None
    feedback_notes: Optional[str] = None


class PoolApplication(BaseModel):
    """One application this person made. Read live from `candidates`, never stored on
    the pool row — a stored copy would go stale the moment anyone changed a stage."""
    candidate_id: str
    job_id: Optional[str] = None
    job_title: Optional[str] = None      # None when the job posting was deleted
    stage: str
    applied_at: Optional[datetime] = None
    ats_score: Optional[float] = None
    # Replayed from resume_pool.archived_applications rather than read live, because the
    # job was hard-deleted and took the Candidate row with it. The title survives here
    # and nowhere else, so this is the one application record that cannot self-update.
    archived: bool = False
    # Populated only on archived rows: who interviewed this person and what they said.
    interviews: List[ArchivedInterview] = []


class ResumeMatchResult(BaseModel):
    id: Optional[str] = None                      # ResumePoolEntry id (None on error rows)
    filename: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    college: Optional[str] = None
    year_of_passing: Optional[int] = None
    job_role: Optional[str] = None
    highest_degree: Optional[str] = None
    # Origin of each field above: "regex" (parsed) or "llm" (inferred by Gemini from
    # the resume). Drives the "verify this" badge. Absent for rows parsed before
    # gap-filling existed, which the UI reads as "regex".
    profile_source: Dict[str, str] = {}
    extracted_skills: List[str] = []
    extracted_experience: Optional[float] = None
    best_match: Optional[JobMatch] = None
    alternates: List[JobMatch] = []
    # ok | needs_review: whether the stored resume text could be read reliably.
    parse_status: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    status: str = "new"
    moved_candidate_id: Optional[str] = None
    error: Optional[str] = None
    # Provenance. Defaulted so the error rows above still construct.
    source: str = "upload"                        # upload | registered
    has_resume: bool = False                      # a downloadable file exists
    source_user_id: Optional[str] = None
    source_candidate_id: Optional[str] = None
    source_stage: Optional[str] = None            # stage of their most recent application
    # Talent-pool view: every application this person made, newest first, with the
    # single bucket that summarises them. See src/services/talent_pool.py.
    applications: List[PoolApplication] = []
    application_count: int = 0
    outcome: str = "never_applied"
    last_activity_at: Optional[datetime] = None
    # Sourcing CRM — HR's own working state, set by hand.
    stage: str = "new"                            # new | ongoing | archived
    notes: Optional[str] = None
    last_contact_at: Optional[datetime] = None
    days_since_contact: Optional[int] = None
    source_channel: Optional[str] = None
    attachments: List[PoolAttachment] = []


class ResumeMatchBatchResponse(BaseModel):
    results: List[ResumeMatchResult]
    active_job_count: int
    message: Optional[str] = None
    warning: Optional[str] = None


class ActiveJobOption(BaseModel):
    """A job HR can promote someone onto. Sent with the pool list because a resume
    with no score has no matched jobs to choose from, and GET /hr/jobs is filtered
    to the caller's own postings while the pool is company-wide."""
    job_id: str
    job_title: str
    department: Optional[str] = None


class ResumePoolListResponse(BaseModel):
    results: List[ResumeMatchResult]
    total: int
    active_job_count: int
    registered_pending: int = 0                   # registered users not yet imported
    orphan_pending: int = 0                       # candidates with no user account
    active_jobs: List[ActiveJobOption] = []


class ImportRegisteredRequest(BaseModel):
    limit: int = 25
    refresh_existing: bool = False
    # Also sweep up candidates with no user account — people HR added by hand whose
    # pool row was later removed, or whose account was deleted.
    include_orphans: bool = True


class ImportRegisteredResponse(BaseModel):
    imported: int
    refreshed: int
    skipped: int
    failed: int
    scored: int
    scoreless: int
    remaining: int
    active_job_count: int
    message: str


POOL_STAGES = ("new", "ongoing", "archived")

# Images attach and display but cannot be ATS-scored without OCR, which the matcher
# does not run. Kept separate from utils.validate_file_upload, which is shared by other
# flows that must stay documents-only.
POOL_DOC_EXTS = {".pdf", ".doc", ".docx"}
POOL_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
POOL_MAX_UPLOAD = 10 * 1024 * 1024


class PoolCandidateCreate(BaseModel):
    """Add someone by hand. Only a name is required — sourcing often starts with
    little more than that, and demanding an email would block the workflow."""
    full_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    job_role: Optional[str] = None
    college: Optional[str] = None
    year_of_passing: Optional[int] = None
    skills: List[str] = []
    source_channel: Optional[str] = None
    notes: Optional[str] = None
    stage: str = "new"


class PoolCandidateUpdate(BaseModel):
    """Every field optional — this backs inline edits, so a request carries only what
    actually changed."""
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    job_role: Optional[str] = None
    college: Optional[str] = None
    year_of_passing: Optional[int] = None
    skills: Optional[List[str]] = None
    source_channel: Optional[str] = None
    notes: Optional[str] = None
    stage: Optional[str] = None
    # Set a specific date — HR often logs a call days after it happened. Sending null
    # explicitly clears it back to "never contacted".
    last_contact_at: Optional[datetime] = None
    mark_contacted: bool = False       # shortcut for "spoke to them just now"


class PoolStageUpdate(BaseModel):
    stage: str


class AttachmentUploadResponse(BaseModel):
    """Returns the refreshed profile plus anything that did NOT make it.

    A file the user picked and that silently vanished is worse than an error — they
    have no way to tell an upload failed from one that was never going to work.
    """
    entry: ResumeMatchResult
    rejected: List[str] = []
    message: str


class StageCount(BaseModel):
    stage: str
    label: str
    count: int


class NamedCount(BaseModel):
    name: str
    count: int


class TrendPoint(BaseModel):
    """One bucket of the additions-over-time series."""
    period: str          # ISO date of the bucket start: 2026-08-24 (week) / 2026-08-01
    label: str           # what the axis shows: "24 Aug" / "Aug 2026"
    count: int


class BandCount(BaseModel):
    """One 10-point band of the match-score histogram."""
    band: str            # "0-9" … "90-100"
    floor: int           # lower bound — sort key, and what the tooltip reads back
    count: int


class ScoreBacklogRequest(BaseModel):
    limit: int = 3


class ScoreBacklogResponse(BaseModel):
    scored: int
    failed: int
    remaining: int          # scoreable rows still unranked — drives the caller's loop
    unscoreable: int        # no stored text; can never be scored, never in `remaining`
    active_job_count: int
    message: str


class TalentPoolMetrics(BaseModel):
    # Everything here obeys the caller's filter EXCEPT `pool_total`, `unscored` and
    # `unscoreable`, which are whole-pool on purpose — see the endpoint for why.
    total: int                 # people matching the current filter
    pool_total: int = 0        # people in the whole pool, so the UI can say "312 of 4180"
    new_count: int
    ongoing_count: int
    archived_count: int
    pipeline: List[StageCount] = []
    sources: List[NamedCount] = []
    outcomes: List[NamedCount] = []
    avg_days_since_contact: Optional[float] = None
    never_contacted: int = 0
    added_last_30_days: int = 0
    with_resume: int = 0
    unscored: int = 0         # ranked against nothing yet, but could be
    unscoreable: int = 0      # no resume or profile text to score at all

    # ── Dashboard series ──────────────────────────────────────────────────────
    added_over_time: List[TrendPoint] = []
    trend_granularity: str = "month"      # "week" | "month" — what one point covers
    score_bands: List[BandCount] = []
    # Filtered rows with no score. NOT a 0-9 band: "never ranked" is not "scored zero".
    unscored_in_view: int = 0
    avg_score: Optional[float] = None
    top_colleges: List[NamedCount] = []
    top_roles: List[NamedCount] = []


class SkillCount(BaseModel):
    skill: str
    count: int


class SkillDemandRow(BaseModel):
    """One demanded skill, measured against the people currently in view."""
    skill: str                       # canonical casing: "Kubernetes", not "kubernetes"
    roles: int                       # active postings asking for it
    role_titles: List[str] = []      # a few, for the tooltip
    candidates: int                  # people who have it outright
    # People with a cluster sibling instead (Docker for Kubernetes). Only computed
    # when `candidates` is 0 — a well-covered skill needs no consolation prize.
    related: int = 0
    related_examples: List[str] = []
    # The grouping that called them related, so the reader can judge it rather than
    # trust it — some of the curated clusters are loose.
    related_cluster: str = ""
    coverage: float = 0.0            # candidates as a % of people with skills on record


class TalentPoolSkills(BaseModel):
    people: int                      # people scanned in the current view
    people_with_skills: int          # of those, how many have any skill recorded
    no_skills_on_record: int         # the rest. NOT "people with no skills".
    distinct_skills: int
    active_roles: int
    demanded_skills: int
    fully_unmet: int                 # demanded by a live role, held by nobody at all
    scanned: int
    truncated: bool = False          # the scan hit its ceiling; counts are a lower bound
    demand: List[SkillDemandRow] = []
    top_skills: List[SkillCount] = []


class MoveToATSRequest(BaseModel):
    job_id: Optional[str] = None       # defaults to the stored best match
    first_name: str
    last_name: str
    email: EmailStr
    phone: Optional[str] = None


def _clamp_experience(value):
    """Fit an extracted experience figure into ResumePoolEntry.experience_years,
    which is DECIMAL(4,2). A garbled resume can yield an absurd number (a stray
    year read as a duration) and MySQL would reject anything over 99.99."""
    if value is None:
        return None
    try:
        return round(max(0.0, min(float(value), 99.99)), 2)
    except (TypeError, ValueError):
        return None


def _json_list(raw) -> List[str]:
    """Parse a JSON TEXT column into a list, tolerating NULL and malformed values."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _person_key(user_id: Optional[str], email: Optional[str]) -> Optional[str]:
    """How the talent pool decides two rows are the same human: the user account when
    there is one, else the email address. Used to bucket applications onto pool rows."""
    if user_id:
        return str(user_id)
    return email.lower() if email else None


def _archived_applications(entry: ResumePoolEntry) -> List[PoolApplication]:
    """Applications replayed off the pool row because their job posting was deleted.

    `_load_application_history` cannot produce these: it reads `candidates` joined to
    `job_postings`, and a hard delete removes both. The snapshot written at delete time
    is the only surviving record of what this person applied for.
    """
    try:
        raw = json.loads(entry.archived_applications) if entry.archived_applications else []
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    out: List[PoolApplication] = []
    for a in raw:
        if not isinstance(a, dict) or not a.get("candidate_id"):
            continue
        applied_at = a.get("applied_at")
        if isinstance(applied_at, str):
            try:
                applied_at = datetime.fromisoformat(applied_at)
            except ValueError:
                applied_at = None
        out.append(PoolApplication(
            candidate_id=a["candidate_id"],
            job_id=a.get("job_id"),
            job_title=a.get("job_title"),
            stage=a.get("stage") or "applied",
            applied_at=applied_at,
            ats_score=a.get("ats_score"),
            archived=True,
            interviews=[
                ArchivedInterview(**iv) for iv in (a.get("interviews") or [])
                if isinstance(iv, dict)
            ],
        ))
    return out


def _entry_to_result(entry: ResumePoolEntry,
                     history: Optional[Dict[str, List[PoolApplication]]] = None
                     ) -> ResumeMatchResult:
    """Render a stored pool entry in the same shape the matcher returns after upload.

    `history` is an optional {person_key: [PoolApplication]} lookup the list endpoint
    builds in two batched queries, so showing someone's full application history costs
    nothing per row.
    """
    matches = []
    try:
        matches = json.loads(entry.match_results) if entry.match_results else []
    except (json.JSONDecodeError, TypeError, ValueError):
        matches = []

    job_matches = [JobMatch(**m) for m in matches if isinstance(m, dict)]

    key = _person_key(entry.source_user_id, entry.email)
    applications = list((history or {}).get(key, []) if key else [])

    # Applications whose job was hard-deleted exist only on the row — the live query
    # cannot find them, because both the Candidate and the job posting are gone. Dedupe
    # by candidate_id so a retried delete cannot list one application twice.
    live_ids = {a.candidate_id for a in applications}
    applications += [a for a in _archived_applications(entry)
                     if a.candidate_id not in live_ids]
    applications.sort(key=lambda a: a.applied_at or datetime.min, reverse=True)

    # Archived stages feed the outcome too: without them, someone whose only application
    # was to a deleted job reads as "never applied", which is plainly untrue.
    outcome = derive_outcome(a.stage for a in applications)
    last_activity = next((a.applied_at for a in applications if a.applied_at), None)

    return ResumeMatchResult(
        parse_status=getattr(entry, "parse_status", None),
        id=entry.id,
        filename=entry.filename or "resume",
        full_name=entry.full_name,
        email=entry.email,
        phone=entry.phone,
        college=entry.college,
        year_of_passing=entry.year_of_passing,
        job_role=entry.job_role,
        highest_degree=entry.highest_degree,
        profile_source=_profile_source(entry),
        extracted_skills=_json_list(entry.extracted_skills),
        extracted_experience=float(entry.experience_years) if entry.experience_years is not None else None,
        best_match=job_matches[0] if job_matches else None,
        alternates=job_matches[1:1 + RESUME_MATCH_ALTERNATES],
        uploaded_at=entry.uploaded_at,
        status=entry.status or "new",
        moved_candidate_id=entry.moved_candidate_id,
        source=entry.source or "upload",
        # bool(path), not storage.exists() — that would be a network round-trip per
        # row on the GCS backend, and the download endpoint already 404s on a miss.
        has_resume=bool(entry.resume_path),
        source_user_id=entry.source_user_id,
        source_candidate_id=entry.source_candidate_id,
        # The stage of their most recent application, so it stays truthful when someone
        # has applied several times.
        source_stage=applications[0].stage if applications else None,
        applications=applications,
        application_count=len(applications),
        outcome=outcome,
        last_activity_at=last_activity,
        stage=entry.stage or "new",
        notes=entry.notes,
        last_contact_at=entry.last_contact_at,
        # Clamped at zero: MySQL rounds fractional seconds up on a 0-precision DATETIME,
        # so a timestamp written moments ago can read as marginally in the future and
        # timedelta.days floors that to -1.
        days_since_contact=(
            max(0, (datetime.utcnow() - entry.last_contact_at).days)
            if entry.last_contact_at else None
        ),
        source_channel=entry.source_channel,
        attachments=[PoolAttachment(
            id=a.id,
            filename=a.filename or "file",
            content_type=a.content_type,
            size_bytes=a.size_bytes,
            is_scoreable=bool(a.is_scoreable),
            uploaded_at=a.uploaded_at,
        ) for a in (entry.attachments or [])],
    )


def _load_application_history(db, company_id: str,
                              entries: List[ResumePoolEntry]
                              ) -> Dict[str, List[PoolApplication]]:
    """Every application made by the people on this page, keyed by person.

    Two queries, deliberately NOT one join. The tenant auto-filter appends
    `company_id == X` for every TenantScoped entity it finds in a query
    (src/tenancy/events.py:86) — so joining JobPosting would add
    `job_postings.company_id = X` to the WHERE, silently turning the outer join into an
    inner one and dropping every application whose job was deleted. A separate
    primary-key lookup for the titles avoids the whole problem.
    """
    user_ids = {e.source_user_id for e in entries if e.source_user_id}
    emails = {e.email for e in entries if e.email}

    clauses = []
    if user_ids:
        clauses.append(Candidate.user_id.in_(user_ids))
    if emails:
        # Email only identifies account-less candidates — see person_applied_exists.
        clauses.append(and_(Candidate.user_id.is_(None), Candidate.email.in_(emails)))
    if not clauses:
        return {}

    rows = (db.query(Candidate.id, Candidate.user_id, Candidate.email, Candidate.job_id,
                     Candidate.current_stage, Candidate.created_at, Candidate.ats_score)
            .filter(Candidate.company_id == company_id)
            .filter(or_(*clauses))
            .order_by(desc(Candidate.created_at))
            .all())

    job_ids = {r.job_id for r in rows if r.job_id}
    titles = dict(
        db.query(JobPosting.id, JobPosting.title).filter(JobPosting.id.in_(job_ids)).all()
    ) if job_ids else {}

    history: Dict[str, List[PoolApplication]] = {}
    for r in rows:
        # The signup placeholder (job_id NULL) is not an application. Including it would
        # make every registered person look like they were in an active pipeline.
        if not r.job_id:
            continue
        key = _person_key(r.user_id, r.email)
        if not key:
            continue
        history.setdefault(key, []).append(PoolApplication(
            candidate_id=r.id,
            job_id=r.job_id,
            job_title=titles.get(r.job_id),
            stage=r.current_stage or "applied",
            applied_at=r.created_at,
            ats_score=float(r.ats_score) if r.ats_score is not None else None,
        ))
    return history


def _prepare_active_jobs(db: Session, company_id: Optional[str]) -> List[Dict[str, Any]]:
    """Load active company jobs once and precompute the scorer inputs, so we don't
    re-parse skills / rebuild JD text for every resume in a batch."""
    jobs = db.query(JobPosting).filter(
        JobPosting.company_id == company_id,
        JobPosting.status == "active",
    ).all()
    return [{
        "job": job,
        "jd_text": f"{job.description or ''} {job.requirements or ''}".strip(),
        "skills": _parse_skills(job.required_skills),
        # None preserved deliberately: it is the sentinel marking a job whose
        # mandatory/preferred split has never been set.
        "preferred": parse_skills_or_none(job.preferred_skills),
        "experience": job.experience_years or 0,
        "department": job.department.name if job.department else None,
    } for job in jobs]


def _score_against_jobs(resume_text: str, prepared_jobs: List[Dict[str, Any]], label: str = ""):
    """Score one resume against every prepared job, ranked best-first.

    Returns (ranked_job_match_dicts, extracted_skills, extracted_experience).
    """
    from src.services.enhanced_ats_service import enhanced_ats_service

    scored = []
    extracted_skills: List[str] = []
    extracted_experience = None

    # Prepare once. The sanitize/clean/normalise/section-detect/parse chain depends only
    # on the resume, and the parse runs a spaCy transformer pass — doing it inside the
    # loop cost one pass per active job to produce an identical result.
    #
    # Unlike a single job failing below, a failed prepare affects every job, so it
    # returns the same empty triple the callers already handle.
    try:
        prepared_resume = enhanced_ats_service.prepare_resume(resume_text)
    except Exception as e:
        logger.error(f"Resume match: preparing {label} failed: {e}")
        return [], [], None

    for pj in prepared_jobs:
        try:
            r = enhanced_ats_service.enhanced_score_candidate(
                resume_text=resume_text,
                job_description=pj["jd_text"],
                required_skills=pj["skills"],
                required_experience=pj["experience"],
                # .get, not [...]: a caller that builds these dicts without the
                # key means "no split recorded", which is exactly None.
                preferred_skills=pj.get("preferred"),
                prepared=prepared_resume,
            )
        except Exception as e:
            logger.error(f"Resume match: scoring {label} vs {pj['job'].id} failed: {e}")
            continue
        # Resume-derived fields are identical across jobs; capture once.
        if not extracted_skills:
            extracted_skills = r.extracted_skills or []
            extracted_experience = r.extracted_experience
        scored.append((pj, r))

    scored.sort(key=lambda pr: pr[1].ats_score, reverse=True)

    def _num(value):
        return round(float(value), 1) if value is not None else None

    ranked = [{
        "job_id": str(pj["job"].id),
        "job_title": pj["job"].title,
        "department": pj["department"],
        "ats_score": round(float(r.ats_score), 1),
        "rating": r.rating or None,
        "matched_skills": r.matched_skills or [],
        "missing_skills": r.missing_skills or [],
        "skills_match_score": _num(getattr(r, "skills_match_score", None)),
        "semantic_score": _num(getattr(r, "semantic_score", None)),
        "experience_score": _num(getattr(r, "experience_score", None)),
        "keyword_score": _num(getattr(r, "keyword_score", None)),
        "education_score": _num(getattr(r, "education_score", None)),
    } for pj, r in scored]

    return ranked, extracted_skills, extracted_experience


@router.post("/resume-match", response_model=ResumeMatchBatchResponse)
async def match_resumes_to_jobs(
    resumes: List[UploadFile] = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Score each uploaded resume against all ACTIVE jobs in the HR's company, store it
    in the resume pool, and return the best-suitable job (plus a few alternates) per
    resume, sorted so the strongest candidates come first.

    Each resume is persisted (file + text + parsed profile + ranked matches) so it stays
    searchable and can later be promoted into a real Candidate via /move-to-ats."""
    import hashlib
    from src.services.resume_profile_parser import parse_resume_profile
    from src.services.storage import storage

    # 1. Load active company jobs ONCE and precompute the scorer inputs.
    prepared_jobs = _prepare_active_jobs(db, current_user.company_id)
    if not prepared_jobs:
        return ResumeMatchBatchResponse(
            results=[],
            active_job_count=0,
            message="No active job postings to match against. Publish a job first.",
        )

    # 2. Cap the batch so a huge upload doesn't stall the request.
    warning = None
    files = resumes
    if len(files) > RESUME_MATCH_MAX_FILES:
        warning = (
            f"Only the first {RESUME_MATCH_MAX_FILES} of {len(files)} resumes were "
            f"processed. Upload the rest in another batch."
        )
        files = files[:RESUME_MATCH_MAX_FILES]

    results: List[ResumeMatchResult] = []
    # (index into `results`, entry) for rows the regex parser left gaps on.
    llm_pending: List[tuple] = []
    for upload in files:
        filename = upload.filename or "resume"

        # Validate type/size; a bad file becomes a per-row error, not a batch failure.
        check = validate_file_upload(upload, max_size=5 * 1024 * 1024)
        if not check.get("valid"):
            results.append(ResumeMatchResult(filename=filename, error=check.get("error")))
            continue

        # Store the file permanently — it backs the pool's download action and any
        # later re-match, so unlike before it is NOT deleted after extraction.
        key = None
        try:
            key = save_file_securely(upload, RESUME_POOL_DIR)
            resume_text, _ = extract_text_from_file(key)
        except Exception as e:
            logger.error(f"Resume match: failed to read {filename}: {e}")
            resume_text = ""

        if not resume_text or not resume_text.strip():
            # Nothing scoreable — don't keep an orphan file around.
            if key:
                try:
                    storage.delete(key)
                except Exception:
                    pass
            results.append(ResumeMatchResult(
                filename=filename,
                error="Could not read any text from this resume.",
            ))
            continue

        ranked, extracted_skills, extracted_experience = _score_against_jobs(
            resume_text, prepared_jobs, label=filename
        )
        if not ranked:
            if key:
                try:
                    storage.delete(key)
                except Exception:
                    pass
            results.append(ResumeMatchResult(
                filename=filename,
                extracted_skills=extracted_skills,
                extracted_experience=extracted_experience,
                error="Scoring failed for this resume.",
            ))
            continue

        profile = parse_resume_profile(resume_text)
        content_hash = hashlib.sha256(resume_text.encode("utf-8", "ignore")).hexdigest()

        # Re-uploading the same resume refreshes the existing row instead of creating a
        # duplicate. Scoped to the company so tenants never collide.
        entry = db.query(ResumePoolEntry).filter(
            ResumePoolEntry.company_id == current_user.company_id,
            ResumePoolEntry.content_hash == content_hash,
        ).first()

        if entry:
            # Normally keep the original file and drop the redundant re-upload. But if
            # the stored path isn't ours — an imported registered candidate points at
            # their own file — take the copy we just saved instead, so the pool never
            # depends on a file it doesn't own.
            if key and key != entry.resume_path:
                if entry.resume_path and not entry.resume_path.startswith(RESUME_POOL_DIR):
                    entry.resume_path = key
                else:
                    try:
                        storage.delete(key)
                    except Exception:
                        pass
        else:
            entry = ResumePoolEntry(
                company_id=current_user.company_id,
                uploaded_by_id=current_user.id,
                content_hash=content_hash,
                resume_path=key,
                uploaded_at=datetime.utcnow(),
            )
            db.add(entry)

        # For a row imported from a registered candidate, the structured profile is
        # better data than anything the parser can infer — so a matching upload fills
        # gaps rather than overwriting. A plain upload keeps its original behaviour.
        keep = entry.source_user_id is not None

        def _set(field, value):
            if value or not keep:
                setattr(entry, field, value)

        entry.filename = filename
        entry.resume_text = resume_text
        _set("full_name", profile.get("full_name"))
        _set("email", profile.get("email"))
        _set("phone", profile.get("phone"))
        _set("college", profile.get("college"))
        _set("year_of_passing", profile.get("year_of_passing"))
        _set("job_role", profile.get("job_role"))
        _set("highest_degree", profile.get("highest_degree"))
        entry.extracted_skills = json.dumps(extracted_skills) if extracted_skills else None
        entry.experience_years = _clamp_experience(extracted_experience)
        entry.best_job_id = ranked[0]["job_id"]
        entry.best_score = ranked[0]["ats_score"]
        entry.match_results = json.dumps(ranked)

        # Attribute each profile field. Read off the entry rather than `profile` so the
        # `keep` guard above is respected — on a registered-candidate row the surviving
        # value is their own, not this parser's. A field the parser found this time is
        # re-marked "regex"; one it still cannot find keeps whatever it had, so an
        # earlier "llm" attribution is not silently laundered into "regex".
        src_map = _profile_source(entry)
        for _f in PROFILE_FIELDS:
            if not getattr(entry, _f, None):
                src_map.pop(_f, None)
            elif profile.get(_f):
                src_map[_f] = "regex"
            else:
                src_map.setdefault(_f, "regex")
        entry.profile_source = json.dumps(src_map) if src_map else None

        try:
            db.commit()
            db.refresh(entry)
        except Exception as e:
            db.rollback()
            logger.error(f"Resume match: failed to store {filename}: {e}")
            results.append(ResumeMatchResult(
                filename=filename,
                error="Could not save this resume to the pool.",
            ))
            continue

        results.append(_entry_to_result(entry))

        # Queue for the gap-fill pass below if the parser left anything blank. Snapshot
        # the values now, while this entry is freshly refreshed: a later file's commit
        # expires it, and re-reading inside the async pass would issue a blocking
        # lazy-load per row from the event loop.
        current = {f: getattr(entry, f, None) for f in PROFILE_FIELDS}
        if missing_fields(current):
            llm_pending.append((len(results) - 1, entry, resume_text, current))

    # 3. Fill the blanks the regex parser left, using Gemini.
    #
    # Deliberately a second pass rather than a call inside the loop: at
    # RESUME_MATCH_MAX_FILES=20 these run concurrently here — roughly one call's
    # latency — where inline they would run serially and add ~20x that to a request
    # that is already scoring every resume against every active job. It also means
    # every pool row is committed before the first call, so a failure here can delay
    # a value but never lose an upload.
    if llm_pending:
        try:
            sem = asyncio.Semaphore(LLM_RESUME_CONCURRENCY)

            async def _fill(text, current):
                async with sem:
                    return await extract_gaps(text, current)

            filled_all = await asyncio.gather(
                *[_fill(t, c) for _, _, t, c in llm_pending], return_exceptions=True
            )

            touched = []
            for (idx, entry, _text, _current), filled in zip(llm_pending, filled_all):
                if isinstance(filled, BaseException):
                    logger.warning(f"Resume match: gap-fill failed for {entry.id}: {filled}")
                    continue
                if not filled:
                    continue
                src_map = _profile_source(entry)
                for field, value in filled.items():
                    setattr(entry, field, value)
                    src_map[field] = "llm"
                entry.profile_source = json.dumps(src_map)
                touched.append((idx, entry))

            if touched:
                db.commit()
                # Re-render only the rows that changed — the versions already in
                # `results` were built before the fill and would understate the row.
                for idx, entry in touched:
                    db.refresh(entry)
                    results[idx] = _entry_to_result(entry)
        except Exception as e:
            db.rollback()
            logger.warning(f"Resume match: LLM gap-fill pass failed: {e}")

    # 4. Sort the batch so the strongest best-matches float to the top
    #    (rows with an error / no best_match sink to the bottom).
    results.sort(key=lambda x: x.best_match.ats_score if x.best_match else -1, reverse=True)

    return ResumeMatchBatchResponse(
        results=results,
        active_job_count=len(prepared_jobs),
        warning=warning,
    )


def _get_pool_entry(db: Session, entry_id: str, current_user: User) -> ResumePoolEntry:
    """Fetch a pool entry, scoped to the caller's company (404 otherwise)."""
    entry = db.query(ResumePoolEntry).filter(
        ResumePoolEntry.id == entry_id,
        ResumePoolEntry.company_id == current_user.company_id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Resume not found")
    return entry


class PoolFilters:
    """The Talent Pool's filter set, declared once and shared.

    Both the Candidates list and the Dashboard narrow the pool with these, and they
    have to narrow it identically -- a dashboard that disagrees with the list sitting
    under it is worse than no dashboard. Declaring the parameters twice is exactly how
    that drift starts, so they are declared here and taken by `Depends()`.

    A plain class rather than a Pydantic query model: this works on every FastAPI
    version the project has been pinned to, and keeps each parameter's description
    attached to the parameter.

    `sort`, `skip` and `limit` are deliberately NOT here. They order and slice a list;
    they mean nothing to an aggregate.
    """

    def __init__(
        self,
        search: Optional[str] = Query(None, description="Match name, email, college, role or skills"),
        college: Optional[str] = Query(
            None, description="College name, or a comma-separated list - matches ANY of them"),
        year_of_passing: Optional[int] = Query(None),
        job_role: Optional[str] = Query(None),
        min_score: Optional[float] = Query(None, ge=0, le=100),
        status: Optional[str] = Query(None, description="new | moved | archived"),
        source: Optional[str] = Query(None, description="upload | registered | applied"),
        outcome: Optional[str] = Query(
            None, description="never_applied | in_process | offer | hired | declined | rejected | applied"),
        stage: Optional[str] = Query(None, description="new | ongoing | archived"),
        skill: Optional[str] = Query(
            None, description="Exactly one skill, as spelled in the profile"),
        uploaded_from: Optional[datetime] = Query(None),
        uploaded_to: Optional[datetime] = Query(None),
    ):
        self.search = search
        self.college = college
        self.year_of_passing = year_of_passing
        self.job_role = job_role
        self.min_score = min_score
        self.status = status
        self.source = source
        self.outcome = outcome
        self.stage = stage
        self.skill = skill
        self.uploaded_from = uploaded_from
        self.uploaded_to = uploaded_to

    def is_active(self) -> bool:
        """Is anything actually narrowing the pool right now?"""
        return any(v not in (None, "") for v in vars(self).values())


def _pool_predicates(db: Session, company_id: str, f: PoolFilters) -> List[Any]:
    """Every WHERE clause the current filter implies, as a list.

    Predicates rather than a Query object, because the list endpoint wants
    `db.query(ResumePoolEntry)` and the dashboard wants a dozen different aggregate
    selects over the same rows. Handing both the same list is what keeps them honest:
    the dashboard's total and the list's total are the same number by construction.
    """
    preds: List[Any] = [ResumePoolEntry.company_id == company_id]

    if f.search:
        term = f"%{f.search.strip()}%"
        preds.append(or_(
            ResumePoolEntry.full_name.ilike(term),
            ResumePoolEntry.email.ilike(term),
            ResumePoolEntry.college.ilike(term),
            ResumePoolEntry.job_role.ilike(term),
            ResumePoolEntry.filename.ilike(term),
            ResumePoolEntry.extracted_skills.ilike(term),
        ))
    if f.college:
        # Campus hiring is a set operation - HR picks a whole region or every IIIT at
        # once - so this is a comma-separated OR, not a single name. One value behaves
        # exactly as it did before. The cap bounds a pathological query string; the
        # largest curated group the UI can send is 30 colleges (~60 spellings).
        terms = [t.strip() for t in f.college.split(",") if t.strip()][:MAX_COLLEGE_TERMS]
        if terms:
            preds.append(or_(*[ResumePoolEntry.college.ilike(f"%{t}%") for t in terms]))
    if f.year_of_passing:
        preds.append(ResumePoolEntry.year_of_passing == f.year_of_passing)
    if f.job_role:
        preds.append(ResumePoolEntry.job_role.ilike(f"%{f.job_role.strip()}%"))
    if f.min_score is not None:
        preds.append(ResumePoolEntry.best_score >= f.min_score)
    if f.status:
        preds.append(ResumePoolEntry.status == f.status)
    if f.source:
        preds.append(ResumePoolEntry.source == f.source)
    if f.stage:
        # Rows predating the stage column read as NULL and belong in the New lane.
        preds.append(
            or_(ResumePoolEntry.stage == "new", ResumePoolEntry.stage.is_(None))
            if f.stage == "new" else ResumePoolEntry.stage == f.stage
        )
    if f.skill and f.skill.strip():
        # Whole array elements only. `extracted_skills` is a JSON array of strings, so
        # matching the QUOTED token is what separates "java" from "javascript" — the
        # free-text `search` above deliberately does not, and cannot back a drill-down
        # that claims to list the people who have exactly this skill.
        #
        # Case-insensitive for free under utf8mb4_unicode_ci, the same property the
        # email identity match relies on (src/services/talent_pool.py:108). No JSON
        # functions, so this still runs on MariaDB.
        #
        # The wildcards are ESCAPED, not stripped. Stripping them turns `skill=%` into
        # an empty term, and an empty term into no predicate at all — a filter that
        # silently matches everything, which is the worst of the three outcomes. And
        # `_` is a single-character wildcard too, so unescaped it would let
        # `Java_cript` match JavaScript.
        esc = (f.skill.strip()
               .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
        preds.append(ResumePoolEntry.extracted_skills.ilike(f'%"{esc}"%', escape="\\"))
    if f.uploaded_from:
        preds.append(ResumePoolEntry.uploaded_at >= f.uploaded_from)
    if f.uploaded_to:
        preds.append(ResumePoolEntry.uploaded_at <= f.uploaded_to)
    if f.outcome:
        # For the list this must be applied BEFORE count() and offset/limit - filtering
        # per page instead would report a total that paging cannot actually reach.
        # Living in the predicate list is what guarantees that for every caller.
        predicate = outcome_filter(db, company_id, f.outcome)
        if predicate is not None:
            preds.append(predicate)

    return preds


@router.get("/resume-pool", response_model=ResumePoolListResponse)
async def list_resume_pool(
    filters: PoolFilters = Depends(),
    sort: str = Query("score", description="score | recent | name | contact"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Search the stored resume pool. This is what makes an uploaded resume findable
    by name, college, year of passing or role long after the upload.

    Takes the same filters as `/talent-pool/metrics`, through the same dependency, so
    the dashboard above this list always describes exactly these people.
    """
    query = (db.query(ResumePoolEntry)
             # Every rendered row reads `entry.attachments`. Without this that is one
             # extra SELECT per row - a whole page of them on every keystroke, now that
             # the dashboard re-queries as you type.
             .options(selectinload(ResumePoolEntry.attachments))
             .filter(*_pool_predicates(db, current_user.company_id, filters)))

    total = query.count()
    if sort == "contact":
        # Longest-neglected first - the working order for a follow-up session. NULLs
        # (never contacted) sort first on ASC in MySQL, which is exactly right.
        query = query.order_by(ResumePoolEntry.last_contact_at.asc())
    elif sort == "recent":
        query = query.order_by(desc(ResumePoolEntry.uploaded_at))
    elif sort == "name":
        query = query.order_by(ResumePoolEntry.full_name.asc(), ResumePoolEntry.email.asc())
    else:
        # NULL best_score sorts last in MySQL, so people with nothing to score sink
        # below everyone who ranked. That is right for "best matches first", but it is
        # why the never-applied and rejected views default to `recent` instead.
        query = query.order_by(desc(ResumePoolEntry.best_score),
                               desc(ResumePoolEntry.uploaded_at))
    entries = query.offset(skip).limit(limit).all()

    history = _load_application_history(db, current_user.company_id, entries)

    active_jobs = (db.query(JobPosting)
                   # `.department.name` below is read once per job; eager-load it or
                   # that is another SELECT per open role on every list call.
                   .options(selectinload(JobPosting.department))
                   .filter(JobPosting.company_id == current_user.company_id,
                           JobPosting.status == "active")
                   .all())

    return ResumePoolListResponse(
        results=[_entry_to_result(e, history) for e in entries],
        total=total,
        active_job_count=len(active_jobs),
        registered_pending=count_importable_users(db, current_user.company_id),
        orphan_pending=count_orphan_candidates(db, current_user.company_id),
        active_jobs=[ActiveJobOption(
            job_id=str(j.id),
            job_title=j.title,
            department=j.department.name if j.department else None,
        ) for j in active_jobs],
    )



# The dashboard's trend chart is bounded on purpose: an unbounded GROUP BY over
# uploaded_at grows a series nobody can read and a query nobody capped.
TREND_DEFAULT_DAYS = 365
# Below this span a month is too coarse to show anything - a campus drive lasts weeks.
TREND_WEEKLY_MAX_DAYS = 120


def _trend_window(f: PoolFilters) -> tuple:
    """(start, end, granularity) for the additions-over-time series.

    Honours the caller's date filter when there is one, because the chart has to agree
    with the rows it sits above. With no date filter it falls back to the last year.
    """
    # End of the current UTC day, not this instant. `uploaded_at` is a 0-precision
    # DATETIME and MySQL ROUNDS the fractional seconds on insert, so a row written
    # moments ago can store a timestamp marginally in the future - and `<= utcnow()`
    # would drop the newest person in the pool out of the newest bucket.
    end = f.uploaded_to or datetime.utcnow().replace(
        hour=23, minute=59, second=59, microsecond=0)
    start = f.uploaded_from or (end - timedelta(days=TREND_DEFAULT_DAYS))
    if start > end:
        start = end
    span = (end - start).days
    return start, end, ("week" if span <= TREND_WEEKLY_MAX_DAYS else "month")


def _trend_periods(start: datetime, end: datetime, granularity: str) -> List[datetime]:
    """Every bucket start in the window, including the empty ones.

    MySQL returns no row for a week nobody joined. Charting only the rows that came
    back closes that gap and draws a flat line over a hiring freeze - so the zero-fill
    happens here, not in the query.
    """
    out: List[datetime] = []
    if granularity == "week":
        cursor = (start - timedelta(days=start.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        while cursor <= end:
            out.append(cursor)
            cursor += timedelta(days=7)
    else:
        cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cursor <= end:
            out.append(cursor)
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


@router.get("/talent-pool/metrics", response_model=TalentPoolMetrics)
async def talent_pool_metrics(
    filters: PoolFilters = Depends(),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Every number and series behind the Dashboard tab.

    Takes the same filters as `GET /resume-pool`, through the same dependency, so the
    charts describe exactly the people the Candidates list would show. `total` is the
    filtered count and `pool_total` the whole pool, so the UI can always say which of
    the two the reader is looking at.

    Three fields deliberately ignore the filter: `pool_total`, and `unscored` /
    `unscoreable`. The last two drive the "Rank all N" button, and `score_backlog()`
    ranks the whole backlog rather than the current view - a filtered count there would
    promise something the button does not do.

    Deliberately aggregate queries rather than loading rows and counting in Python -
    this has to stay cheap as the pool grows past what one page can show.
    """
    cid = current_user.company_id
    preds = _pool_predicates(db, cid, filters)

    def agg(*cols):
        """One aggregate query over the filtered pool."""
        return db.query(*cols).select_from(ResumePoolEntry).filter(*preds)

    total = int(agg(func.count(ResumePoolEntry.id)).scalar() or 0)
    pool_total = int(db.query(func.count(ResumePoolEntry.id))
                       .filter(ResumePoolEntry.company_id == cid).scalar() or 0)

    stage_rows = dict(agg(ResumePoolEntry.stage, func.count(ResumePoolEntry.id))
                      .group_by(ResumePoolEntry.stage).all())
    counts = {s: int(stage_rows.get(s, 0)) for s in POOL_STAGES}
    # Rows written before `stage` existed read as NULL; show them as New rather than
    # silently dropping them from a total HR is trying to reconcile.
    counts["new"] += int(stage_rows.get(None, 0))

    labels = {"new": "New prospects", "ongoing": "Ongoing", "archived": "Archived"}

    source_labels = {
        "upload": "Uploaded by HR", "registered": "Registered on portal",
        "applied": "Applied", "manual": "Added by hand",
    }
    sources = []
    for channel, n in (agg(ResumePoolEntry.source_channel, func.count(ResumePoolEntry.id))
                       .filter(ResumePoolEntry.source_channel.isnot(None))
                       .group_by(ResumePoolEntry.source_channel).all()):
        sources.append(NamedCount(name=channel, count=int(n)))
    # Anyone without an explicit sourcing channel falls back to how they got here, so
    # the breakdown always accounts for every person.
    for src, n in (agg(ResumePoolEntry.source, func.count(ResumePoolEntry.id))
                   .filter(ResumePoolEntry.source_channel.is_(None))
                   .group_by(ResumePoolEntry.source).all()):
        sources.append(NamedCount(name=source_labels.get(src, src or "Unknown"), count=int(n)))
    sources.sort(key=lambda s: s.count, reverse=True)

    avg_days = None
    # UTC_TIMESTAMP(), not NOW(): last_contact_at is written with datetime.utcnow() and
    # no session time_zone is set, so NOW() would measure the gap between UTC data and
    # the server's local clock - 5.5 hours of it on an IST host. The per-row
    # `days_since_contact` in _entry_to_result already uses UTC; these two have to agree.
    contacted = (agg(func.avg(func.datediff(func.utc_timestamp(),
                                            ResumePoolEntry.last_contact_at)))
                 .filter(ResumePoolEntry.last_contact_at.isnot(None)).scalar())
    if contacted is not None:
        avg_days = round(float(contacted), 1)

    cutoff = datetime.utcnow() - timedelta(days=30)

    # One scan for all six buckets. Counting them one COUNT(*) at a time meant six
    # scans, each re-running up to six correlated EXISTS per row - and every filter
    # predicate multiplied that.
    outcome_cols = outcome_sum_columns(db, cid)
    outcome_counts = []
    if total:
        row = agg(*[col for _, col in outcome_cols]).one()
        outcome_counts = [NamedCount(name=name, count=int(row[i] or 0))
                          for i, (name, _) in enumerate(outcome_cols) if row[i]]

    # Whole-pool on purpose - see the docstring.
    unscored, unscoreable = count_unscored(db, cid)

    # ── Additions over time ───────────────────────────────────────────────────
    start, end, granularity = _trend_window(filters)
    window = [ResumePoolEntry.uploaded_at >= start, ResumePoolEntry.uploaded_at <= end]
    if granularity == "week":
        # YEARWEEK mode 3 is ISO: weeks start Monday and week 1 is the one with four
        # days in the new year. Modes that read default_week_format make the chart
        # depend on a server setting.
        bucket = func.yearweek(ResumePoolEntry.uploaded_at, 3)

        def key_of(d):
            """The Python inverse of the bucket expression above, for zero-filling."""
            return int(d.strftime("%G%V"))
    else:
        # The format string is bound as a parameter, so its % escapes never reach the
        # DBAPI's own interpolation.
        bucket = func.date_format(ResumePoolEntry.uploaded_at, "%Y-%m")

        def key_of(d):
            """The Python inverse of the bucket expression above, for zero-filling."""
            return d.strftime("%Y-%m")

    trend_rows = dict(agg(bucket, func.count(ResumePoolEntry.id))
                      .filter(*window).group_by(bucket).all())
    trend_rows = {(int(k) if granularity == "week" else str(k)): int(v)
                  for k, v in trend_rows.items() if k is not None}
    added_over_time = [
        TrendPoint(
            period=d.strftime("%Y-%m-%d"),
            label=d.strftime("%d %b") if granularity == "week" else d.strftime("%b %Y"),
            count=trend_rows.get(key_of(d), 0),
        )
        for d in _trend_periods(start, end, granularity)
    ]

    # ── Match-score distribution ──────────────────────────────────────────────
    band_of = func.floor(ResumePoolEntry.best_score / 10)
    band_rows = dict(agg(band_of, func.count(ResumePoolEntry.id))
                     .filter(ResumePoolEntry.best_score.isnot(None))
                     .group_by(band_of).all())
    band_counts: Dict[int, int] = {}
    for raw, n in band_rows.items():
        if raw is None:
            continue
        # A perfect 100 floors to 10; it belongs in the top band, not an eleventh one.
        band_counts[min(int(raw), 9)] = band_counts.get(min(int(raw), 9), 0) + int(n)
    score_bands = [
        BandCount(band=f"{i * 10}-{100 if i == 9 else i * 10 + 9}",
                  floor=i * 10, count=band_counts.get(i, 0))
        for i in range(10)
    ]
    # "Never ranked" is not "scored zero" - it must never land in the 0-9 bar.
    unscored_in_view = int(agg(func.count(ResumePoolEntry.id))
                           .filter(ResumePoolEntry.best_score.is_(None)).scalar() or 0)
    avg_score_raw = agg(func.avg(ResumePoolEntry.best_score)).scalar()

    def _top(column, limit: int = 10) -> List[NamedCount]:
        """The most common values of one free-form column in the current view.

        Grouped on TRIM() only. `college` and `job_role` are typed by hand and parsed
        out of resumes, so "IIT Madras" and "I.I.T. Madras" stay two rows - the card
        says so rather than presenting this grouping as authoritative.
        """
        key = func.trim(column)
        rows = (agg(key, func.count(ResumePoolEntry.id))
                .filter(column.isnot(None), key != "")
                .group_by(key)
                .order_by(desc(func.count(ResumePoolEntry.id)))
                .limit(limit).all())
        return [NamedCount(name=str(name), count=int(n)) for name, n in rows]

    return TalentPoolMetrics(
        unscored=unscored,
        unscoreable=unscoreable,
        total=total,
        pool_total=pool_total,
        new_count=counts["new"],
        ongoing_count=counts["ongoing"],
        archived_count=counts["archived"],
        pipeline=[StageCount(stage=s, label=labels[s], count=counts[s]) for s in POOL_STAGES],
        sources=sources,
        outcomes=outcome_counts,
        avg_days_since_contact=avg_days,
        never_contacted=int(agg(func.count(ResumePoolEntry.id))
                            .filter(ResumePoolEntry.last_contact_at.is_(None)).scalar() or 0),
        added_last_30_days=int(agg(func.count(ResumePoolEntry.id))
                               .filter(ResumePoolEntry.uploaded_at >= cutoff).scalar() or 0),
        with_resume=int(agg(func.count(ResumePoolEntry.id))
                        .filter(ResumePoolEntry.resume_path.isnot(None)).scalar() or 0),
        added_over_time=added_over_time,
        trend_granularity=granularity,
        score_bands=score_bands,
        unscored_in_view=unscored_in_view,
        avg_score=round(float(avg_score_raw), 1) if avg_score_raw is not None else None,
        top_colleges=_top(ResumePoolEntry.college),
        top_roles=_top(ResumePoolEntry.job_role),
    )


@router.get("/talent-pool/skills", response_model=TalentPoolSkills)
async def talent_pool_skills(
    filters: PoolFilters = Depends(),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Skills supply against demand: can we staff the roles we have open?

    Takes the same filters as `/resume-pool` and `/talent-pool/metrics`, through the
    same dependency, so supply describes exactly the people the Candidates list would
    show. Demand stays company-wide on purpose — narrowing the pool to 2024 graduates
    does not change what the open roles need, and letting the gap shrink as you filter
    would make the number worthless.

    Kept off `/talent-pool/metrics` deliberately: this one parses JSON for the whole
    filtered set, a different cost profile, and the Dashboard tab should not pay for it.
    """
    from src.services.talent_pool_skills import skill_report

    preds = _pool_predicates(db, current_user.company_id, filters)
    try:
        return TalentPoolSkills(**skill_report(db, current_user.company_id, preds))
    except Exception as e:
        logger.error(f"Talent pool skills report failed: {e}")
        raise HTTPException(status_code=500, detail="Could not build the skills report.")


@router.post("/talent-pool/score-backlog", response_model=ScoreBacklogResponse)
async def score_talent_pool_backlog(
    payload: ScoreBacklogRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Rank a small batch of unscored people against the currently open roles.

    Imported people arrive unscored, so the pool's central promise — a match score per
    person — is unmet until this runs. It is deliberately batched: scoring costs one
    model pass per person per open role, and this executes inside the web request
    because no Redis is provisioned for a real queue. The caller repeats until
    `remaining` is zero.
    """
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Your account isn't linked to a company.")

    try:
        counts = score_backlog(db, company_id=current_user.company_id, limit=payload.limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Backlog scoring failed: {e}")
        raise HTTPException(status_code=500, detail="Could not score these candidates.")

    if counts["active_job_count"] == 0:
        # Scoring against nothing would report success while doing no work, and the
        # caller would loop on a `remaining` that never falls.
        raise HTTPException(
            status_code=400,
            detail="There are no open roles to rank against. Publish a job first.",
        )

    message = f"Ranked {counts['scored']} " + ("person" if counts["scored"] == 1 else "people")
    if counts["failed"]:
        message += f", {counts['failed']} could not be read"
    if counts["remaining"]:
        message += f". {counts['remaining']} to go"
    message += "."

    return ScoreBacklogResponse(message=message, **counts)


@router.post("/resume-pool", response_model=ResumeMatchResult, status_code=201)
async def create_pool_candidate(
    payload: PoolCandidateCreate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Add someone to the talent pool by hand.

    Stays in the pool only — no Candidate row is created, because that table requires
    a job and the point of a pool is holding people you have not matched to a role yet.
    Use Move to ATS when there is one.
    """
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Your account isn't linked to a company.")
    if payload.stage not in POOL_STAGES:
        raise HTTPException(status_code=400, detail=f"Stage must be one of {', '.join(POOL_STAGES)}")

    email = str(payload.email).strip() if payload.email else None
    if email:
        existing = db.query(ResumePoolEntry).filter(
            ResumePoolEntry.company_id == current_user.company_id,
            ResumePoolEntry.email == email,
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"{existing.full_name or email} is already in the talent pool.",
            )

    entry = ResumePoolEntry(
        company_id=current_user.company_id,
        uploaded_by_id=current_user.id,
        source="manual",
        stage=payload.stage,
        full_name=payload.full_name.strip(),
        email=email,
        phone=(payload.phone or None),
        job_role=(payload.job_role or None),
        college=(payload.college or None),
        year_of_passing=payload.year_of_passing,
        extracted_skills=json.dumps(payload.skills) if payload.skills else None,
        source_channel=(payload.source_channel or None),
        notes=(payload.notes or None),
        filename=payload.full_name.strip(),
        uploaded_at=datetime.utcnow(),
    )
    db.add(entry)
    try:
        db.commit()
        db.refresh(entry)
    except Exception as e:
        db.rollback()
        logger.error(f"Could not add pool candidate: {e}")
        raise HTTPException(status_code=500, detail="Could not add this candidate.")

    return _entry_to_result(entry)


@router.patch("/resume-pool/{entry_id}", response_model=ResumeMatchResult)
async def update_pool_candidate(
    entry_id: str,
    payload: PoolCandidateUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Inline edit. Only the fields present in the request are touched, so an edit to
    one field cannot blank another that was left alone."""
    entry = _get_pool_entry(db, entry_id, current_user)
    data = payload.model_dump(exclude_unset=True)

    if "stage" in data and data["stage"] not in POOL_STAGES:
        raise HTTPException(status_code=400, detail=f"Stage must be one of {', '.join(POOL_STAGES)}")

    for field in ("full_name", "phone", "job_role", "college", "year_of_passing",
                  "source_channel", "notes", "stage"):
        if field in data:
            value = data[field]
            setattr(entry, field, value.strip() if isinstance(value, str) and value.strip() else
                    (None if isinstance(value, str) else value))
    if "email" in data:
        entry.email = str(data["email"]).strip() if data["email"] else None
    if "skills" in data and data["skills"] is not None:
        entry.extracted_skills = json.dumps(data["skills"]) if data["skills"] else None
    # An explicit date wins over the shortcut, so a form that sends both is not
    # silently overridden by "now".
    if "last_contact_at" in data:
        entry.last_contact_at = data["last_contact_at"]
    elif data.get("mark_contacted"):
        entry.last_contact_at = datetime.utcnow()

    try:
        db.commit()
        db.refresh(entry)
    except Exception as e:
        db.rollback()
        logger.error(f"Could not update pool entry {entry_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not save these changes.")

    history = _load_application_history(db, current_user.company_id, [entry])
    return _entry_to_result(entry, history)


@router.patch("/resume-pool/{entry_id}/stage", response_model=ResumeMatchResult)
async def move_pool_candidate_stage(
    entry_id: str,
    payload: PoolStageUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """One-click move between New, Ongoing and Archived."""
    if payload.stage not in POOL_STAGES:
        raise HTTPException(status_code=400, detail=f"Stage must be one of {', '.join(POOL_STAGES)}")

    entry = _get_pool_entry(db, entry_id, current_user)
    entry.stage = payload.stage
    db.commit()
    db.refresh(entry)

    history = _load_application_history(db, current_user.company_id, [entry])
    return _entry_to_result(entry, history)


def _reject_reason(upload) -> Optional[str]:
    """Why this file cannot be stored, or None if it can."""
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in POOL_DOC_EXTS | POOL_IMAGE_EXTS:
        return f"{upload.filename or 'file'} — {ext or 'no extension'} is not accepted"
    size = getattr(upload, "size", None)
    if size and size > POOL_MAX_UPLOAD:
        mb = size / (1024 * 1024)
        return f"{upload.filename} — {mb:.1f} MB is over the 10 MB limit"
    return None


def _store_attachment(db, entry, upload, current_user) -> Optional[str]:
    """Save one file and hang it off the profile. Returns the storage key."""
    ext = os.path.splitext(upload.filename or "")[1].lower()
    key = save_file_securely(upload, RESUME_POOL_DIR)

    db.add(ResumePoolAttachment(
        company_id=current_user.company_id,
        entry_id=entry.id,
        filename=upload.filename or f"file{ext}",
        file_path=key,
        content_type=upload.content_type,
        size_bytes=getattr(upload, "size", None),
        is_scoreable=ext in POOL_DOC_EXTS,
        uploaded_by_id=current_user.id,
    ))

    # The first scoreable document also becomes the profile's scored resume, so a
    # hand-added candidate starts matching without a second, separate action.
    if ext in POOL_DOC_EXTS and not entry.resume_path:
        _adopt_as_resume(entry, key, upload.filename)
    return key


def _adopt_as_resume(entry, key: str, filename: Optional[str]) -> None:
    entry.resume_path = key
    entry.filename = filename or entry.filename
    try:
        text, _ = extract_text_from_file(key)
        if text and text.strip():
            entry.resume_text = text
            entry.content_hash = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
    except Exception as e:
        logger.warning(f"Could not read text from {key}: {e}")


@router.post("/resume-pool/{entry_id}/attachments", response_model=AttachmentUploadResponse)
async def upload_pool_attachments(
    entry_id: str,
    files: List[UploadFile] = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Attach documents to a profile — CV, portfolio, a photographed resume.

    Images are stored and displayed but flagged unscoreable: the matcher does not run
    OCR, so scoring one would silently produce a meaningless zero.

    Anything that could not be stored comes back named, with the reason. Dropping a
    file the user deliberately picked without telling them is not acceptable.
    """
    entry = _get_pool_entry(db, entry_id, current_user)
    saved, rejected = 0, []

    for upload in files[:10]:
        reason = _reject_reason(upload)
        if reason:
            rejected.append(reason)
            continue
        try:
            _store_attachment(db, entry, upload, current_user)
            saved += 1
        except Exception as e:
            logger.error(f"Attachment upload failed for {entry_id}: {e}")
            rejected.append(f"{upload.filename} — could not be stored")

    if not saved:
        raise HTTPException(
            status_code=400,
            detail=("; ".join(rejected) if rejected
                    else "No usable files. Accepts PDF, DOC, DOCX, PNG or JPG up to 10 MB."),
        )

    try:
        db.commit()
        db.refresh(entry)
    except Exception as e:
        db.rollback()
        logger.error(f"Could not save attachments for {entry_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not save these files.")

    message = f"{saved} file{'s' if saved > 1 else ''} added"
    if rejected:
        message += f", {len(rejected)} skipped"

    history = _load_application_history(db, current_user.company_id, [entry])
    return AttachmentUploadResponse(
        entry=_entry_to_result(entry, history),
        rejected=rejected,
        message=message + ".",
    )


@router.put("/resume-pool/{entry_id}/attachments/{attachment_id}",
            response_model=AttachmentUploadResponse)
async def replace_pool_attachment(
    entry_id: str,
    attachment_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Swap the file behind an attachment, keeping its place on the profile.

    Replacing rather than delete-then-add matters when the file is the profile's scored
    resume: the new document takes over the match, and the old one is only removed from
    storage once the new one is safely saved.
    """
    from src.services.storage import storage

    entry = _get_pool_entry(db, entry_id, current_user)
    att = db.query(ResumePoolAttachment).filter(
        ResumePoolAttachment.id == attachment_id,
        ResumePoolAttachment.entry_id == entry_id,
        ResumePoolAttachment.company_id == current_user.company_id,
    ).first()
    if not att:
        raise HTTPException(status_code=404, detail="File not found")

    reason = _reject_reason(file)
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    old_path = att.file_path
    was_resume = bool(old_path) and entry.resume_path == old_path
    ext = os.path.splitext(file.filename or "")[1].lower()

    try:
        key = save_file_securely(file, RESUME_POOL_DIR)
    except Exception as e:
        logger.error(f"Could not store replacement for {attachment_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not store the new file.")

    att.file_path = key
    att.filename = file.filename or att.filename
    att.content_type = file.content_type
    att.size_bytes = getattr(file, "size", None)
    att.is_scoreable = ext in POOL_DOC_EXTS
    att.uploaded_by_id = current_user.id
    att.uploaded_at = datetime.utcnow()

    if was_resume:
        if ext in POOL_DOC_EXTS:
            _adopt_as_resume(entry, key, file.filename)
        else:
            # An image cannot carry the match, so the profile loses its scored resume
            # rather than silently keeping a path that no longer exists.
            entry.resume_path = None
            entry.resume_text = None
            entry.content_hash = None

    try:
        db.commit()
        db.refresh(entry)
    except Exception as e:
        db.rollback()
        logger.error(f"Could not replace attachment {attachment_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not replace this file.")

    # Only now the swap is committed, and only if we owned the old file.
    if old_path and old_path != key and old_path.startswith(RESUME_POOL_DIR):
        try:
            storage.delete(old_path)
        except Exception as e:
            logger.warning(f"Could not delete replaced file {old_path}: {e}")

    history = _load_application_history(db, current_user.company_id, [entry])
    return AttachmentUploadResponse(
        entry=_entry_to_result(entry, history),
        rejected=[],
        message=f"Replaced with {att.filename}.",
    )


@router.get("/resume-pool/{entry_id}/attachments/{attachment_id}")
async def download_pool_attachment(
    entry_id: str,
    attachment_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    from src.services.storage import storage, open_binary

    _get_pool_entry(db, entry_id, current_user)   # tenant check
    att = db.query(ResumePoolAttachment).filter(
        ResumePoolAttachment.id == attachment_id,
        ResumePoolAttachment.entry_id == entry_id,
        ResumePoolAttachment.company_id == current_user.company_id,
    ).first()
    if not att or not att.file_path or not storage.exists(att.file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return StreamingResponse(
        open_binary(att.file_path),
        media_type=att.content_type or "application/octet-stream",
        # Always a download: an uploaded .html/.svg shown inline would run script on
        # our origin. file_headers escapes the name (a " in it broke the header).
        headers={**file_headers(att.filename or "file", "application/octet-stream"),
                 "Content-Disposition": content_disposition(att.filename or "file", False)},
    )


@router.delete("/resume-pool/{entry_id}/attachments/{attachment_id}")
async def delete_pool_attachment(
    entry_id: str,
    attachment_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    from src.services.storage import storage

    entry = _get_pool_entry(db, entry_id, current_user)
    att = db.query(ResumePoolAttachment).filter(
        ResumePoolAttachment.id == attachment_id,
        ResumePoolAttachment.entry_id == entry_id,
        ResumePoolAttachment.company_id == current_user.company_id,
    ).first()
    if not att:
        raise HTTPException(status_code=404, detail="File not found")

    path = att.file_path
    # Detach it from the scored resume first, or the profile would point at a file
    # that no longer exists.
    if path and entry.resume_path == path:
        entry.resume_path = None
    db.delete(att)
    db.commit()

    # Only ever delete files the pool owns — an imported row can point at a candidate's
    # own resume, which belongs to their profile and past applications.
    if path and path.startswith(RESUME_POOL_DIR):
        try:
            storage.delete(path)
        except Exception as e:
            logger.warning(f"Could not delete attachment file {path}: {e}")

    return {"success": True, "message": "File removed"}


@router.post("/resume-pool/import-registered", response_model=ImportRegisteredResponse)
async def import_registered_candidates(
    payload: ImportRegisteredRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Pull everyone the company already knows into the talent pool.

    Two passes: people who registered on the portal, then candidates who have no user
    account at all (added by hand, or whose account was removed). Between them the pool
    covers every person in the database.

    Declared ABOVE /resume-pool/{entry_id} on purpose — FastAPI matches routes in
    declaration order and the path parameter would otherwise swallow this one.

    Runs in batches: scoring is one sentence-transformer pass per person per open
    job, so a whole tenant in a single request would time out. The response reports
    what is left so the caller can ask for the next batch.
    """
    if not current_user.company_id:
        raise HTTPException(
            status_code=400,
            detail="Your account isn't linked to a company, so there is nothing to import.",
        )

    try:
        counts = import_registered_users(
            db,
            company_id=current_user.company_id,
            actor_user_id=current_user.id,
            limit=payload.limit,
            refresh_existing=payload.refresh_existing,
        )
        if payload.include_orphans:
            # Spend whatever is left of the batch on account-less candidates.
            spent = counts["imported"] + counts["refreshed"] + counts["skipped"] + counts["failed"]
            budget = max(0, payload.limit - spent)
            if budget:
                orphans = import_orphan_candidates(
                    db,
                    company_id=current_user.company_id,
                    actor_user_id=current_user.id,
                    limit=budget,
                )
                for key in ("imported", "refreshed", "skipped", "failed", "scored",
                            "scoreless", "remaining"):
                    counts[key] += orphans[key]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Import registered candidates failed: {e}")
        raise HTTPException(status_code=500, detail="Could not import registered candidates.")

    handled = counts["imported"] + counts["refreshed"]
    if handled == 0 and counts["remaining"] == 0:
        message = "Everyone who registered is already in the pool."
    else:
        bits = [f"Added {counts['imported']}" if counts["imported"] else ""]
        if counts["refreshed"]:
            bits.append(f"refreshed {counts['refreshed']}")
        summary = ", ".join(b for b in bits if b) or "Nothing added"
        message = f"{summary}. {counts['scored']} ranked"
        if counts["scoreless"]:
            message += f", {counts['scoreless']} have nothing to score yet"
        message += "."
        if counts["remaining"]:
            message += f" {counts['remaining']} still to import."
        if counts["failed"]:
            message += f" {counts['failed']} failed — check the logs."

    return ImportRegisteredResponse(message=message, **counts)


@router.get("/resume-pool/{entry_id}", response_model=ResumeMatchResult)
async def get_resume_pool_entry(
    entry_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Full detail for one stored resume, including every scored job."""
    entry = _get_pool_entry(db, entry_id, current_user)
    result = _entry_to_result(entry)
    # Detail view shows the complete ranking, not just the top few alternates.
    try:
        matches = json.loads(entry.match_results) if entry.match_results else []
        result.alternates = [JobMatch(**m) for m in matches[1:] if isinstance(m, dict)]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return result


@router.get("/resume-pool/{entry_id}/resume")
async def download_pool_resume(
    entry_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Download the stored resume file."""
    from fastapi.responses import StreamingResponse
    from src.services.storage import storage, open_binary

    entry = _get_pool_entry(db, entry_id, current_user)
    if not entry.resume_path or not storage.exists(entry.resume_path):
        raise HTTPException(status_code=404, detail="Resume file is no longer available")

    ext = os.path.splitext(entry.resume_path)[1].lower()
    media_type = {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(ext, "application/octet-stream")

    return StreamingResponse(
        open_binary(entry.resume_path),
        media_type=media_type,
        headers={**file_headers(entry.filename or "resume" + ext, media_type),
                 "Content-Disposition": content_disposition(entry.filename or "resume" + ext, False)},
    )


@router.post("/resume-pool/{entry_id}/rematch", response_model=ResumeMatchResult)
async def rematch_pool_resume(
    entry_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Re-score a stored resume against the jobs that are active *now* — useful once
    new roles have been posted since the resume was uploaded."""
    entry = _get_pool_entry(db, entry_id, current_user)

    # An imported candidate may have had nothing to score at import time and filled in
    # their profile or uploaded a resume since. Re-match is the natural place to pick
    # that up, so re-run the import for just this person before giving up.
    if not entry.resume_text and entry.source == "registered" and entry.source_user_id:
        try:
            import_registered_users(
                db,
                company_id=current_user.company_id,
                actor_user_id=current_user.id,
                limit=1,
                refresh_existing=True,
                only_user_id=entry.source_user_id,
            )
            db.refresh(entry)
        except Exception as e:
            logger.warning(f"Re-match refresh failed for pool entry {entry_id}: {e}")

    if not entry.resume_text:
        raise HTTPException(
            status_code=400,
            detail="Nothing to score yet — this candidate has no resume or profile details.",
        )

    prepared_jobs = _prepare_active_jobs(db, current_user.company_id)
    if not prepared_jobs:
        raise HTTPException(status_code=400, detail="No active job postings to match against")

    ranked, extracted_skills, extracted_experience = _score_against_jobs(
        entry.resume_text, prepared_jobs, label=entry.filename or entry.id
    )
    if not ranked:
        raise HTTPException(status_code=500, detail="Scoring failed for this resume")

    entry.extracted_skills = json.dumps(extracted_skills) if extracted_skills else None
    entry.experience_years = _clamp_experience(extracted_experience)
    entry.best_job_id = ranked[0]["job_id"]
    entry.best_score = ranked[0]["ats_score"]
    entry.match_results = json.dumps(ranked)
    db.commit()
    db.refresh(entry)

    return _entry_to_result(entry)


@router.post("/resume-pool/{entry_id}/move-to-ats", response_model=Dict[str, Any])
async def move_pool_resume_to_ats(
    entry_id: str,
    payload: MoveToATSRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Promote a stored resume into a real Candidate.

    Mirrors the candidate-apply flow (Candidate + ATSResult + stage history) so a
    promoted resume moves through exactly the same pipeline as someone who applied
    through the portal. The candidate has no user account — HR manages them.
    """
    entry = _get_pool_entry(db, entry_id, current_user)

    # An entry may be promoted more than once: one strong resume can belong on several
    # open roles, and an imported registered candidate is usually ALREADY a Candidate
    # for the job they applied to themselves. The only thing worth blocking is a
    # duplicate on the SAME job, which the (job_id, email) guard below already does —
    # and that guard also catches "this person applied to this job through the portal".

    # Resolve target job — the caller's choice, else the stored best match.
    job_id = payload.job_id or entry.best_job_id
    if not job_id:
        raise HTTPException(status_code=400, detail="No job selected and no best match stored")

    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    email = str(payload.email).strip()

    # Same guard the apply flow uses — but report it as a conflict with context so HR
    # can see who is already in the pipeline rather than a bare failure.
    duplicate = db.query(Candidate).filter(
        Candidate.job_id == job_id,
        Candidate.email == email,
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"{duplicate.first_name} {duplicate.last_name} is already in the pipeline "
                   f"for {job.title} (stage: {duplicate.current_stage})",
        )

    # Score against the job as it is now, through the shared pipeline, so the new ATS
    # result carries the full breakdown, provenance and parse status. (This used to copy
    # the pool's stored match, which lacked the breakdown and could predate a JD edit.)
    inputs = build_inputs_for_candidate(db, user_id=getattr(entry, "source_user_id", None),
                                        resume_text=entry.resume_text or "")
    outcome = score_for_job(inputs, job)
    held = outcome.parse["status"] == "needs_review"
    ats_score = float(outcome.result.ats_score or 0)

    # company_id must be set or the candidate exists in MySQL but stays invisible
    # behind the tenant filter (see docs/DASHBOARD_DATA_PITFALLS.md).
    company_id = job.company_id or current_user.company_id

    candidate = Candidate(
        user_id=None,                       # HR-managed: no portal login is created
        company_id=company_id,
        job_id=job_id,
        email=email,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        phone=(payload.phone or entry.phone or None),
        resume_path=entry.resume_path,
        resume_text=entry.resume_text,
        extracted_skills=entry.extracted_skills,
        # Candidate.experience_years is an Integer while the pool keeps one decimal.
        experience_years=int(round(float(entry.experience_years))) if entry.experience_years is not None else None,
        ats_score=ats_score,
        **from_pool_channel(entry.source_channel),
    )
    db.add(candidate)
    db.flush()                              # need candidate.id for the child rows

    persist_ats_result(db, candidate=candidate, job=job, outcome=outcome,
                       event_type="scored", actor_id=current_user.id)

    # Same stage progression the apply flow writes, so the audit trail is identical.
    db.add(CandidateStageHistory(
        company_id=company_id, candidate_id=candidate.id,
        from_stage=None, to_stage="applied", changed_by_id=current_user.id,
        notes=f"Added from Resume Matcher pool by {current_user.email}",
    ))
    candidate.current_stage = "ats_screening"
    db.add(CandidateStageHistory(
        company_id=company_id, candidate_id=candidate.id,
        from_stage="applied", to_stage="ats_screening", changed_by_id=current_user.id,
        notes=f"ATS screening completed automatically. Score: {ats_score}/100",
    ))
    if held:
        candidate.current_stage = "hr_review"
        db.add(CandidateStageHistory(
            company_id=company_id, candidate_id=candidate.id,
            from_stage="ats_screening", to_stage="hr_review", changed_by_id=current_user.id,
            notes="Resume could not be read reliably, so it was not ranked: manual review.",
        ))
    elif ats_score >= ATS_REVIEW_THRESHOLD:
        candidate.current_stage = "hr_review"
        db.add(CandidateStageHistory(
            company_id=company_id, candidate_id=candidate.id,
            from_stage="ats_screening", to_stage="hr_review", changed_by_id=current_user.id,
            notes=f"Auto-advanced: ATS score {ats_score} meets the review threshold",
        ))

    entry.status = "moved"
    entry.moved_candidate_id = candidate.id

    try:
        db.commit()
        db.refresh(candidate)
    except Exception as e:
        db.rollback()
        logger.error(f"Move to ATS failed for pool entry {entry_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not move this resume to the ATS")

    return {
        "success": True,
        "candidate_id": candidate.id,
        "job_id": job_id,
        "job_title": job.title,
        "ats_score": ats_score,
        "resume_needs_review": held,
        "current_stage": candidate.current_stage,
        "message": f"{candidate.first_name} {candidate.last_name} added to {job.title}",
    }


@router.delete("/resume-pool/{entry_id}")
async def delete_pool_resume(
    entry_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Remove a stored resume and its file."""
    from src.services.storage import storage

    entry = _get_pool_entry(db, entry_id, current_user)
    path = entry.resume_path
    promoted = entry.moved_candidate_id
    # Only files the pool itself saved live under RESUME_POOL_DIR. A row imported from
    # a registered candidate points at THEIR file (resumes/<uuid> from an application,
    # or uploads/resumes/<uid>_<hex> from their profile) — deleting that would destroy
    # the resume their profile and every past application still serve.
    owned = bool(path) and path.startswith(RESUME_POOL_DIR)

    db.delete(entry)
    db.commit()

    # Drop the file only when we own it and no Candidate is still pointing at it.
    if owned and not promoted:
        try:
            storage.delete(path)
        except Exception as e:
            logger.warning(f"Could not delete pooled resume file {path}: {e}")

    return {"success": True, "message": "Resume removed from the pool"}


@router.get("/jobs/{job_id}", response_model=JobPostingResponse)
async def get_job_details(
    job_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific job posting"""
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check permissions
    if current_user.role != "admin":
        hr_profile = get_hr_profile(db, current_user)
        if not can_access_job(db, job, hr_profile):
            raise HTTPException(status_code=403, detail="Access denied")
        return create_job_response(job, viewer=current_user, viewer_hr_id=hr_profile.id)

    return create_job_response(job, viewer=current_user)


# IMPORTANT: Specific sub-path routes MUST come before generic {job_id} catch-all
# Otherwise FastAPI treats "approve" / "assign-interviewer" as the job_id parameter

@router.put("/jobs/{job_id}/approve")
async def approve_job(
    job_id: str,
    approval: JobApprovalRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """HR approves a draft job — requires salary details before making it visible to candidates"""
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_team(db, job, current_user)
    if job.status == "active":
        raise HTTPException(status_code=400, detail="Job is already active")
    if job.status == "closed":
        raise HTTPException(status_code=400, detail="Cannot approve a closed job")
    if approval.salary_min > approval.salary_max:
        raise HTTPException(status_code=400, detail="Minimum salary cannot exceed maximum salary")

    # Keep what the job said before approval as a version, if it was never scored.
    ensure_jd_version(db, job, current_user.id)
    # Update job with HR-provided details
    job.salary_min = approval.salary_min
    job.salary_max = approval.salary_max
    if approval.location:
        job.location = approval.location
    if approval.employment_type:
        job.employment_type = approval.employment_type
    if approval.description:
        job.description = approval.description
    if approval.requirements:
        job.requirements = approval.requirements
    if approval.required_skills is not None:
        job.required_skills = json.dumps(approval.required_skills)
    if approval.preferred_skills is not None:
        job.preferred_skills = _preferred_json(approval.preferred_skills,
                                               approval.required_skills or _parse_skills(job.required_skills))
    job.status = "active"
    job.updated_at = datetime.utcnow()
    ensure_jd_version(db, job, current_user.id)

    # Notify the interviewer who created the job
    if job.created_by_id:
        notif = Notification(
            user_id=job.created_by_id,
            title="Job Approved & Live",
            message=f'Your job "{job.title}" has been approved by HR and is now visible to candidates.',
            type="job_approved",
            reference_type="job",
            reference_id=str(job.id)
        )
        db.add(notif)

    db.commit()
    return {"message": f"Job '{job.title}' approved and now active", "job_id": str(job.id), "status": "active"}


@router.put("/jobs/{job_id}/assign-interviewer")
async def assign_interviewer_to_job(
    job_id: str,
    interviewer_id: str = Query(..., description="Interviewer profile ID"),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Assign an interviewer to a job posting"""
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_team(db, job, current_user)
    interviewer = active_interviewer_query(db, current_user.company_id).filter(
        InterviewerProfile.id == interviewer_id,
        User.company_id == current_user.company_id
    ).first()
    if not interviewer:
        # Distinguish "no such interviewer" from "deactivated" — HR needs to know which,
        # and a 404 on a name they can still see in a stale tab is baffling.
        deactivated = db.query(InterviewerProfile).join(
            User, InterviewerProfile.user_id == User.id
        ).filter(
            InterviewerProfile.id == interviewer_id,
            User.company_id == current_user.company_id
        ).first()
        if deactivated:
            raise HTTPException(
                status_code=400,
                detail="That interviewer has been deactivated and cannot be assigned."
            )
        raise HTTPException(status_code=404, detail="Interviewer not found")
    job.interviewer_id = interviewer_id
    job.updated_at = datetime.utcnow()
    db.commit()
    return {
        "message": "Interviewer assigned successfully",
        "interviewer_name": f"{interviewer.user.first_name} {interviewer.user.last_name}",
        "interviewer_email": interviewer.user.email
    }


@router.put("/jobs/{job_id}/assign-hr")
async def assign_hr_to_job(
    job_id: str,
    hr_id: str = Query(..., description="HR profile ID to assign"),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Assign or reassign an HR to a job posting.

    Kept for existing callers; POST /jobs/{job_id}/transfer is the richer form. Both go
    through transfer_job so offers, interviews, history and notifications move together.
    """
    job = _company_job_or_404(db, job_id, current_user)
    _require_job_owner_or_admin(db, job, current_user)
    hr_profile = _assignable_hr_or_error(db, hr_id, current_user)

    # This endpoint always treated re-assigning the current owner as a harmless no-op.
    if str(job.hr_id or "") != str(hr_profile.id):
        transfer_job(db, job=job, to_hr=hr_profile, actor=current_user)
        db.commit()

    return {
        "message": "HR assigned to job successfully",
        "job_id": job_id,
        "job_title": job.title,
        "hr_name": f"{hr_profile.user.first_name} {hr_profile.user.last_name}",
        "hr_email": hr_profile.user.email
    }


def _company_job_or_404(db: Session, job_id: str, current_user: User) -> JobPosting:
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _require_job_owner_or_admin(db: Session, job: JobPosting, current_user: User) -> None:
    """Managing a job's HR team, transferring it and permanently deleting it are the
    owner's calls. Co-HRs get full day-to-day access, but not these."""
    if current_user.role == "admin":
        return
    # A job nobody owns yet (e.g. admin-created, hr_id NULL) has no owner to defer to;
    # it stays open to any HR in the company, as it was before co-HRs existed.
    if not job.hr_id:
        return
    if not is_job_owner(job, get_hr_profile(db, current_user)):
        raise HTTPException(status_code=403,
                            detail="Only the job's owner HR or an admin can do this.")


def _require_job_team(db: Session, job: Optional[JobPosting], current_user: User) -> None:
    """Day-to-day work on a job — editing it, inviting, scheduling, offering — is for
    its team: the owner HR and co-HRs, or an admin. Being in the same company is not
    enough; the HR job list is already scoped this way (job_access_filter), and the
    actions behind it now are too.

    Same exception as _require_job_owner_or_admin: a job nobody owns yet (hr_id NULL,
    e.g. an interviewer's draft awaiting approval) stays open to any HR in the company.
    Callers pass a job already scoped to the caller's company.
    """
    if current_user.role == "admin" or job is None or not job.hr_id:
        return
    if not can_access_job(db, job, get_hr_profile(db, current_user)):
        raise HTTPException(status_code=403,
                            detail="You are not on this job's HR team.")


def _assignable_hr_or_error(db: Session, hr_id: str, current_user: User) -> HRProfile:
    """An ACTIVE HR profile in the caller's company, or the right HTTP error."""
    hr_profile = active_hr_query(db, current_user.company_id).filter(
        HRProfile.id == hr_id,
        User.company_id == current_user.company_id
    ).first()
    if not hr_profile:
        # Same split as assign_interviewer_to_job: a deactivated person is a different
        # problem from a bad id, and HR needs to be told which.
        deactivated = db.query(HRProfile).join(User, HRProfile.user_id == User.id).filter(
            HRProfile.id == hr_id,
            User.company_id == current_user.company_id
        ).first()
        if deactivated:
            raise HTTPException(
                status_code=400,
                detail="That HR user has been deactivated and cannot be assigned."
            )
        raise HTTPException(status_code=404, detail="HR profile not found")
    return hr_profile


@router.post("/jobs/transfer-bulk")
async def transfer_jobs_bulk(
    body: BulkJobTransferRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Hand every job one HR owns over to another HR in a single transaction.

    Defaults to the caller's own jobs, excluding closed ones. `from_hr_id` may name a
    deactivated HR — clearing out a departed colleague's jobs is the main reason to use it.
    """
    to_hr = _assignable_hr_or_error(db, body.to_hr_id, current_user)

    if (body.from_hr_id and current_user.role != "admin"
            and str(body.from_hr_id) != str(get_hr_profile(db, current_user).id)):
        raise HTTPException(status_code=403, detail="Only an admin can transfer another HR's jobs.")
    if body.from_hr_id:
        from_hr = db.query(HRProfile).join(User, HRProfile.user_id == User.id).filter(
            HRProfile.id == body.from_hr_id,
            User.company_id == current_user.company_id
        ).first()
        if not from_hr:
            raise HTTPException(status_code=404, detail="Source HR profile not found")
    else:
        from_hr = get_hr_profile(db, current_user)

    if str(from_hr.id) == str(to_hr.id):
        raise HTTPException(status_code=400, detail="Pick a different HR to transfer the jobs to.")

    q = db.query(JobPosting).filter(
        JobPosting.company_id == current_user.company_id,
        JobPosting.hr_id == from_hr.id,
    )
    if body.statuses:
        q = q.filter(JobPosting.status.in_(body.statuses))
    else:
        q = q.filter(or_(JobPosting.status.is_(None), JobPosting.status != "closed"))
    jobs = q.order_by(JobPosting.created_at.desc()).all()

    if not jobs:
        return {"message": "No jobs matched — nothing was transferred.", "transferred": 0, "jobs": []}

    moved = [
        transfer_job(db, job=j, to_hr=to_hr, actor=current_user,
                     reason=body.reason, bulk=True, notify=False)
        for j in jobs
    ]

    # One summary per person instead of one bell entry per job.
    actor_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip() or current_user.email
    to_name = hr_display_name(to_hr)
    reason = (body.reason or "").strip()
    suffix = f" Reason: {reason}" if reason else ""
    titles = ", ".join(m["job_title"] for m in moved[:5]) + (" …" if len(moved) > 5 else "")
    if to_hr.user_id != current_user.id:
        create_notification(
            db, user_id=to_hr.user_id, company_id=current_user.company_id,
            title=f"{len(moved)} job(s) transferred to you",
            message=f"{actor_name} transferred {len(moved)} job(s) to you: {titles}.{suffix}",
            type="job_transferred", reference_type="job", reference_id=moved[0]["job_id"],
            commit=False,
        )
    if from_hr.user_id != current_user.id:
        create_notification(
            db, user_id=from_hr.user_id, company_id=current_user.company_id,
            title=f"{len(moved)} job(s) transferred away",
            message=f"{actor_name} transferred {len(moved)} of your job(s) to {to_name}: {titles}.{suffix}",
            type="job_transferred", reference_type="job", reference_id=moved[0]["job_id"],
            commit=False,
        )

    db.commit()
    return {
        "message": f"Transferred {len(moved)} job(s) to {to_name}",
        "transferred": len(moved),
        "jobs": moved,
    }


@router.post("/jobs/{job_id}/transfer")
async def transfer_job_to_hr(
    job_id: str,
    body: JobTransferRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Hand a job over to another HR. Its candidates, interviews and offers go with it,
    both HRs are notified, and the move is recorded in the job's transfer history."""
    job = _company_job_or_404(db, job_id, current_user)
    _require_job_owner_or_admin(db, job, current_user)
    to_hr = _assignable_hr_or_error(db, body.to_hr_id, current_user)

    try:
        result = transfer_job(db, job=job, to_hr=to_hr, actor=current_user, reason=body.reason)
    except JobTransferError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()

    return {"message": f"'{job.title}' transferred to {result['to_hr_name']}", **result}


@router.get("/jobs/{job_id}/transfer-history")
async def get_job_transfer_history(
    job_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Every hand-over of this job, newest first."""
    from src.models.models import JobTransferHistory

    _company_job_or_404(db, job_id, current_user)
    rows = db.query(JobTransferHistory).filter(
        JobTransferHistory.job_id == job_id,
        JobTransferHistory.company_id == current_user.company_id,
    ).order_by(JobTransferHistory.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "from_hr_name": r.from_hr_name,
            "to_hr_name": r.to_hr_name,
            "transferred_by_email": r.transferred_by_email,
            "reason": r.reason,
            "offers_moved": r.offers_moved or 0,
            "interviews_moved": r.interviews_moved or 0,
            "bulk": bool(r.bulk),
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ==================== CO-HRs (shared jobs) ====================
# A co-HR works the SAME job as its owner — nothing is copied. The owner or an admin
# manages the team; a co-HR may only remove themselves.

def _hr_person(profile: Optional[HRProfile]) -> Optional[Dict[str, Any]]:
    if not profile or not profile.user:
        return None
    return {"hr_id": str(profile.id), "name": hr_display_name(profile), "email": profile.user.email}


@router.get("/jobs/{job_id}/collaborators")
async def get_job_collaborators(
    job_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """The job's HR team: its owner and its co-HRs."""
    job = _company_job_or_404(db, job_id, current_user)
    if current_user.role != "admin" and not can_access_job(db, job, get_hr_profile(db, current_user)):
        raise HTTPException(status_code=403, detail="Access denied")

    rows = (db.query(JobHRCollaborator)
            .filter(JobHRCollaborator.job_id == job.id)
            .order_by(JobHRCollaborator.created_at).all())
    return {
        "owner": _hr_person(job.hr) if job.hr_id else None,
        "collaborators": [
            {**(_hr_person(r.hr) or {"hr_id": str(r.hr_id), "name": None, "email": None}),
             "added_by_email": r.added_by.email if r.added_by else None,
             "created_at": r.created_at}
            for r in rows
        ],
    }


@router.post("/jobs/{job_id}/collaborators")
async def add_job_collaborator(
    job_id: str,
    body: AddJobCollaboratorRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Give another HR access to this job. The job is shared, not duplicated."""
    from sqlalchemy.exc import IntegrityError

    job = _company_job_or_404(db, job_id, current_user)
    _require_job_owner_or_admin(db, job, current_user)
    hr_profile = _assignable_hr_or_error(db, body.hr_id, current_user)

    if is_job_owner(job, hr_profile):
        raise HTTPException(status_code=400, detail="That HR already owns this job.")
    if is_collaborator(db, job, hr_profile):
        raise HTTPException(status_code=409, detail="That HR is already on this job.")

    db.add(JobHRCollaborator(
        company_id=job.company_id or current_user.company_id,
        job_id=job.id,
        hr_id=hr_profile.id,
        added_by_id=current_user.id,
    ))
    actor_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip() or current_user.email
    if hr_profile.user_id != current_user.id:
        create_notification(
            db, user_id=hr_profile.user_id, company_id=job.company_id,
            title="Job shared with you",
            message=f"{actor_name} added you as an HR on '{job.title}'. You now have full access to it.",
            type="job_shared", reference_type="job", reference_id=job.id, commit=False,
        )
    try:
        db.commit()
    except IntegrityError:
        # Two adds raced past the check above; the unique constraint kept it to one row.
        db.rollback()
        raise HTTPException(status_code=409, detail="That HR is already on this job.")

    return {"message": f"{hr_display_name(hr_profile)} now has access to '{job.title}'",
            **(_hr_person(hr_profile) or {})}


@router.delete("/jobs/{job_id}/collaborators/{hr_id}")
async def remove_job_collaborator(
    job_id: str,
    hr_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Take a co-HR off the job. The owner or an admin can remove anyone; a co-HR can
    remove only themselves (leave the job)."""
    job = _company_job_or_404(db, job_id, current_user)
    me = get_hr_profile(db, current_user) if current_user.role != "admin" else None
    leaving = me is not None and str(me.id) == str(hr_id)
    if not leaving:
        _require_job_owner_or_admin(db, job, current_user)

    row = db.query(JobHRCollaborator).filter(
        JobHRCollaborator.job_id == job.id,
        JobHRCollaborator.hr_id == hr_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="That HR is not a co-HR on this job.")

    removed = row.hr
    db.delete(row)
    if not leaving and removed and removed.user_id != current_user.id:
        actor_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip() or current_user.email
        create_notification(
            db, user_id=removed.user_id, company_id=job.company_id,
            title="Removed from job",
            message=f"{actor_name} removed you from '{job.title}'.",
            type="job_shared", reference_type="job", reference_id=job.id, commit=False,
        )
    db.commit()
    return {"message": "You left the job" if leaving
            else f"{hr_display_name(removed) or 'HR'} removed from '{job.title}'"}


@router.put("/jobs/{job_id}")
async def update_job(
    job_id: str,
    job_data: JobPostingCreate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Update a job posting"""
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_team(db, job, current_user)
    # Snapshot the job as it was, then again after the edit. A new version appears only
    # when something scoring reads changed, and scores measured against the old one are
    # then shown as "scored against an earlier job description".
    ensure_jd_version(db, job, current_user.id)
    job.title = job_data.title
    job.description = job_data.description
    job.requirements = job_data.requirements
    job.department_id = job_data.department_id
    job.location = job_data.location
    job.employment_type = job_data.employment_type
    job.salary_min = job_data.salary_min
    job.salary_max = job_data.salary_max
    job.experience_years = job_data.experience_years
    job.required_skills = json.dumps(job_data.required_skills)
    if job_data.preferred_skills is not None:
        job.preferred_skills = _preferred_json(job_data.preferred_skills, job_data.required_skills)
    job.updated_at = datetime.utcnow()
    ensure_jd_version(db, job, current_user.id)
    db.commit()
    return create_job_response(job)


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Delete a job posting (sets status to closed)"""
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_team(db, job, current_user)
    job.status = "closed"
    job.updated_at = datetime.utcnow()
    db.commit()

    # Return this job's candidates to the talent pool so HR can resurface them onto
    # another role. They stay in this job's pipeline as well — a close is reversible, and
    # removing them would make reopening lossy.
    #
    # Committed AFTER the close, in its own transaction, on purpose: rolling back a pool
    # failure must not also undo the close the user asked for.
    try:
        from src.services.resume_pool_import import return_candidates_to_pool
        candidates = db.query(Candidate).filter(Candidate.job_id == job_id).all()
        if candidates:
            return_candidates_to_pool(db, job=job, candidates=candidates,
                                      actor_user_id=current_user.id)
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("Talent pool sync skipped when closing job %s: %s", job_id, e)

    return {"message": "Job closed successfully"}


@router.put("/jobs/{job_id}/reopen")
async def reopen_job(
    job_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Reopen a soft-closed job posting (sets status back to active).

    The counterpart to the soft close above. Without this a close is permanent —
    `update_job` never writes `status`, and `approve_job` refuses a closed job.

    Reopening always lands on `active`: the column keeps no history, so a job that
    was closed while still a draft comes back published. The `!= "closed"` guard
    keeps this from being a general-purpose status setter."""
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_team(db, job, current_user)
    if job.status != "closed":
        raise HTTPException(status_code=400, detail="Only a closed job can be reopened")
    job.status = "active"
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "Job reopened successfully"}


@router.delete("/jobs/{job_id}/permanent")
async def delete_job_permanent(
    job_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Permanently delete a job posting AND all of its dependent data — candidates,
    ATS results, interviews + feedback, questions, code/proctoring data and offers.
    Irreversible. FK-safe: children are removed bottom-up in a single transaction
    before the job row itself, so a missing table rolls the whole thing back cleanly.
    (The plain DELETE /jobs/{id} above is only a soft close — status='closed'.)

    What survives, by design: the PEOPLE. Step 0 copies every candidate back to the
    talent pool, and that snapshot now also carries each completed interview — the
    colleagues who conducted it and what they recommended. Deleting a job used to erase
    who interviewed whom, because Interview and InterviewFeedback are the only place
    that is recorded; the pool entry is now the surviving copy.
    """
    from src.models.models import (
        JobDeletionArchive, ProctoringSession, ProctoringTranscript, ProctoringViolation,
        ProctoringReview, AssessmentExplanation,
    )
    from src.models.question_models import (
        Question as InterviewQuestion, QuestionSetAssignment,
        CandidateQuestionHistory, CommunicationAnswer,
    )
    from src.models.judge_models import InterviewCodeSubmission

    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_owner_or_admin(db, job, current_user)

    # ---- Collect every dependent id up front ----
    cand_ids = [r[0] for r in db.query(Candidate.id).filter(Candidate.job_id == job_id).all()]
    iv_cond = Interview.job_id == job_id
    if cand_ids:
        iv_cond = or_(iv_cond, Interview.candidate_id.in_(cand_ids))
    interview_ids = [r[0] for r in db.query(Interview.id).filter(iv_cond).all()]
    question_ids = [r[0] for r in db.query(InterviewQuestion.id).filter(
        InterviewQuestion.interview_id.in_(interview_ids)).all()] if interview_ids else []
    session_ids = [r[0] for r in db.query(ProctoringSession.id).filter(
        ProctoringSession.test_id.in_(interview_ids)).all()] if interview_ids else []

    def bulk_delete(model, *conditions):
        conds = [c for c in conditions if c is not None]
        if conds:
            db.query(model).filter(or_(*conds)).delete(synchronize_session=False)

    # 0a) Snapshot the TESTS written for this job and who wrote them.
    # Templates have no candidate, so unlike a completed interview they cannot ride along
    # on a pool entry — without this row the author of a job's test is simply erased.
    # Read before any delete runs, for the same reason step 0 is ordered first.
    template_rows = db.query(Interview).filter(
        Interview.job_id == job_id,
        Interview.candidate_id.is_(None),
    ).all() if job_id else []
    tests_snapshot = []
    for t in template_rows:
        author = (t.interviewer_name or "").strip()
        tests_snapshot.append({
            "interview_id": t.id,
            "title": t.title,
            "interview_type": t.interview_type,
            "status": t.status,
            "author_name": author or None,
            "author_email": (t.interviewer_email or "").strip() or None,
            # created_by is the account that actually saved it; interviewer_name can be a
            # display value typed into the wizard, so both are kept.
            "created_by": t.created_by,
            "question_count": db.query(InterviewQuestion).filter(
                InterviewQuestion.interview_id == t.id).count(),
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    archived_interview_count = 0
    _in_iv = interview_ids or None
    _in_cand = cand_ids or None
    _in_q = question_ids or None

    # 0) Preserve the people BEFORE anything is destroyed.
    # This has to run first, not just before the candidates are deleted: it snapshots
    # each application's ATS score, and ATSResult is removed in step 4 below. It also
    # needs job.title, which step 5 destroys. For an HR-created candidate (no portal
    # account — everyone promoted via Move to ATS) the Candidate row is the only copy of
    # their name, email, phone, skills and resume pointer, so losing this loses them.
    # Deliberately NOT wrapped in try/except: if the pool copy fails, the delete must
    # roll back and leave the job intact rather than destroy what it could not preserve.
    if _in_cand:
        from src.services.resume_pool_import import return_candidates_to_pool
        return_candidates_to_pool(
            db,
            job=job,
            candidates=db.query(Candidate).filter(Candidate.id.in_(cand_ids)).all(),
            actor_user_id=current_user.id,
            archive_applications=True,
        )
        archived_interview_count = db.query(Interview).filter(
            Interview.candidate_id.in_(cand_ids),
            func.upper(Interview.status) == "COMPLETED",
        ).count()

    # 1) Proctoring (deepest — references interviews via test_id)
    if session_ids:
        # Reviews reference both sessions and violations, so they go first.
        bulk_delete(ProctoringReview, ProctoringReview.session_id.in_(session_ids))
        bulk_delete(ProctoringViolation, ProctoringViolation.session_id.in_(session_ids))
        bulk_delete(ProctoringTranscript, ProctoringTranscript.session_id.in_(session_ids))
        bulk_delete(ProctoringSession, ProctoringSession.id.in_(session_ids))

    # 2) Rows referencing interviews / interview_questions / candidates (leaf level)
    bulk_delete(InterviewFeedback, InterviewFeedback.interview_id.in_(interview_ids) if _in_iv else None)
    bulk_delete(AssessmentExplanation,
                AssessmentExplanation.interview_id.in_(interview_ids) if _in_iv else None)
    bulk_delete(QuestionSetAssignment,
                QuestionSetAssignment.interview_id.in_(interview_ids) if _in_iv else None,
                QuestionSetAssignment.question_id.in_(question_ids) if _in_q else None)
    bulk_delete(InterviewCodeSubmission,
                InterviewCodeSubmission.interview_id.in_(interview_ids) if _in_iv else None,
                InterviewCodeSubmission.question_id.in_(question_ids) if _in_q else None)
    bulk_delete(CommunicationAnswer,
                CommunicationAnswer.interview_id.in_(interview_ids) if _in_iv else None,
                CommunicationAnswer.candidate_id.in_(cand_ids) if _in_cand else None,
                CommunicationAnswer.question_id.in_(question_ids) if _in_q else None)
    bulk_delete(CandidateQuestionHistory,
                CandidateQuestionHistory.interview_id.in_(interview_ids) if _in_iv else None,
                CandidateQuestionHistory.candidate_id.in_(cand_ids) if _in_cand else None,
                CandidateQuestionHistory.source_question_id.in_(question_ids) if _in_q else None)

    # 3) Interview questions (now unreferenced), then interviews
    bulk_delete(InterviewQuestion, InterviewQuestion.id.in_(question_ids) if _in_q else None)
    # A retest points at the attempt it replaced. InnoDB checks that self-reference row by
    # row inside one DELETE, so unlink the attempts first or the delete can fail midway.
    if _in_iv:
        db.query(Interview).filter(Interview.id.in_(interview_ids)).update(
            {Interview.previous_attempt_id: None}, synchronize_session=False)
    bulk_delete(Interview, Interview.id.in_(interview_ids) if _in_iv else None,
                Interview.job_id == job_id)

    # 4) Candidate-scoped and job-scoped records
    bulk_delete(CandidateStageHistory, CandidateStageHistory.candidate_id.in_(cand_ids) if _in_cand else None)
    bulk_delete(ATSResult,
                ATSResult.candidate_id.in_(cand_ids) if _in_cand else None,
                ATSResult.job_id == job_id)
    bulk_delete(Offer,
                Offer.candidate_id.in_(cand_ids) if _in_cand else None,
                Offer.job_id == job_id)
    # 4a) Talent-pool pointers, BEFORE the candidates they reference.
    # resume_pool rows are independent records — an uploaded CV outlives the job it
    # happened to match — so clear the pointers rather than deleting the rows. All
    # three columns are nullable. Skipping this is what made the whole delete fail
    # with MySQL 1451 on resume_pool_ibfk_*, rolling back the entire transaction.
    if _in_cand:
        # Reset the lane first, while moved_candidate_id still identifies the rows.
        # The Candidate this entry produced is about to be destroyed, so the entry is
        # no longer promoted — return it to the pool for HR to move onto another job.
        # Only 'moved' is rewound; an 'archived' row stays archived.
        db.query(ResumePoolEntry).filter(
            ResumePoolEntry.moved_candidate_id.in_(cand_ids),
            ResumePoolEntry.status == "moved",
        ).update({ResumePoolEntry.status: "new"}, synchronize_session=False)
        db.query(ResumePoolEntry).filter(
            ResumePoolEntry.moved_candidate_id.in_(cand_ids)
        ).update({ResumePoolEntry.moved_candidate_id: None}, synchronize_session=False)
        db.query(ResumePoolEntry).filter(
            ResumePoolEntry.source_candidate_id.in_(cand_ids)
        ).update({ResumePoolEntry.source_candidate_id: None}, synchronize_session=False)
    db.query(ResumePoolEntry).filter(
        ResumePoolEntry.best_job_id == job_id
    ).update({ResumePoolEntry.best_job_id: None}, synchronize_session=False)

    bulk_delete(Candidate, Candidate.id.in_(cand_ids) if _in_cand else None)
    bulk_delete(CandidateRequest, CandidateRequest.job_id == job_id)
    bulk_delete(JobHRCollaborator, JobHRCollaborator.job_id == job_id)

    # 5) The archive row, then the job itself. Written last so it only exists if every
    # destructive step above succeeded — a rolled-back delete must not leave a record
    # claiming a job was removed.
    title = job.title
    db.add(JobDeletionArchive(
        company_id=job.company_id or current_user.company_id,
        job_id=str(job.id),
        job_title=title,
        department_name=job.department.name if job.department else None,
        deleted_by_id=current_user.id,
        deleted_by_email=current_user.email,
        candidates_archived=len(cand_ids),
        interviews_archived=archived_interview_count,
        tests=json.dumps(tests_snapshot) if tests_snapshot else None,
    ))
    db.delete(job)
    db.commit()
    return {
        "message": (
            f"Job '{title}' was permanently deleted. "
            f"{len(cand_ids)} candidate(s) and their completed interview records were "
            f"kept in the talent pool; {len(tests_snapshot)} test(s) and their authors "
            "are listed under Deleted Jobs."
        ),
        "deleted_candidates": len(cand_ids),
        "deleted_interviews": len(interview_ids),
        "archived_candidates": len(cand_ids),
        "archived_interviews": archived_interview_count,
        "archived_tests": len(tests_snapshot),
    }


@router.get("/deleted-jobs")
async def list_deleted_jobs(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Jobs that were permanently deleted, and the tests written for them.

    The counterpart to DELETE /jobs/{id}/permanent. Candidates and their completed
    interviews survive in the talent pool; a test template has no candidate to attach to,
    so this is the only surviving record of who authored a deleted job's test.
    """
    from src.models.models import JobDeletionArchive

    q = db.query(JobDeletionArchive)
    if current_user.company_id:
        q = q.filter(JobDeletionArchive.company_id == current_user.company_id)

    total = q.count()
    rows = (q.order_by(JobDeletionArchive.created_at.desc())
             .offset(skip).limit(limit).all())

    out = []
    for r in rows:
        try:
            tests = json.loads(r.tests) if r.tests else []
        except (json.JSONDecodeError, TypeError, ValueError):
            tests = []
        out.append({
            "id": str(r.id),
            "job_id": r.job_id,
            "job_title": r.job_title,
            "department": r.department_name,
            "deleted_by": r.deleted_by_email,
            "deleted_at": r.created_at,
            "candidates_archived": r.candidates_archived or 0,
            "interviews_archived": r.interviews_archived or 0,
            "tests": tests,
        })
    return {"total": total, "results": out}



@router.get("/hr-profiles")
async def get_available_hr_profiles(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get all available HR profiles for assignment"""
    # Active only: this is an assignment picker, and a deactivated HR cannot log in.
    profiles = active_hr_query(db, current_user.company_id).all()
    return [
        {
            "id": str(p.id),
            "name": f"{p.user.first_name} {p.user.last_name}",
            "email": p.user.email,
            "department": p.department.name if p.department else "—",
            "employee_id": p.employee_id
        }
        for p in profiles if p.user
    ]


@router.get("/interviewers")
async def get_available_interviewers(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get all available interviewers for assignment"""
    # Active only. This feeds HR's "Assign Interviewer" dropdown and the panel picker,
    # so listing a deactivated user here is precisely how they get re-assigned.
    profiles = active_interviewer_query(db, current_user.company_id).all()
    return [
        {
            "id": str(p.id),
            "name": f"{p.user.first_name} {p.user.last_name}",
            "email": p.user.email,
            "department": p.department.name if p.department else "—",
            "expertise": json.loads(p.expertise_areas) if isinstance(p.expertise_areas, str) and p.expertise_areas else [],
            "experience_years": p.years_experience
        }
        for p in profiles if p.user
    ]


@router.get("/panel-staff")
async def get_panel_staff(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Active HR/Admin users of the company, selectable as ad-hoc panel members.

    Interviewers (with a profile) come from /hr/interviewers; this fills the gap so
    HR can also put an HR/Admin colleague on a panel without an interviewer account.
    """
    q = db.query(User).filter(
        User.role.in_(["hr", "admin"]),
        User.is_active == True,  # noqa: E712
    )
    if current_user.company_id:
        q = q.filter(User.company_id == current_user.company_id)
    return [
        {
            "name": f"{u.first_name} {u.last_name}".strip() or u.email,
            "email": u.email,
            "role": u.role,
        }
        for u in q.all()
    ]


class ProctoringOverride(BaseModel):
    """Per-send proctoring override from the ATS "Review Emails" modal.

    Every field is optional: only the ones explicitly set are applied on top of the
    template's settings, and an absent block means "use the template as-is". The
    override lands on the per-candidate clones only — the interviewer's source test
    is never modified.
    """
    enable_webcam_monitoring: Optional[bool] = None
    enable_tab_switch_detection: Optional[bool] = None
    require_fullscreen: Optional[bool] = None
    copy_paste_allowed: Optional[bool] = None


class SendTestInviteRequest(BaseModel):
    candidate_ids: List[str]
    test_deadline: str
    test_id: Optional[int] = None
    test_link: Optional[str] = None
    reject_others: bool = True
    invite_subject: Optional[str] = None
    invite_body: Optional[str] = None
    reject_subject: Optional[str] = None
    reject_body: Optional[str] = None
    # "Send from" overrides for this batch only. Omitted = use the mailbox
    # configured for the stage (Pipeline Notifications), then the company default.
    # The invite and the auto-rejection are separate steps, so each picks its own.
    mailbox_id: Optional[str] = None
    reject_mailbox_id: Optional[str] = None
    # Two-round test: when True, this invite is Round 1 (MCQ only); coding becomes
    # Round 2, sent later via /send-round2-invite after HR reviews Round 1 scores.
    two_round: bool = False
    # Optional proctoring override applied to this batch's clones only (see above).
    proctoring: Optional[ProctoringOverride] = None
    # Optional per-question-type minutes for this batch's clones only, e.g.
    # {"multiple_choice": 2, "coding": 20}. The candidate's clock is the sum of their own
    # questions' time_limit_minutes, so changing the exam's length means changing those —
    # a bare total would move the advertised number without moving the clock. Like the
    # proctoring block, this never touches the template or already-sent invites.
    time_per_question_type: Optional[Dict[str, int]] = None


def _qtype_in_round(qtype: Optional[str], round_type: Optional[str]) -> bool:
    """Which questions belong in a round. round_type None = combined (all questions).
    'coding' round keeps only coding questions; 'mcq' round keeps everything else."""
    if not round_type:
        return True
    is_coding = (qtype or "").lower() == "coding"
    return is_coding if round_type == "coding" else (not is_coding)


def _clone_test_for_candidate(db, source_test, candidate, job, current_user,
                              *, round_num: int = 1, round_type: Optional[str] = None,
                              security_overrides: Optional[dict] = None,
                              time_overrides: Optional[dict] = None):
    """Clone a test template into a per-candidate Interview, copying only the
    questions that belong to the given round. Returns the new Interview (flushed).

    round_type: None=combined (all questions), 'mcq'=Round 1 (non-coding),
    'coding'=Round 2 (coding only). The source template id is stashed in
    interview_config_data['_source_test_id'] so Round 2 can find the template later.

    security_overrides: proctoring flags to apply on top of the template's
    security_settings_data for this clone only. None (the default) copies the
    template's blob verbatim.

    time_overrides: {question_type: minutes} applied to this clone's questions only, which
    is what actually changes the exam length — the candidate's clock is the sum of their
    own questions' time_limit_minutes. None keeps each question's template time.
    """
    import json as _json
    from src.models.question_models import (
        Question as InterviewQuestion, QuestionSetAssignment, CandidateQuestionHistory)

    try:
        cfg = _json.loads(source_test.interview_config_data) if source_test.interview_config_data else {}
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    cfg["_source_test_id"] = source_test.id

    title = source_test.title or "Test"
    if round_type == "mcq":
        title = f"{title} — Round 1 (MCQ)"
    elif round_type == "coding":
        title = f"{title} — Round 2 (Coding)"

    # Proctoring: copy the template's blob byte-for-byte unless HR overrode it for
    # this send, in which case merge the overridden keys over the template's values.
    # This column is load-bearing — the candidate's test reads its flags off the
    # clone (candidate_routes.get_my_interviews), not off the template.
    security_data = source_test.security_settings_data
    if security_overrides:
        merged = _parse_security_settings(security_data)
        merged.update(security_overrides)
        security_data = _json.dumps(merged, default=str)

    candidate_interview = Interview(
        title=title,
        description=source_test.description,
        job_id=job.id,
        candidate_id=candidate.id,
        company_id=current_user.company_id,
        job_position=source_test.job_position,
        job_title=source_test.job_title or job.title,
        job_level=source_test.job_level,
        role_type=source_test.role_type,
        job_details_data=source_test.job_details_data,
        interview_type=source_test.interview_type,
        difficulty_level=source_test.difficulty_level,
        scheduled_datetime=source_test.scheduled_datetime,
        duration_minutes=source_test.duration_minutes,
        interview_config_data=_json.dumps(cfg),
        skills_config_data=source_test.skills_config_data,
        question_format_data=source_test.question_format_data,
        evaluation_rules_data=source_test.evaluation_rules_data,
        ai_generation_rules_data=source_test.ai_generation_rules_data,
        security_settings_data=security_data,
        skill_type_targets_data=source_test.skill_type_targets_data,
        interviewer_name=source_test.interviewer_name,
        interviewer_email=source_test.interviewer_email,
        interviewer_details_data=source_test.interviewer_details_data,
        meeting_link=source_test.meeting_link,
        status="SCHEDULED",
        test_round=round_num,
        round_type=round_type,
        created_by=current_user.email,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(candidate_interview)
    db.flush()  # get the new interview ID

    # Assemble the source questions (same logic as the single-round flow)
    if source_test.skill_type_targets_data:
        source_questions = _assemble_for_candidate(db, source_test, candidate.id)
    else:
        set_numbers = db.query(QuestionSetAssignment.set_number).filter(
            QuestionSetAssignment.interview_id == source_test.id
        ).distinct().order_by(QuestionSetAssignment.set_number).all()
        set_numbers = [r[0] for r in set_numbers]
        if set_numbers:
            existing_count = db.query(Interview).filter(
                Interview.job_id == job.id,
                Interview.candidate_id.isnot(None)
            ).count()
            assigned_set_num = set_numbers[(existing_count - 1) % len(set_numbers)]
            set_question_ids = [r[0] for r in
                db.query(QuestionSetAssignment.question_id).filter(
                    QuestionSetAssignment.interview_id == source_test.id,
                    QuestionSetAssignment.set_number == assigned_set_num
                ).order_by(QuestionSetAssignment.question_order).all()]
            source_questions = db.query(InterviewQuestion).filter(
                InterviewQuestion.id.in_(set_question_ids)
            ).all() if set_question_ids else []
        else:
            source_questions = db.query(InterviewQuestion).filter(
                InterviewQuestion.interview_id == source_test.id,
                InterviewQuestion.is_approved == True
            ).all()

    # Keep only questions for this round (combined keeps everything).
    source_questions = [q for q in source_questions if _qtype_in_round(q.question_type, round_type)]

    # The candidate's clock is the sum of their own questions' times, so a round that carries
    # only some of the template's types must advertise its own shorter duration, not the
    # template's full one.
    def _q_minutes(sq):
        """Minutes for this question on the clone — HR's per-send override wins."""
        if time_overrides:
            override = time_overrides.get(sq.question_type)
            if override:
                return int(override)
        return sq.time_limit_minutes or 5

    round_duration = sum(_q_minutes(sq) for sq in source_questions)
    if round_duration:
        candidate_interview.duration_minutes = round_duration
        # Keep the clone's config in step with the clock it will actually run.
        if time_overrides:
            try:
                _cfg = _json.loads(candidate_interview.interview_config_data) if candidate_interview.interview_config_data else {}
            except (ValueError, TypeError):
                _cfg = {}
            _cfg["duration_minutes"] = round_duration
            _cfg["time_per_question_type"] = {**(_cfg.get("time_per_question_type") or {}), **time_overrides}
            candidate_interview.interview_config_data = _json.dumps(_cfg, default=str)

    for sq in source_questions:
        db.add(InterviewQuestion(
            interview_id=candidate_interview.id,
            question_text=sq.question_text,
            question_type=sq.question_type,
            difficulty_level=sq.difficulty_level,
            category=sq.category,
            bloom_taxonomy_level=sq.bloom_taxonomy_level,
            options=sq.options,
            correct_answer=sq.correct_answer,
            sample_answer=sq.sample_answer,
            code_template=sq.code_template,
            test_cases=sq.test_cases,
            time_limit_minutes=_q_minutes(sq),
            points=sq.points,
            status=sq.status,
            is_approved=sq.is_approved,
            generation_model=sq.generation_model,
            skill_name=sq.skill_name,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        if source_test.skill_type_targets_data:
            db.add(CandidateQuestionHistory(
                candidate_id=candidate.id,
                source_question_id=sq.id,
                interview_id=candidate_interview.id,
            ))

    return candidate_interview


def _assemble_for_candidate(db, source_test, candidate_id):
    """Randomly assemble a unique question set for a candidate from the approved pool,
    respecting per-skill/type counts and avoiding previously seen questions."""
    import json as _json
    import random as _random
    from src.models.question_models import Question as InterviewQuestion, CandidateQuestionHistory

    targets = _json.loads(source_test.skill_type_targets_data)

    # Get all question IDs this candidate has already been assigned
    seen_ids = {row[0] for row in db.query(CandidateQuestionHistory.source_question_id).filter(
        CandidateQuestionHistory.candidate_id == candidate_id
    ).all()}

    assembled = []
    for target in targets:
        pool = db.query(InterviewQuestion).filter(
            InterviewQuestion.interview_id == source_test.id,
            InterviewQuestion.skill_name == target["skill_name"],
            InterviewQuestion.question_type == target["question_type"],
            InterviewQuestion.is_pool_question == True,
            InterviewQuestion.is_approved == True
        ).all()

        needed = target["count_per_set"]
        unseen = [q for q in pool if q.id not in seen_ids]
        seen_pool = [q for q in pool if q.id in seen_ids]

        if len(unseen) >= needed:
            selected = _random.sample(unseen, needed)
        elif len(pool) >= needed:
            # Backfill from previously seen questions (retake / pool partially exhausted)
            selected = unseen + _random.sample(seen_pool, needed - len(unseen))
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough approved '{target['question_type']}' for '{target['skill_name']}'. "
                       f"Need {needed}, only {len(pool)} in pool."
            )

        assembled.extend(selected)

    _random.shuffle(assembled)
    return assembled


@router.post("/jobs/{job_id}/send-test-invite")
async def send_test_invite_and_reject(
    job_id: str,
    request: SendTestInviteRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Send test invitation to selected candidates and auto-reject others using custom templates"""
    from src.services.email_service import _send, dispatch

    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_team(db, job, current_user)

    all_candidates = db.query(Candidate).filter(Candidate.job_id == job_id).all()
    _frontend_base = os.getenv("FRONTEND_URL", "http://localhost:3001")
    test_link = request.test_link or f"{_frontend_base}/candidate/interviews"

    # Get the source test template if test_id provided
    source_test = None
    if request.test_id:
        source_test = _company_template_or_404(db, request.test_id, current_user)

    # Proctoring override for this batch, if HR reconfigured it in the review modal.
    # exclude_none keeps flags HR did not touch on the template's own value.
    proctoring_overrides = (
        request.proctoring.model_dump(exclude_none=True) if request.proctoring else None
    )
    # Per-send question timing, applied to this batch's clones only.
    time_overrides = {
        k: int(v) for k, v in (request.time_per_question_type or {}).items() if v
    } or None or None
    invite_proctored = _test_is_proctored(source_test, proctoring_overrides)

    # Pre-validate pool readiness before processing any candidates (fail-fast)
    from src.models.question_models import Question as InterviewQuestion, QuestionSetAssignment, CandidateQuestionHistory
    import random as _random
    import json as _json

    if source_test and source_test.skill_type_targets_data:
        targets = _json.loads(source_test.skill_type_targets_data)
        for target in targets:
            available = db.query(InterviewQuestion).filter(
                InterviewQuestion.interview_id == source_test.id,
                InterviewQuestion.skill_name == target["skill_name"],
                InterviewQuestion.question_type == target["question_type"],
                InterviewQuestion.is_pool_question == True,
                InterviewQuestion.is_approved == True
            ).count()
            if available < target["count_per_set"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient pool for '{target['skill_name']}/{target['question_type']}': "
                           f"need {target['count_per_set']}, only {available} approved."
                )

    # Resolve per-step senders + extra recipients once for this bulk operation.
    # Extra CC recipients are added only to the first message of each kind so a
    # bulk run doesn't deliver one duplicate copy per candidate.
    from src.services.pipeline_notifications import resolve_step_sender
    from src.services.email_service import delivery_outcome
    ti_smtp, ti_disp = resolve_step_sender(
        db, job.company_id, "test_invitation", request.mailbox_id, job=job)
    rej_smtp, rej_disp = resolve_step_sender(
        db, job.company_id, "candidate_rejected", request.reject_mailbox_id, job=job)
    ti_cc_sent = False
    rej_cc_sent = False

    # Only candidates who have NOT yet received an ATS-stage email are actionable.
    # After apply, a candidate rests at 'ats_screening' or 'hr_review' (legacy: 'applied').
    # Any other stage (shortlisted/rejected/interview/...) means they were already emailed
    # from this stage, so never invite or reject them again.
    UNSENT_STAGES = ("applied", "ats_screening", "hr_review")

    invited = 0
    rejected = 0
    skipped = 0
    # Applicants whose resume could not be read reliably are never auto-rejected: they
    # were not really ranked, so "not selected" says nothing about them. A person decides.
    held_ids = needs_review_candidate_ids(db, job.id)
    held_for_review = 0
    # Delivery failures, tracked separately from the business counts above so the
    # response can distinguish "we sent 12" from "we tried 12 times".
    invite_failed = 0
    reject_failed = 0
    for c in all_candidates:
        name = f"{c.first_name} {c.last_name}"
        is_unsent = c.current_stage in UNSENT_STAGES
        if c.id in request.candidate_ids and is_unsent:
            prev_stage = c.current_stage
            c.current_stage = "shortlisted"
            c.updated_at = datetime.utcnow()
            db.add(CandidateStageHistory(
                candidate_id=c.id, from_stage=prev_stage, to_stage="shortlisted",
                changed_by_id=current_user.id, notes="Selected for test"
            ))

            # Clone the test for this candidate so they see it in "My Interviews".
            # Two-round: this invite is Round 1 (MCQ only); coding is sent later as
            # Round 2 via /send-round2-invite. Combined: clone everything (round_type=None).
            if source_test:
                _clone_test_for_candidate(
                    db, source_test, c, job, current_user,
                    round_num=1,
                    round_type=("mcq" if request.two_round else None),
                    security_overrides=proctoring_overrides,
                    time_overrides=time_overrides,
                )

            # Send custom invite email
            subject = (request.invite_subject or f"Test Invitation – {job.title}")
            body = (request.invite_body or "").replace("{{test_link}}", test_link).replace("{{candidate_name}}", name).replace("{{deadline}}", request.test_deadline)
            body_html = body.replace("\n", "<br>")
            if invite_proctored:
                body_html += MONITORING_EMAIL_NOTICE
            _res = dispatch(c.email, subject, f"<div style='font-family:sans-serif;line-height:1.7'>{body_html}</div>",
                  smtp_config=ti_smtp, cc=(ti_disp["cc"] if not ti_cc_sent else None), db=db)
            ti_cc_sent = True
            # dispatch reports failure by return value. Counting before reading it
            # meant "invitations sent to N" was really "N attempts made".
            if delivery_outcome(_res)["ok"]:
                invited += 1
            else:
                invite_failed += 1
                logger.warning(f"test-invite email to {c.email} not delivered")
        elif request.reject_others and is_unsent and c.id in held_ids:
            held_for_review += 1
        elif request.reject_others and is_unsent:
            prev_stage = c.current_stage
            c.current_stage = "rejected"
            c.updated_at = datetime.utcnow()
            db.add(CandidateStageHistory(
                candidate_id=c.id, from_stage=prev_stage, to_stage="rejected",
                changed_by_id=current_user.id, notes="Auto-rejected during shortlisting"
            ))
            subject = (request.reject_subject or f"Application Update – {job.title}")
            body = (request.reject_body or "").replace("{{candidate_name}}", name)
            body_html = body.replace("\n", "<br>")
            _res = dispatch(c.email, subject, f"<div style='font-family:sans-serif;line-height:1.7'>{body_html}</div>",
                  smtp_config=rej_smtp, cc=(rej_disp["cc"] if not rej_cc_sent else None), db=db)
            rej_cc_sent = True
            rejected += 1
            if not delivery_outcome(_res)["ok"]:
                reject_failed += 1
                logger.warning(f"auto-reject email to {c.email} not delivered")
        elif c.id in request.candidate_ids and not is_unsent:
            # Selected, but already emailed from this stage — skip to avoid duplicate mail.
            skipped += 1

    db.commit()

    message = f"Test invitations sent to {invited} candidates. {rejected} candidates auto-rejected."
    if invite_failed or reject_failed:
        message += (f" WARNING: {invite_failed} invitation and {reject_failed} "
                    f"rejection emails could not be sent.")
    if skipped:
        message += f" {skipped} already-notified candidates were skipped."
    if held_for_review:
        message += (f" {held_for_review} candidate{'s were' if held_for_review != 1 else ' was'} not rejected "
                    "because their resume needs manual review.")

    return {
        "message": message,
        "invited": invited,
        "rejected": rejected,
        "skipped": skipped,
        "held_for_review": held_for_review,
        "test_assigned": source_test.title if source_test else "No test linked"
    }


class SendRound2InviteRequest(BaseModel):
    candidate_ids: List[str]
    test_deadline: Optional[str] = None
    source_test_id: Optional[int] = None   # template; inferred from the Round 1 test if omitted
    test_link: Optional[str] = None
    invite_subject: Optional[str] = None
    invite_body: Optional[str] = None
    # "Send from" override for this batch (see SendTestInviteRequest.mailbox_id).
    mailbox_id: Optional[str] = None


def _company_template_or_404(db, test_id, current_user) -> Interview:
    """A test template (candidate_id IS NULL) in the caller's company, or 404.

    Test ids are sequential integers. Loaded by id alone, a template from another
    company would be cloned — questions and answer keys included — into this
    company's candidate test, where HR can then read it back.
    """
    t = db.query(Interview).filter(
        Interview.id == test_id,
        Interview.company_id == current_user.company_id,
        Interview.candidate_id.is_(None),
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Test not found")
    return t


def _resolve_source_template(db, job_id, round1_iv, company_id):
    """Find the test template (candidate_id IS NULL) a per-candidate test was cloned
    from: prefer the id stashed in the Round 1 test's config, else newest job template.
    The stashed id is data, so it is only trusted inside the caller's company."""
    import json as _json
    if round1_iv is not None and round1_iv.interview_config_data:
        try:
            cfg = _json.loads(round1_iv.interview_config_data)
            sid = cfg.get("_source_test_id") if isinstance(cfg, dict) else None
            if sid:
                t = db.query(Interview).filter(
                    Interview.id == sid,
                    Interview.company_id == company_id,
                    Interview.candidate_id.is_(None),
                ).first()
                if t:
                    return t
        except Exception:
            pass
    return db.query(Interview).filter(
        Interview.company_id == company_id,
        Interview.job_id == job_id,
        Interview.candidate_id.is_(None),
        Interview.is_scheduled_interview == False,  # noqa: E712
    ).order_by(Interview.created_at.desc()).first()


@router.post("/jobs/{job_id}/send-round2-invite")
async def send_round2_invite(
    job_id: str,
    body: SendRound2InviteRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Round 2 (Coding): after reviewing Round 1 (MCQ) scores, HR advances selected
    candidates. Clones the coding-only questions from the same test template into a
    new per-candidate test. Only candidates whose Round 1 test is COMPLETED qualify."""
    from src.services.email_service import _send, dispatch

    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_team(db, job, current_user)
    explicit_source = (_company_template_or_404(db, body.source_test_id, current_user)
                       if body.source_test_id else None)

    _frontend_base = os.getenv("FRONTEND_URL", "http://localhost:3001")
    test_link = body.test_link or f"{_frontend_base}/candidate/interviews"
    deadline = body.test_deadline or ""

    from src.models.question_models import Question as InterviewQuestion
    # Per-step sender + extra recipients (CC added only to the first invite).
    from src.services.pipeline_notifications import resolve_step_sender
    r2_smtp, r2_disp = resolve_step_sender(
        db, job.company_id, "test_invitation", body.mailbox_id, job=job)
    r2_cc_sent = False

    advanced = 0
    skipped = []
    for cid in body.candidate_ids:
        c = db.query(Candidate).filter(
            Candidate.id == cid, Candidate.company_id == current_user.company_id
        ).first()
        if not c:
            skipped.append({"candidate_id": cid, "reason": "not found"})
            continue

        # The candidate's completed Round 1 (MCQ) test for this job.
        round1 = db.query(Interview).filter(
            Interview.candidate_id == cid,
            Interview.job_id == job_id,
            Interview.round_type == "mcq",
        ).order_by(Interview.updated_at.desc()).first()
        if not round1 or round1.status != "COMPLETED":
            skipped.append({"candidate_id": cid, "reason": "Round 1 not completed"})
            continue

        # Don't create a duplicate Round 2 test.
        existing_r2 = db.query(Interview).filter(
            Interview.candidate_id == cid,
            Interview.job_id == job_id,
            Interview.round_type == "coding",
        ).first()
        if existing_r2:
            skipped.append({"candidate_id": cid, "reason": "Round 2 already sent"})
            continue

        source_test = explicit_source or _resolve_source_template(
            db, job_id, round1, current_user.company_id)
        if not source_test:
            skipped.append({"candidate_id": cid, "reason": "source test template not found"})
            continue

        r2 = _clone_test_for_candidate(
            db, source_test, c, job, current_user, round_num=2, round_type="coding")
        if not db.query(InterviewQuestion).filter(
                InterviewQuestion.interview_id == r2.id).count():
            # Template has no coding questions — nothing to assess in Round 2.
            db.delete(r2)
            skipped.append({"candidate_id": cid, "reason": "no coding questions in the test"})
            continue

        prev_stage = c.current_stage
        c.current_stage = "shortlisted"
        c.updated_at = datetime.utcnow()
        db.add(CandidateStageHistory(
            candidate_id=cid, from_stage=prev_stage, to_stage="shortlisted",
            changed_by_id=current_user.id, notes="Advanced to Round 2 (Coding)"
        ))

        name = f"{c.first_name} {c.last_name}"
        subject = (body.invite_subject or f"Round 2 (Coding) – {job.title}")
        text = (body.invite_body or
                f"Dear {name},\n\nCongratulations on clearing Round 1 — a strong result that puts you among "
                f"our top candidates! We are delighted to invite you to Round 2 (Coding) for the {job.title} role, "
                f"your chance to showcase your problem-solving skills.\n\n"
                f"Test Link: {{{{test_link}}}}\nComplete By: {deadline}\n\n"
                f"Take your time, trust your preparation, and give it your best. Best of luck — we're rooting for you!\n\n"
                f"Warm regards,\nThe Talent Acquisition Team")
        text = text.replace("{{test_link}}", test_link).replace("{{candidate_name}}", name).replace("{{deadline}}", deadline)
        notice = MONITORING_EMAIL_NOTICE if _test_is_proctored(r2) else ""
        try:
            dispatch(c.email, subject, f"<div style='font-family:sans-serif;line-height:1.7'>{text.replace(chr(10), '<br>')}{notice}</div>",
                     smtp_config=r2_smtp, cc=(r2_disp["cc"] if not r2_cc_sent else None))
            r2_cc_sent = True
        except Exception as e:
            logger.warning(f"round-2 invite email to {c.email} failed: {e}")
        advanced += 1

    db.commit()
    return {
        "message": f"Round 2 (Coding) sent to {advanced} candidate(s)."
                   + (f" {len(skipped)} skipped." if skipped else ""),
        "advanced": advanced,
        "skipped": skipped,
    }


# ==================== INTERVIEW FEEDBACK ENDPOINTS ====================

@router.get("/feedback")
async def get_all_feedback(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get all interview feedback for HR review"""
    from sqlalchemy.orm import aliased
    IVUser = aliased(User)   # the interviewer's User row
    fb_q = (
        db.query(InterviewFeedback)
        .join(Interview, InterviewFeedback.interview_id == Interview.id)
        .outerjoin(JobPosting, Interview.job_id == JobPosting.id)
        .outerjoin(InterviewerProfile, InterviewFeedback.interviewer_id == InterviewerProfile.id)
        .outerjoin(IVUser, InterviewerProfile.user_id == IVUser.id)
    )
    # Scoped by the interview's own company. The interviewer's company is not a
    # scope: matching on it would admit feedback on another company's interview by
    # anyone who happens to share an interviewer's company.
    fb_q = fb_q.filter(
        Interview.company_id == current_user.company_id,
        InterviewFeedback.company_id == current_user.company_id,
    )
    # HR sees feedback for the jobs they are on; admins see the whole company.
    if current_user.role != "admin":
        fb_q = fb_q.filter(job_access_filter(get_hr_profile(db, current_user).id))
    feedbacks = fb_q.order_by(InterviewFeedback.created_at.desc()).all()

    results = []
    for fb in feedbacks:
        iv = fb.interview
        if not iv:
            continue
        candidate_name = "—"
        candidate_email = ""
        candidate_phone = ""
        candidate_id = ""
        # Resolve job — prefer direct link, fall back to candidate's job
        cand_job = iv.candidate.job if iv.candidate else None
        job_title = iv.job_title or (iv.job.title if iv.job else None) or (cand_job.title if cand_job else "—")
        job_id = iv.job_id or (iv.job.id if iv.job else None) or (cand_job.id if cand_job else None)
        if iv.candidate:
            candidate_name = f"{iv.candidate.first_name} {iv.candidate.last_name}"
            candidate_email = iv.candidate.email
            candidate_phone = iv.candidate.phone or ""
            candidate_id = str(iv.candidate.id)

        results.append({
            "feedback_id": str(fb.id),
            "interview_id": iv.id,
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "candidate_email": candidate_email,
            "candidate_phone": candidate_phone,
            "job_title": job_title,
            "job_id": str(job_id) if job_id else None,
            "technical_score": float(fb.technical_score) if fb.technical_score else 0,
            "communication_score": float(fb.communication_score) if fb.communication_score else 0,
            "problem_solving_score": float(fb.problem_solving_score) if fb.problem_solving_score else 0,
            "cultural_fit_score": float(fb.cultural_fit_score) if fb.cultural_fit_score else 0,
            "overall_score": float(fb.overall_score) if fb.overall_score else 0,
            "strengths": fb.strengths,
            "weaknesses": fb.weaknesses,
            "hire_recommendation": fb.hire_recommendation,
            "recommendations": fb.recommendations,
            "additional_notes": fb.additional_notes,
            "created_at": fb.created_at,
        })

    return results


# ==================== HR-SUBMITTED INTERVIEW FEEDBACK ====================

class HRInterviewFeedbackCreate(BaseModel):
    technical_score: float = 0
    communication_score: float = 0
    problem_solving_score: float = 0
    cultural_fit_score: float = 0
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    recommendations: Optional[str] = ""
    hire_recommendation: str = "proceed"
    additional_notes: Optional[str] = None


@router.post("/interviews/{interview_id}/feedback")
async def hr_submit_interview_feedback(
    interview_id: int,
    body: HRInterviewFeedbackCreate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """HR records (or edits) interview feedback for a scheduled interview.

    Mirrors the interviewer feedback flow but is company-scoped and works for
    offline / ad-hoc panels that have no interviewer login. Upserts the single
    InterviewFeedback row for the interview, marks it completed, and advances the
    candidate to interview_completed.
    """
    # Authorize the SAME way the Scheduled-Interviews list does (via the job), so any
    # interview HR can see is feedback-able — the interview's own company_id may be
    # NULL/orphaned while the job carries the real company.
    q = db.query(Interview).join(JobPosting, Interview.job_id == JobPosting.id).filter(
        Interview.id == interview_id)
    if current_user.company_id:
        q = q.filter(JobPosting.company_id == current_user.company_id)
    if current_user.role != "admin":
        hr_profile = get_hr_profile(db, current_user)
        q = q.filter(job_access_filter(hr_profile.id))
    interview = q.first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Normalize scores: accept 0-10 or 0-100.
    scores = {}
    for attr in ("technical_score", "communication_score", "problem_solving_score", "cultural_fit_score"):
        val = float(getattr(body, attr) or 0)
        if val <= 10:
            val *= 10
        scores[attr] = max(0.0, min(100.0, val))

    valid = ["strong_hire", "hire", "proceed", "no_hire", "strong_no_hire"]
    if body.hire_recommendation not in valid:
        raise HTTPException(status_code=400, detail=f"hire_recommendation must be one of: {valid}")

    overall = (scores["technical_score"] * 0.3 + scores["communication_score"] * 0.2
               + scores["problem_solving_score"] * 0.3 + scores["cultural_fit_score"] * 0.2)

    # Resolve the interview's lead interviewer profile (if any) for attribution;
    # ad-hoc / offline panelists have no profile → leave NULL (column is nullable).
    interviewer_profile_id = None
    if interview.interviewer_email:
        u = db.query(User).filter(
            User.company_id == current_user.company_id,
            User.email == interview.interviewer_email,
        ).first()
        if u:
            prof = db.query(InterviewerProfile).filter(InterviewerProfile.user_id == u.id).first()
            if prof:
                interviewer_profile_id = prof.id

    strengths_str = json.dumps([s for s in (body.strengths or []) if str(s).strip()])
    weaknesses_str = json.dumps([w for w in (body.weaknesses or []) if str(w).strip()])

    fb = db.query(InterviewFeedback).filter(InterviewFeedback.interview_id == interview_id).first()
    if fb:  # upsert — let HR edit
        fb.technical_score = scores["technical_score"]
        fb.communication_score = scores["communication_score"]
        fb.problem_solving_score = scores["problem_solving_score"]
        fb.cultural_fit_score = scores["cultural_fit_score"]
        fb.overall_score = overall
        fb.strengths = strengths_str
        fb.weaknesses = weaknesses_str
        fb.recommendations = body.recommendations or ""
        fb.hire_recommendation = body.hire_recommendation
        fb.additional_notes = body.additional_notes
        if interviewer_profile_id and not fb.interviewer_id:
            fb.interviewer_id = interviewer_profile_id
    else:
        fb = InterviewFeedback(
            company_id=current_user.company_id,
            interview_id=interview_id,
            interviewer_id=interviewer_profile_id,
            technical_score=scores["technical_score"],
            communication_score=scores["communication_score"],
            problem_solving_score=scores["problem_solving_score"],
            cultural_fit_score=scores["cultural_fit_score"],
            overall_score=overall,
            strengths=strengths_str,
            weaknesses=weaknesses_str,
            recommendations=body.recommendations or "",
            hire_recommendation=body.hire_recommendation,
            additional_notes=body.additional_notes,
        )
        db.add(fb)

    interview.status = "COMPLETED"
    if interview.candidate_id:
        cand = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
        if cand and cand.current_stage != "interview_completed":
            prev = cand.current_stage
            cand.current_stage = "interview_completed"
            db.add(CandidateStageHistory(
                candidate_id=interview.candidate_id,
                from_stage=prev,
                to_stage="interview_completed",
                changed_by_id=current_user.id,
                notes=f"Interview feedback recorded by HR. Overall score: {round(overall, 1)}",
            ))

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save feedback: {e}")

    return {
        "message": "Interview feedback saved",
        "interview_id": interview_id,
        "overall_score": round(overall, 2),
        "hire_recommendation": body.hire_recommendation,
    }


# ==================== TEST RESULTS ENDPOINTS ====================

# Verdict severity for picking a candidate's "best" coding verdict (lower = better).
_CODING_SEVERITY = {"AC": 0, "WA": 1, "RE": 2, "TLE": 3, "MLE": 4, "CE": 5, "PENDING": 6}
_CODING_EMPTY = {"coding_verdict": None, "coding_score": 0.0,
                 "coding_max_score": 0.0, "coding_solved": 0, "coding_total": 0}


def _coding_summary(db, interview, cache):
    """Aggregate ONE interview's coding results from the interview path.

    The coding test a candidate actually takes is stored as ``interview_questions``
    (coding) graded into ``interview.answers_json`` on final submit, plus per-question
    in-test attempts in ``interview_code_submissions``. (The standalone
    ``coding_problems``/``code_submissions`` bank is unused, so the old aggregator that
    read it always returned zeros.)

    Best-attempt semantics: for each coding question we take the better of the finalized
    answer and the candidate's best in-test graded attempt. Returns coding_verdict /
    coding_score / coding_max_score / coding_solved / coding_total. ``coding_verdict`` is
    ``AC`` only when every coding question is solved, else the best (most favorable) verdict
    achieved, or ``None`` when nothing was attempted.

    ``InterviewCodeSubmission.score`` and ``answers_json['points']`` are the SAME scale
    (both ``points_awarded`` — see judge_routes grade_question + interview_grading), so they
    are directly comparable.
    """
    from src.models.question_models import Question as InterviewQuestion
    from src.models.judge_models import InterviewCodeSubmission

    if not interview:
        return dict(_CODING_EMPTY)

    user_id = interview.candidate.user_id if interview.candidate else None

    # Coding questions + their max points for this interview (cached per interview).
    if interview.id not in cache:
        cqs = db.query(InterviewQuestion).filter(
            InterviewQuestion.interview_id == interview.id,
            InterviewQuestion.question_type == "coding",
        ).all()
        cache[interview.id] = {q.id: float(q.points or 0) for q in cqs}
    q_points = cache[interview.id]
    total = len(q_points)
    if total == 0:
        return dict(_CODING_EMPTY)
    max_score = float(sum(q_points.values()))

    # Finalized graded answers (coding entries carry a 'verdict' key).
    try:
        answers = json.loads(interview.answers_json) if interview.answers_json else []
    except Exception:
        answers = []
    finalized = {a.get("question_id"): a for a in answers
                 if isinstance(a, dict) and a.get("verdict")}

    # Best in-test graded attempt per question (verdict set = actually judged).
    best_sub = {}
    if user_id:
        subs = db.query(InterviewCodeSubmission).filter(
            InterviewCodeSubmission.interview_id == interview.id,
            InterviewCodeSubmission.user_id == user_id,
            InterviewCodeSubmission.verdict.isnot(None),
        ).all()
        for s in subs:
            cur = best_sub.get(s.question_id)
            if cur is None or (s.score or 0) > (cur.score or 0):
                best_sub[s.question_id] = s

    score = 0.0
    solved = 0
    best_sev = None
    attempted = False
    for qid in q_points:
        pts, verdict = None, None
        fa = finalized.get(qid)
        if fa:
            pts, verdict = float(fa.get("points") or 0), fa.get("verdict")
        bs = best_sub.get(qid)
        if bs and (pts is None or (bs.score or 0) > pts):
            pts, verdict = float(bs.score or 0), bs.verdict
        if verdict is None:
            continue
        attempted = True
        score += pts or 0
        if verdict == "AC":
            solved += 1
        sev = _CODING_SEVERITY.get(verdict, 6)
        best_sev = sev if best_sev is None else min(best_sev, sev)

    if not attempted:
        verdict = None
    elif solved == total:
        verdict = "AC"
    else:
        inv = {v: k for k, v in _CODING_SEVERITY.items()}
        verdict = inv.get(best_sev)

    return {"coding_verdict": verdict, "coding_score": round(score, 2),
            "coding_max_score": round(max_score, 2), "coding_solved": solved,
            "coding_total": total}


@router.get("/test-results")
async def get_test_results(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get completed and pending (SCHEDULED) test results grouped by job, with scores and red flags"""
    # Tests in the caller's company; an HR sees only the jobs they are on, as on
    # GET /interviews. Candidate contact details, scores and accommodation requests
    # are in every row, so this must never be the whole table.
    def _scoped():
        q = db.query(Interview).filter(Interview.company_id == current_user.company_id)
        if current_user.role != "admin":
            q = q.join(JobPosting, Interview.job_id == JobPosting.id).filter(
                JobPosting.company_id == current_user.company_id,
                job_access_filter(get_hr_profile(db, current_user).id),
            )
        return q

    # COMPLETED: all finished tests (exclude scheduled meeting records)
    completed_ivs = _scoped().filter(
        Interview.status == "COMPLETED",
        Interview.is_scheduled_interview == False,  # noqa: E712
    ).order_by(Interview.updated_at.desc()).all()

    # SCHEDULED: only candidate-assigned pending test invites (exclude meetings and template drafts)
    scheduled_ivs = _scoped().filter(
        Interview.status == "SCHEDULED",
        Interview.candidate_id.isnot(None),
        Interview.is_scheduled_interview == False,  # noqa: E712
    ).order_by(Interview.updated_at.desc()).all()

    # Group every test for a candidate+job into ONE row. A two-round candidate's
    # Round 1 (MCQ) and Round 2 (Coding) are merged: the MCQ score goes in the score
    # columns, the coding result in the coding columns — no duplicate rows.
    import re as _re
    from collections import defaultdict as _dd
    from datetime import datetime as _dt

    def _pick(cands):
        """Most relevant interview: COMPLETED first, then newest."""
        if not cands:
            return None
        return sorted(cands, key=lambda i: (i.status == "COMPLETED", i.updated_at or i.created_at or _dt.min),
                      reverse=True)[0]

    def _base_title(t):
        return _re.sub(r"\s*[—-]\s*Round\s*\d+.*$", "", t or "", flags=_re.I).strip() or (t or "")

    groups: dict = _dd(list)
    for iv in completed_ivs + scheduled_ivs:
        groups[(iv.candidate_id, iv.job_id)].append(iv)

    results = []
    job_problems_cache: dict = {}  # job_id -> ([problem_ids], {problem_id: max_pts})
    for (cand_id, job_id_k), ivs in groups.items():
        mcq = _pick([i for i in ivs if i.round_type == "mcq"])
        coding_r = _pick([i for i in ivs if i.round_type == "coding"])
        combined = _pick([i for i in ivs if not i.round_type])
        two_round = bool(mcq or coding_r)
        primary = coding_r or mcq or combined or _pick(ivs)
        if not primary:
            continue

        mcq_done = bool(mcq and mcq.status == "COMPLETED")
        coding_done = bool(coding_r and coding_r.status == "COMPLETED")
        any_done = mcq_done or coding_done or bool(combined and combined.status == "COMPLETED")

        candidate_name = candidate_email = candidate_phone = ""
        if primary.candidate:
            candidate_name = f"{primary.candidate.first_name} {primary.candidate.last_name}"
            candidate_email = primary.candidate.email
            candidate_phone = primary.candidate.phone or ""
        else:
            candidate_name = "—"
        job_title = primary.job_title or (primary.job.title if primary.job else "—")

        # Score columns: for two-round use the MCQ round's marks; else the single test's.
        score_iv = mcq if two_round and mcq else (combined or primary)
        test_score = float(score_iv.test_score) if score_iv and score_iv.test_score is not None else None
        total_marks = score_iv.total_marks if score_iv else None

        # Coding columns: for two-round use the Coding round; else the single test.
        coding = dict(_CODING_EMPTY)
        coding_src = coding_r if two_round else (combined or primary)
        if coding_src:
            try:
                coding = _coding_summary(db, coding_src, job_problems_cache)
            except Exception:
                coding = dict(_CODING_EMPTY)

        # Red flags from the proctoring session of the round being shown.
        # Also collect the interview ids that actually have a proctoring session, so
        # HR can open the full per-flag report (GET /api/v1/proctor/results/{id}) for
        # each contributing round — keeps the detail modal consistent with this count.
        red_flags = 0
        unreviewed_flags = 0
        proctor_interview_ids = []
        try:
            from src.models.models import (
                ProctoringSession, ProctoringViolation, ProctoringReview,
                PROCTORING_MARKER_TYPES, PROCTORING_UNREVIEWABLE_TYPES,
            )
            cuid = primary.candidate.user_id if primary.candidate else None
            if cuid:
                seen_iv_ids = set()
                for src_iv in (coding_r, mcq, combined, primary):
                    if not src_iv or src_iv.id in seen_iv_ids:
                        continue
                    seen_iv_ids.add(src_iv.id)
                    # Every session for this test, as the report shows them — a reload
                    # mid-test starts a new one. This used to count one arbitrary
                    # session (.first(), unordered) and include marker rows like
                    # LIVE_ENROLLED, so it disagreed with the report it links to.
                    session_ids = [sid for (sid,) in db.query(ProctoringSession.id).filter(
                        ProctoringSession.candidate_id == cuid,
                        ProctoringSession.test_id == src_iv.id).all()]
                    if not session_ids:
                        continue
                    flag_rows = db.query(ProctoringViolation.id, ProctoringViolation.violation_type).filter(
                        ProctoringViolation.session_id.in_(session_ids),
                        ~ProctoringViolation.violation_type.in_(
                            PROCTORING_MARKER_TYPES | PROCTORING_UNREVIEWABLE_TYPES),
                    ).all()
                    reviewed = {vid for (vid,) in db.query(ProctoringReview.violation_id).filter(
                        ProctoringReview.session_id.in_(session_ids),
                        ProctoringReview.violation_id.isnot(None)).distinct().all()}
                    red_flags += len(flag_rows)
                    unreviewed_flags += sum(1 for vid, _ in flag_rows if vid not in reviewed)
                    proctor_interview_ids.append(src_iv.id)
        except Exception:
            red_flags = 0
            unreviewed_flags = 0
            proctor_interview_ids = []

        interviewer_name = interviewer_email = interviewer_id = ""
        if primary.job and primary.job.interviewer and primary.job.interviewer.user:
            iu = primary.job.interviewer.user
            interviewer_name = f"{iu.first_name} {iu.last_name}"
            interviewer_email = iu.email
            interviewer_id = str(primary.job.interviewer_id)

        round2_status = ("completed" if coding_done else "sent" if coding_r else "pending" if mcq_done else None)
        round2_pending = bool(mcq_done and coding_r is None)

        # Retests and accommodations. `open_iv` is the attempt the candidate has not taken
        # yet (a fresh invite or a granted retest) — adjustments apply to that one.
        open_iv = _pick([i for i in ivs if i.status != "COMPLETED"])
        retest_pending = bool(open_iv and open_iv.previous_attempt_id)
        acc_iv = open_iv or primary
        earlier_attempts = sorted(
            [i.id for i in ivs if i.status == "COMPLETED" and i.id not in proctor_interview_ids
             and i.round_type == primary.round_type and i.id != primary.id],
            reverse=True)
        try:
            from src.models.models import AssessmentExplanation
            explanation_count = db.query(AssessmentExplanation).filter(
                AssessmentExplanation.interview_id.in_([i.id for i in ivs])).count()
        except Exception:
            explanation_count = 0

        results.append({
            "id": primary.id,
            "title": _base_title(primary.title),
            "job_title": job_title,
            "job_id": job_id_k,
            "candidate_id": cand_id,
            "candidate_name": candidate_name,
            "candidate_email": candidate_email,
            "candidate_phone": candidate_phone,
            "candidate_stage": primary.candidate.current_stage if primary.candidate else "",
            "interview_type": primary.interview_type,
            "difficulty_level": primary.difficulty_level,
            "duration_minutes": primary.duration_minutes,
            "rating": primary.rating,
            "status": primary.status,
            "pending": not any_done,
            "test_score": test_score,
            "total_marks": total_marks,
            "red_flags": red_flags,
            "unreviewed_flags": unreviewed_flags,
            # Earlier attempts follow the current one so HR sees the whole history.
            "proctor_interview_ids": proctor_interview_ids + earlier_attempts,
            "attempt_number": primary.attempt_number or 1,
            "retest_pending": retest_pending,
            "open_interview_id": open_iv.id if open_iv else None,
            "accommodation_status": acc_iv.accommodation_status,
            "accommodation_request": acc_iv.accommodation_request,
            "accommodations": approved_accommodations(acc_iv) or None,
            "accommodations_summary": describe_accommodations(approved_accommodations(acc_iv)),
            "explanation_count": explanation_count,
            "coding_verdict": coding["coding_verdict"],
            "coding_score": coding["coding_score"],
            "coding_max_score": coding["coding_max_score"],
            "coding_solved": coding["coding_solved"],
            "coding_total": coding["coding_total"],
            "interviewer_name": interviewer_name,
            "interviewer_email": interviewer_email,
            "interviewer_id": interviewer_id,
            "completed_at": primary.updated_at,
            # ── merged two-round metadata (one row per candidate) ──
            "two_round": two_round,
            "mcq_done": mcq_done,
            "coding_done": coding_done,
            "round2_status": round2_status,          # 'pending' | 'sent' | 'completed' | None
            "round2_pending": round2_pending,        # Round 1 done, Round 2 not yet sent
            "round_type": None if two_round else primary.round_type,
        })

    return results


class RetestRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=2000)
    accommodations: Optional[dict] = None


def _hr_attempt(db: Session, current_user: User, interview_id: int) -> Interview:
    """A candidate's test attempt this HR may act on, or 404/403."""
    iv = db.query(Interview).filter(
        Interview.id == interview_id,
        Interview.company_id == current_user.company_id,
        Interview.candidate_id.isnot(None),
    ).first()
    if not iv:
        raise HTTPException(status_code=404, detail="Test attempt not found")
    if current_user.role != "admin" and iv.job:
        if not can_access_job(db, iv.job, get_hr_profile(db, current_user)):
            raise HTTPException(status_code=403, detail="You don't have access to this job")
    return iv


@router.post("/test-results/{interview_id}/retest")
async def grant_retest(
    interview_id: int,
    body: RetestRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Give a candidate another attempt at a test.

    Creates a NEW attempt cloned from the same template, pointing back at this one. The
    earlier attempt, its score and its proctoring record are left exactly as they were,
    so a retest never erases what happened. A reason is required and recorded in the
    candidate's stage history. Optional accommodations are approved for the new attempt.
    """
    from src.services.assessment_fairness import source_template_id, set_accommodations
    from src.models.question_models import Question as InterviewQuestion

    attempt = _hr_attempt(db, current_user, interview_id)
    if (attempt.status or "").upper() != "COMPLETED":
        raise HTTPException(status_code=409, detail="Only a submitted test can be retaken.")
    pending = db.query(Interview).filter(
        Interview.previous_attempt_id == attempt.id,
        Interview.status != "COMPLETED",
    ).first()
    if pending:
        raise HTTPException(status_code=409, detail="A retest for this attempt is already waiting for the candidate.")

    # The template id is read out of the attempt's config, so it is only trusted inside
    # the caller's company.
    template = db.query(Interview).filter(
        Interview.id == source_template_id(attempt),
        Interview.company_id == current_user.company_id,
        Interview.candidate_id.is_(None),
    ).first() if source_template_id(attempt) else None
    if not template:
        raise HTTPException(status_code=409, detail="The original test template for this attempt no longer exists, so it can't be retaken. Send a new test instead.")
    candidate = db.query(Candidate).filter(
        Candidate.id == attempt.candidate_id,
        Candidate.company_id == current_user.company_id,
    ).first()
    job = attempt.job or (db.query(JobPosting).filter(
        JobPosting.id == attempt.job_id,
        JobPosting.company_id == current_user.company_id,
    ).first() if attempt.job_id else None)
    if not candidate or not job:
        raise HTTPException(status_code=409, detail="The candidate or job for this attempt no longer exists.")

    # Keep this attempt's per-send settings: proctoring overrides live in its security
    # blob, and per-question-type minutes on its own question copies.
    security = _parse_security_settings(attempt.security_settings_data) or None
    minutes = {}
    for q in db.query(InterviewQuestion).filter(InterviewQuestion.interview_id == attempt.id).all():
        minutes.setdefault(q.question_type, q.time_limit_minutes)
    # Undo any extra time the earlier attempt had, so it isn't counted twice.
    base = (parse_accommodations(attempt.accommodations).get("_base_minutes") or {})
    if base:
        minutes = {}
        for q in db.query(InterviewQuestion).filter(InterviewQuestion.interview_id == attempt.id).all():
            minutes.setdefault(q.question_type, int(base.get(str(q.id), q.time_limit_minutes or 5)))

    retest = _clone_test_for_candidate(
        db, template, candidate, job, current_user,
        round_num=attempt.test_round or 1, round_type=attempt.round_type,
        security_overrides=security, time_overrides=minutes or None,
    )
    retest.attempt_number = (attempt.attempt_number or 1) + 1
    retest.previous_attempt_id = attempt.id
    if body.accommodations:
        try:
            set_accommodations(db, retest, "approved", body.accommodations)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    reason = body.reason.strip()
    note = f"Retest granted (attempt {retest.attempt_number}): {reason}"
    summary = describe_accommodations(approved_accommodations(retest))
    if summary:
        note += f" Adjustments: {', '.join(summary)}."
    if candidate.current_stage in ("interview_scheduled", "offer_sent", "hired"):
        db.add(CandidateStageHistory(candidate_id=candidate.id, from_stage=candidate.current_stage,
                                     to_stage=candidate.current_stage, changed_by_id=current_user.id, notes=note))
    else:
        update_candidate_stage(db, candidate.id, "shortlisted", current_user.id, note)
    db.commit()

    # Tell the candidate. Same wording rules as a first invite, including the monitoring
    # notice when the test is proctored.
    from src.services.email_service import dispatch, delivery_outcome
    email_sent = False
    try:
        base_url = os.getenv("FRONTEND_URL", "http://localhost:3001")
        name = f"{candidate.first_name} {candidate.last_name}".strip()
        body_html = (
            f"Dear {name},<br><br>You have been given another attempt at the assessment for "
            f"<b>{job.title}</b>.<br><br>Open it from your dashboard: "
            f"<a href='{base_url}/candidate/interviews'>{base_url}/candidate/interviews</a>"
        )
        if summary:
            body_html += "<br><br>Adjustments for this attempt: " + ", ".join(summary) + "."
        if _test_is_proctored(retest):
            body_html += MONITORING_EMAIL_NOTICE
        res = dispatch(candidate.email, f"Another attempt: {job.title} assessment",
                       f"<div style='font-family:sans-serif;line-height:1.7'>{body_html}</div>", db=db)
        email_sent = delivery_outcome(res)["ok"]
    except Exception as e:
        logger.warning(f"retest email to {candidate.email} failed: {e}")

    return {
        "retest_interview_id": retest.id,
        "attempt_number": retest.attempt_number,
        "previous_attempt_id": attempt.id,
        "accommodations_summary": summary,
        "email_sent": email_sent,
    }


class AccommodationDecision(BaseModel):
    status: str                                  # approved | declined
    extra_time_percent: int = 0
    audio_monitoring_off: bool = False
    gaze_flags_off: bool = False
    fullscreen_not_required: bool = False
    notes: Optional[str] = None


@router.patch("/interviews/{interview_id}/accommodations")
async def decide_accommodations(
    interview_id: int,
    body: AccommodationDecision,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Approve specific adjustments for an attempt the candidate hasn't taken yet, or
    decline a request. Approving extra time lengthens this candidate's own questions."""
    from src.services.assessment_fairness import set_accommodations
    iv = _hr_attempt(db, current_user, interview_id)
    if (iv.status or "").upper() in ("COMPLETED", "IN_PROGRESS"):
        raise HTTPException(status_code=409, detail="Adjustments can only be changed before the test starts.")
    try:
        set_accommodations(db, iv, body.status, body.model_dump(exclude={"status"}))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    iv.updated_at = datetime.utcnow()
    if iv.candidate_id:
        summary = describe_accommodations(approved_accommodations(iv))
        cand = db.query(Candidate).filter(Candidate.id == iv.candidate_id).first()
        if cand:
            db.add(CandidateStageHistory(
                candidate_id=cand.id, from_stage=cand.current_stage, to_stage=cand.current_stage,
                changed_by_id=current_user.id,
                notes=(f"Test adjustments approved: {', '.join(summary) or 'none'}" if body.status == "approved"
                       else "Test adjustment request declined") + (f". {body.notes.strip()}" if body.notes else ""),
            ))
    db.commit()
    return {
        "interview_id": iv.id,
        "accommodation_status": iv.accommodation_status,
        "accommodations": approved_accommodations(iv) or None,
        "accommodations_summary": describe_accommodations(approved_accommodations(iv)),
        "duration_minutes": iv.duration_minutes,
    }


@router.get("/test-results/{interview_id}/answers")
async def get_test_result_answers(
    interview_id: int,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """
    Per-question answer detail for a completed test, for HR review. Enriches the
    stored answers_json with question text and, for communication questions, the
    transcript, delivery breakdown, and audio playback URL.
    """
    from src.models.question_models import Question as InterviewQuestion, CommunicationAnswer

    # Company- and job-team-scoped: this returns answers, transcripts, recordings and
    # every question's correct answer, and test ids are sequential.
    interview = _hr_attempt(db, current_user, interview_id)

    try:
        stored = json.loads(interview.answers_json) if interview.answers_json else []
    except (ValueError, TypeError):
        stored = []

    questions = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id
    ).all()
    q_map = {q.id: q for q in questions}

    comm_map = {
        c.question_id: c
        for c in db.query(CommunicationAnswer).filter(
            CommunicationAnswer.interview_id == interview_id
        ).all()
    }

    answers = []
    for a in stored:
        qid = a.get("question_id")
        q = q_map.get(qid)
        entry = {
            "question_id": qid,
            "question_text": q.question_text if q else None,
            "question_type": q.question_type if q else a.get("type"),
            "points": a.get("points"),
        }
        if (a.get("type") == "communication") or (q and (q.question_type or "").lower() == "communication"):
            rec = comm_map.get(qid)
            entry.update({
                "type": "communication",
                "transcript": a.get("transcript") or (rec.transcript if rec else None),
                "communication_score": a.get("communication_score") or (rec.communication_score if rec else None),
                "awarded": a.get("awarded"),
                # Always from the row: answers_json may hold a legacy "/uploads/..." path.
                "audio_url": voice_answer_url(rec.id, current_user) if rec and rec.audio_path else None,
                "breakdown": a.get("breakdown") or (
                    json.loads(rec.breakdown_json) if rec and rec.breakdown_json else {}
                ),
            })
        else:
            entry.update({
                "selected_option": a.get("selected_option"),
                "is_correct": a.get("is_correct"),
                "correct_answer": q.correct_answer if q else None,
                # Judge was unreachable at submit time, so this question was left
                # ungraded AND dropped from total_marks. Without surfacing it, HR
                # sees a confidently-wrong score over a silently shortened
                # denominator. Cleared by POST /api/v1/judge/regrade-pending.
                "pending_regrade": bool(a.get("pending_regrade")),
                "verdict": a.get("verdict"),
                "max_points": a.get("max_points"),
            })
        answers.append(entry)

    return {
        "interview_id": interview_id,
        "test_score": float(interview.test_score) if interview.test_score is not None else None,
        "total_marks": interview.total_marks,
        "has_pending_regrade": any(a.get("pending_regrade") for a in answers),
        "answers": answers,
    }


# ==================== TEST RESULT ACTIONS (Select/Reject/Schedule) ====================

class TestResultActionRequest(BaseModel):
    candidate_id: str
    action: str  # "select" or "reject"
    rejection_reason: Optional[str] = None

@router.post("/test-results/action")
async def test_result_action(
    body: TestResultActionRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Select or reject a candidate after test results review (HR screening round)"""
    candidate = db.query(Candidate).filter(
        Candidate.id == body.candidate_id,
        Candidate.company_id == current_user.company_id
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    _require_job_team(db, candidate.job, current_user)

    if body.action == "select":
        update_candidate_stage(db, body.candidate_id, "interview", current_user.id, "Selected after test — moved to Phone Screen")
        db.commit()
        return {"message": f"{candidate.first_name} {candidate.last_name} moved to Phone Screen", "stage": "interview"}
    elif body.action == "reject":
        update_candidate_stage(db, body.candidate_id, "rejected", current_user.id, body.rejection_reason or "Rejected after test review")
        db.commit()
        from src.services.email_service import notify_candidate_rejected
        notify_candidate_rejected(db, candidate)
        return {"message": f"{candidate.first_name} {candidate.last_name} rejected", "stage": "rejected"}
    else:
        raise HTTPException(status_code=400, detail="Invalid action. Use 'select' or 'reject'")


def _interview_type_from_job_category(job_category: Optional[str]) -> str:
    """Map a job's job_category to a default Interview.interview_type."""
    mapping = {
        "technical": "TECHNICAL",
        "non_technical": "NON_TECHNICAL",
        "mixed": "MIXED",
    }
    return mapping.get((job_category or "technical").lower(), "TECHNICAL")


class ManualPanelist(BaseModel):
    name: str
    email: EmailStr


class ScheduleInterviewFromHRRequest(BaseModel):
    candidate_id: str
    job_id: str
    interview_type: Optional[str] = None
    mode: str = "online"
    scheduled_datetime: str
    duration_minutes: int = 60
    meeting_link: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    send_email: bool = False
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    # Panel interview: InterviewerProfile ids. If omitted, falls back to the job's
    # single assigned interviewer (preserves prior single-interviewer behavior).
    interviewer_ids: Optional[List[str]] = None
    # Ad-hoc panelists (typed name+email, or existing HR/Admin staff picked in the
    # UI) who have no InterviewerProfile. Appended to the panel and matched to a
    # company user by email when possible so they still get the in-app notification.
    manual_panelists: Optional[List[ManualPanelist]] = None
    # "Send from" override for the candidate's interview email. The panel invite
    # keeps its own step mailbox — it goes to staff, not the candidate.
    mailbox_id: Optional[str] = None

@router.post("/schedule-interview")
async def hr_schedule_interview(
    body: ScheduleInterviewFromHRRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """HR schedules an interview with the job's assigned interviewer after test screening"""
    candidate = db.query(Candidate).filter(
        Candidate.id == body.candidate_id,
        Candidate.company_id == current_user.company_id
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    job = db.query(JobPosting).filter(
        JobPosting.id == body.job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_team(db, job, current_user)

    # Panel roster — shared with PUT /hr/interviews/{id}/panel so the two cannot drift.
    # Order is HR's selection order (first = lead); a deactivated pick raises 400.
    panel = build_panel(
        db,
        current_user.company_id,
        interviewer_ids=body.interviewer_ids,
        manual_panelists=body.manual_panelists,
        job=job,
    )

    lead = panel[0] if panel else None
    panel_names = ", ".join(p["name"] for p in panel) if panel else "TBD"

    resolved_type = body.interview_type or _interview_type_from_job_category(getattr(job, "job_category", None))

    interview = Interview(
        title=f"Interview — {candidate.first_name} {candidate.last_name} for {job.title}",
        description=f"{'Online' if body.mode == 'online' else 'Offline'} {resolved_type} interview",
        job_id=body.job_id,
        candidate_id=body.candidate_id,
        company_id=current_user.company_id,
        job_position=job.title,
        job_title=job.title,
        interview_type=resolved_type,
        difficulty_level="MEDIUM",
        scheduled_datetime=body.scheduled_datetime,
        duration_minutes=body.duration_minutes,
        meeting_link=body.meeting_link if body.mode == "online" else None,
        interviewer_name=lead["name"] if lead else "",
        interviewer_email=lead["email"] if lead else "",
        panel_members=json.dumps(panel) if panel else None,
        notes=f"Mode: {body.mode}\n{('Location: ' + body.location) if body.location else ''}\n{body.notes or ''}".strip(),
        is_scheduled_interview=True,
        status="SCHEDULED",
        created_by=current_user.email,
    )
    db.add(interview)

    # Move candidate to technical interview stage
    update_candidate_stage(db, body.candidate_id, "technical_interview", current_user.id, f"{resolved_type} interview scheduled ({body.mode})")
    db.commit()
    db.refresh(interview)

    # Notify every panel member in-app (skip ad-hoc panelists with no account)
    for member in panel:
        if not member.get("user_id"):
            continue
        db.add(Notification(
            user_id=member["user_id"],
            title="Interview Scheduled",
            message=f"Panel interview with {candidate.first_name} {candidate.last_name} for '{job.title}' on {body.scheduled_datetime}. Mode: {body.mode}.",
            type="interview_scheduled",
            reference_type="interview",
            reference_id=str(interview.id),
        ))
    if panel:
        db.commit()

    # Notify candidate (in-app)
    if getattr(candidate, "user_id", None):
        from src.services.notification_service import create_notification
        create_notification(
            db,
            user_id=candidate.user_id,
            company_id=candidate.company_id,
            title="Interview scheduled",
            message=f"Your {body.interview_type} interview for '{job.title}' is scheduled for {body.scheduled_datetime} ({body.mode}).",
            type="interview_scheduled",
            reference_type="interview",
            reference_id=interview.id,
        )

    # Send email to candidate
    email_sent = False
    email_status = "skipped"   # sent | queued | failed | skipped
    email_error = None
    if body.send_email:
        try:
            meeting_link_or_location = (
                body.meeting_link if body.mode == "online" and body.meeting_link
                else (body.location or "TBD")
            )
            interviewer_name_str = panel_names
            candidate_name_str = f"{candidate.first_name} {candidate.last_name}"
            from src.services.pipeline_notifications import resolve_step_sender
            smtp_config, disp = resolve_step_sender(
                db, job.company_id, "interview_scheduled", body.mailbox_id, job=job)
            if body.email_subject or body.email_body:
                # Use custom subject/body supplied by HR, substituting placeholders
                from src.services.email_service import _send, dispatch
                placeholders = {
                    "{{candidate_name}}": candidate_name_str,
                    "{{job_title}}": job.title,
                    "{{interview_type}}": body.interview_type or "",
                    "{{datetime}}": str(body.scheduled_datetime),
                    "{{mode}}": body.mode or "",
                    "{{meeting_details}}": meeting_link_or_location,
                    "{{interviewer_name}}": interviewer_name_str,
                }
                subj = body.email_subject or f"Interview Scheduled – {job.title}"
                html = body.email_body or ""
                for k, v in placeholders.items():
                    subj = subj.replace(k, v)
                    html = html.replace(k, v)
                send_result = _send(candidate.email, subj, html.replace("\n", "<br>"),
                                    smtp_config=smtp_config, cc=disp["cc"])
            else:
                from src.services.email_service import send_interview_scheduled
                send_result = send_interview_scheduled(
                    candidate.email,
                    candidate_name_str,
                    job.title,
                    body.scheduled_datetime,
                    meeting_link_or_location,
                    interviewer_name_str,
                    smtp_config=smtp_config,
                    cc=disp["cc"]
                )
            # These senders report failure by return value rather than by raising,
            # so the result has to be read — reaching this line is not proof the
            # candidate got anything.
            from src.services.email_service import delivery_outcome
            outcome = delivery_outcome(send_result)
            email_status = outcome["status"]
            email_sent = outcome["ok"]          # true for sent OR queued
            email_error = outcome["error"]
            if not outcome["ok"]:
                logger.warning(
                    f"interview-scheduled email to {candidate.email} not delivered: "
                    f"{outcome['error_type']} — {outcome['error']}"
                )
        except Exception as e:
            email_sent = False
            email_status = "failed"
            email_error = str(e)
            logger.warning(f"interview-scheduled email to {candidate.email} failed: {e}")

    # Invite each panel member by email (independent of the candidate email toggle —
    # panelists are the staff conducting the interview). Never let a failure here
    # break scheduling.
    if panel:
        try:
            from src.services.email_service import send_panel_invite, resolve_sender
            from src.services.pipeline_notifications import resolve_step_dispatch
            panel_disp = resolve_step_dispatch(db, job.company_id, "panel_invite",
                                               interview=interview, job=job)
            smtp_config = panel_disp["smtp_config"] or resolve_sender(db, job.company_id, "candidate")
            meeting_details = (
                body.meeting_link if body.mode == "online" and body.meeting_link
                else (body.location or "TBD")
            )
            candidate_full = f"{candidate.first_name} {candidate.last_name}"
            panel_member_emails = {p["email"] for p in panel}
            # CC the configured extras only once (on the first invite), and never a
            # panelist who already receives their own invite as the primary recipient.
            extra_cc = [e for e in panel_disp["cc"] if e not in panel_member_emails]
            for idx, member in enumerate(panel):
                fellow = [p["name"] for p in panel if p["email"] != member["email"]]
                try:
                    send_panel_invite(
                        member["email"], member["name"], candidate_full, job.title,
                        str(body.scheduled_datetime), meeting_details, fellow,
                        smtp_config=smtp_config,
                        cc=(extra_cc if idx == 0 else None),
                    )
                except Exception as _pe:
                    logger.warning(f"panel-invite email to {member['email']} failed: {_pe}")
        except Exception as e:
            logger.warning(f"panel-invite dispatch failed: {e}")

    email_note = {
        "sent": " — email sent to candidate",
        "queued": " — email queued for delivery",
        "failed": " — WARNING: candidate email could not be sent",
    }.get(email_status, "")

    return {
        "message": f"Panel interview scheduled with {panel_names}" + email_note,
        "interview_id": interview.id,
        "interviewer": lead["email"] if lead else None,
        "panel": [p["email"] for p in panel],
        "mode": body.mode,
        "scheduled_at": body.scheduled_datetime,
        "email_sent": email_sent,
        "email_status": email_status,
        "email_error": email_error,
    }


class UpdatePanelRequest(BaseModel):
    interviewer_ids: Optional[List[str]] = None
    manual_panelists: Optional[List[ManualPanelist]] = None
    # Off switches for a silent correction (fixing a typo'd name, say).
    notify: bool = True


@router.put("/interviews/{interview_id}/panel")
async def update_interview_panel(
    interview_id: int,
    body: UpdatePanelRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Change who is on a scheduled interview's panel.

    Until this existed, panel_members was write-once: set at creation, and otherwise only
    touched by the deactivation cascade. Swapping an interviewer meant cancelling the
    interview and booking a new one.

    Only the delta is notified. Re-inviting the whole panel on every edit trains people to
    ignore the invite, and the unchanged members already have the meeting.
    """
    interview = db.query(Interview).filter(
        Interview.id == interview_id,
        Interview.company_id == current_user.company_id,
    ).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    if not interview.is_scheduled_interview:
        raise HTTPException(
            status_code=409,
            detail="Only a scheduled interview has a panel. Tests are edited in the wizard.",
        )
    if (interview.status or "").upper() not in ("SCHEDULED", "RESCHEDULED"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot re-panel an interview that is {interview.status}.",
        )

    job = interview.job
    _require_job_team(db, job, current_user)
    old_panel = parse_panel(interview)
    new_panel = build_panel(
        db,
        current_user.company_id,
        interviewer_ids=body.interviewer_ids,
        manual_panelists=body.manual_panelists,
        job=None,          # no silent fallback to the job default on an explicit edit
    )
    added, removed = diff_panel(old_panel, new_panel)

    apply_panel(interview, new_panel)
    interview.updated_at = datetime.utcnow()

    candidate = interview.candidate
    candidate_full = (
        f"{candidate.first_name} {candidate.last_name}" if candidate else "the candidate"
    )
    job_title = job.title if job else (interview.job_title or "the role")
    when = interview.scheduled_datetime

    from src.services.notification_service import create_notification

    if body.notify:
        for member in added:
            if member.get("user_id"):
                create_notification(
                    db,
                    user_id=member["user_id"],
                    company_id=current_user.company_id,
                    title="Added to an interview panel",
                    message=f"You are on the panel for {candidate_full} — '{job_title}' on {when}.",
                    type="interview_scheduled",
                    reference_type="interview",
                    reference_id=interview.id,
                    commit=False,
                )
        for member in removed:
            if member.get("user_id"):
                create_notification(
                    db,
                    user_id=member["user_id"],
                    company_id=current_user.company_id,
                    title="Removed from an interview panel",
                    message=f"You are no longer on the panel for {candidate_full} — '{job_title}'.",
                    type="interview_scheduled",
                    reference_type="interview",
                    reference_id=interview.id,
                    commit=False,
                )

    db.commit()
    db.refresh(interview)

    # Invite only the newly added, and never let a mail failure undo the panel change.
    if body.notify and added:
        try:
            from src.services.email_service import send_panel_invite, resolve_sender
            from src.services.pipeline_notifications import resolve_step_dispatch
            company_id = job.company_id if job else current_user.company_id
            panel_disp = resolve_step_dispatch(db, company_id, "panel_invite",
                                               interview=interview, job=job)
            smtp_config = panel_disp["smtp_config"] or resolve_sender(db, company_id, "candidate")
            meeting_details = interview.meeting_link or "TBD"
            member_emails = {p["email"] for p in new_panel}
            extra_cc = [e for e in panel_disp["cc"] if e not in member_emails]
            for idx, member in enumerate(added):
                fellow = [p["name"] for p in new_panel if p["email"] != member["email"]]
                try:
                    send_panel_invite(
                        member["email"], member["name"], candidate_full, job_title,
                        str(when), meeting_details, fellow,
                        smtp_config=smtp_config,
                        cc=(extra_cc if idx == 0 else None),
                    )
                except Exception as e:
                    logger.warning(f"panel invite to {member['email']} failed: {e}")
        except Exception as e:
            logger.warning(f"panel invite dispatch failed for interview {interview.id}: {e}")

    return {
        "interview_id": interview.id,
        "panel": [p["email"] for p in new_panel],
        "added": [p["email"] for p in added],
        "removed": [p["email"] for p in removed],
        "needs_interviewer": not new_panel,
    }



# ==================== ATS SCREENING ENDPOINTS ====================

@router.get("/jobs/{job_id}/candidates", response_model=JobCandidatesEnhancedResponse)
async def get_job_candidates_enhanced(
    job_id: str,
    sort_by: str = Query("ats_score", description="Sort by field"),
    order: str = Query("desc", description="Sort order"),
    min_score: Optional[float] = Query(None, description="Minimum ATS score filter"),
    max_score: Optional[float] = Query(None, description="Maximum ATS score filter"),
    stage: Optional[str] = Query(None, description="Filter by candidate stage"),
    search: Optional[str] = Query(None, description="Search in candidate names and emails"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Get enhanced candidates for a job with optimized ATS ranking and color coding
    
    Enhanced Features:
    - ATS score sorting with color coding and priority
    - Advanced filtering options with pagination
    - Search functionality across multiple fields
    - Detailed ATS breakdown with interpretation
    - Performance optimizations for large datasets
    """
    try:
        # Validate pagination parameters
        page, per_page = validate_pagination_params(page, per_page, 100)
        
        # Validate sort parameters
        valid_sort_fields = ["ats_score", "applied_at", "name"]
        sort_by = validate_sort_params(sort_by, valid_sort_fields, "ats_score")
        sort_order = validate_order_params(order)
        
        # Check if job exists and permissions
        job = db.query(JobPosting).filter(
            JobPosting.id == job_id,
            JobPosting.company_id == current_user.company_id,
        ).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        if current_user.role != "admin":
            hr_profile = get_hr_profile(db, current_user)
            if not can_access_job(db, job, hr_profile):
                raise HTTPException(status_code=403, detail="Access denied")
        
        # Subquery: get only the latest ATSResult per candidate
        # (guards against duplicates when ATS scoring ran more than once)
        latest_ats_subq = db.query(
            ATSResult.candidate_id.label('cid'),
            func.max(ATSResult.id).label('latest_id')
        ).group_by(ATSResult.candidate_id).subquery()

        # Build optimized query with proper joins
        query = db.query(Candidate, ATSResult).join(
            latest_ats_subq, Candidate.id == latest_ats_subq.c.cid
        ).join(
            ATSResult, ATSResult.id == latest_ats_subq.c.latest_id
        ).filter(Candidate.job_id == job_id,
                 # A merged duplicate is kept for undo, not shown as a second person.
                 Candidate.merged_into_id.is_(None))
        
        # Apply filters efficiently
        if min_score is not None:
            query = query.filter(effective_score_col() >= min_score)

        if max_score is not None:
            query = query.filter(effective_score_col() <= max_score)
        
        if stage:
            query = query.filter(Candidate.current_stage == stage)
        
        if search:
            search_filter = or_(
                Candidate.first_name.ilike(f"%{search}%"),
                Candidate.last_name.ilike(f"%{search}%"),
                Candidate.email.ilike(f"%{search}%")
            )
            query = query.filter(search_filter)
        
        # Get total count for pagination
        total_count = query.count()
        
        # Apply sorting
        if sort_by == "ats_score":
            if sort_order == "desc":
                query = query.order_by(desc(effective_score_col()))
            else:
                query = query.order_by(effective_score_col())
        elif sort_by == "applied_at":
            if sort_order == "desc":
                query = query.order_by(desc(Candidate.created_at))
            else:
                query = query.order_by(Candidate.created_at)
        elif sort_by == "name":
            if sort_order == "desc":
                query = query.order_by(desc(Candidate.last_name), desc(Candidate.first_name))
            else:
                query = query.order_by(Candidate.last_name, Candidate.first_name)
        
        # Apply pagination
        offset = (page - 1) * per_page
        candidates = query.offset(offset).limit(per_page).all()
        
        # Job-description version numbers, for "scored against v2 (current v3)".
        from src.models.models import JobDescriptionVersion
        from src.services.jd_versions import staleness
        from src.services.enhanced_ats_service import SCORING_RULES_VERSION
        from src.services import parse_quality as _pq
        jd_no = {v.id: v.version_no for v in db.query(JobDescriptionVersion).filter(
            JobDescriptionVersion.job_id == job_id).all()}

        # Create enhanced candidate responses with error handling
        result = []
        for candidate, ats_result in candidates:
            try:
                eff = effective_score(ats_result) or 0.0
                ats_interpretation = get_ats_interpretation(eff)
                stale = staleness(ats_result, job, SCORING_RULES_VERSION)
                try:
                    issues = json.loads(ats_result.parse_issues) if ats_result.parse_issues else []
                except (TypeError, ValueError):
                    issues = []

                # Parse the stored breakdown JSON for the full recruiter view.
                plagiarism_flag = False
                jd_overlap = None
                breakdown = None
                edu_score = cert_score = proj_score = 0.0
                rating = recommendation = None
                strengths = weaknesses = []
                missing_preferred = []
                try:
                    breakdown = ats_result.scoring_breakdown
                    if isinstance(breakdown, str):
                        breakdown = json.loads(breakdown)
                    breakdown = breakdown or {}
                    plag = breakdown.get("plagiarism") or {}
                    plagiarism_flag = bool(plag.get("flagged", False))
                    jd_overlap = plag.get("jd_overlap")
                    ind = breakdown.get("individual_scores") or {}
                    edu_score = ind.get("education", 0.0)
                    cert_score = ind.get("certification", 0.0)
                    proj_score = ind.get("project", 0.0)
                    rating = breakdown.get("rating")
                    recommendation = breakdown.get("hiring_recommendation")
                    strengths = breakdown.get("strengths") or []
                    weaknesses = breakdown.get("weaknesses") or []
                    missing_preferred = breakdown.get("missing_preferred_skills") or []
                except (ValueError, TypeError, AttributeError):
                    breakdown = None

                result.append(ATSCandidateResponse(
                    candidate_id=str(candidate.id),
                    name=f"{candidate.first_name} {candidate.last_name}",
                    email=candidate.email,
                    phone=candidate.phone,
                    current_stage=candidate.current_stage,
                    ats_score=eff,
                    ats_level=ats_interpretation['level'],
                    ats_color=ats_interpretation['color'],
                    skills_match_score=ats_result.skills_match_score,
                    semantic_score=ats_result.semantic_score,
                    experience_score=ats_result.experience_score,
                    keyword_score=ats_result.keyword_score,
                    matched_skills=json.loads(ats_result.matched_skills) if isinstance(ats_result.matched_skills, str) else (ats_result.matched_skills or []),
                    missing_skills=json.loads(ats_result.missing_skills) if isinstance(ats_result.missing_skills, str) else (ats_result.missing_skills or []),
                    extracted_skills=json.loads(candidate.extracted_skills) if isinstance(candidate.extracted_skills, str) else (candidate.extracted_skills or []),
                    experience_years=candidate.experience_years,
                    applied_at=candidate.created_at,
                    resume_url=application_file_url(candidate, "resume", current_user),
                    photo_url=application_file_url(candidate, "photo", current_user),
                    plagiarism_flag=plagiarism_flag,
                    jd_overlap=jd_overlap,
                    education_score=edu_score,
                    certification_score=cert_score,
                    project_score=proj_score,
                    rating=rating,
                    hiring_recommendation=recommendation,
                    strengths=strengths,
                    weaknesses=weaknesses,
                    missing_preferred_skills=missing_preferred,
                    scoring_breakdown=breakdown,
                    model_score=float(ats_result.ats_score) if ats_result.ats_score is not None else None,
                    override_score=ats_result.override_score,
                    is_overridden=ats_result.override_score is not None,
                    parse_status=ats_result.parse_status,
                    parse_issues=_pq.describe(issues),
                    mandatory_met=ats_result.mandatory_met,
                    mandatory_total=ats_result.mandatory_total,
                    evidence_coverage=ats_result.evidence_coverage,
                    scoring_source=ats_result.scoring_source,
                    scoring_rules_version=ats_result.scoring_rules_version,
                    jd_version_no=jd_no.get(ats_result.jd_version_id),
                    is_stale=stale in ("jd_changed", "rules_updated"),
                    stale_reason=stale,
                    scored_at=ats_result.scored_at,
                    source=candidate.source,
                    source_label=SOURCES.get(candidate.source, "Not recorded"),
                    source_detail=candidate.source_detail,
                ))
            except Exception as candidate_error:
                print(f"Error processing candidate {candidate.id}: {candidate_error}")
                continue
        
        # Calculate ATS distribution using shared utility
        ats_distribution = calculate_ats_distribution([c.ats_score for c in result if c.ats_score])
        
        return JobCandidatesEnhancedResponse(
            job_id=job_id,
            job_title=job.title,
            total_candidates=total_count,
            candidates=result,
            ats_distribution=ats_distribution,
            current_rules_version=SCORING_RULES_VERSION,
            current_jd_version_no=jd_no.get(job.current_jd_version_id),
            rules_versions_present=sorted({c.scoring_rules_version for c in result if c.scoring_rules_version}),
            needs_review_count=sum(1 for c in result if c.parse_status == "needs_review"),
        )
        
    except HTTPException:
        raise  # Re-raise HTTP exceptions
    except Exception as e:
        import traceback
        print(f"Error in get_job_candidates_enhanced: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error processing candidates")

@router.post("/jobs/{job_id}/select-candidates", response_model=BulkActionResponse)
async def select_candidates_bulk(
    job_id: str,
    selection: CandidateSelectionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Enhanced candidate selection with multiple strategies
    
    Selection Types:
    - 'top_n': Select top N candidates by ATS score
    - 'score_threshold': Select candidates above minimum score
    - 'top_percentile': Select top X% candidates by ATS score
    - 'manual': Select specific candidate IDs
    
    Features:
    - Bulk stage movement
    - Automatic rejection option
    - Detailed processing feedback
    """
    # Check if job exists and permissions
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if current_user.role != "admin":
        hr_profile = get_hr_profile(db, current_user)
        if not can_access_job(db, job, hr_profile):
            raise HTTPException(status_code=403, detail="Access denied")

    # Get all candidates for this job
    all_candidates = db.query(Candidate).filter(Candidate.job_id == job_id).all()
    selected_candidates = []
    held_ids = needs_review_candidate_ids(db, job_id)
    # Only results whose resume was read reliably take part in a ranking. NULL = scored
    # before parse status existed, treated as ok.
    _ranked = or_(ATSResult.parse_status.is_(None), ATSResult.parse_status != "needs_review")
    
    # Apply selection strategy
    if selection.selection_type == "top_n":
        if not selection.top_n:
            raise HTTPException(status_code=400, detail="top_n is required for top_n selection")
        
        # Get ATS results for sorting
        candidates_with_ats = db.query(Candidate, ATSResult).join(
            ATSResult, Candidate.id == ATSResult.candidate_id
        ).filter(Candidate.job_id == job_id, _ranked).order_by(desc(effective_score_col())).limit(selection.top_n).all()
        
        selected_candidates = [c[0] for c in candidates_with_ats]
        
    elif selection.selection_type == "score_threshold":
        if not selection.min_score:
            raise HTTPException(status_code=400, detail="min_score is required for score_threshold selection")
        
        candidates_with_ats = db.query(Candidate, ATSResult).join(
            ATSResult, Candidate.id == ATSResult.candidate_id
        ).filter(
            Candidate.job_id == job_id,
            _ranked,
            effective_score_col() >= selection.min_score
        ).all()
        
        selected_candidates = [c[0] for c in candidates_with_ats]
        
    elif selection.selection_type == "top_percentile":
        if not selection.percentile:
            raise HTTPException(status_code=400, detail="percentile is required for top_percentile selection (1-100)")

        candidates_with_ats = db.query(Candidate, ATSResult).join(
            ATSResult, Candidate.id == ATSResult.candidate_id
        ).filter(Candidate.job_id == job_id, _ranked).order_by(desc(effective_score_col())).all()

        if candidates_with_ats:
            import math
            count_to_select = max(1, math.ceil(len(candidates_with_ats) * selection.percentile / 100))
            selected_candidates = [c[0] for c in candidates_with_ats[:count_to_select]]

    elif selection.selection_type == "manual":
        if not selection.candidate_ids:
            raise HTTPException(status_code=400, detail="candidate_ids is required for manual selection")

        selected_candidates = db.query(Candidate).filter(
            Candidate.id.in_(selection.candidate_ids),
            Candidate.job_id == job_id
        ).all()

    else:
        raise HTTPException(status_code=400, detail=f"Invalid selection_type: {selection.selection_type}")
    
    if not selected_candidates:
        raise HTTPException(status_code=404, detail="No valid candidates found for selection")
    
    # Move selected candidates to next stage
    moved_count = 0
    for candidate in selected_candidates:
        if candidate.current_stage == "applied":
            candidate.current_stage = selection.next_stage
            candidate.updated_at = datetime.utcnow()
            
            # Create stage history
            stage_history = CandidateStageHistory(
                candidate_id=candidate.id,
                from_stage="applied",
                to_stage=selection.next_stage,
                changed_by_id=current_user.id,
                notes=f"Selected via {selection.selection_type} strategy"
            )
            db.add(stage_history)
            moved_count += 1
    
    # Optionally reject others
    rejected_count = 0
    rejected_candidates = []
    if selection.reject_others:
        selected_id_set = {c.id for c in selected_candidates}
        for candidate in all_candidates:
            if candidate.id not in selected_id_set and candidate.current_stage not in ("rejected", "hired"):
                if candidate.id in held_ids:
                    continue
                prev_stage = candidate.current_stage
                candidate.current_stage = "rejected"
                candidate.updated_at = datetime.utcnow()

                # Create stage history
                stage_history = CandidateStageHistory(
                    candidate_id=candidate.id,
                    from_stage=prev_stage,
                    to_stage="rejected",
                    changed_by_id=current_user.id,
                    notes=selection.rejection_reason or "Auto-rejected during candidate selection"
                )
                db.add(stage_history)
                rejected_count += 1
                rejected_candidates.append(candidate)

    db.commit()

    # Send rejection emails after the transaction commits
    from src.services.email_service import notify_candidate_rejected
    for c in rejected_candidates:
        notify_candidate_rejected(db, c)

    held = len([c for c in all_candidates if c.id in held_ids
                and c.current_stage not in ("rejected", "hired")
                and c.id not in {x.id for x in selected_candidates}])
    return BulkActionResponse(
        message=f"Successfully processed {len(selected_candidates)} candidates using {selection.selection_type} strategy"
                + (f". {held} held for manual review (resume could not be read reliably)." if held else ""),
        selected_count=moved_count,
        rejected_count=rejected_count,
        moved_to_stage=selection.next_stage,
        job_id=job_id,
        held_for_review=held,
    )

@router.post("/jobs/{job_id}/move-to-screening")
async def move_candidates_to_screening(
    job_id: str,
    request: MoveToScreeningRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Move selected candidates to screening phase (PHASE 3)
    
    - **job_id**: Job ID
    - **candidate_ids**: List of candidate IDs to move to screening
    - **reject_others**: Whether to reject other candidates automatically
    """
    # Check job exists and permissions
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if current_user.role != "admin":
        hr_profile = get_hr_profile(db, current_user)
        if not can_access_job(db, job, hr_profile):
            raise HTTPException(status_code=403, detail="Access denied")

    # Get all candidates for this job
    all_candidates = db.query(Candidate).filter(Candidate.job_id == job_id).all()
    selected_candidates = db.query(Candidate).filter(
        Candidate.id.in_(request.candidate_ids),
        Candidate.job_id == job_id
    ).all()
    
    if not selected_candidates:
        raise HTTPException(status_code=404, detail="No valid candidates found")
    
    # Move selected candidates to screening
    moved_count = 0
    for candidate in selected_candidates:
        if candidate.current_stage == "applied":
            update_candidate_stage(
                db, candidate.id, "screening", current_user.id,
                "Moved to screening by HR"
            )
            moved_count += 1
    
    # Optionally reject others
    rejected_count = 0
    rejected_candidates = []
    held_for_review = 0
    if request.reject_others:
        selected_id_set = {c.id for c in selected_candidates}
        held_ids = needs_review_candidate_ids(db, job_id)
        for candidate in all_candidates:
            if candidate.id not in selected_id_set and candidate.current_stage not in ("rejected", "hired"):
                if candidate.id in held_ids:      # resume unreadable: a person decides
                    held_for_review += 1
                    continue
                update_candidate_stage(
                    db, candidate.id, "rejected", current_user.id,
                    "Auto-rejected during screening selection"
                )
                rejected_count += 1
                rejected_candidates.append(candidate)

    db.commit()

    # Send rejection emails after the transaction commits
    from src.services.email_service import notify_candidate_rejected
    for c in rejected_candidates:
        notify_candidate_rejected(db, c)

    return {
        "message": "Candidates moved to screening successfully",
        "moved_to_screening": moved_count,
        "auto_rejected": rejected_count,
        "held_for_review": held_for_review,
        "job_id": job_id
    }

# ==================== INTERVIEW MANAGEMENT ENDPOINTS ====================

@router.post("/interviews")
async def schedule_interview(
    interview_data: InterviewCreate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Schedule an interview (PHASE 7)
    
    - **candidate_id**: Candidate ID
    - **interviewer_id**: Interviewer ID
    - **interview_type**: Type of interview (technical, behavioral, hr, final)
    - **scheduled_datetime**: Interview date and time
    - **duration_minutes**: Interview duration in minutes
    - **meeting_link**: Optional meeting link
    """
    # Validate candidate — in the caller's company, on a job the caller is on. Looked up
    # by id alone, this scheduled interviews on another company's candidate, moved their
    # pipeline stage and emailed them from that company's own mail server.
    candidate = db.query(Candidate).filter(
        Candidate.id == interview_data.candidate_id,
        Candidate.company_id == current_user.company_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if not candidate.job_id:
        raise HTTPException(status_code=400, detail="Candidate has no associated job — cannot schedule interview")
    _require_job_team(db, candidate.job, current_user)

    # Check if an interview already exists for this specific candidate + job combination
    existing = db.query(Interview).filter(
        Interview.job_id == candidate.job_id,
        Interview.candidate_id == interview_data.candidate_id,
        Interview.status.notin_(["CANCELLED", "RESCHEDULED"])
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"An interview already exists for this candidate (Interview ID: {existing.id}, Status: {existing.status})."
        )

    # Validate interviewer
    from src.models.models import InterviewerProfile
    # Tenant scoping was absent here — this matched on id alone, across companies.
    interviewer = active_interviewer_query(db, current_user.company_id).filter(
        InterviewerProfile.id == interview_data.interviewer_id
    ).first()
    if not interviewer:
        raise HTTPException(status_code=404, detail="Interviewer not found or deactivated")
    
    # Create interview
    _mode = "online" if interview_data.meeting_link else "offline"
    job_for_default = db.query(JobPosting).filter(
        JobPosting.id == candidate.job_id,
        JobPosting.company_id == current_user.company_id,
    ).first()
    resolved_type = interview_data.interview_type or _interview_type_from_job_category(
        getattr(job_for_default, "job_category", None) if job_for_default else None
    )
    db_interview = Interview(
        candidate_id=interview_data.candidate_id,
        job_id=candidate.job_id,
        company_id=current_user.company_id,
        interview_type=resolved_type,
        scheduled_datetime=interview_data.scheduled_datetime,
        duration_minutes=interview_data.duration_minutes,
        meeting_link=interview_data.meeting_link,
        interviewer_name=f"{interviewer.user.first_name} {interviewer.user.last_name}",
        interviewer_email=interviewer.user.email,
        notes=f"Mode: {_mode}",
        is_scheduled_interview=True,
        status="SCHEDULED",
        created_by=current_user.email,
    )

    db.add(db_interview)

    # Update candidate stage
    update_candidate_stage(
        db, candidate.id, "interview_scheduled", current_user.id,
        f"Scheduled {resolved_type} interview"
    )

    db.commit()
    db.refresh(db_interview)

    # Notify candidate (in-app)
    if getattr(candidate, "user_id", None):
        from src.services.notification_service import create_notification
        create_notification(
            db,
            user_id=candidate.user_id,
            company_id=candidate.company_id,
            title="Interview scheduled",
            message=f"Your {resolved_type} interview for '{db_interview.job.title if db_interview.job else 'a role'}' is scheduled for {interview_data.scheduled_datetime}.",
            type="interview_scheduled",
            reference_type="interview",
            reference_id=db_interview.id,
        )

    from src.services.email_service import send_interview_scheduled
    from src.services.pipeline_notifications import resolve_step_sender
    if candidate.email:
        try:
            _cid = getattr(candidate, "company_id", None)
            smtp_config, disp = resolve_step_sender(
                db, _cid, "interview_scheduled", interview_data.mailbox_id,
                job=db_interview.job)
            send_interview_scheduled(
                candidate.email,
                f"{candidate.first_name} {candidate.last_name}",
                db_interview.job.title if db_interview.job else "",
                str(interview_data.scheduled_datetime),
                db_interview.meeting_link or "",
                db_interview.interviewer_name or "",
                smtp_config=smtp_config,
                cc=disp["cc"]
            )
        except Exception as _e:
            logger.warning(f"interview-scheduled email to {candidate.email} failed: {_e}")

    return {
        "message": "Interview scheduled successfully",
        "interview_id": str(db_interview.id),
        "candidate_name": f"{candidate.first_name} {candidate.last_name}",
        "interviewer_name": db_interview.interviewer_name,
        "scheduled_datetime": db_interview.scheduled_datetime
    }

class DashboardCounts(BaseModel):
    jobs: int
    active_jobs: int
    interviews: int
    offers: int


def _dashboard_counts(db: Session, current_user: User, own_only: bool, hr_profile) -> DashboardCounts:
    """The counting itself, shared by GET /dashboard/counts and GET /my-activity.

    Extracted rather than copied precisely because of the "fix both together or
    neither" rule in the caller's docstring — a second copy of these queries is
    a second thing to forget.

    `own_only` is the caller's decision: the dashboard grants admins a
    company-wide view, while a personal profile page always means "mine".
    """
    # ── jobs ── mirrors GET /jobs
    jobs_q = db.query(func.count(JobPosting.id)).filter(
        JobPosting.company_id == current_user.company_id
    )
    if own_only:
        jobs_q = jobs_q.filter(job_access_filter(hr_profile.id))
    jobs = jobs_q.scalar() or 0

    # The card reads "Active Jobs" and the click filters status=active, so count
    # exactly that one status rather than "not closed".
    # .filter() returns a new Query rather than mutating jobs_q, so this is the same
    # scoping narrowed by one status, not a second pass over a consumed query.
    active_jobs = jobs_q.filter(JobPosting.status == "active").scalar() or 0

    # ── interviews ── mirrors GET /interviews
    iv_q = db.query(func.count(Interview.id)).join(
        JobPosting, Interview.job_id == JobPosting.id
    )
    if current_user.company_id:
        iv_q = iv_q.filter(JobPosting.company_id == current_user.company_id)
    if own_only:
        iv_q = iv_q.filter(job_access_filter(hr_profile.id))
    interviews = iv_q.scalar() or 0

    # ── offers ── mirrors GET /offers
    of_q = db.query(func.count(Offer.id)).join(Candidate).join(JobPosting)
    if current_user.company_id:
        of_q = of_q.filter(JobPosting.company_id == current_user.company_id)
    if own_only:
        # Job-based, not Offer.hr_id: a co-HR's offer on a shared job is the owner's too.
        of_q = of_q.filter(job_access_filter(hr_profile.id))
    offers = of_q.scalar() or 0

    return DashboardCounts(
        jobs=jobs, active_jobs=active_jobs, interviews=interviews, offers=offers
    )


@router.get("/dashboard/counts", response_model=DashboardCounts)
async def get_dashboard_counts(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Totals for the HR overview cards.

    The cards used to count the arrays the dashboard had already fetched, but
    GET /jobs, /interviews and /offers all default to limit=50 and the dashboard
    passes no limit — so an HR with 80 jobs saw "50", and the number silently
    stopped moving at the cap. Counting in SQL is also cheaper than shipping
    hundreds of serialised rows just to take their length.

    Each count repeats the scoping of the list endpoint it describes,
    deliberately and line for line, so a card can never disagree with the list it
    opens. That includes the `if current_user.company_id:` guards on interviews and
    offers, which DASHBOARD_DATA_PITFALLS.md #2 rightly calls a mistake — but
    fixing it here and not there would make the count and the list disagree, which
    is the one thing this endpoint exists to prevent. Fix both together or neither.
    """
    # Admins see every job in the company; everyone else is scoped to their own,
    # matching the list endpoints.
    own_only = current_user.role != "admin"
    hr_profile = get_hr_profile(db, current_user) if own_only else None
    return _dashboard_counts(db, current_user, own_only, hr_profile)


@router.get("/interviews")
async def get_scheduled_interviews(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get scheduled interviews"""
    query = db.query(Interview).join(JobPosting, Interview.job_id == JobPosting.id)
    if current_user.company_id:
        query = query.filter(JobPosting.company_id == current_user.company_id)

    if current_user.role != "admin":
        hr_profile = get_hr_profile(db, current_user)
        query = query.filter(job_access_filter(hr_profile.id))

    if status:
        # Stored uppercase; normalise the client's casing so ?status=scheduled works.
        query = query.filter(func.upper(Interview.status) == status.upper())

    interviews = query.order_by(Interview.created_at.desc()).offset(skip).limit(limit).all()

    result = []
    for iv in interviews:
        candidate_name = "—"
        candidate_email = ""
        candidate_phone = ""
        resume_url = None
        candidate_id = iv.candidate_id
        if iv.candidate:
            candidate_name = f"{iv.candidate.first_name} {iv.candidate.last_name}"
            candidate_email = iv.candidate.email or ""
            candidate_phone = iv.candidate.phone or ""
            resume_url = application_file_url(iv.candidate, "resume", current_user)
        job_title = iv.job_title or (iv.job.title if iv.job else "—")

        # Detect mode from notes
        mode = "online"
        location = ""
        if iv.notes:
            if "Mode: offline" in iv.notes:
                mode = "offline"
            for line in iv.notes.split("\n"):
                if line.startswith("Location:"):
                    location = line.replace("Location:", "").strip()

        panel_members = json.loads(iv.panel_members) if iv.panel_members else []

        result.append({
            "id": iv.id,
            "title": iv.title,
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "candidate_email": candidate_email,
            "candidate_phone": candidate_phone,
            "resume_url": resume_url,
            "job_title": job_title,
            "interview_type": iv.interview_type,
            "scheduled_datetime": iv.scheduled_datetime,
            "duration_minutes": iv.duration_minutes,
            "meeting_link": iv.meeting_link,
            "mode": mode,
            "location": location,
            "status": iv.status,
            "interviewer_name": iv.interviewer_name or "—",
            "interviewer_email": iv.interviewer_email or "",
            "panel_members": panel_members,
            "notes": iv.notes,
            "created_at": iv.created_at,
            "is_scheduled_interview": bool(iv.is_scheduled_interview),
            # Derived, not stored: a scheduled interview with an empty panel and no lead
            # has nobody to run it — usually its interviewer was deactivated.
            "needs_interviewer": (
                bool(iv.is_scheduled_interview)
                and not panel_members
                and not (iv.interviewer_email or "").strip()
            ),
        })

    return result

# ==================== OFFER MANAGEMENT ENDPOINTS ====================

@router.post("/offers")
async def create_offer(
    offer_data: OfferCreate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Create and send an offer (PHASE 10)
    
    - **candidate_id**: Candidate ID
    - **offer_type**: Type of offer (full_time, contract, internship)
    - **salary**: Offered salary
    - **bonus**: Optional bonus
    - **start_date**: Start date
    - **expiry_date**: Offer expiry date
    - **terms**: Offer terms and conditions
    """
    # Validate candidate — in the caller's company, on a job the caller is on. By id
    # alone this sent a forged offer letter to another company's candidate from that
    # company's own mail server.
    candidate = db.query(Candidate).filter(
        Candidate.id == offer_data.candidate_id,
        Candidate.company_id == current_user.company_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if not candidate.job_id:
        raise HTTPException(status_code=400, detail="Candidate has no associated job — cannot create offer")
    _require_job_team(db, candidate.job, current_user)
    
    # Get HR profile
    hr_profile = get_hr_profile(db, current_user)
    
    # Parse dates
    from datetime import date as date_type
    try:
        start = datetime.strptime(offer_data.start_date, "%Y-%m-%d").date() if isinstance(offer_data.start_date, str) else offer_data.start_date
        expiry = datetime.strptime(offer_data.expiry_date, "%Y-%m-%d").date() if isinstance(offer_data.expiry_date, str) else offer_data.expiry_date
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    # Create offer
    db_offer = Offer(
        candidate_id=offer_data.candidate_id,
        job_id=candidate.job_id,
        hr_id=hr_profile.id,
        company_id=current_user.company_id,
        offer_type=offer_data.offer_type,
        salary=offer_data.salary,
        bonus=offer_data.bonus,
        start_date=start,
        expiry_date=expiry,
        terms=offer_data.terms,
        status="sent",
        sent_at=datetime.utcnow()
    )
    
    db.add(db_offer)

    # Update candidate stage
    update_candidate_stage(
        db, candidate.id, "offer_sent", current_user.id,
        "Offer sent to candidate"
    )

    db.commit()
    db.refresh(db_offer)

    # Notify candidate (in-app)
    if getattr(candidate, "user_id", None):
        from src.services.notification_service import create_notification
        create_notification(
            db,
            user_id=candidate.user_id,
            company_id=candidate.company_id,
            title="You have an offer! 🎉",
            message=f"An offer for '{candidate.job.title if candidate.job else 'a role'}' has been sent to you. Review it in your Interviews tab.",
            type="offer",
            reference_type="offer",
            reference_id=db_offer.id,
        )

    from src.services.email_service import send_offer_letter, delivery_outcome
    from src.services.pipeline_notifications import resolve_step_sender
    # The offer itself is already created and visible in the candidate's portal —
    # that is independent of whether the notification email got out. Report the
    # two separately instead of claiming both succeeded.
    email_sent = False
    email_status = "skipped"
    email_error = None
    if candidate.email:
        try:
            _cid = getattr(candidate, "company_id", None)
            smtp_config, disp = resolve_step_sender(
                db, _cid, "offer_letter", offer_data.mailbox_id, job=candidate.job)
            send_result = send_offer_letter(
                candidate.email,
                f"{candidate.first_name} {candidate.last_name}",
                candidate.job.title if candidate.job else "",
                float(offer_data.salary),
                float(offer_data.bonus) if offer_data.bonus else 0.0,
                str(start),
                str(expiry),
                offer_data.offer_type or "",
                offer_data.terms or "",
                smtp_config=smtp_config,
                cc=disp["cc"]
            )
            # send_* reports failure by return value, not by raising — the
            # try/except above never saw a failed send.
            outcome = delivery_outcome(send_result)
            email_status = outcome["status"]
            email_sent = outcome["ok"]
            email_error = outcome["error"]
            if not outcome["ok"]:
                logger.warning(
                    f"offer-letter email to {candidate.email} not delivered: "
                    f"{outcome['error_type']} — {outcome['error']}"
                )
        except Exception as _e:
            email_status = "failed"
            email_error = str(_e)
            logger.warning(f"offer-letter email to {candidate.email} failed: {_e}")

    email_note = {
        "sent": " — offer letter emailed to the candidate",
        "queued": " — offer letter queued for delivery",
        "failed": " — WARNING: the offer letter email could not be sent",
        "skipped": " — no email address on file, so nothing was sent",
    }.get(email_status, "")

    return {
        "message": "Offer created" + email_note,
        "email_sent": email_sent,
        "email_status": email_status,
        "email_error": email_error,
        "offer_id": str(db_offer.id),
        "candidate_name": f"{candidate.first_name} {candidate.last_name}",
        "salary": float(db_offer.salary),
        "expiry_date": db_offer.expiry_date
    }

@router.get("/offers")
async def get_offers(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get offers created by HR"""
    query = db.query(Offer).join(Candidate).join(JobPosting).filter(
        JobPosting.company_id == current_user.company_id,
        Offer.company_id == current_user.company_id,
    )

    if current_user.role != "admin":
        hr_profile = get_hr_profile(db, current_user)
        # Every offer on a job this HR owns or co-HRs — must match _dashboard_counts.
        query = query.filter(job_access_filter(hr_profile.id))

    if status:
        query = query.filter(Offer.status == status)
    
    offers = query.order_by(Offer.created_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    for offer in offers:
        candidate_name = "—"
        if offer.candidate:
            candidate_name = f"{offer.candidate.first_name} {offer.candidate.last_name}"
        job_title = offer.job.title if offer.job else "—"

        result.append({
            "id": str(offer.id),
            "candidate_name": candidate_name,
            "job_title": job_title,
            "offer_type": offer.offer_type,
            "salary": float(offer.salary),
            "bonus": float(offer.bonus) if offer.bonus else None,
            "status": offer.status,
            "created_at": offer.created_at,
            "sent_at": offer.sent_at,
            "expiry_date": offer.expiry_date
        })

    return result


# ==================== CANDIDATE REQUEST ENDPOINTS (from Interviewers) ====================

def _format_candidate_request(r, requester, job=None):
    """Format a candidate request for API response"""
    return {
        "id": r.id,
        "requested_by_name": f"{requester.first_name} {requester.last_name}" if requester else "Unknown",
        "requested_by_email": requester.email if requester else "",
        "job_title": r.job_title,
        "department": r.department,
        "role_level": r.role_level,
        "employment_type": r.employment_type,
        "num_candidates": r.num_candidates,
        "required_skills": json.loads(r.required_skills) if r.required_skills else [],
        "preferred_skills": json.loads(r.preferred_skills) if r.preferred_skills else [],
        "min_experience_years": r.min_experience_years,
        "max_experience_years": r.max_experience_years,
        "education_requirement": r.education_requirement,
        "job_description": r.job_description,
        "urgency": r.urgency,
        "notes": r.notes,
        "status": r.status,
        "hr_notes": r.hr_notes,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
        "matched_job_id": str(job.id) if job else None,
        "matched_job_title": job.title if job else None,
    }


@router.get("/email-log")
async def get_email_log(
    to_email: Optional[str] = None,
    step_key: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Delivery history for outbound mail — "did candidate X get their offer?".

    Before this existed the only trace of a message was a log line, and the send
    helpers report failure by return value rather than by raising, so a failed
    offer letter left nothing behind at all.

    ``status`` is the three-state delivery contract: ``queued`` (accepted, worker
    has not finished), ``sent``, ``failed``, plus ``deferred`` for a rate-limit
    hold. A queued row that never becomes ``sent`` means the worker is not
    draining — that is the signal to look for.
    """
    from src.models.models import EmailLog

    q = db.query(EmailLog)
    # The before_compile filter already scopes this, but mail rows are written
    # from the worker where no tenant is bound, so filter explicitly too — a
    # company_id that somehow landed NULL must not leak into another tenant's view.
    if current_user.role != "superadmin":
        q = q.filter(EmailLog.company_id == current_user.company_id)
    if to_email:
        # utf8mb4_unicode_ci compares case-insensitively, so a plain ==
        # matches regardless of how the address was cased when sent.
        q = q.filter(EmailLog.to_email == to_email.strip())
    if step_key:
        q = q.filter(EmailLog.step_key == step_key)
    if status:
        q = q.filter(EmailLog.status == status)
    if since:
        try:
            q = q.filter(EmailLog.created_at >= datetime.fromisoformat(since))
        except ValueError:
            raise HTTPException(status_code=400,
                                detail="since must be ISO-8601, e.g. 2026-09-01")

    total = q.count()
    rows = (q.order_by(EmailLog.created_at.desc())
             .offset(max(0, skip)).limit(min(max(1, limit), 200)).all())

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "entries": [
            {
                "id": r.id,
                "to_email": r.to_email,
                "subject": r.subject,
                "step_key": r.step_key,
                "status": r.status,
                "error_type": r.error_type,
                "error": r.error,
                "attempt": r.attempt,
                "sender_user": r.sender_user,
                "reference_type": r.reference_type,
                "reference_id": r.reference_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.get("/candidates/{candidate_id}/stage-history")
async def get_candidate_stage_history(
    candidate_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Return stage history + current stage for a candidate — used by HR pipeline stage tracker."""
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.company_id == current_user.company_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    _require_job_team(db, candidate.job, current_user)

    history = (
        db.query(CandidateStageHistory)
        .filter(CandidateStageHistory.candidate_id == candidate_id,
                CandidateStageHistory.company_id == current_user.company_id)
        .order_by(CandidateStageHistory.created_at.asc())
        .all()
    )

    return {
        "candidate_id": candidate_id,
        "name": f"{candidate.first_name} {candidate.last_name}",
        "email": candidate.email,
        "current_stage": candidate.current_stage,
        "job_title": candidate.job.title if candidate.job else None,
        "source": source_view(candidate),
        "merged_into_id": candidate.merged_into_id,
        "withdrawn_at": candidate.withdrawn_at.isoformat() if candidate.withdrawn_at else None,
        "withdrawal_reason": candidate.withdrawal_reason,
        "stage_history": [
            {
                "from_stage": h.from_stage,
                "to_stage": h.to_stage,
                "notes": h.notes,
                "changed_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in history
        ],
    }


@router.get("/candidate-requests")
async def get_all_candidate_requests(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """HR views all candidate requests from interviewers"""
    cr_q = db.query(CandidateRequest).join(
        User, CandidateRequest.requested_by == User.id
    ).filter(
        CandidateRequest.company_id == current_user.company_id,
        User.company_id == current_user.company_id,
    )
    requests = cr_q.order_by(CandidateRequest.created_at.desc()).all()
    result = []
    for r in requests:
        requester = db.query(User).filter(User.id == r.requested_by).first()
        result.append(_format_candidate_request(r, requester))
    return result


@router.get("/my-candidate-requirements")
async def get_my_candidate_requirements(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get candidate requests/requirements for jobs assigned to this HR, plus unmatched pending requests"""
    hr_profile = get_hr_profile(db, current_user)

    # Get all jobs assigned to this HR — tenant-scoped so legacy NULL-company rows
    # don't surface for users who do have a company_id (would otherwise 404 on update).
    my_jobs = db.query(JobPosting).filter(
        job_access_filter(hr_profile.id),
        JobPosting.company_id == current_user.company_id,
    ).all()
    my_job_titles = {j.title.strip().lower(): j for j in my_jobs}

    # This company's candidate requests, matched to jobs by id then title. Every request
    # also comes back as "unmatched" below, so this must never be the whole table.
    all_requests = db.query(CandidateRequest).filter(
        CandidateRequest.company_id == current_user.company_id,
    ).order_by(CandidateRequest.created_at.desc()).all()

    my_job_ids = {str(j.id) for j in my_jobs}

    matched_result = []
    matched_request_ids = set()
    for r in all_requests:
        matched_job = None
        # Direct link first (most reliable)
        if r.job_id and r.job_id in my_job_ids:
            matched_job = next((j for j in my_jobs if str(j.id) == r.job_id), None)
        # Fallback: title matching
        if not matched_job:
            matched_job = my_job_titles.get(r.job_title.strip().lower()) if r.job_title else None
        if not matched_job:
            for title, job in my_job_titles.items():
                if r.job_title and (title in r.job_title.strip().lower() or r.job_title.strip().lower() in title):
                    matched_job = job
                    break

        if matched_job:
            requester = db.query(User).filter(User.id == r.requested_by).first()
            matched_result.append(_format_candidate_request(r, requester, matched_job))
            matched_request_ids.add(r.id)

    # Build jobs list with their matched requests
    jobs_with_requirements = []
    for j in my_jobs:
        matching_reqs = [r for r in matched_result if r["matched_job_id"] == str(j.id)]
        skills = json.loads(j.required_skills) if isinstance(j.required_skills, str) else (j.required_skills or [])
        candidate_count = db.query(Candidate).filter(Candidate.job_id == j.id).count()
        # Check if test exists
        test = db.query(Interview).filter(Interview.job_id == j.id, Interview.candidate_id.is_(None)).first()
        jobs_with_requirements.append({
            "job_id": str(j.id),
            "job_title": j.title,
            "department": j.department.name if j.department else "—",
            "department_id": str(j.department_id) if j.department_id else None,
            "status": j.status,
            "required_skills": skills,
            "experience_years": j.experience_years,
            "employment_type": j.employment_type,
            "location": j.location,
            "candidate_count": candidate_count,
            "test_status": test.status if test else None,
            "test_id": test.id if test else None,
            "created_at": j.created_at,
            "candidate_requests": matching_reqs
        })

    # Include unmatched pending/new requests so HR can see ALL incoming requests
    unmatched_requests = []
    for r in all_requests:
        if r.id not in matched_request_ids:
            requester = db.query(User).filter(User.id == r.requested_by).first()
            unmatched_requests.append(_format_candidate_request(r, requester))

    return {
        "hr_name": f"{current_user.first_name} {current_user.last_name}",
        "total_jobs": len(my_jobs),
        "jobs": jobs_with_requirements,
        "unmatched_requests": unmatched_requests
    }


@router.put("/candidate-requests/{request_id}")
async def update_candidate_request_status(
    request_id: str,
    status_update: dict,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """HR updates status of a candidate request (approve/reject/fulfill)"""
    cr = db.query(CandidateRequest).filter(
        CandidateRequest.id == request_id,
        CandidateRequest.company_id == current_user.company_id,
    ).first()
    if not cr:
        raise HTTPException(status_code=404, detail="Candidate request not found")

    if "status" in status_update:
        cr.status = status_update["status"]
    if "hr_notes" in status_update:
        cr.hr_notes = status_update["hr_notes"]
    cr.updated_at = datetime.utcnow()
    db.commit()
    return {"message": f"Request updated to '{cr.status}'", "id": cr.id}


# ==================== NOTIFICATION ENDPOINTS ====================

@router.get("/notifications")
async def get_my_notifications(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get all notifications for the current HR user"""
    from src.models.models import Notification
    notifs = db.query(Notification).filter(
        Notification.user_id == current_user.id
    ).order_by(Notification.created_at.desc()).limit(50).all()
    return [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "type": n.type,
            "reference_type": n.reference_type,
            "reference_id": n.reference_id,
            "is_read": n.is_read,
            "created_at": n.created_at
        }
        for n in notifs
    ]


@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Mark a notification as read"""
    from src.models.models import Notification
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_read = True
    db.commit()
    return {"message": "Notification marked as read"}


@router.put("/notifications/read-all")
async def mark_all_notifications_read(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Mark all notifications as read"""
    from src.models.models import Notification
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).update({"is_read": True})
    db.commit()
    return {"message": "All notifications marked as read"}


# ==================== PIPELINE STEP NOTIFICATIONS ====================
# Per-company, per-step extra CC recipients (only the addresses HR explicitly
# types) and an optional custom sender mailbox. See
# src/services/pipeline_notifications.py.

class PipelineStepConfigIn(BaseModel):
    step_key:     str
    enabled:      bool = True
    extra_emails: List[EmailStr] = Field(default_factory=list)
    as_cc:        bool = True
    mailbox_id:   Optional[str] = None        # Admin-defined CompanyMailbox; None = company default


class PipelineNotificationsPayload(BaseModel):
    steps: List[PipelineStepConfigIn] = Field(default_factory=list)


@router.get("/mailboxes")
async def hr_list_mailboxes(
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Read-only list of the company's active sender mailboxes for the HR
    dropdown. Credentials are never exposed — only id, name, from_email, and
    whether a password has been set.

    `has_password` matters because a mailbox without one is silently unusable:
    resolve_step_dispatch skips it and the step falls back to the company default
    sender. Without this flag HR could select such a mailbox, save successfully,
    and never learn that mail still went out from the default address.
    """
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    return _selectable_mailboxes(db, current_user.company_id)


def _selectable_mailboxes(db: Session, company_id: str) -> List[dict]:
    """Active mailboxes for this company, shaped for a 'send from' dropdown.

    Credentials are never included — only ``has_password``, because a mailbox
    without one is silently unusable at send time.
    """
    from src.models.models import CompanyMailbox
    rows = (
        db.query(CompanyMailbox)
        .filter(CompanyMailbox.company_id == company_id,
                CompanyMailbox.is_active == True)  # noqa: E712
        .order_by(CompanyMailbox.name)
        .all()
    )
    return [
        {
            "id": m.id,
            "name": m.name,
            "from_email": m.smtp_from_email or m.smtp_user or "",
            # Boolean only — never the credential itself.
            "has_password": bool(m.smtp_pass),
        }
        for m in rows
    ]


@router.get("/mail-preview")
async def hr_mail_preview(
    step_key: str,
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    """What address will this step's mail actually go out from, and what else could it.

    Powers the "Send from" control on the HR send dialogs: it reports the
    resolved default (the mailbox an admin/HR configured for this stage, or the
    company default when there is none) plus the list HR may pick from.

    Never returns credentials.
    """
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    from src.services import email_service
    from src.services import pipeline_notifications as pn

    if not pn.is_valid_step(step_key):
        raise HTTPException(status_code=400, detail=f"Unknown step '{step_key}'")

    cid = current_user.company_id
    step = pn.describe_step_dispatch(db, cid, step_key)

    # Where it lands when the step has no usable mailbox of its own.
    chain = email_service.describe_sender_chain(db, cid, "candidate")
    active = next((c for c in chain if c.get("active")), None)
    default_from = (active or {}).get("sender", {}) or {}

    using_step_mailbox = bool(step["mailbox"] and step["mailbox"]["usable"])
    return {
        "step_key": step_key,
        "label": step["label"],
        # The mailbox HR configured for this stage — the dropdown's default
        # selection. Reported even when unusable so the UI can explain itself.
        "default_mailbox_id": (step["mailbox"] or {}).get("id"),
        "from_email": (step["mailbox"]["from_email"] if using_step_mailbox
                       else default_from.get("from_email") or ""),
        "source": "step_mailbox" if using_step_mailbox else "company_default",
        "source_label": (step["mailbox"]["name"] if using_step_mailbox
                         else (active or {}).get("label") or "Platform default"),
        "fallback_reason": step["fallback_reason"],
        "cc": step["cc"],
        "enabled": step["enabled"],
        "mailboxes": _selectable_mailboxes(db, cid),
    }


class ComposeMailRequest(BaseModel):
    to: EmailStr
    cc: List[EmailStr] = Field(default_factory=list)
    subject: str
    body: str
    mailbox_id: Optional[str] = None
    # Lets the offer-resend dialog reuse this endpoint and still resolve its sender
    # through the offer_letter mailbox HR configured for that stage.
    step_key: str = "custom_mail"


@router.post("/send-mail")
async def hr_send_mail(
    request: ComposeMailRequest,
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Send an ad-hoc email from the HR dashboard's Compose Mail dialog.

    Goes through ``dispatch``, not ``_send``: dispatch is the only path that
    enqueues onto the RQ queue hireflow-email-worker consumes, and therefore the
    only one that honours the mailbox's hourly_limit. A direct _send would bypass
    the provider cap and get the company's SMTP account throttled or blocked.

    Returns dispatch's own result, so the caller can tell "queued" from "sent" —
    the UI must not claim delivery for a job that has not run yet.
    """
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")

    from src.services import pipeline_notifications as pn
    from src.services.pipeline_notifications import resolve_step_sender
    from src.services.email_service import dispatch

    if not pn.is_valid_step(request.step_key):
        raise HTTPException(status_code=400, detail=f"Unknown step '{request.step_key}'")

    subject = (request.subject or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="Subject is required")
    if not (request.body or "").strip():
        raise HTTPException(status_code=400, detail="Message body is required")

    # One call for the whole precedence chain (HR's pick → step mailbox → company
    # default → env). It also verifies the chosen mailbox belongs to this company,
    # so a guessed id cannot borrow another tenant's SMTP credentials.
    smtp, disp = resolve_step_sender(
        db, current_user.company_id, request.step_key,
        request.mailbox_id, category="candidate",
    )

    # dispatch de-dupes CC case-insensitively and drops the primary recipient.
    cc = list(disp["cc"]) + [str(a) for a in request.cc]

    body_html = request.body.replace("\n", "<br>")
    result = dispatch(
        str(request.to), subject,
        f"<div style='font-family:sans-serif;line-height:1.7'>{body_html}</div>",
        smtp_config=smtp, cc=cc or None,
        hourly_limit=(smtp or {}).get("hourly_limit"),
    )

    logger.info("HR ad-hoc mail from %s to %s (step=%s, mode=%s)",
                current_user.id, request.to, request.step_key, result.get("mode"))
    return {"queued": True, "cc": cc, **result}


# ── Mail templates ───────────────────────────────────────────────────────────
# Starter set seeded per company on first read. Kept server-side so the ids stay
# stable across browsers — they used to be re-created from a hardcoded array in
# HRDashboard.jsx, so two HR users could hold different "tpl-1"s.
_DEFAULT_MAIL_TEMPLATES = [
    ("offer", "Standard Offer Letter", "Offer Letter – {{job_title}}",
     "Dear {{candidate_name}},\n\nWe are pleased to extend an offer of employment for the position of {{job_title}}.\n\nPosition: {{job_title}}\nEmployment Type: {{offer_type}}\nAnnual Salary: ₹{{salary}}\nSigning Bonus: ₹{{bonus}}\nStart Date: {{start_date}}\nOffer Expires: {{expiry_date}}\n\nPlease respond to this offer by the expiry date.\n\nCongratulations and welcome to the team!\nHireFlow Recruitment Team"),
    ("offer", "Informal Offer (Warm Tone)", "Great news, {{candidate_name}}! – {{job_title}} Offer",
     "Hi {{candidate_name}},\n\nExciting news — we'd love to officially welcome you aboard as our new {{job_title}}!\n\nHere's a quick summary of your offer:\n• Salary: ₹{{salary}} per year\n• Bonus: ₹{{bonus}}\n• Start Date: {{start_date}}\n• Offer valid until: {{expiry_date}}\n\nWe're really excited to have you join the team. Please let us know if you have any questions!\n\nWarm regards,\nHireFlow Team"),
    ("rejection", "Application Rejection", "Your Application for {{job_title}}",
     "Dear {{candidate_name}},\n\nThank you for your interest in the {{job_title}} position and for taking the time to go through our interview process.\n\nAfter careful consideration, we have decided to move forward with another candidate whose experience more closely matches our current needs.\n\nWe appreciate your time and encourage you to apply for future openings that align with your skills.\n\nBest regards,\nHireFlow Recruitment Team"),
    ("rejection", "Post-Interview Rejection", "Follow-up on Your Interview – {{job_title}}",
     "Dear {{candidate_name}},\n\nThank you for taking the time to interview with us for the {{job_title}} role. It was a pleasure getting to know you.\n\nAfter careful deliberation, we have decided to proceed with another candidate who more closely fits our requirements at this time.\n\nWe were impressed by your background and would encourage you to apply again for future roles that match your profile.\n\nWe wish you all the best in your job search.\n\nKind regards,\nHireFlow Recruitment Team"),
    ("rejection", "ATS / Early Stage Rejection", "Your Application Status – {{job_title}}",
     "Dear {{candidate_name}},\n\nThank you for applying for the {{job_title}} position at our company.\n\nWe have reviewed your application and, after consideration, we will not be moving forward with your candidacy at this time as we are looking for a slightly different skill set for this role.\n\nWe appreciate your interest and wish you success in your future endeavours.\n\nRegards,\nHireFlow Recruitment Team"),
    ("interview_invite", "Interview Invitation", "Interview Invitation – {{job_title}}",
     "Dear {{candidate_name}},\n\nWe are pleased to invite you for an interview for the position of {{job_title}}.\n\nPlease confirm your availability at your earliest convenience.\n\nBest regards,\nHireFlow Recruitment Team"),
    ("interview_invite", "Technical Round Invite", "Technical Interview – {{job_title}}",
     "Dear {{candidate_name}},\n\nCongratulations on progressing to the Technical Interview round for the {{job_title}} position!\n\nThis round will assess your technical skills and problem-solving ability. Please be prepared to discuss your past projects and complete a short technical exercise.\n\nWe will share the interview schedule and joining details separately.\n\nPlease confirm your availability by replying to this email.\n\nBest regards,\nHireFlow Recruitment Team"),
    ("interview_invite", "Final Round Invite", "Final Interview Round – {{job_title}}",
     "Dear {{candidate_name}},\n\nWe are delighted to invite you to the Final Interview round for the {{job_title}} position.\n\nThis is the last stage of our selection process and will include a discussion with senior leadership.\n\nPlease confirm your availability and we will coordinate a suitable time slot.\n\nWe look forward to speaking with you!\n\nWarm regards,\nHireFlow Recruitment Team"),
    ("welcome", "Welcome Onboard", "Welcome to the Team, {{candidate_name}}!",
     "Dear {{candidate_name}},\n\nWelcome aboard! We are thrilled to have you join us as {{job_title}}.\n\nYour start date is {{start_date}}. Please arrive at 9:00 AM and ask for HR at reception.\n\nLooking forward to working with you!\n\nHireFlow Team"),
]

MAIL_TEMPLATE_STAGES = ("offer", "rejection", "interview_invite", "welcome", "custom")


class MailTemplateRequest(BaseModel):
    stage: str = "custom"
    name: str
    subject: Optional[str] = ""
    body: Optional[str] = ""


def _mail_template_dict(t: MailTemplate) -> Dict[str, Any]:
    return {
        "id": t.id, "stage": t.stage, "name": t.name,
        "subject": t.subject or "", "body": t.body or "",
        "is_builtin": bool(t.is_builtin), "is_active": bool(t.is_active),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _owned_mail_template(db: Session, template_id: str, company_id: str) -> MailTemplate:
    """Fetch a template, 404ing on another company's id rather than 403ing — a 403
    would confirm the id exists."""
    t = (
        db.query(MailTemplate)
        .filter(MailTemplate.id == template_id,
                MailTemplate.company_id == company_id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@router.get("/mail-templates")
async def list_mail_templates(
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    """List this company's mail templates, seeding the starter set on first read."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    cid = current_user.company_id

    rows = (
        db.query(MailTemplate)
        .filter(MailTemplate.company_id == cid)
        .order_by(MailTemplate.created_at.asc())
        .all()
    )
    if not rows:
        for stage, name, subject, body in _DEFAULT_MAIL_TEMPLATES:
            # company_id set explicitly rather than relying on the before_flush
            # auto-stamp — see docs/DASHBOARD_DATA_PITFALLS.md #1.
            db.add(MailTemplate(company_id=cid, created_by=current_user.id,
                                stage=stage, name=name, subject=subject,
                                body=body, is_builtin=True))
        db.commit()
        rows = (
            db.query(MailTemplate)
            .filter(MailTemplate.company_id == cid)
            .order_by(MailTemplate.created_at.asc())
            .all()
        )
        logger.info("Seeded %d default mail templates for company %s", len(rows), cid)

    return [_mail_template_dict(t) for t in rows]


@router.post("/mail-templates")
async def create_mail_template(
    request: MailTemplateRequest,
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    if not (request.name or "").strip():
        raise HTTPException(status_code=400, detail="Template name is required")
    stage = request.stage if request.stage in MAIL_TEMPLATE_STAGES else "custom"

    existing = (
        db.query(MailTemplate)
        .filter(MailTemplate.company_id == current_user.company_id,
                MailTemplate.name == request.name.strip())
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="A template with that name already exists")

    t = MailTemplate(
        company_id=current_user.company_id, created_by=current_user.id,
        stage=stage, name=request.name.strip(),
        subject=request.subject or "", body=request.body or "",
        is_builtin=False,
    )
    db.add(t)
    db.commit()
    db.refresh(t)   # company_id/defaults are not populated on the instance until this
    return _mail_template_dict(t)


@router.put("/mail-templates/{template_id}")
async def update_mail_template(
    template_id: str,
    request: MailTemplateRequest,
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    t = _owned_mail_template(db, template_id, current_user.company_id)

    if (request.name or "").strip():
        t.name = request.name.strip()
    if request.stage in MAIL_TEMPLATE_STAGES:
        t.stage = request.stage
    t.subject = request.subject or ""
    t.body = request.body or ""
    t.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(t)
    return _mail_template_dict(t)


@router.delete("/mail-templates/{template_id}")
async def delete_mail_template(
    template_id: str,
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    t = _owned_mail_template(db, template_id, current_user.company_id)
    db.delete(t)
    db.commit()
    return {"deleted": True, "id": template_id}


@router.get("/pipeline-notifications")
async def get_pipeline_notifications(
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Return per-step notification config for this company, with defaults filled
    for steps that have no saved row. Passwords are never returned."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    from src.models.models import PipelineStepConfig
    from src.services import pipeline_notifications as pn

    rows = {
        r.step_key: r
        for r in db.query(PipelineStepConfig)
        .filter(PipelineStepConfig.company_id == current_user.company_id)
        .all()
    }
    steps = []
    for key in pn.STEP_KEYS:
        if key in rows:
            data = pn.serialize_row(rows[key])
            data["label"] = pn.STEP_LABELS[key]
        else:
            data = pn.default_step_payload(key)
        steps.append(data)
    return {"steps": steps}


@router.put("/pipeline-notifications")
async def update_pipeline_notifications(
    payload: PipelineNotificationsPayload,
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Upsert per-step notification config. Unknown step keys are ignored. CC is
    only the explicitly-typed ``extra_emails`` — there is no role-based mapping."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    from src.models.models import PipelineStepConfig, CompanyMailbox
    from src.services import pipeline_notifications as pn

    # Valid mailbox ids for this company (to reject foreign / stale references).
    valid_mailbox_ids = {
        m.id for m in db.query(CompanyMailbox.id)
        .filter(CompanyMailbox.company_id == current_user.company_id).all()
    }

    for item in payload.steps:
        if not pn.is_valid_step(item.step_key):
            continue
        emails = [str(e) for e in item.extra_emails]
        mailbox_id = item.mailbox_id if item.mailbox_id in valid_mailbox_ids else None

        row = (
            db.query(PipelineStepConfig)
            .filter(
                PipelineStepConfig.company_id == current_user.company_id,
                PipelineStepConfig.step_key == item.step_key,
            )
            .first()
        )
        if not row:
            row = PipelineStepConfig(
                company_id=current_user.company_id, step_key=item.step_key
            )
            db.add(row)

        row.enabled      = item.enabled
        row.extra_emails = json.dumps(emails)
        row.notify_roles = json.dumps([])   # role-based CC removed; always empty
        row.as_cc        = item.as_cc
        row.mailbox_id   = mailbox_id
        row.updated_at   = datetime.utcnow()

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save pipeline notifications: {e}")

    return await get_pipeline_notifications(current_user=current_user, db=db)


@router.post("/pipeline-notifications/test")
async def test_pipeline_notification(
    step_key: str = Query(...),
    to: EmailStr = Query(...),
    current_user: User = Depends(require_role(["hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Send a sample message for one step to ``to``, applying that step's
    configured extra recipients and custom sender (so HR can verify routing)."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User is not associated with a company")
    from src.services import email_service
    from src.services.pipeline_notifications import resolve_step_dispatch, STEP_LABELS, is_valid_step

    if not is_valid_step(step_key):
        raise HTTPException(status_code=400, detail="Unknown step_key")

    disp = resolve_step_dispatch(db, current_user.company_id, step_key)
    smtp_config = disp["smtp_config"] or email_service.resolve_sender(
        db, current_user.company_id, "candidate")
    label = STEP_LABELS.get(step_key, step_key)
    result = email_service._send(
        to,
        f"[Test] {label} notification",
        f"<p>This is a test of the <b>{label}</b> pipeline notification. "
        f"Extra recipients configured for this step are CC'd.</p>",
        smtp_config=smtp_config,
        cc=disp["cc"],
    )
    return {
        "sent": result.get("sent"),
        "error": result.get("error"),
        "error_type": result.get("error_type"),
        "cc": disp["cc"],
        "using_step_smtp": disp["smtp_config"] is not None,
    }


# ==================== MY ACTIVITY (personal profile page) ====================

ACTIVITY_WINDOW_DAYS = 30
ACTIVITY_FEED_LIMIT = 10
_NOTE_SENTINEL = "__note__"


def _require_hr_self(current_user: User = Depends(get_current_user)) -> User:
    """Exactly role == "hr". Deliberately NOT AuthMiddleware.get_current_hr.

    That dependency also admits admins, which would be wrong here twice over:

    1. get_hr_profile() INSERTs and commits an HRProfile when one is missing, so
       an admin merely opening their own profile page would silently acquire an
       HR profile — a write side effect of a GET.
    2. The admin path in _dashboard_counts is company-wide, and company-wide
       totals under a heading reading "Your work at a glance" are simply false.

    An admin who genuinely owns job postings sees nothing here. That is the
    accepted cost: company numbers belong on the admin analytics screens, not on
    a personal page.
    """
    if (current_user.role or "").lower() != "hr":
        raise HTTPException(status_code=403, detail="HR access required")
    return current_user


@router.get("/my-activity")
async def get_my_activity(
    current_user: User = Depends(_require_hr_self),
    db: Session = Depends(get_db),
):
    """Everything the profile page shows about this HR's own work."""
    # Read-only lookup on purpose — get_hr_profile() would CREATE a row, and a
    # GET must not. No profile row means no jobs can reference it (JobPosting.hr_id
    # FKs hr_profiles.id), so zeros are the correct answer, not a degraded one.
    hr_profile = db.query(HRProfile).filter(HRProfile.user_id == current_user.id).first()

    if hr_profile is None:
        at_a_glance = DashboardCounts(jobs=0, active_jobs=0, interviews=0, offers=0)
    else:
        at_a_glance = _dashboard_counts(db, current_user, own_only=True, hr_profile=hr_profile)

    since = datetime.utcnow() - timedelta(days=ACTIVITY_WINDOW_DAYS)
    if hr_profile is None:
        window = {"jobs_created": 0, "candidates": 0, "interviews_scheduled": 0, "offers_sent": 0}
    else:
        # Mirrors admin_routes.get_hr_analytics, with one deliberate difference:
        # that version's candidate count has no date filter, so it reports a
        # lifetime total inside a block labelled with a period. Here the label
        # says "last 30 days", so the filter has to be real.
        window = {
            "jobs_created": db.query(func.count(JobPosting.id)).filter(
                JobPosting.hr_id == hr_profile.id,
                JobPosting.created_at >= since,
            ).scalar() or 0,
            "candidates": db.query(func.count(Candidate.id)).join(
                JobPosting, Candidate.job_id == JobPosting.id
            ).filter(
                job_access_filter(hr_profile.id),
                Candidate.created_at >= since,
            ).scalar() or 0,
            "interviews_scheduled": db.query(func.count(Interview.id)).join(
                Candidate, Interview.candidate_id == Candidate.id
            ).join(
                JobPosting, Candidate.job_id == JobPosting.id
            ).filter(
                job_access_filter(hr_profile.id),
                Interview.created_at >= since,
            ).scalar() or 0,
            "offers_sent": db.query(func.count(Offer.id)).filter(
                Offer.hr_id == hr_profile.id,
                Offer.created_at >= since,
            ).scalar() or 0,
        }

    # ── recent activity ──
    # Filtered on the actor, which makes a cross-HR leak structurally impossible.
    #
    # coalesce, not a bare `!= _NOTE_SENTINEL`: candidate self-applications write
    # from_stage=NULL, and SQL evaluates NULL != 'x' as NULL rather than true, so
    # the naive form would silently drop every "applied" event.
    history = (
        db.query(CandidateStageHistory)
        .filter(
            CandidateStageHistory.changed_by_id == current_user.id,
            CandidateStageHistory.company_id == current_user.company_id,
            func.coalesce(CandidateStageHistory.from_stage, "") != _NOTE_SENTINEL,
        )
        .order_by(desc(CandidateStageHistory.created_at))
        .limit(ACTIVITY_FEED_LIMIT)
        .all()
    )

    # Resolved with flat IN queries rather than a join: the tenancy auto-filter
    # injects JobPosting.company_id into the WHERE clause, which would silently
    # turn an outer join inner and drop candidates that have no job.
    candidates = {}
    jobs = {}
    if history:
        cand_ids = {h.candidate_id for h in history if h.candidate_id}
        if cand_ids:
            candidates = {
                c.id: c for c in db.query(Candidate).filter(Candidate.id.in_(cand_ids)).all()
            }
        job_ids = {c.job_id for c in candidates.values() if c.job_id}
        if job_ids:
            jobs = {
                j.id: j for j in db.query(JobPosting).filter(JobPosting.id.in_(job_ids)).all()
            }

    recent_activity = []
    for row in history:
        cand = candidates.get(row.candidate_id)
        job = jobs.get(cand.job_id) if cand and cand.job_id else None
        notes = (row.notes or "")[:160] or None
        recent_activity.append({
            "id": str(row.id),
            "candidate_id": str(row.candidate_id) if row.candidate_id else None,
            "candidate_name": f"{cand.first_name} {cand.last_name}".strip() if cand else None,
            "job_id": str(cand.job_id) if cand and cand.job_id else None,
            "job_title": job.title if job else None,
            "from_stage": row.from_stage,
            "to_stage": row.to_stage,
            "notes": notes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    return {
        "at_a_glance": at_a_glance.model_dump(),
        "last_30_days": {
            "days": ACTIVITY_WINDOW_DAYS,
            "since": since.isoformat(),
            **window,
        },
        "recent_activity": recent_activity,
    }
