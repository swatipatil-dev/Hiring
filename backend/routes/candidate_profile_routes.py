"""
CANDIDATE PROFILE ROUTES
Handles all extended profile sections:
  work experience, education, skills, certifications, projects,
  preferences, social links, file uploads, and completion score.
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import uuid, os, json, re, logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

from src.config.database_config_mysql import get_db
from src.auth import AuthMiddleware
from src.models.models import (
    User,
    CandidateProfileExt,
    CandidateWorkExperience,
    CandidateEducation,
    CandidateSkill,
    CandidateCertification,
    CandidateProject,
)

router = APIRouter(prefix="/api/v1/candidate/profile", tags=["Candidate Profile"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_or_create_profile(user_id: str, db: Session) -> CandidateProfileExt:
    profile = db.query(CandidateProfileExt).filter(
        CandidateProfileExt.user_id == user_id
    ).first()
    if not profile:
        profile = CandidateProfileExt(user_id=user_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _json_list(value) -> list:
    """Safely decode a JSON-stored text column to a Python list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            pass
    return []


# Lines that begin like a sentence/description rather than a project title.
_PROJECT_DESC_STARTERS = re.compile(
    r'^(developed|built|created|designed|implemented|worked|using|used|responsible|'
    r'led|managed|the|a|an|this|it|we|i|my)\b',
    re.IGNORECASE,
)
_LEADING_BULLET = re.compile(r'^[\s•\-\*·▪‣◦>\.]+')
_TECH_LINE = re.compile(
    r'^(tech|technologies|tech\s*stack|stack|tools|built\s*with)\b\s*[:\-]',
    re.IGNORECASE,
)
_DATE_TOKEN = re.compile(
    r'\b((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}|(19|20)\d{2})\b',
    re.IGNORECASE,
)
_DEGREE_KW = re.compile(
    r'\b(b[\.\- ]?tech|m[\.\- ]?tech|b[\.\- ]?e|m[\.\- ]?e|b[\.\- ]?sc|m[\.\- ]?sc|'
    r'b[\.\- ]?com|m[\.\- ]?com|bachelor|master|mba|mca|bca|ph[\.\- ]?d|diploma)\b',
    re.IGNORECASE,
)
_INSTITUTION_KW = re.compile(r'\b(university|college|institute|school|academy)\b', re.IGNORECASE)

# ── Extraction confidence ──────────────────────────────────────────────────────
# This number decides whether a row arrives pre-ticked in the review screen (see
# HIGH_CONF in ResumeImportReview.jsx). It expresses ONE thing: how well anchored
# the extraction was — not how tidy the resulting string looks.
#
#   SECTION  — found under a real section heading, so the resume's own layout
#              confirms what the text is. The strongest signal available, and the
#              path these helpers exist to prefer.
#   SCAN     — the whole-document ATS fallback, used only when no heading matched.
#              Noisy by construction: it leaks skills and institution names in as
#              "companies", which is why the filtering below has to exist.
#   INFERRED — read out of prose by the model, or a title-shape guess. Plausible,
#              unverified: always an opt-in.
#
# These were miscalibrated: section rows carried 0.6 (below the 0.7 threshold) while
# scan rows inherited up to 0.85 from extract_education_info. The better extraction
# arrived unticked and the worse one pre-ticked, so in practice nothing was ever
# pre-ticked and the candidate re-confirmed every row by hand. extract_education_info
# scores the degree STRING and cannot know its row came from an unanchored scan, so
# the route overrides it rather than trusting the value through.
CONF_SECTION = 0.8
CONF_SCAN = 0.5
CONF_INFERRED = 0.5


def _work_experience_from_section(section_text: str) -> List[Dict[str, Any]]:
    """Extract (company, title) pairs from the detected Experience section only.

    Handles the common two-line entry layout
    ``<Company> <date-range>`` / ``<Title> <location>``: the company sits on
    the line carrying the date range, the title is the next non-bullet line.
    Avoids the whole-document ATS scan that mistakes the "EXPERIENCE" heading
    for a company. Returns [] when nothing usable is found.
    """
    if not section_text or not section_text.strip():
        return []
    lines = [ln.strip() for ln in section_text.split("\n") if ln.strip()]
    out: List[Dict[str, Any]] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if _LEADING_BULLET.match(line):
            i += 1
            continue
        m = _DATE_TOKEN.search(line)
        if m:
            company = line[: m.start()].strip(" .,-–—|")
            title = ""
            j = i + 1
            if j < n and not _LEADING_BULLET.match(lines[j]) and not _DATE_TOKEN.search(lines[j]):
                title = lines[j].strip(" .,-–—|")
                i = j
            if company or title:
                out.append({"title": title, "company": company, "confidence": CONF_SECTION})
        i += 1
    return out[:15]


def _education_from_section(section_text: str) -> List[Dict[str, Any]]:
    """Extract (degree, institution) pairs from the detected Education section.

    Degree = line matching a degree keyword with the trailing date range
    stripped; institution = text up to and including the institution keyword
    (drops the location that usually follows it). Pairs them positionally.
    """
    if not section_text or not section_text.strip():
        return []
    lines = [ln.strip() for ln in section_text.split("\n") if ln.strip()]
    degrees: List[str] = []
    institutions: List[str] = []
    for line in lines:
        if _DEGREE_KW.search(line):
            m = _DATE_TOKEN.search(line)
            deg = (line[: m.start()] if m else line).strip(" .,-–—|")
            if deg:
                degrees.append(deg)
        elif _INSTITUTION_KW.search(line):
            im = _INSTITUTION_KW.search(line)
            end = im.end()
            # Keep a trailing "of <Name>" so "University of Madrid" survives
            # intact; otherwise cut after the keyword to drop the location that
            # usually follows (e.g. "Stanford University  California").
            of_match = re.match(
                r'\s+of\s+[A-Za-z][\w&.\'-]*(?:\s+[A-Za-z][\w&.\'-]*){0,3}',
                line[end:],
            )
            if of_match:
                end += of_match.end()
            inst = line[:end].strip(" .,-–—|")
            if inst:
                institutions.append(inst)
    out: List[Dict[str, Any]] = []
    rows = max(len(degrees), len(institutions))
    for k in range(rows):
        deg = degrees[k] if k < len(degrees) else ""
        inst = institutions[k] if k < len(institutions) else ""
        if deg or inst:
            out.append({"degree": deg, "institution": inst, "confidence": CONF_SECTION})
    return out[:10]


def _certifications_from_section(section_text: str) -> List[str]:
    """Extract full certification names from the detected Certifications section.

    The ATS regex only recognises a fixed vendor list and truncates names
    ("AWS Certified Solutions Architect" → "AWS Certified"); reading the section
    line-by-line preserves the full name and captures custom/org certs the
    regex misses. Bullets and trailing dates are stripped. Returns [] when
    there's no Certifications section, so the caller can fall back to the regex.
    """
    from src.services.enhanced_ats_service import SPOKEN_LANGUAGES_SET, SOFT_SKILLS_SET, DEGREE_TITLES_SET
    if not section_text or not section_text.strip():
        return []
    out: List[str] = []
    seen = set()
    for raw in section_text.split("\n"):
        line = _LEADING_BULLET.sub("", raw).strip(" .,-–—|()")
        # Strip a trailing date / year ("... – Associate (2022)").
        m = _DATE_TOKEN.search(line)
        if m and m.start() > 0:
            line = line[: m.start()].strip(" .,-–—|()")
        # Need at least a few letters; drop date-only lines and over-long prose.
        if not (3 <= len(line) <= 120) or not re.search(r'[A-Za-z]{3,}', line):
            continue
        key = line.lower()
        if key in seen or key in SPOKEN_LANGUAGES_SET or key in SOFT_SKILLS_SET or key in DEGREE_TITLES_SET:
            continue
        seen.add(key)
        out.append(line)
    return out[:20]


def _projects_from_section(section_text: str) -> List[Dict[str, Any]]:
    """Extract project *titles* from the resume's Projects section only.

    Far more precise than scanning the whole document: we look only inside the
    detected "Projects" section and keep short, title-like lines, dropping
    bullet descriptions and full sentences. Returns [] when there's no clear
    Projects section — better to surface nothing than to surface noise.
    """
    if not section_text or not section_text.strip():
        return []
    lines = [ln.strip() for ln in section_text.split("\n")]
    out: List[Dict[str, Any]] = []
    seen = set()

    def _clean_title(line: str) -> str:
        t = _LEADING_BULLET.sub("", line).strip()
        # Keep the part before a separator (dates, tech list, link, summary).
        return re.split(r'\s[\|–—\-:(]\s|\s{2,}', t)[0].strip(" .,-–—|")

    def _add(title: str) -> None:
        if len(title) < 3:
            return
        key = title.lower()
        if key in seen:
            return
        seen.add(key)
        out.append({"name": title, "confidence": CONF_INFERRED})

    # Primary (high precision): a project title is the non-bullet line directly
    # above a "Tech:"/"Technologies:" line — the common
    # "<Project Name>\nTech: ...\n- bullets" resume layout. This avoids the
    # wrapped-bullet fragments that a naive line scan mistakes for titles.
    has_tech = False
    prev = ""
    for line in lines:
        if not line:
            continue
        if _TECH_LINE.match(line):
            has_tech = True
            if prev and not _LEADING_BULLET.match(prev):
                _add(_clean_title(prev))
        prev = line
    if has_tech:
        return out[:15]

    # Fallback (no Tech markers): keep short, title-like lines. Require an
    # uppercase/alphanumeric start so wrapped mid-sentence bullet fragments
    # (which begin lowercase) are dropped, and skip bullet lines outright.
    for raw in lines:
        if not raw or _LEADING_BULLET.match(raw):
            continue
        if len(raw) > 80 or _PROJECT_DESC_STARTERS.match(raw):
            continue
        first = raw[:1]
        if not first.isalnum() or (first.isalpha() and not first.isupper()):
            continue
        words = raw.split()
        if not (1 <= len(words) <= 10):
            continue
        _add(_clean_title(raw))
        if len(out) >= 15:
            break
    return out


def _serialize_profile(user: User, profile: CandidateProfileExt) -> dict:
    """Build the full profile response dict."""
    work_exps = sorted(
        profile.work_experiences,
        key=lambda x: x.start_date or "",
        reverse=True,
    )
    educations = sorted(
        profile.educations,
        key=lambda x: x.start_year or 0,
        reverse=True,
    )

    return {
        # Base user fields
        "id": str(user.id),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "gender": user.gender,
        "date_of_birth": str(user.date_of_birth) if user.date_of_birth else None,
        # Extended personal
        "alternate_phone": profile.alternate_phone,
        "nationality": profile.nationality,
        "marital_status": profile.marital_status,
        "languages": _json_list(profile.languages),
        "profile_photo_path": profile.profile_photo_path,
        # Location — Current Address
        "current_address_line": profile.current_address_line,
        "current_city": profile.current_city,
        "state": profile.state,
        "country": profile.country,
        "pincode": profile.pincode,
        # Location — Permanent Address
        "permanent_address_line": profile.permanent_address_line,
        "permanent_city": profile.permanent_city,
        "permanent_state": profile.permanent_state,
        "permanent_country": profile.permanent_country,
        "permanent_pincode": profile.permanent_pincode,
        "permanent_same_as_current": profile.permanent_same_as_current,
        "willing_to_relocate": profile.willing_to_relocate,
        "preferred_locations": _json_list(profile.preferred_locations),
        # Professional summary
        "headline": profile.headline,
        "about": profile.about,
        "total_experience_years": profile.total_experience_years,
        "current_employment_status": profile.current_employment_status,
        "notice_period": profile.notice_period,
        "open_to_work": profile.open_to_work,
        # Documents
        "resume_path": profile.resume_path,
        "cover_letter_path": profile.cover_letter_path,
        "portfolio_file_path": profile.portfolio_file_path,
        # Sub-sections
        "work_experiences": [
            {
                "id": str(w.id),
                "job_title": w.job_title,
                "company_name": w.company_name,
                "industry": w.industry,
                "employment_type": w.employment_type,
                "location": w.location,
                "start_date": w.start_date,
                "end_date": w.end_date,
                "currently_working": w.currently_working,
                "description": w.description,
                "key_achievements": w.key_achievements,
            }
            for w in work_exps
        ],
        "educations": [
            {
                "id": str(e.id),
                "education_level": e.education_level,
                "degree": e.degree,
                "field_of_study": e.field_of_study,
                "institution_name": e.institution_name,
                "university_or_board": e.university_or_board,
                "start_year": e.start_year,
                "end_year": e.end_year,
                "currently_pursuing": e.currently_pursuing,
                "grade": e.grade,
                "description": e.description,
            }
            for e in educations
        ],
        "skills": [
            {
                "id": str(s.id),
                "skill_name": s.skill_name,
                "proficiency_level": s.proficiency_level,
                "years_of_experience": s.years_of_experience,
                "category": s.category,
            }
            for s in profile.skills
        ],
        "certifications": [
            {
                "id": str(c.id),
                "certificate_name": c.certificate_name,
                "issuing_organization": c.issuing_organization,
                "issue_date": c.issue_date,
                "expiry_date": c.expiry_date,
                "does_not_expire": c.does_not_expire,
                "credential_id": c.credential_id,
                "credential_url": c.credential_url,
                "certificate_file_path": c.certificate_file_path,
            }
            for c in profile.certifications
        ],
        "projects": [
            {
                "id": str(p.id),
                "project_title": p.project_title,
                "project_type": p.project_type,
                "description": p.description,
                "technologies_used": _json_list(p.technologies_used),
                "role": p.role,
                "start_date": p.start_date,
                "end_date": p.end_date,
                "ongoing": p.ongoing,
                "project_url": p.project_url,
                "repository_url": p.repository_url,
            }
            for p in profile.projects
        ],
        "preferences": {
            "preferred_job_roles": _json_list(profile.preferred_job_roles),
            "preferred_industries": _json_list(profile.preferred_industries),
            "preferred_job_type": profile.preferred_job_type,
            "work_mode_preference": profile.work_mode_preference,
            "expected_salary_min": float(profile.expected_salary_min) if profile.expected_salary_min else None,
            "expected_salary_max": float(profile.expected_salary_max) if profile.expected_salary_max else None,
            "salary_currency": profile.salary_currency,
            "salary_type": profile.salary_type,
            "preferred_locations": _json_list(profile.preferred_locations),
        },
        "links": {
            "linkedin_url": profile.linkedin_url,
            "github_url": profile.github_url,
            "portfolio_url": profile.portfolio_url,
            "twitter_url": profile.twitter_url,
            "behance_url": profile.behance_url,
            "dribbble_url": profile.dribbble_url,
            "other_url": profile.other_url,
            "other_url_label": profile.other_url_label,
        },
        "completion": _compute_completion(user, profile),
    }


def build_profile_text(profile: CandidateProfileExt) -> str:
    """
    Assemble a candidate's structured profile into a resume-like text document
    for ATS scoring.

    The section headers used here ("Professional Summary", "Skills",
    "Work Experience", "Education", "Certifications", "Projects") deliberately
    match the keys in enhanced_ats_service.SECTION_HEADERS so the engine's
    section-aware scoring (weighted skills + weighted semantic) kicks in.

    Returns "" when the profile has no usable scoring substance
    (no skills, work experience, or education) — the caller then falls back
    to the resume file's extracted text.
    """
    if profile is None:
        return ""

    skill_names = [s.skill_name for s in profile.skills if s.skill_name]
    has_substance = bool(skill_names or profile.work_experiences or profile.educations)
    if not has_substance:
        return ""

    parts: List[str] = []

    # ── Professional Summary (also carries explicit experience for the engine) ──
    summary_bits: List[str] = []
    if profile.headline:
        summary_bits.append(profile.headline)
    if profile.about:
        summary_bits.append(profile.about)
    if profile.total_experience_years:
        # Explicit phrasing so extract_experience_years() picks it up reliably.
        summary_bits.append(f"{profile.total_experience_years} years of experience")
    if summary_bits:
        parts.append("Professional Summary\n" + "\n".join(summary_bits))

    # ── Skills ──
    if skill_names:
        parts.append("Skills\n" + ", ".join(skill_names))

    # ── Work Experience (year ranges help the date-range experience extractor) ──
    if profile.work_experiences:
        exp_lines: List[str] = []
        for w in profile.work_experiences:
            head_bits = [b for b in [w.job_title, ("at " + w.company_name) if w.company_name else None] if b]
            line = " ".join(head_bits)
            start_year = (w.start_date or "")[:4]
            end_year = "Present" if w.currently_working else (w.end_date or "")[:4]
            if start_year and end_year:
                line = f"{line} ({start_year} - {end_year})".strip()
            if w.description:
                line += "\n" + w.description
            if w.key_achievements:
                line += "\n" + w.key_achievements
            exp_lines.append(line.strip())
        parts.append("Work Experience\n" + "\n\n".join(exp_lines))

    # ── Education ──
    if profile.educations:
        edu_lines: List[str] = []
        for e in profile.educations:
            bits = [b for b in [e.degree, e.field_of_study, e.institution_name, e.university_or_board] if b]
            line = ", ".join(bits)
            if e.grade:
                line += f" — {e.grade}"
            if e.description:
                line += "\n" + e.description
            if line.strip():
                edu_lines.append(line.strip())
        if edu_lines:
            parts.append("Education\n" + "\n".join(edu_lines))

    # ── Certifications ──
    if profile.certifications:
        cert_lines = [
            " — ".join(b for b in [c.certificate_name, c.issuing_organization] if b)
            for c in profile.certifications
        ]
        cert_lines = [c for c in cert_lines if c.strip()]
        if cert_lines:
            parts.append("Certifications\n" + "\n".join(cert_lines))

    # ── Projects ──
    if profile.projects:
        proj_lines: List[str] = []
        for p in profile.projects:
            line = p.project_title or ""
            techs = _json_list(p.technologies_used)
            if techs:
                line += " (" + ", ".join(str(t) for t in techs) + ")"
            if p.description:
                line += "\n" + p.description
            if line.strip():
                proj_lines.append(line.strip())
        if proj_lines:
            parts.append("Projects\n" + "\n".join(proj_lines))

    return "\n\n".join(parts).strip()


def _compute_completion(user: User, profile: CandidateProfileExt) -> dict:
    score = 0
    missing = []

    # Personal Info — 10 %
    if user.first_name and user.last_name and user.phone and user.gender and user.date_of_birth:
        score += 10
    else:
        missing.append("Complete personal information (name, phone, gender, date of birth)")

    # Profile photo — 5 %
    if profile.profile_photo_path:
        score += 5
    else:
        missing.append("Upload a profile photo")

    # Headline / About — 10 %
    if profile.headline or profile.about:
        score += 10
    else:
        missing.append("Add a professional headline or summary")

    # Work experience — 20 %
    if profile.work_experiences:
        score += 20
    else:
        missing.append("Add at least one work experience entry")

    # Education — 15 %
    if profile.educations:
        score += 15
    else:
        missing.append("Add at least one education entry")

    # Skills (≥ 3) — 15 %
    skill_count = len(profile.skills)
    if skill_count >= 3:
        score += 15
    else:
        missing.append(f"Add at least 3 skills (currently {skill_count})")

    # Resume uploaded — 15 %
    if profile.resume_path:
        score += 15
    else:
        missing.append("Upload your resume / CV")

    # Job preferences — 10 %
    if profile.preferred_job_type or profile.work_mode_preference or profile.expected_salary_min:
        score += 10
    else:
        missing.append("Fill in job preferences")

    if score >= 91:
        label = "All Star"
    elif score >= 71:
        label = "Strong"
    elif score >= 41:
        label = "Intermediate"
    else:
        label = "Beginner"

    return {"score": score, "label": label, "missing": missing}


# ── Pydantic models ───────────────────────────────────────────────────────────

class PersonalUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    alternate_phone: Optional[str] = None
    nationality: Optional[str] = None
    marital_status: Optional[str] = None
    languages: Optional[List[str]] = None


class LocationUpdate(BaseModel):
    # Current Address
    current_address_line: Optional[str] = None
    current_city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    # Permanent Address
    permanent_address_line: Optional[str] = None
    permanent_city: Optional[str] = None
    permanent_state: Optional[str] = None
    permanent_country: Optional[str] = None
    permanent_pincode: Optional[str] = None
    permanent_same_as_current: Optional[bool] = None
    willing_to_relocate: Optional[bool] = None
    preferred_locations: Optional[List[str]] = None


class SummaryUpdate(BaseModel):
    headline: Optional[str] = None
    about: Optional[str] = None
    total_experience_years: Optional[float] = None
    current_employment_status: Optional[str] = None
    notice_period: Optional[str] = None
    open_to_work: Optional[bool] = None


class WorkExperienceCreate(BaseModel):
    job_title: str
    company_name: str
    industry: Optional[str] = None
    employment_type: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    currently_working: Optional[bool] = False
    description: Optional[str] = None
    key_achievements: Optional[str] = None


class WorkExperienceUpdate(BaseModel):
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    industry: Optional[str] = None
    employment_type: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    currently_working: Optional[bool] = None
    description: Optional[str] = None
    key_achievements: Optional[str] = None


class EducationCreate(BaseModel):
    institution_name: str
    education_level: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    university_or_board: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    currently_pursuing: Optional[bool] = False
    grade: Optional[str] = None
    description: Optional[str] = None


class EducationUpdate(BaseModel):
    institution_name: Optional[str] = None
    education_level: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    university_or_board: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    currently_pursuing: Optional[bool] = None
    grade: Optional[str] = None
    description: Optional[str] = None


class SkillCreate(BaseModel):
    skill_name: str
    proficiency_level: Optional[str] = None
    years_of_experience: Optional[float] = None
    category: Optional[str] = None


class CertificationCreate(BaseModel):
    certificate_name: str
    issuing_organization: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    does_not_expire: Optional[bool] = False
    credential_id: Optional[str] = None
    credential_url: Optional[str] = None


class CertificationUpdate(BaseModel):
    certificate_name: Optional[str] = None
    issuing_organization: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    does_not_expire: Optional[bool] = None
    credential_id: Optional[str] = None
    credential_url: Optional[str] = None


class ProjectCreate(BaseModel):
    project_title: str
    project_type: Optional[str] = None
    description: Optional[str] = None
    technologies_used: Optional[List[str]] = None
    role: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    ongoing: Optional[bool] = False
    project_url: Optional[str] = None
    repository_url: Optional[str] = None


class ProjectUpdate(BaseModel):
    project_title: Optional[str] = None
    project_type: Optional[str] = None
    description: Optional[str] = None
    technologies_used: Optional[List[str]] = None
    role: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    ongoing: Optional[bool] = None
    project_url: Optional[str] = None
    repository_url: Optional[str] = None


class PreferencesUpdate(BaseModel):
    preferred_job_roles: Optional[List[str]] = None
    preferred_industries: Optional[List[str]] = None
    preferred_job_type: Optional[str] = None
    work_mode_preference: Optional[str] = None
    expected_salary_min: Optional[float] = None
    expected_salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    salary_type: Optional[str] = None
    preferred_locations: Optional[List[str]] = None


class LinksUpdate(BaseModel):
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    twitter_url: Optional[str] = None
    behance_url: Optional[str] = None
    dribbble_url: Optional[str] = None
    other_url: Optional[str] = None
    other_url_label: Optional[str] = None


# ── Personal / Location / Summary ─────────────────────────────────────────────

@router.put("/personal")
async def update_personal(
    data: PersonalUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)

    # User-table fields
    if data.first_name is not None:
        current_user.first_name = data.first_name
    if data.last_name is not None:
        current_user.last_name = data.last_name
    if data.phone is not None:
        current_user.phone = data.phone
    if data.gender is not None:
        if data.gender not in ("male", "female", "other"):
            raise HTTPException(status_code=400, detail="gender must be male, female, or other")
        current_user.gender = data.gender
    if data.date_of_birth is not None:
        from datetime import datetime as dt
        current_user.date_of_birth = dt.strptime(data.date_of_birth, "%Y-%m-%d").date()

    # Extended profile fields
    if data.alternate_phone is not None:
        profile.alternate_phone = data.alternate_phone
    if data.nationality is not None:
        profile.nationality = data.nationality
    if data.marital_status is not None:
        profile.marital_status = data.marital_status
    if data.languages is not None:
        profile.languages = json.dumps(data.languages)

    db.commit()
    db.refresh(current_user)
    db.refresh(profile)
    return {"message": "Personal info updated"}


@router.put("/location")
async def update_location(
    data: LocationUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)

    scalar_fields = (
        "current_address_line", "current_city", "state", "country", "pincode",
        "permanent_address_line", "permanent_city", "permanent_state",
        "permanent_country", "permanent_pincode", "permanent_same_as_current",
        "willing_to_relocate",
    )
    for field in scalar_fields:
        val = getattr(data, field)
        if val is not None:
            setattr(profile, field, val)

    # When "same as current" is set, mirror current address into permanent fields
    if data.permanent_same_as_current:
        profile.permanent_address_line = profile.current_address_line
        profile.permanent_city        = profile.current_city
        profile.permanent_state       = profile.state
        profile.permanent_country     = profile.country
        profile.permanent_pincode     = profile.pincode

    if data.preferred_locations is not None:
        profile.preferred_locations = json.dumps(data.preferred_locations)

    db.commit()
    return {"message": "Location updated"}


@router.put("/summary")
async def update_summary(
    data: SummaryUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)

    for field in ("headline", "about", "total_experience_years",
                  "current_employment_status", "notice_period", "open_to_work"):
        val = getattr(data, field)
        if val is not None:
            setattr(profile, field, val)

    db.commit()

    # Sync notice_period back to Candidate rows so HR dashboard reflects the update
    if data.notice_period is not None:
        from src.models.models import Candidate as _Cand
        db.query(_Cand).filter(_Cand.user_id == current_user.id).update(
            {"notice_period": data.notice_period}, synchronize_session=False
        )
        db.commit()

    return {"message": "Summary updated"}


# ── Work Experience ───────────────────────────────────────────────────────────

@router.post("/work-experience")
async def add_work_experience(
    data: WorkExperienceCreate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = CandidateWorkExperience(profile_id=profile.id, **data.dict())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"message": "Work experience added", "id": str(entry.id)}


@router.put("/work-experience/{entry_id}")
async def update_work_experience(
    entry_id: str,
    data: WorkExperienceUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = db.query(CandidateWorkExperience).filter(
        CandidateWorkExperience.id == entry_id,
        CandidateWorkExperience.profile_id == profile.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    for field, val in data.dict(exclude_none=True).items():
        setattr(entry, field, val)
    db.commit()
    return {"message": "Work experience updated"}


@router.delete("/work-experience/{entry_id}")
async def delete_work_experience(
    entry_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = db.query(CandidateWorkExperience).filter(
        CandidateWorkExperience.id == entry_id,
        CandidateWorkExperience.profile_id == profile.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return {"message": "Work experience deleted"}


# ── Education ─────────────────────────────────────────────────────────────────

@router.post("/education")
async def add_education(
    data: EducationCreate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = CandidateEducation(profile_id=profile.id, **data.dict())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"message": "Education added", "id": str(entry.id)}


@router.put("/education/{entry_id}")
async def update_education(
    entry_id: str,
    data: EducationUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = db.query(CandidateEducation).filter(
        CandidateEducation.id == entry_id,
        CandidateEducation.profile_id == profile.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    for field, val in data.dict(exclude_none=True).items():
        setattr(entry, field, val)
    db.commit()
    return {"message": "Education updated"}


@router.delete("/education/{entry_id}")
async def delete_education(
    entry_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = db.query(CandidateEducation).filter(
        CandidateEducation.id == entry_id,
        CandidateEducation.profile_id == profile.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return {"message": "Education deleted"}


# ── Skills ────────────────────────────────────────────────────────────────────

@router.post("/skills")
async def add_skill(
    data: SkillCreate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    if len(profile.skills) >= 30:
        raise HTTPException(status_code=400, detail="Maximum 30 skills allowed")
    skill = CandidateSkill(profile_id=profile.id, **data.dict())
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return {"message": "Skill added", "id": str(skill.id)}


@router.delete("/skills/{skill_id}")
async def delete_skill(
    skill_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    skill = db.query(CandidateSkill).filter(
        CandidateSkill.id == skill_id,
        CandidateSkill.profile_id == profile.id,
    ).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    db.delete(skill)
    db.commit()
    return {"message": "Skill removed"}


# ── Certifications ────────────────────────────────────────────────────────────

@router.post("/certifications")
async def add_certification(
    data: CertificationCreate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = CandidateCertification(profile_id=profile.id, **data.dict())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"message": "Certification added", "id": str(entry.id)}


@router.put("/certifications/{entry_id}")
async def update_certification(
    entry_id: str,
    data: CertificationUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = db.query(CandidateCertification).filter(
        CandidateCertification.id == entry_id,
        CandidateCertification.profile_id == profile.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Certification not found")

    for field, val in data.dict(exclude_none=True).items():
        setattr(entry, field, val)
    db.commit()
    return {"message": "Certification updated"}


@router.delete("/certifications/{entry_id}")
async def delete_certification(
    entry_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = db.query(CandidateCertification).filter(
        CandidateCertification.id == entry_id,
        CandidateCertification.profile_id == profile.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Certification not found")
    db.delete(entry)
    db.commit()
    return {"message": "Certification deleted"}


# ── Projects ──────────────────────────────────────────────────────────────────

@router.post("/projects")
async def add_project(
    data: ProjectCreate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    payload = data.dict()
    if payload.get("technologies_used") is not None:
        payload["technologies_used"] = json.dumps(payload["technologies_used"])
    entry = CandidateProject(profile_id=profile.id, **payload)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"message": "Project added", "id": str(entry.id)}


@router.put("/projects/{entry_id}")
async def update_project(
    entry_id: str,
    data: ProjectUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = db.query(CandidateProject).filter(
        CandidateProject.id == entry_id,
        CandidateProject.profile_id == profile.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    for field, val in data.dict(exclude_none=True).items():
        if field == "technologies_used":
            val = json.dumps(val)
        setattr(entry, field, val)
    db.commit()
    return {"message": "Project updated"}


@router.delete("/projects/{entry_id}")
async def delete_project(
    entry_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    entry = db.query(CandidateProject).filter(
        CandidateProject.id == entry_id,
        CandidateProject.profile_id == profile.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(entry)
    db.commit()
    return {"message": "Project deleted"}


# ── Preferences & Links ───────────────────────────────────────────────────────

@router.put("/preferences")
async def update_preferences(
    data: PreferencesUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    for field, val in data.dict(exclude_none=True).items():
        if field in ("preferred_job_roles", "preferred_industries", "preferred_locations"):
            val = json.dumps(val)
        setattr(profile, field, val)
    db.commit()

    # Sync expected_salary_min → Candidate.expected_salary so HR sees it
    if data.expected_salary_min is not None:
        from src.models.models import Candidate as _Cand
        db.query(_Cand).filter(_Cand.user_id == current_user.id).update(
            {"expected_salary": float(data.expected_salary_min)}, synchronize_session=False
        )
        db.commit()

    return {"message": "Preferences updated"}


@router.put("/links")
async def update_links(
    data: LinksUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    for field, val in data.dict(exclude_none=True).items():
        setattr(profile, field, val)
    db.commit()

    # Sync linkedin/github/portfolio back to Candidate rows so HR dashboard reflects updates
    link_sync = {k: v for k, v in data.dict(exclude_none=True).items()
                 if k in ("linkedin_url", "github_url", "portfolio_url")}
    if link_sync:
        from src.models.models import Candidate as _Cand
        db.query(_Cand).filter(_Cand.user_id == current_user.id).update(
            link_sync, synchronize_session=False
        )
        db.commit()

    return {"message": "Links updated"}


# ── File uploads ──────────────────────────────────────────────────────────────

@router.post("/photo")
async def upload_profile_photo(
    photo: UploadFile = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    allowed_ext = {".jpg", ".jpeg", ".png"}
    ext = Path(photo.filename).suffix.lower() if photo.filename else ""
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="Only JPG/PNG files are allowed")

    content = await photo.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Photo must be under 2 MB")

    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"
    path = f"uploads/profile_photos/{filename}"
    from src.services.storage import storage
    storage.save_bytes(content, path)

    profile = _get_or_create_profile(current_user.id, db)
    profile.profile_photo_path = path
    db.commit()
    return {"message": "Photo uploaded", "path": path}


@router.post("/documents/resume")
async def upload_resume(
    file: UploadFile = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="Resume must be a PDF file")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Resume must be under 5 MB")

    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}.pdf"
    path = f"uploads/resumes/{filename}"
    from src.services.storage import storage
    storage.save_bytes(content, path)

    profile = _get_or_create_profile(current_user.id, db)
    profile.resume_path = path
    db.commit()
    return {"message": "Resume uploaded", "path": path, "filename": file.filename}


@router.post("/documents/cover-letter")
async def upload_cover_letter(
    file: UploadFile = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    allowed_ext = {".pdf", ".doc", ".docx"}
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="Cover letter must be PDF or DOC/DOCX")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Cover letter must be under 5 MB")

    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"
    path = f"uploads/cover_letters/{filename}"
    from src.services.storage import storage
    storage.save_bytes(content, path)

    profile = _get_or_create_profile(current_user.id, db)
    profile.cover_letter_path = path
    db.commit()
    return {"message": "Cover letter uploaded", "path": path, "filename": file.filename}


# ── Completion ────────────────────────────────────────────────────────────────

@router.get("/completion")
async def get_completion(
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    profile = _get_or_create_profile(current_user.id, db)
    return _compute_completion(current_user, profile)


# ── Resume Auto-Fill (Smart Profile Import) ───────────────────────────────────

@router.post("/import/parse")
async def parse_resume_for_import(
    resume: UploadFile = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Parse an uploaded resume and return structured, editable data for review.

    Writes NOTHING to the profile — the candidate reviews/edits the result and
    only then calls /import/apply. Reuses the same extraction the ATS uses.
    """
    suffix = Path(resume.filename or "").suffix.lower()
    if suffix not in (".pdf", ".docx", ".doc"):
        raise HTTPException(status_code=400, detail="Please upload a PDF or Word (.docx/.doc) resume")

    import tempfile
    from src.services.enhanced_ats_service import EnhancedATSScoringService, detect_sections

    tmp_path = None
    try:
        contents = await resume.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name
        try:
            parsed = EnhancedATSScoringService().enhanced_parse_resume(tmp_path)
        except ValueError as e:
            # No text extracted (e.g. an image-only scan that OCR couldn't read)
            raise HTTPException(status_code=422, detail=str(e))

        # Section-anchor projects/experience/education instead of the noisy
        # whole-document scan the ATS extractor uses; fall back to the ATS
        # values only when the section yields nothing.
        sections = detect_sections(parsed.text)
        section_projects = _projects_from_section(sections.get("projects", ""))

        section_work = _work_experience_from_section(sections.get("experience", ""))
        if section_work:
            work_experience = [
                {
                    "title": w["title"],
                    "company": w["company"],
                    "duration_months": None,
                    "confidence": w["confidence"],
                }
                for w in section_work
            ]
        else:
            # No Experience section detected (e.g. an unrecognised heading like
            # "Professional Journey"): the ATS whole-document scan leaks skills
            # and institutions in as "companies", so filter those out rather
            # than surface garbage entries for review.
            skill_set = {s.lower() for s in (parsed.skills or []) if s}
            work_experience = []
            for w in (parsed.work_experience or []):
                title = (w.get("title") or "").strip()
                company = (w.get("company") or "").strip()
                # Require a real job title: the ATS scan's false positives
                # (the candidate's name, heading words, stray section text) all
                # arrive with an empty title, so this drops them.
                if not title:
                    continue
                probe = company or title
                if probe.lower() in skill_set:
                    continue
                if _INSTITUTION_KW.search(probe) or _DEGREE_KW.search(probe):
                    continue
                work_experience.append({
                    "title": title,
                    "company": company,
                    "duration_months": w.get("duration_months"),
                    # Not w["confidence"]: the extractor rates the string, not the
                    # fact that this row came from an unanchored scan.
                    "confidence": CONF_SCAN,
                })

        section_edu = _education_from_section(sections.get("education", ""))
        if section_edu:
            education = [
                {
                    "degree": e["degree"],
                    "institution": e["institution"],
                    "confidence": e["confidence"],
                }
                for e in section_edu
            ]
        else:
            education = [
                {
                    "degree": (e.get("degree") or "").strip(),
                    "institution": "",
                    # extract_education_info returns up to 0.85 for a clean degree
                    # string; that says nothing about this unanchored row.
                    "confidence": CONF_SCAN,
                }
                for e in (parsed.education or [])
                if e.get("degree")
            ]

        # Hoisted out of the response dict so the gap check below can see it.
        certifications = (_certifications_from_section(sections.get("certifications", ""))
                          or [c for c in (parsed.certifications or []) if c])

        # Ask Gemini for the sections the regex extractors could not read AT ALL.
        # A section with even one row is left alone: topping it up would leave the
        # candidate unable to tell which rows came off their own resume. Nothing is
        # written here — they review, edit and tick each item before /import/apply
        # inserts anything.
        draft = {
            "work_experience": work_experience,
            "education": education,
            "certifications": certifications,
            "projects": section_projects,
        }
        ai_sections = []
        try:
            from src.services.llm_profile_extractor import extract_sections
            filled = await extract_sections(parsed.text, draft)
            for key, items in filled.items():
                draft[key] = items
                ai_sections.append(key)
        except Exception as e:
            # An import or scheduling failure must not cost the candidate their parse.
            logger.warning(f"Resume import: AI section fill failed: {e}")

        # Experience is derived AFTER the fill, so AI-recovered roles still count.
        from src.services.enhanced_ats_service import enhanced_ats_service
        total_exp = enhanced_ats_service.calculate_work_experience_years_from_entries(
            draft["work_experience"])
        if total_exp == 0.0 and parsed.experience_years:
            total_exp = parsed.experience_years

        return {
            "skills": [s for s in parsed.skills if s],
            "total_experience_years": total_exp,
            "contact": parsed.contact_info or {},
            "work_experience": draft["work_experience"],
            "education": draft["education"],
            "certifications": draft["certifications"],
            "projects": draft["projects"],
            "languages": [l for l in (parsed.languages or []) if l],
            "confidence_score": round(float(parsed.confidence_score), 2),
            # Which sections came from the model rather than the resume's own layout.
            # Section-level because the fill is all-or-nothing, and because
            # certifications are plain strings with nowhere to hang a per-item flag.
            "ai_sections": ai_sections,
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class ResumeImportApply(BaseModel):
    skills: List[str] = []
    work_experience: List[Dict[str, Any]] = []   # {title, company, duration_months?}
    education: List[Dict[str, Any]] = []          # {degree?, institution?}
    certifications: List[str] = []
    projects: List[Dict[str, Any]] = []           # {name, description?}
    languages: List[str] = []
    total_experience_years: Optional[float] = None
    headline: Optional[str] = None


@router.post("/import/apply")
async def apply_resume_import(
    data: ResumeImportApply,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Append the candidate-reviewed resume data to their profile.

    Append-only for list sections (deduped where sensible, skills capped at 30);
    scalar profile fields are filled only if currently empty (never overwritten).
    All writes happen in a single transaction.
    """
    profile = _get_or_create_profile(current_user.id, db)
    counts = {
        "skills_added": 0, "skills_skipped": 0, "work_experience_added": 0,
        "education_added": 0, "certifications_added": 0, "projects_added": 0,
    }

    # Skills — dedupe case-insensitively, respect the 30-skill cap
    existing_skills = {s.skill_name.lower() for s in profile.skills if s.skill_name}
    slots = 30 - len(profile.skills)
    for name in data.skills:
        nm = (name or "").strip()
        if not nm or nm.lower() in existing_skills:
            continue
        if slots <= 0:
            counts["skills_skipped"] += 1
            continue
        db.add(CandidateSkill(
            profile_id=profile.id, skill_name=nm[:100],
            proficiency_level="Beginner", category="Technical",
        ))
        existing_skills.add(nm.lower())
        slots -= 1
        counts["skills_added"] += 1

    # Work experience — job_title & company_name are NOT NULL, so fill sensibly
    for w in data.work_experience:
        title = (w.get("title") or "").strip()
        company = (w.get("company") or "").strip()
        if not title and not company:
            continue
        db.add(CandidateWorkExperience(
            profile_id=profile.id,
            job_title=(title or "Role")[:255],
            company_name=(company or "Company")[:255],
        ))
        counts["work_experience_added"] += 1

    # Education — institution_name is NOT NULL
    for e in data.education:
        degree = (e.get("degree") or "").strip()
        institution = (e.get("institution") or "").strip()
        if not degree and not institution:
            continue
        db.add(CandidateEducation(
            profile_id=profile.id,
            degree=(degree[:100] or None),
            institution_name=(institution or degree or "Institution")[:255],
        ))
        counts["education_added"] += 1

    # Certifications — dedupe case-insensitively
    existing_certs = {c.certificate_name.lower() for c in profile.certifications if c.certificate_name}
    for name in data.certifications:
        nm = (name or "").strip()
        if not nm or nm.lower() in existing_certs:
            continue
        db.add(CandidateCertification(profile_id=profile.id, certificate_name=nm[:255]))
        existing_certs.add(nm.lower())
        counts["certifications_added"] += 1

    # Projects
    for p in data.projects:
        title = (p.get("name") or p.get("title") or "").strip()
        if not title:
            continue
        db.add(CandidateProject(
            profile_id=profile.id,
            project_title=title[:255],
            description=(p.get("description") or None),
        ))
        counts["projects_added"] += 1

    # Scalars — fill only if currently empty (never clobber curated data)
    if data.total_experience_years is not None and not profile.total_experience_years:
        profile.total_experience_years = data.total_experience_years
    if data.headline and not profile.headline:
        profile.headline = data.headline[:120]

    db.commit()
    return {"message": "Profile updated from resume", **counts}
