"""
CANDIDATE PROFILE ROUTES
Handles all extended profile sections:
  work experience, education, skills, certifications, projects,
  preferences, social links, file uploads, and completion score.
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, field_validator
import uuid, os, json, re, logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Extraction confidence ──────────────────────────────────────────────────────
# This number decides whether a row arrives pre-ticked in the review screen (see
# HIGH_CONF in ResumeImportReview.jsx). It expresses ONE thing: how well anchored
# the extraction was — not how tidy the resulting string looks.
#
#   SECTION  — read under a real section heading, with both halves present. The
#              resume's own layout confirms what the text is.
#   PARTIAL  — heading-anchored, but one half missing (a degree with no
#              institution). Real information, not yet worth auto-accepting.
#   SCAN     — the whole-document fallback, used only when no heading matched.
#              Noisy by construction: it leaks skills and institution names in as
#              "companies", which is why the filtering below has to exist.
#   INFERRED — read out of prose by the model, or a title-shape guess. Plausible,
#              unverified: always an opt-in.
#
# Work rows read from a real Experience section used to carry 0.6, below the 0.7
# threshold, so they were never pre-ticked. The scan fallback meanwhile inherited
# up to 0.85 from extract_education_info — which rates the degree STRING and cannot
# know its row came from an unanchored scan — so the worse extraction outranked the
# better one. The route overrides that value rather than trusting it through.
CONF_SECTION = 0.85
CONF_SECTION_PARTIAL = 0.6
CONF_SCAN = 0.5
CONF_INFERRED = 0.5

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


#  helpers
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
# Only words that essentially never open a real project title belong here.
_PROJECT_DESC_STARTERS = re.compile(
    r'^(developed|built|created|designed|implemented|worked|using|used|responsible|'
    r'led|managed|this|it|we|i|my|preprocessed|trained|achieved|deployed|'
    r'applied|verified|delivered|evaluated|fine-tuned|formulated|maintained|'
    r'conducted|calculated|performed|utilizing|utilizes|leverages|leveraging|'
    r'spearheaded|orchestrated|established|accelerated|pioneered|transformed)\b',
    re.IGNORECASE,
)
# Participles and articles that open descriptions AND plenty of genuine titles
# ("Automated Attendance System", "Integrated Payment Gateway", "The Weather
# App"). These disqualify a line only when a second description signal agrees —
# see the is_action_desc block in _projects_from_section().
_AMBIGUOUS_TITLE_STARTERS = re.compile(
    r'^(automated|integrated|optimized|engineered|extracted|analyzed|constructed|'
    r'configured|generated|the|a|an)\b',
    re.IGNORECASE,
)
# A line made up entirely of links, link labels and separators carries no
# description text — its URLs are captured separately.
_URL_ONLY = re.compile(
    r'^(?:(?:github|gitlab|repo|repository|demo|live|url|link|site|website)\s*[:\-]\s*'
    r'|https?://\S+|www\.\S+|[\s|,;.\-–—]+)+$',
    re.IGNORECASE,
)
# Institution words that never appear in a project title, unlike "school",
# "institute", "academy", "center" or "campus", which routinely do.
_STRONG_INSTITUTION_KW = re.compile(r'\b(university|college|vidyalaya)\b', re.IGNORECASE)
# Infinitive phrases: "Payroll to manage HR data" → description, not a title
_INFINITIVE_PHRASE = re.compile(
    r'\bto\s+(manage|handle|process|predict|detect|analyze|analyse|build|support|create|'
    r'generate|develop|monitor|track|store|provide|enable|improve|reduce|automate|'
    r'classify|implement|integrate|evaluate|test|deploy|serve|display|perform|connect|'
    r'allow|ensure|facilitate|access|visualize|extract|schedule|maintain|update|delete)\b',
    re.IGNORECASE,
)
# Lines that end with adverbs typical of description sentences ("...efficiently")
_DESC_TAIL = re.compile(
    r'\b(efficiently|effectively|accurately|automatically|seamlessly|dynamically|'
    r'successfully|robustly|rapidly|reliably|securely|significantly)\s*\.?$',
    re.IGNORECASE,
)
_LEADING_BULLET = re.compile(r'^(?:\(cid:\d+\)|[\s•\-\*·▪‣◦>.\u2022\u25cf\u25aa\u25b6\u25c6\u2605\u2705\U0001F000-\U0001FAFF])+')
_CID_EMOJI_CLEAN = re.compile(r'\(cid:\d+\)|[\u2022\u25cf\u25aa\u25b6\u25c6\u2605\u2705\U0001F000-\U0001FAFF]', re.UNICODE)
_TECH_LINE = re.compile(
    r'^(tech|technologies|tech\s*stack|stack|tools|built\s*with)\b\s*[:\-]',
    re.IGNORECASE,
)
_DATE_TOKEN = re.compile(
    r'\b((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}|(19|20)\d{2})\b',
    re.IGNORECASE,
)
# "Jan 2020 - Mar 2021", "2018 – Present", "2016-2020"
_DATE_RANGE = re.compile(
    r'(\d{4}|\w{3,9}\.?\s*\d{4})\s*(?:[-–—]|to)+\s*'
    r'(\d{4}|\w{3,9}\.?\s*\d{4}|present|current|ongoing)',
    re.IGNORECASE,
)
_ONGOING_WORDS = ("present", "current", "ongoing")
_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _normalize_month_year(token: str) -> Optional[str]:
    """Normalise a date token to YYYY-MM or YYYY.

    start_date/end_date are String(7) on CandidateProject, CandidateWorkExperience
    and friends, so "Jan 2020" (8 chars) has to become "2020-01" before it can be
    stored.
    """
    if not token:
        return None
    token = token.strip().strip(" .,-–—|")
    m_month = re.match(r'([A-Za-z]{3,9})\.?\s*((?:19|20)\d{2})$', token)
    if m_month:
        mon = _MONTHS.get(m_month.group(1)[:3].lower())
        if mon:
            return "%s-%s" % (m_month.group(2), mon)
        return m_month.group(2)
    m_year = re.match(r'((?:19|20)\d{2})$', token)
    return m_year.group(1) if m_year else None


def _parse_date_range(text: str) -> tuple:
    """Pull (start_date, end_date, ongoing) out of a free-text date range."""
    if not text:
        return None, None, False
    m = _DATE_RANGE.search(text)
    if not m:
        # A lone year/month with no range still tells us when it started.
        m_single = _DATE_TOKEN.search(text)
        return (_normalize_month_year(m_single.group(0)) if m_single else None), None, False
    start = _normalize_month_year(m.group(1))
    end_raw = m.group(2).strip()
    if end_raw.lower() in _ONGOING_WORDS:
        return start, None, True
    return start, _normalize_month_year(end_raw), False


def _year_of(token: str) -> Optional[int]:
    """The four-digit year inside a date token, as an int, for *_year columns."""
    if not token:
        return None
    m = re.search(r'(19|20)\d{2}', token)
    return int(m.group(0)) if m else None


def _coerce_year(value) -> Optional[int]:
    """A plausible four-digit year from client-supplied JSON, else None."""
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2100 else None


def _months_between(start: Optional[str], end: Optional[str], ongoing: bool = False) -> Optional[int]:
    """Whole months spanned by two YYYY[-MM] strings; counts to today when ongoing.

    Feeds duration_months, which the parse route hands to
    calculate_work_experience_years_from_entries() to total up experience.
    """
    def _ym(token):
        if not token:
            return None
        parts = token.split("-")
        try:
            return int(parts[0]), (int(parts[1]) if len(parts) > 1 else 1)
        except ValueError:
            return None

    begin = _ym(start)
    if not begin:
        return None
    if ongoing:
        now = datetime.utcnow()
        finish = (now.year, now.month)
    else:
        finish = _ym(end)
        if not finish:
            return None
    months = (finish[0] - begin[0]) * 12 + (finish[1] - begin[1])
    return months if months >= 0 else None
_DEGREE_KW = re.compile(
    r'\b('
    r'b[\.\- ]?tech|m[\.\- ]?tech|b[\.\- ]?e|m[\.\- ]?e|b[\.\- ]?sc|m[\.\- ]?sc|'
    r'b[\.\- ]?com|m[\.\- ]?com|b[\.\- ]?a|m[\.\- ]?a|bba|mba|mca|bca|ph[\.\- ]?d|doctorate|diploma|polytechnic|'
    r'bachelor|bachelors|master|masters|'
    r'intermediate|class\s*(xii|12|x|10)|12th|10th|hsc|ssc|plus\s*two|\+2|senior\s*secondary|secondary\s*school|high\s*school|matriculation|junior\s*college'
    r')\b',
    re.IGNORECASE,
)
_INSTITUTION_KW = re.compile(
    r'\b(university|college|institute|institution|school|academy|vidyalaya|polytechnic|campus|center|centre|hall)\b',
    re.IGNORECASE,
)
_GRADE_CGPA_RE = re.compile(
    r'\b(cgpa|gpa|percentage|marks|grade|score|distinction|first\s*class|honors|honours)\b.*$',
    re.IGNORECASE,
)

_URL_PATTERN = re.compile(r'(?:https?://|www\.|[\w\-]+\.(?:com|org|io|net|edu|gov|co|in|dev|app|me|ai))', re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
_PHONE_PATTERN = re.compile(r'(?:\+?\d{1,3}[\s\.\-]*)?\(?\d{2,5}\)?[\s\.\-]*\d{3,5}[\s\.\-]*\d{3,5}')
_LOCATION_PATTERN = re.compile(r'\b([A-Z][a-zA-Z\s]{2,25}),\s*([A-Z][a-zA-Z\s]{2,20}|[A-Z]{2})\b')

# ── Phase 2: Alias Mapping for Field Labels ───────────────────────────────────
_TECH_ALIASES: set = {
    "tech", "technologies", "tech stack", "techstack", "stack",
    "tools", "built with", "languages", "language", "frameworks",
    "libraries", "toolset", "tech tools", "languages used",
    "frameworks & libraries", "language & tools", "technologies used",
    "tooling", "skills used", "platforms", "environment", "using",
    "tech used", "technology stack", "technology used", "key technologies",
    "tools & technologies", "frameworks used", "libs",
}
_ROLE_ALIASES: set = {
    "role", "position", "my role", "as a", "designation",
    "contribution", "responsibilities", "my responsibilities",
    "played role", "i worked as", "served as", "worked as",
}
_DATE_ALIASES: set = {
    "duration", "timeline", "period", "date", "time",
    "from", "span", "during", "start date", "end date",
}
_LINK_ALIASES: set = {
    "live url", "live link", "demo", "demo url", "website",
    "hosted at", "deployed at", "app url", "project url",
    "live", "deployment", "app link", "hosted",
}
_REPO_ALIASES: set = {
    "github", "repository", "repo", "source code", "code",
    "gitlab", "bitbucket", "git", "source", "github link",
    "github url", "repo url",
}

# ── Phase 3: project_type Inference ──────────────────────────────────────────
_PROJECT_TYPE_MAP: dict = {
    "personal": "Personal",
    "individual": "Personal",
    "side": "Personal",
    "self": "Personal",
    "hobby": "Personal",
    "freelance": "Personal",
    "academic": "Academic",
    "college": "Academic",
    "university": "Academic",
    "course": "Academic",
    "capstone": "Academic",
    "thesis": "Academic",
    "research": "Academic",
    "lab": "Academic",
    "professional": "Professional",
    "work": "Professional",
    "internship": "Professional",
    "industry": "Professional",
    "corporate": "Professional",
    "client": "Professional",
    "open source": "Open-source",
    "open-source": "Open-source",
    "opensource": "Open-source",
    "oss": "Open-source",
    "community": "Open-source",
}


def _infer_project_type(section_header: str) -> Optional[str]:
    """Infer project_type from the section header string.
    Returns None if no confident match — don't default blindly."""
    if not section_header:
        return None
    low = section_header.lower()
    for keyword, ptype in _PROJECT_TYPE_MAP.items():
        if keyword in low:
            return ptype
    return None


def _match_alias(prefix: str, alias_set: set, threshold: int = 78) -> bool:
    """Fuzzy-match a field label prefix against an alias set using rapidfuzz."""
    try:
        from rapidfuzz import process, fuzz
        best = process.extractOne(prefix.lower().strip(), alias_set, scorer=fuzz.ratio)
        return best is not None and best[1] >= threshold
    except Exception:
        # Fallback: exact or substring match
        p = prefix.lower().strip()
        return p in alias_set or any(a in p for a in alias_set)


def _spacy_is_description(line: str) -> bool:
    """True when a line reads as a description sentence rather than a project title.

    Prefers spaCy — a description has verbs dominating proper nouns — and falls
    back to _keyword_is_description() when spaCy is unavailable, so the answer is
    defined either way rather than silently permissive.
    """
    try:
        from src.services.enhanced_ats_service import _get_nlp
        nlp = _get_nlp()
        if nlp is not None:
            doc = nlp(line)
            verb_count = sum(1 for t in doc if t.pos_ == "VERB")
            propn_count = sum(1 for t in doc if t.pos_ in ("PROPN", "NOUN"))
            # A description typically has verbs dominating over proper nouns
            return verb_count > 0 and verb_count >= propn_count
    except Exception:
        pass
    return _keyword_is_description(line)


def _work_experience_from_section(section_text: str) -> List[Dict[str, Any]]:
    """Extract (company, title, dates) from the detected Experience section only."""
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
            # The date half of the line was already matched to split the
            # company name off; keep it rather than discarding it.
            start_date, end_date, currently_working = _parse_date_range(line[m.start():])
            title = ""
            j = i + 1
            if j < n and not _LEADING_BULLET.match(lines[j]) and not _DATE_TOKEN.search(lines[j]):
                title = lines[j].strip(" .,-–—|")
                i = j
            if company or title:
                out.append({
                    "title": title,
                    "company": company,
                    "start_date": start_date,
                    "end_date": end_date,
                    "currently_working": currently_working,
                    "duration_months": _months_between(start_date, end_date, currently_working),
                    "confidence": CONF_SECTION,
                })
        i += 1
    return out[:15]


def _complete_degree_title(deg_text: str, next_line_text: str = "") -> str:
    """Clean CID/emoji font artifacts and auto-complete cut-off degree titles."""
    deg_text = _CID_EMOJI_CLEAN.sub("", deg_text).strip(" .,-–—|")
    if not deg_text:
        return ""

    if next_line_text:
        next_clean = _CID_EMOJI_CLEAN.sub("", next_line_text).strip(" .,-–—|")
        if re.match(r'^(engineering|technology|management|science|sciences|systems)\b', next_clean, re.I):
            if not _INSTITUTION_KW.search(next_clean):
                return f"{deg_text} {next_clean}".strip(" .,-–—|")

    deg_lower = deg_text.lower()
    if deg_lower.endswith("computer science and"):
        return deg_text + " Engineering"
    elif deg_lower.endswith("electronics and"):
        return deg_text + " Communication Engineering"
    elif deg_lower.endswith("electrical and"):
        return deg_text + " Electronics Engineering"
    elif deg_lower.endswith("artificial intelligence and"):
        return deg_text + " Machine Learning"
    elif deg_lower.endswith("information"):
        return deg_text + " Technology"

    deg_text = re.sub(r'\s+(?:and|&|in|of|,)\s*$', '', deg_text, flags=re.I)
    return deg_text.strip(" .,-–—|")


# Terms that name a qualification but also appear inside institution names ("Sri
# Chaitanya Junior College", "Zilla Parishad High School"). A line whose only
# degree hits are these is an institution, not a qualification.
_SCHOOL_TYPE_KW = re.compile(r'\b(junior\s*college|high\s*school|secondary\s*school)\b', re.IGNORECASE)


def _is_degree_line(text: str) -> bool:
    """True when the line names a qualification rather than an institution."""
    if not _DEGREE_KW.search(text):
        return False
    return bool(_DEGREE_KW.search(_SCHOOL_TYPE_KW.sub(" ", text)))


def _trim_institution(text: str) -> str:
    """Cut an institution name after its institution keyword so the trailing
    location drops ("Malla Reddy University Hyderabad" -> "Malla Reddy
    University"), while keeping a following "of <Name>" intact so
    "University of Madrid" survives."""
    im = _INSTITUTION_KW.search(text)
    if not im:
        return text.strip(" .,-–—|")
    end = im.end()
    of_match = re.match(r"\s+of\s+[A-Za-z][\w&.'-]*(?:\s+[A-Za-z][\w&.'-]*){0,3}", text[end:])
    if of_match:
        end += of_match.end()
    # A comma-qualified campus belongs to the name ("University of
    # California, Berkeley"); a bare trailing word is the city and goes.
    campus = re.match(r",\s*[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,2}", text[end:])
    if campus:
        end += campus.end()
    return text[:end].strip(" .,-–—|")


def _education_from_section(section_text: str) -> List[Dict[str, Any]]:
    """Extract (degree, institution) pairs dynamically across single-line and multi-line education layouts."""
    if not section_text or not section_text.strip():
        return []

    lines = [ln.strip() for ln in section_text.split("\n") if ln.strip()]
    out: List[Dict[str, Any]] = []

    from src.services.enhanced_ats_service import _is_section_header

    i = 0
    while i < len(lines):
        raw_line = _LEADING_BULLET.sub("", lines[i]).strip(" .,-–—|")
        raw_line = _CID_EMOJI_CLEAN.sub("", raw_line).strip(" .,-–—|")
        if not raw_line or len(raw_line) < 3:
            i += 1
            continue

        matched_hdr = _is_section_header(raw_line)
        if matched_hdr == "education":
            i += 1
            continue
        if matched_hdr and matched_hdr != "education":
            break

        if _URL_PATTERN.search(raw_line) or _EMAIL_PATTERN.search(raw_line) or _PHONE_PATTERN.search(raw_line):
            i += 1
            continue

        m_deg = _DEGREE_KW.search(raw_line)
        if m_deg:
            m_date = _DATE_TOKEN.search(raw_line)
            clean_line = raw_line[: m_date.start()] if m_date else raw_line
            m_grade = _GRADE_CGPA_RE.search(clean_line)
            if m_grade and m_grade.start() > 0:
                clean_line = clean_line[: m_grade.start()]
            clean_line = clean_line.strip(" .,-–—|")

            # The date and grade were matched only to trim the degree string;
            # keep their values too, for start_year/end_year/grade.
            start_year = end_year = None
            currently_pursuing = False
            if m_date:
                d_start, d_end, d_ongoing = _parse_date_range(raw_line[m_date.start():])
                start_year, end_year = _year_of(d_start), _year_of(d_end)
                currently_pursuing = d_ongoing
            # m_grade searched the already-date-truncated line; the grade often
            # sits after the dates ("... 2018 - 2022 CGPA: 8.6"), so read the
            # value off the full line.
            m_grade_full = _GRADE_CGPA_RE.search(raw_line)
            grade = m_grade_full.group(0).strip(" .,-–—|")[:50] if m_grade_full else None

            degree_part = clean_line
            inst_part = ""

            m_sep = re.search(r'\s+(?:from|at|[-–—|])\s+', clean_line, re.I)
            if m_sep:
                p1 = clean_line[:m_sep.start()].strip(" .,-–—|")
                p2 = clean_line[m_sep.end():].strip(" .,-–—|")
                if _DEGREE_KW.search(p1):
                    degree_part = p1
                    inst_part = _trim_institution(p2)
                elif _DEGREE_KW.search(p2):
                    degree_part = p2
                    inst_part = _trim_institution(p1)
            elif _INSTITUTION_KW.search(clean_line):
                parts = [p.strip() for p in clean_line.split(',') if p.strip()]
                deg_parts = [p for p in parts if _DEGREE_KW.search(p)]
                inst_parts = [p for p in parts if not _DEGREE_KW.search(p)]
                if deg_parts:
                    degree_part = ", ".join(deg_parts)
                if inst_parts:
                    inst_part = ", ".join(inst_parts)

            next_line_str = lines[i + 1] if i + 1 < len(lines) else ""
            degree_part = _complete_degree_title(degree_part, next_line_str)

            # Multi-line check: if institution not found inline, check the NEXT line(s)
            if not inst_part and i + 1 < len(lines):
                next_raw = _LEADING_BULLET.sub("", lines[i + 1]).strip(" .,-–—|")
                next_raw = _CID_EMOJI_CLEAN.sub("", next_raw).strip(" .,-–—|")
                if next_raw and not _is_degree_line(next_raw) and not _is_section_header(next_raw):
                    is_inst_kw = bool(_INSTITUTION_KW.search(next_raw))
                    is_not_cgpa_date = (
                        not bool(_DATE_TOKEN.search(next_raw))
                        and not bool(re.search(r'\b(cgpa|gpa|grade|percentage|marks|distinction)\b', next_raw, re.I))
                    )

                    if is_inst_kw or (is_not_cgpa_date and len(next_raw) <= 90):
                        clean_next = _DATE_TOKEN.sub("", next_raw)
                        clean_next = _GRADE_CGPA_RE.sub("", clean_next).strip(" .,-–—|")
                        # Split off specialization details like "— Specialization: Computer Science & AI"
                        clean_next_parts = re.split(r'\s+[–—|]\s+|\s+-\s+|,\s*Specialization', clean_next, maxsplit=1, flags=re.I)
                        inst_part = _trim_institution(clean_next_parts[0])
                        i += 1  # consumed institution line

            out.append({
                "degree": degree_part,
                "institution": inst_part,
                "start_year": start_year,
                "end_year": end_year,
                "currently_pursuing": currently_pursuing,
                "grade": grade,
                "confidence": CONF_SECTION if inst_part else CONF_SECTION_PARTIAL
            })
        elif _INSTITUTION_KW.search(raw_line):
            inst = _trim_institution(raw_line)
            if out and not out[-1]["institution"]:
                out[-1]["institution"] = inst
            else:
                out.append({"degree": "", "institution": inst,
                            "confidence": CONF_SECTION_PARTIAL})

        i += 1

    return out[:10]


def _certifications_from_section(section_text: str) -> List[str]:
    """Extract certification names dynamically for any resume, excluding noise and contact lines."""
    if not section_text or not section_text.strip():
        return []
    from src.services.enhanced_ats_service import SPOKEN_LANGUAGES_SET, SOFT_SKILLS_SET, DEGREE_TITLES_SET, _is_section_header
    out: List[str] = []
    seen = set()

    for raw in section_text.split("\n"):
        line = _LEADING_BULLET.sub("", raw).strip(" .,-–—|")
        if not line or len(line) < 3 or len(line) > 100:
            continue

        if _URL_PATTERN.search(line) or _EMAIL_PATTERN.search(line) or _PHONE_PATTERN.search(line):
            continue

        if line.count(",") >= 2 or line.count(";") >= 2:
            continue

        low = line.lower()
        if low in seen or low in SPOKEN_LANGUAGES_SET or low in SOFT_SKILLS_SET or low in DEGREE_TITLES_SET:
            continue

        if _is_section_header(line):
            continue

        if not re.search(r'[A-Za-z]{3,}', line):
            continue

        m = _DATE_TOKEN.search(line)
        if m and m.start() > 0:
            line = line[: m.start()].strip(" .,-–—|")

        key = line.lower()
        if key not in seen:
            seen.add(key)
            out.append(line)

    return out[:20]


def _keyword_is_description(text: str) -> bool:
    """Fast description sentence detector (action verbs, period termination, or past-tense verbs).

    The no-spaCy fallback for _spacy_is_description(); it used to shadow that
    function outright, which left the spaCy tiebreaker dead code.
    """
    if not text:
        return False
    if text.rstrip().endswith('.'):
        return True
    words = [w.strip('.,()[]{}') for w in text.lower().split()]
    action_verbs = {
        "built", "developed", "created", "implemented", "managed", "designed",
        "engineered", "preprocessed", "trained", "achieved", "deployed",
        "applied", "verified", "delivered", "optimized", "integrated", "automated",
        "constructed", "formulated", "calculated", "performed", "generated", "spearheaded"
    }
    return any(w in action_verbs for w in words)


def _projects_from_section(section_text: str, section_header: str = "") -> List[Dict[str, Any]]:
    """Extract structured projects from the Projects section.

    Uses all 5 precision phases:
      Phase 1 — Title classifier hardening (infinitive-phrase / desc-tail rejection)
      Phase 2 — Alias mapping for field labels (tech / role / date / url / repo)
      Phase 3 — project_type inference from section header
      Phase 4 — spaCy NER tiebreaker for borderline lines
      Phase 5 — rapidfuzz fuzzy alias matching

    Fields extracted (all 10 from Edit Project modal):
      title, project_type, role, technologies_used, description,
      ongoing, start_date, end_date, project_url, repository_url
    Empty fields are left None — never filled with garbage.
    """
    if not section_text or not section_text.strip():
        return []

    lines = [ln.strip() for ln in section_text.split("\n") if ln.strip()]
    if not lines:
        return []

    # Phase 3: infer project_type from section header once for all projects
    inferred_type = _infer_project_type(section_header)

    projects: List[Dict[str, Any]] = []
    current_proj: Optional[Dict[str, Any]] = None
    seen_titles: set = set()

    def _save_current():
        nonlocal current_proj
        if current_proj and current_proj.get("name"):
            t_key = current_proj["name"].lower()
            if t_key not in seen_titles:
                seen_titles.add(t_key)
                if isinstance(current_proj.get("description"), list):
                    desc_text = " ".join(current_proj["description"]).strip()
                    current_proj["description"] = desc_text if desc_text else None
                # Normalise empty strings to None — never send blank strings to DB
                for field in ("role", "technologies_used", "project_url", "repository_url",
                               "start_date", "end_date", "project_type"):
                    if current_proj.get(field) == "":
                        current_proj[field] = None
                current_proj.setdefault("confidence", CONF_INFERRED)
                projects.append(current_proj)
        current_proj = None

    from src.services.enhanced_ats_service import _is_section_header

    for raw in lines:
        line = _LEADING_BULLET.sub("", raw).strip(" .,-–—|")
        if not line:
            continue

        # ── Section boundary ──────────────────────────────────────────────────
        matched_hdr = _is_section_header(raw.strip())
        if matched_hdr == "projects":
            continue
        if matched_hdr and matched_hdr != "projects":
            _save_current()
            break

        # ── Phase 1: classify this line ───────────────────────────────────────
        is_bullet = bool(_LEADING_BULLET.match(raw)) or bool(
            re.match(r'^\s*[•\*·▪‣◦►■\u2022\u25cf\u25aa\u25b6]', raw)
        )
        # Phase 1: infinitive phrases and description-tail adverbs
        is_infinitive = bool(_INFINITIVE_PHRASE.search(line))
        is_desc_tail  = bool(_DESC_TAIL.search(line))

        is_action_desc = bool(_PROJECT_DESC_STARTERS.match(line))
        if not is_action_desc and _AMBIGUOUS_TITLE_STARTERS.match(line):
            # "Automated Attendance System" is a title; "Automated the nightly
            # export." is not. Require corroboration before rejecting.
            is_action_desc = (
                is_bullet
                # `line` has had trailing punctuation stripped, so the sentence
                # period only survives on the raw line.
                or raw.rstrip().endswith('.')
                or is_infinitive
                or is_desc_tail
            )

        # ── Phase 2: detect labeled field lines using alias maps ──────────────
        # A labeled field line looks like   "<label>: <value>"
        _label_val = re.match(r'^([A-Za-z][\w &\-/]{1,35})\s*[:\-]\s*(.+)$', line)
        _prefix    = _label_val.group(1).strip() if _label_val else ""
        _value     = _label_val.group(2).strip() if _label_val else ""

        is_tech_label = (
            bool(_TECH_LINE.match(raw))
            or bool(re.match(r'^(tech|technologies|tools|stack)\b', line, re.I))
            or (bool(_label_val) and _match_alias(_prefix, _TECH_ALIASES))
        )
        is_role_label = (
            bool(re.match(r'^(role|position|as\s+a)\b', line, re.I))
            or (bool(_label_val) and _match_alias(_prefix, _ROLE_ALIASES))
        )
        is_date_label = (
            bool(_DATE_TOKEN.search(line)) and len(line) <= 30
            or (bool(_label_val) and _match_alias(_prefix, _DATE_ALIASES) and bool(_DATE_TOKEN.search(_value)))
        )
        is_link = bool(_URL_PATTERN.search(line))
        is_repo_label  = bool(_label_val) and _match_alias(_prefix, _REPO_ALIASES)
        is_live_label  = bool(_label_val) and _match_alias(_prefix, _LINK_ALIASES)

        # ── is_title_candidate — all phases combined ──────────────────────────
        # Title candidate evaluation with separator awareness
        _sep_match = re.search(r'\s+[-\u2013\u2014|]\s+', line)
        _title_part = line[:_sep_match.start()].strip() if _sep_match else line
        _title_word_count = len(_title_part.split())

        is_location = bool(_LOCATION_PATTERN.search(line))
        # Reject education lines, but not titles that merely mention a school,
        # institute, academy or centre ("Online Learning Platform for Schools").
        is_degree   = _is_degree_line(line) or bool(_STRONG_INSTITUTION_KW.search(line))
        is_job_title = bool(re.search(r'\b(?:Full-?Stack|Software|Backend|Frontend|Data|Machine Learning)\s+(?:Engineer|Developer|Manager|Intern|Architect)\b', line, re.I))
        _comma_parts = [p.strip() for p in line.split(',')]
        is_raw_skill_list = len(_comma_parts) >= 3 and all(len(p) <= 25 for p in _comma_parts)

        is_title_candidate = (
            not is_bullet
            and not is_action_desc
            and not is_infinitive        # Phase 1: reject infinitive phrases
            and not is_desc_tail         # Phase 1: reject description-tail sentences
            and not is_tech_label
            and not is_role_label
            and not is_link
            and not is_date_label
            and not is_location          # Guardrail: reject address/location lines
            and not is_degree            # Guardrail: reject education/degree lines
            and not is_job_title         # Guardrail: reject job titles
            and not is_raw_skill_list    # Guardrail: reject raw comma-separated skill lists
            and 3 <= len(line) <= 120
            and 1 <= _title_word_count <= 8
            and (line[0].isupper() or line[0].isdigit())
        )

        # Phase 4: spaCy NER tiebreaker for borderline candidates
        if is_title_candidate and _title_word_count >= 5:
            if _spacy_is_description(_title_part):
                is_title_candidate = False

        # ── Handle title candidate → start new project ────────────────────────
        if is_title_candidate:
            _save_current()

            tech_in_title = ""
            m_bracket = re.search(r'\[(.*?)\]', line)   # only square brackets → tech tags
            if m_bracket:
                tech_in_title = m_bracket.group(1).strip()

            # Strip bracket tags and split on em-dash/hyphen/pipe separators for clean title
            clean_title = re.sub(r'\s*\[.*?\]', '', line).strip(" .,-–—|")
            clean_title = re.split(r'\s+[-–—|]\s+', clean_title)[0].strip(" .,-–—|")

            current_proj = {
                "name":             clean_title,
                "title":            clean_title,
                # Phase 3: inferred from section header, else None
                "project_type":     inferred_type,
                "role":             None,
                "technologies_used": tech_in_title if tech_in_title else None,
                "description":      [],
                "start_date":       None,
                "end_date":         None,
                "ongoing":          False,
                "project_url":      None,
                "repository_url":   None,
            }
            continue

        if current_proj is None:
            continue

        # ── Field population using alias maps (Phase 2) ───────────────────────
        # ── URL extraction (unconditional on any line) ─────────────────────────
        m_gh = re.search(r'https?://(?:www\.)?(?:github|gitlab)\.com/[\w\-]+(?:/[\w\-]+)?', line, re.I)
        if m_gh and not current_proj.get("repository_url"):
            current_proj["repository_url"] = m_gh.group(0)
        elif (is_live_label or (is_link and "github.com" not in line.lower() and "gitlab.com" not in line.lower())) and not current_proj.get("project_url"):
            m_url = re.search(r'https?://[\w\.\-]+\.[a-z]{2,}(?:/[\w\-]*)*', line, re.I)
            if m_url:
                current_proj["project_url"] = m_url.group(0)

        if is_tech_label:
            tech_val = _value if _label_val else re.split(r'[:\-]\s*', line, 1)[-1].strip()
            # Strip "| GitHub: ..." or "| Demo: ..." from tech_val if present on same line
            tech_val = re.split(r'\s*\|\s*(?:github|repo|repository|demo|live|url|link)\s*[:\-]', tech_val, flags=re.I)[0].strip()
            if tech_val:
                current_proj["technologies_used"] = tech_val

        elif is_role_label:
            role_val = _value if _label_val else re.split(r'[:\-]\s*', line, 1)[-1].strip()
            if role_val:
                current_proj["role"] = role_val

        elif is_date_label:
            date_src = _value if _label_val else line
            if _DATE_RANGE.search(date_src):
                start, end, ongoing = _parse_date_range(date_src)
                current_proj["start_date"] = start
                current_proj["end_date"] = end
                current_proj["ongoing"] = ongoing

        else:
            # Accumulate description lines — only real sentences (len >= 10).
            # A line that is nothing but links was already captured as a URL
            # above, so it would otherwise show up twice.
            if len(line) >= 10 and not _URL_ONLY.match(line):
                current_proj["description"].append(line)

    _save_current()
    return projects[:15]


def _job_preferences_from_section(section_text: str) -> Dict[str, Any]:
    if not section_text or not section_text.strip():
        return {}
    prefs = {}
    lines = [ln.strip() for ln in section_text.split("\n") if ln.strip()]
    for line in lines:
        line_low = line.lower()
        if "role" in line_low or "target" in line_low or "title" in line_low:
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                prefs["preferred_role"] = parts[1].strip()
        elif "location" in line_low or "preferred location" in line_low:
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                prefs["preferred_location"] = parts[1].strip()
        elif "salary" in line_low or "expected" in line_low:
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                prefs["expected_salary"] = parts[1].strip()
        elif "mode" in line_low or "remote" in line_low or "hybrid" in line_low or "onsite" in line_low:
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                prefs["work_mode"] = parts[1].strip()
    return prefs


def _location_from_section(section_text: str, full_text: str) -> Dict[str, Any]:
    """Extract location info dynamically for any city/state/country."""
    text = section_text if section_text and section_text.strip() else full_text[:1000]
    if not text:
        return {}
    m = _LOCATION_PATTERN.search(text)
    if m:
        return {
            "city": m.group(1).strip(),
            "state_or_country": m.group(2).strip(),
        }
    return {}


def _extract_link(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(0) if m else None


def _skills_from_section(section_text: str, fallback_skills: List[str]) -> List[str]:
    """Extract skills dynamically for any resume, cleaning brackets and filtering sentences."""
    from src.services.enhanced_ats_service import SPOKEN_LANGUAGES_SET, SOFT_SKILLS_SET, DEGREE_TITLES_SET
    out: List[str] = []
    seen: set = set()

    def _add_skill(s: str) -> None:
        clean_s = re.sub(r'^[^\w]+|[^\w]+$', '', s).strip()
        clean_s = re.sub(r'^[A-Za-z\s]+\(\s*', '', clean_s).strip()
        clean_s = clean_s.rstrip(')')

        if not clean_s or len(clean_s) <= 1:
            return
        low_s = clean_s.lower()
        if (
            low_s in seen
            or low_s in DEGREE_TITLES_SET
            or low_s in SOFT_SKILLS_SET
            or low_s in SPOKEN_LANGUAGES_SET
            or _URL_PATTERN.search(low_s)
            or _EMAIL_PATTERN.search(low_s)
        ):
            return
        seen.add(low_s)
        out.append(clean_s)

    if section_text and section_text.strip():
        raw_items = re.split(r'[\n,;•·▪|:]\s*', section_text)
        for item in raw_items:
            item_str = item.strip()
            if not item_str or len(item_str) > 40 or _PROJECT_DESC_STARTERS.match(item_str):
                continue
            _add_skill(item_str)
        if out:
            return out[:30]

    for s in fallback_skills:
        _add_skill(s)
    return out[:30]


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

    @field_validator("first_name", "last_name")
    @classmethod
    def _name_not_blank(cls, v):
        """A name may be left out of the payload, but never emptied.

        first_name is NOT NULL on the model, but "" satisfies that in MySQL, so an edit
        form that posted an empty field permanently erased the person's name. Whitespace
        is stripped first so "   " is not a loophole.
        """
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be blank")
        return v

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
    resume: Optional[UploadFile] = File(None),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Parse an uploaded resume and return structured, editable data for review.

    Writes NOTHING to the profile — the candidate reviews/edits the result and
    only then calls /import/apply. Reuses the same extraction the ATS uses.
    """
    if resume and resume.filename:
        suffix = Path(resume.filename or "").suffix.lower()
        if suffix not in (".pdf", ".docx", ".doc"):
            raise HTTPException(status_code=400, detail="Please upload a PDF or Word (.docx/.doc) resume")
        contents = await resume.read()
    else:
        profile = _get_or_create_profile(current_user.id, db)
        if not profile.resume_path:
            raise HTTPException(status_code=400, detail="No resume uploaded yet. Please upload a resume first.")
        from src.services.storage import storage
        try:
            contents = storage.read_bytes(profile.resume_path)
        except Exception:
            raise HTTPException(status_code=404, detail="Uploaded resume file not found in storage.")
        suffix = Path(profile.resume_path).suffix.lower()
        if suffix not in (".pdf", ".docx", ".doc"):
            suffix = ".pdf"

    import tempfile
    from src.services.enhanced_ats_service import (
        EnhancedATSScoringService, detect_sections, _is_section_header,
    )

    tmp_path = None
    try:
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

        # 1. Personal Info
        contact = parsed.contact_info or {}
        from src.services.resume_profile_parser import parse_resume_profile
        profile_identity = parse_resume_profile(parsed.text)
        full_name = profile_identity.get("full_name")
        first_name = current_user.first_name if current_user and current_user.first_name else (full_name.split()[0] if full_name else None)
        last_name = current_user.last_name if current_user and current_user.last_name else (" ".join(full_name.split()[1:]) if full_name and len(full_name.split()) > 1 else None)

        personal_info = {
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "email": contact.get("email") or getattr(current_user, "email", None),
            "phone": contact.get("phone") or getattr(current_user, "phone", None),
        }

        # 2. Documents & Links
        documents_links = {
            "linkedin_url": _extract_link(parsed.text, r'https?://(?:www\.)?linkedin\.com/in/[\w\-]+'),
            "github_url": _extract_link(parsed.text, r'https?://(?:www\.)?github\.com/[\w\-]+'),
            "portfolio_url": _extract_link(parsed.text, r'https?://(?:www\.)?(?!linkedin|github)[\w\.\-]+\.[a-z]{2,}(?:/[\w\-]*)*'),
        }

        # 3. Location
        location = _location_from_section(sections.get("location", ""), parsed.text)

        # 4. Work Experience & Summary
        summary = (sections.get("summary", "") or "").strip()
        section_work = _work_experience_from_section(sections.get("experience", ""))
        if section_work:
            work_experience = [
                {
                    "title": w["title"],
                    "company": w["company"],
                    "start_date": w.get("start_date"),
                    "end_date": w.get("end_date"),
                    "currently_working": w.get("currently_working", False),
                    "duration_months": w.get("duration_months"),
                    "confidence": w["confidence"],
                }
                for w in section_work
            ]
        elif sections.get("experience", ""):
            skill_set = {s.lower() for s in (parsed.skills or []) if s}
            work_experience = []
            for w in (parsed.work_experience or []):
                title = (w.get("title") or "").strip()
                company = (w.get("company") or "").strip()
                if not title:
                    continue
                probe = company or title
                if probe.lower() in skill_set or _INSTITUTION_KW.search(probe) or _DEGREE_KW.search(probe):
                    continue
                work_experience.append({
                    "title": title,
                    "company": company,
                    "duration_months": w.get("duration_months"),
                    "confidence": round(float(w.get("confidence", 0.5)), 2),
                })
        else:
            work_experience = []

        from src.services.enhanced_ats_service import enhanced_ats_service
        total_exp = enhanced_ats_service.calculate_work_experience_years_from_entries(work_experience)
        if total_exp == 0.0 and parsed.experience_years and len(work_experience) > 0:
            total_exp = parsed.experience_years

        work_experience_summary = {
            "summary": summary if summary else None,
            "total_experience_years": total_exp,
        }

        # 5. Education
        section_edu = _education_from_section(sections.get("education", ""))
        if section_edu:
            education = [
                {
                    "degree": e["degree"],
                    "institution": e["institution"],
                    "start_year": e.get("start_year"),
                    "end_year": e.get("end_year"),
                    "currently_pursuing": e.get("currently_pursuing", False),
                    "grade": e.get("grade"),
                    "confidence": e["confidence"],
                }
                for e in section_edu
            ]
        elif sections.get("education", ""):
            education = [
                {
                    "degree": (e.get("degree") or "").strip(),
                    "institution": "",
                    # Not e["confidence"]: that rates the degree string, and says
                    # nothing about this row coming from an unanchored scan.
                    "confidence": CONF_SCAN,
                }
                for e in (parsed.education or [])
                if e.get("degree")
            ]
        else:
            education = []

        # 6. Skills
        skills = _skills_from_section(sections.get("skills", ""), parsed.skills or [])

        # 7. Certifications (STRICT: only from certifications section, NO fallback if section missing)
        certifications = _certifications_from_section(sections.get("certifications", ""))

        # 8. Projects — pass section_header for project_type inference (Phase 3)
        _proj_section_text = sections.get("projects", "")
        # detect_sections() drops the header line from the section body, so
        # recover it with the same classifier that drew the boundary. A plain
        # text search would take the first "projects" anywhere in the document
        # -- "led 3 projects" in a work bullet would then type every project.
        _proj_header = "projects"
        for _ln in parsed.text.split("\n"):
            if _is_section_header(_ln) == "projects":
                _proj_header = _ln.strip()
                break
        projects = _projects_from_section(_proj_section_text, section_header=_proj_header)

        # 9. Job Preferences
        job_preferences = _job_preferences_from_section(sections.get("job_preferences", ""))

        # 10. Ask Gemini for the sections the regex extractors could not read —
        # either empty, or filled with demonstrable junk. A section that genuinely
        # parsed is never touched: topping it up would leave the candidate unable to
        # tell which rows came off their own resume. Nothing is written here; they
        # review, edit and tick each item before /import/apply inserts anything.
        draft = {
            "work_experience": work_experience,
            "education": education,
            "certifications": certifications,
            "projects": projects,
        }
        ai_sections = []
        try:
            from src.services.llm_profile_extractor import extract_sections
            filled = await extract_sections(parsed.text, draft)
            for _key, _items in filled.items():
                draft[_key] = _items
                ai_sections.append(_key)
        except Exception as _e:
            # An import or scheduling failure must not cost the candidate their parse.
            logger.warning(f"Resume import: AI section fill failed: {_e}")

        work_experience = draft["work_experience"]
        education = draft["education"]
        certifications = draft["certifications"]
        projects = draft["projects"]

        # Recompute experience so AI-recovered roles count toward the total.
        if "work_experience" in ai_sections:
            total_exp = enhanced_ats_service.calculate_work_experience_years_from_entries(
                work_experience) or total_exp

        return {
            "personal_info": personal_info,
            "documents_links": documents_links,
            "location": location,
            "work_experience_summary": work_experience_summary,
            "education": education,
            "skills": skills,
            "certifications": certifications,
            "projects": projects,
            "job_preferences": job_preferences,

            # Backward-compatibility fields for ResumeImportReview component
            "total_experience_years": total_exp,
            "contact": personal_info,
            "work_experience": work_experience,
            "languages": [l for l in (parsed.languages or []) if l],
            "confidence_score": round(float(parsed.confidence_score), 2),
            # Which sections came from the model rather than the resume's own
            # layout. Section-level because the fill is all-or-nothing, and because
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
            start_date=(w.get("start_date") or None),
            end_date=(w.get("end_date") or None),
            currently_working=bool(w.get("currently_working") or False),
            location=((w.get("location") or "").strip()[:100] or None),
            employment_type=((w.get("employment_type") or "").strip()[:50] or None),
            description=(w.get("description") or None),
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
            field_of_study=((e.get("field_of_study") or "").strip()[:100] or None),
            start_year=_coerce_year(e.get("start_year")),
            end_year=_coerce_year(e.get("end_year")),
            currently_pursuing=bool(e.get("currently_pursuing") or False),
            grade=((e.get("grade") or "").strip()[:50] or None),
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

    # Projects — populate all fields matching the CandidateProject model & Edit Project modal
    for p in data.projects:
        title = (p.get("name") or p.get("title") or "").strip()
        if not title:
            continue
        db.add(CandidateProject(
            profile_id=profile.id,
            project_title=title[:255],
            description=(p.get("description") or None),
            technologies_used=(p.get("technologies_used") or p.get("technologies") or p.get("tech_stack") or None),
            role=(p.get("role") or None),
            project_type=(p.get("project_type") or "Personal")[:50],
            start_date=(p.get("start_date") or None),
            end_date=(p.get("end_date") or None),
            ongoing=bool(p.get("ongoing") or p.get("is_ongoing") or False),
            project_url=(p.get("project_url") or p.get("live_url") or None),
            repository_url=(p.get("repository_url") or p.get("github_url") or None),
        ))
        counts["projects_added"] += 1

    # Scalars — fill only if currently empty (never clobber curated data)
    if data.total_experience_years is not None and not profile.total_experience_years:
        profile.total_experience_years = data.total_experience_years
    if data.headline and not profile.headline:
        profile.headline = data.headline[:120]

    db.commit()
    return {"message": "Profile updated from resume", **counts}
