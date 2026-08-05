"""
RESUME PROFILE PARSER
=====================

Pulls the identity fields the Resume Matcher needs out of raw resume text:
name, contact details, college, year of passing, job role and highest degree.

The existing `enhanced_ats_service` already extracts skills, experience, email/phone
and degree keywords — that is reused here rather than reimplemented. What it does NOT
do is find a candidate's *name*, their *college*, or their *graduation year*, which is
what the heuristics below add.

Design rule: **return None rather than guess.** A blank field prompts HR to fill it in
on the Move-to-ATS modal; a confidently-wrong name or college silently pollutes the
searchable pool and is far harder to notice.

The public surface is a single function so an LLM-backed implementation can be dropped
in later without touching any caller.
"""

import re
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Name heuristics ───────────────────────────────────────────────────────────

# Lines that look like a name but aren't. Compared case-insensitively against the
# whole stripped line.
_NAME_STOPLINES = {
    "resume", "curriculum vitae", "cv", "profile", "personal profile",
    "personal details", "personal information", "contact", "contact details",
    "summary", "professional summary", "career objective", "objective",
    "biodata", "bio data", "about me",
}

# Tokens that disqualify a line from being a name (job titles, section words).
_NAME_BAD_TOKENS = {
    "engineer", "developer", "manager", "analyst", "consultant", "intern",
    "student", "university", "college", "institute", "experience", "education",
    "skills", "projects", "email", "phone", "mobile", "address", "linkedin",
    "github", "portfolio", "www", "http", "https",
}

_NAME_ALLOWED_RE = re.compile(r"^[A-Za-z][A-Za-z.'\-]*$")


# ── College heuristics ────────────────────────────────────────────────────────

_INSTITUTION_WORDS = (
    r"University|College|Institute|Institution|Polytechnic|Academy|"
    r"Vidyalaya|Vidyapeeth|Mahavidyalaya|School of [A-Z][a-z]+"
)

# "Indian Institute of Technology Bombay", "Pune Institute of Computer Technology",
# "R. V. College of Engineering" — up to 5 leading capitalised tokens, plus an
# optional trailing "of <Words>" tail.
_COLLEGE_RE = re.compile(
    r"((?:[A-Z][\w.&'\-]*\s+){0,5}(?:" + _INSTITUTION_WORDS + r")"
    r"(?:\s+of\s+(?:[A-Z][\w.&'\-]*\s*){1,4})?)",
    re.UNICODE,
)

# Legacy shape kept from enhanced_ats_service: "University of Pune".
_COLLEGE_OF_RE = re.compile(
    r"((?:University|College|Institute|Institution)\s+of\s+[A-Za-z][A-Za-z\s]{2,40})",
)

_EDUCATION_HEADERS = re.compile(
    r"^\s*(education|academic|academics|educational\s+background|"
    r"academic\s+(?:background|qualifications?|details)|qualifications?)\s*:?\s*$",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(19[7-9]\d|20\d{2})\b")

# Degree keywords, richest first, so "b.tech" wins over a bare "b".
_DEGREE_PATTERNS = [
    ("PhD", r"\b(?:ph\.?\s?d|doctorate|doctoral)\b"),
    ("MBA", r"\bm\.?\s?b\.?\s?a\b"),
    ("M.Tech", r"\b(?:m\.?\s?tech|master\s+of\s+technology)\b"),
    ("M.E.", r"\b(?:m\.?\s?e\.?|master\s+of\s+engineering)\b"),
    ("M.Sc", r"\b(?:m\.?\s?sc|master\s+of\s+science)\b"),
    ("MCA", r"\bm\.?\s?c\.?\s?a\b"),
    ("M.Com", r"\bm\.?\s?com\b"),
    ("MA", r"\b(?:m\.?\s?a\.?|master\s+of\s+arts)\b"),
    ("Master", r"\bmaster'?s?\b"),
    ("B.Tech", r"\b(?:b\.?\s?tech|bachelor\s+of\s+technology)\b"),
    ("B.E.", r"\b(?:b\.?\s?e\.?|bachelor\s+of\s+engineering)\b"),
    ("B.Sc", r"\b(?:b\.?\s?sc|bachelor\s+of\s+science)\b"),
    ("BCA", r"\bb\.?\s?c\.?\s?a\b"),
    ("B.Com", r"\bb\.?\s?com\b"),
    ("BBA", r"\bb\.?\s?b\.?\s?a\b"),
    ("BA", r"\b(?:b\.?\s?a\.?|bachelor\s+of\s+arts)\b"),
    ("Bachelor", r"\bbachelor'?s?\b"),
    ("Diploma", r"\bdiploma\b"),
]

# Ranked so "highest degree" means something. Higher wins.
_DEGREE_RANK = {
    "Diploma": 1,
    "BA": 2, "BBA": 2, "B.Com": 2, "BCA": 2, "B.Sc": 2, "B.E.": 2, "B.Tech": 2, "Bachelor": 2,
    "MA": 3, "M.Com": 3, "MCA": 3, "M.Sc": 3, "M.E.": 3, "M.Tech": 3, "MBA": 3, "Master": 3,
    "PhD": 4,
}

# Role lines like "Objective: seeking a Backend Developer role".
_ROLE_HINT_RE = re.compile(
    r"(?:seeking|applying\s+for|position\s+of|role\s+of|objective\s*:?)\s+"
    r"(?:an?\s+)?([A-Za-z][A-Za-z /&+\-]{3,45}?)"
    r"(?:\s+(?:role|position|at|with|in)\b|[.,;\n]|$)",
    re.IGNORECASE,
)

_ROLE_KEYWORDS = (
    "engineer", "developer", "designer", "analyst", "manager", "consultant",
    "architect", "administrator", "scientist", "specialist", "lead", "intern",
    "tester", "recruiter", "executive", "officer", "associate", "accountant",
)

# "Objective:" and "Seeking a …" can both introduce the same phrase, so the hint
# regex may capture the verb along with the role. Strip it back off.
_ROLE_PREFIX_RE = re.compile(
    r"^(?:seeking|looking\s+for|applying\s+for|aspiring|to\s+work\s+as|"
    r"worked\s+as|working\s+as|employed\s+as|position\s+of|role\s+of)\s+"
    r"(?:an?\s+|the\s+)?",
    re.IGNORECASE,
)

# The shared ATS phone regex stops after ~7 digits, which loses spaced formats like
# "+91 98765 43210". This one takes a full run of digits and separators, then the
# digit count is validated by the caller.
_PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[\s.\-]?)?(?:\(\d{2,4}\)[\s.\-]?)?\d(?:[\s.\-]?\d){6,14}"
)

# "2019-2024" and "2019 – 2024" have as many digits as a phone number. Employment
# and education date ranges are everywhere in a resume, so reject them explicitly.
_YEAR_RANGE_RE = re.compile(r"^(?:19|20)\d{2}\s*[-–—/]\s*(?:19|20)\d{2}$")


def _clean_lines(text: str) -> List[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _looks_like_name(line: str) -> bool:
    """A name is 2-4 alphabetic tokens, mostly capitalised, with no contact noise."""
    stripped = line.strip().strip("|-–—•*")
    if not stripped or len(stripped) > 60:
        return False
    if stripped.lower() in _NAME_STOPLINES:
        return False
    if any(ch.isdigit() for ch in stripped) or "@" in stripped:
        return False

    tokens = stripped.split()
    if not 2 <= len(tokens) <= 4:
        return False
    if any(t.lower().strip(".,") in _NAME_BAD_TOKENS for t in tokens):
        return False
    if not all(_NAME_ALLOWED_RE.match(t) for t in tokens):
        return False

    # Require capitalisation on every token — "john doe" from a body sentence is
    # rejected, "JOHN DOE" and "John Doe" are accepted.
    return all(t[0].isupper() for t in tokens)


def extract_full_name(text: str) -> Optional[str]:
    """Names live in the resume header — scan only the first handful of lines."""
    for line in _clean_lines(text)[:8]:
        if _looks_like_name(line):
            name = line.strip().strip("|-–—•*").strip()
            # Normalise ALL-CAPS headers to title case; leave mixed case alone.
            return name.title() if name.isupper() else name
    return None


def _education_window(lines: List[str]) -> List[str]:
    """Lines belonging to the education section, or all lines when there's no header."""
    start = None
    for i, line in enumerate(lines):
        if _EDUCATION_HEADERS.match(line):
            start = i + 1
            break
    if start is None:
        return lines
    # Run until the next ALL-CAPS/short header-looking line, capped at 25 lines.
    window = []
    for line in lines[start:start + 25]:
        if _EDUCATION_HEADERS.match(line):
            break
        window.append(line)
    return window or lines


def extract_college(text: str) -> Optional[str]:
    """Longest institution-looking phrase, preferring the education section."""
    lines = _clean_lines(text)
    for scope in (_education_window(lines), lines):
        candidates: List[str] = []
        for line in scope:
            candidates.extend(m.strip(" ,.-") for m in _COLLEGE_OF_RE.findall(line))
            candidates.extend(m.strip(" ,.-") for m in _COLLEGE_RE.findall(line))
        candidates = [c for c in (re.sub(r"\s+", " ", c) for c in candidates) if 6 <= len(c) <= 120]
        if candidates:
            # Longest match is the most specific ("Indian Institute of Technology"
            # beats a bare "Institute").
            return max(candidates, key=len)
    return None


def extract_year_of_passing(text: str, college: Optional[str] = None) -> Optional[int]:
    """Latest plausible graduation year, preferring years near the college mention."""
    lines = _clean_lines(text)
    max_year = datetime.utcnow().year + 6   # allow in-progress degrees
    min_year = 1970

    def _years_in(chunk: str) -> List[int]:
        return [int(y) for y in _YEAR_RE.findall(chunk) if min_year <= int(y) <= max_year]

    # Pass 1 — years within ±2 lines of the college name.
    if college:
        needle = college.lower()
        for i, line in enumerate(lines):
            if needle in line.lower():
                window = " ".join(lines[max(0, i - 2):i + 3])
                years = _years_in(window)
                if years:
                    return max(years)

    # Pass 2 — years anywhere in the education section.
    years = _years_in(" ".join(_education_window(lines)))
    return max(years) if years else None


def extract_highest_degree(text: str) -> Optional[str]:
    """Highest-ranked degree keyword present in the text."""
    lowered = text.lower()
    found = [label for label, pattern in _DEGREE_PATTERNS if re.search(pattern, lowered)]
    if not found:
        return None
    return max(found, key=lambda label: (_DEGREE_RANK.get(label, 0), -found.index(label)))


def extract_phone(text: str) -> Optional[str]:
    """First plausible phone number, skipping date ranges and other numeric noise."""
    for match in _PHONE_RE.finditer(text):
        raw = re.sub(r"\s+", " ", match.group().strip())
        if _YEAR_RANGE_RE.match(raw):
            continue
        digits = re.sub(r"\D", "", raw)
        if 8 <= len(digits) <= 15:
            return raw
    return None


def _role_from_work_experience(text: str) -> Optional[str]:
    """Most recent job title, via the ATS service's existing experience extractor."""
    try:
        from src.services.enhanced_ats_service import enhanced_ats_service
        entries = enhanced_ats_service.extract_work_experience(text) or []
    except Exception as e:                       # extractor is best-effort
        logger.debug(f"work-experience extraction unavailable: {e}")
        return None

    for entry in entries:                        # entries come newest-first
        title = (entry.get("title") or "").strip()
        if 3 <= len(title) <= 60 and any(k in title.lower() for k in _ROLE_KEYWORDS):
            return re.sub(r"\s+", " ", title)
    return None


def extract_job_role(text: str) -> Optional[str]:
    """Target role: the latest held job title, else an objective/summary hint."""
    role = _role_from_work_experience(text)
    if role:
        return role

    match = _ROLE_HINT_RE.search(text)
    if match:
        candidate = re.sub(r"\s+", " ", match.group(1)).strip(" .,-")
        candidate = _ROLE_PREFIX_RE.sub("", candidate).strip(" .,-")
        if 3 <= len(candidate) <= 45 and any(k in candidate.lower() for k in _ROLE_KEYWORDS):
            return candidate

    # Experience lines read "Senior Data Analyst at Acme Corp, 2019-2024" or
    # "Software Engineer, Infosys (2021 - Present)" — take the part before the
    # company separator.
    for line in _clean_lines(text):
        head = re.split(r"\s+(?:at|@|\||,|-|–)\s+|,", line, maxsplit=1)[0].strip(" .,-|")
        head = _ROLE_PREFIX_RE.sub("", head).strip(" .,-|")
        if 3 <= len(head) <= 45 and any(k in head.lower() for k in _ROLE_KEYWORDS):
            if not any(ch.isdigit() for ch in head) and "@" not in head:
                return re.sub(r"\s+", " ", head)
    return None


def parse_resume_profile(text: str) -> Dict[str, Any]:
    """Extract the identity fields the resume pool stores.

    Returns a dict with keys: full_name, email, phone, college, year_of_passing,
    job_role, highest_degree. Any field the heuristics cannot determine is None —
    HR fills it in when promoting the resume to a candidate.
    """
    empty: Dict[str, Any] = {
        "full_name": None, "email": None, "phone": None, "college": None,
        "year_of_passing": None, "job_role": None, "highest_degree": None,
    }
    if not text or not text.strip():
        return empty

    profile = dict(empty)

    # Email already has a working extractor — reuse it. Phone uses the local regex
    # because the shared one truncates spaced formats ("+91 98765 43210" → "+91 98765").
    try:
        from src.services.enhanced_ats_service import enhanced_ats_service
        profile["email"] = (enhanced_ats_service.extract_contact_info(text) or {}).get("email")
    except Exception as e:
        logger.debug(f"email extraction failed: {e}")

    try:
        profile["phone"] = extract_phone(text)
    except Exception as e:
        logger.debug(f"phone extraction failed: {e}")

    # Each heuristic is independent — one failing must not blank the rest.
    for key, fn in (
        ("full_name", extract_full_name),
        ("college", extract_college),
        ("highest_degree", extract_highest_degree),
        ("job_role", extract_job_role),
    ):
        try:
            profile[key] = fn(text)
        except Exception as e:
            logger.debug(f"{key} extraction failed: {e}")

    try:
        profile["year_of_passing"] = extract_year_of_passing(text, profile["college"])
    except Exception as e:
        logger.debug(f"year_of_passing extraction failed: {e}")

    return profile
