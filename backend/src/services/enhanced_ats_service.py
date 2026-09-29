"""
🔍 ENHANCED ATS SERVICE MODULE
Advanced resume processing with OCR support, enhanced parsing, and detailed candidate analysis
"""

import os
import re
import json
import logging
import pathlib
import unicodedata
import hashlib
import functools
from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from datetime import datetime
import pdfplumber
import numpy as np
from pydantic import BaseModel

from src.services.institution_tiers import best_tier
from src.services.pincode_region import is_rural
from src.services.jd_overlap import jd_copy_overlap
from src.services.ats_evidence import collect_evidence
from src.services.ats_text import contains_whole_word

# Resume<->JD verbatim-copy handling. Default is flag-only (no score change);
# set > 0 to subtract points from flagged candidates.
JD_COPY_PENALTY = 0.0

# ==================== MODULE-LEVEL AI/NLP SETUP ====================

# --- Data paths ---
_DATA_DIR = pathlib.Path(__file__).parent.parent / "data"

# Run scripts/enrich_skill_data.py to expand skill coverage before deploying

# --- C4.1: Skill alias map (loaded once at import) ---
ALIAS_MAP: Dict[str, str] = json.loads((_DATA_DIR / "skill_aliases.json").read_text(encoding="utf-8"))

def normalize_skill(token: str) -> str:
    """Return canonical skill name for a token, or the token itself if unknown."""
    return ALIAS_MAP.get(token.lower().strip(), token.strip())

# --- C4.3: Canonical skills set (canonical names + all alias keys for variant matching) ---
CANONICAL_SKILLS_SET: set = (
    set(s.lower() for s in json.loads((_DATA_DIR / "canonical_skills.json").read_text(encoding="utf-8")))
    | set(ALIAS_MAP.keys())  # includes "lang chain", "llm", "lang graph", etc.
)

# --- Skill clustering — partial credit when a related tech is found ---
SKILL_CLUSTERS: Dict[str, List[str]] = json.loads((_DATA_DIR / "skill_clusters.json").read_text(encoding="utf-8"))
# Reverse map: lowercase canonical name → cluster name
SKILL_TO_CLUSTER: Dict[str, str] = {
    member.lower(): cluster_name
    for cluster_name, members in SKILL_CLUSTERS.items()
    for member in members
}
CLUSTER_PARTIAL_CREDIT = 0.6  # 60% of full credit for a same-category skill

# Identifies the scoring RULES that produced a result: weights, sub-score formulas,
# evidence handling. Stored with every ATS result so a score can be traced to the rules
# behind it, and results scored under older rules are shown as such. Bump it whenever a
# change would move a score.
#   2026.10-v1  the 7-factor recruiter rubric as it stood before explainability work
#   2026.10-v2  mandatory vs preferred: explicit preferred skills used as given (an empty
#               list means none); inferred ones exclude mandatory skills; "meets X of Y"
#   2026.10-v3  evidence: skills credited by where they are shown (experience/projects >
#               skills list > related > not found) on the full text; unknown education /
#               experience parts excluded rather than scored 0; no credit for bare words
#   2026.10-v4  weights from DEFAULT_ATS_WEIGHTS (experience 20, project 8, semantic 1,
#               keyword 1 — was 15/5/5/5), merged in with the per-factor excerpts
SCORING_RULES_VERSION = "2026.10-v4"

SENTENCE_TRANSFORMERS_AVAILABLE = False
_SEMANTIC_MODEL = None
st_util = None
_SEMANTIC_LOAD_ATTEMPTED = False
_CLUSTER_EMBEDDINGS: Dict[str, Any] = {}

def _get_semantic_model():
    global _SEMANTIC_MODEL, _SEMANTIC_LOAD_ATTEMPTED, SENTENCE_TRANSFORMERS_AVAILABLE, st_util
    if not _SEMANTIC_LOAD_ATTEMPTED:
        _SEMANTIC_LOAD_ATTEMPTED = True
        try:
            from sentence_transformers import SentenceTransformer, util as st_util
            _SEMANTIC_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            SENTENCE_TRANSFORMERS_AVAILABLE = True
        except Exception as _st_err:
            logging.warning("Semantic model unavailable (%s). Falling back to TF-IDF.", _st_err)
            _SEMANTIC_MODEL = None
            st_util = None
            SENTENCE_TRANSFORMERS_AVAILABLE = False
    return _SEMANTIC_MODEL

# Embedding cache, keyed on SHA-256 of the text. Genuinely LRU: an earlier version
# evicted by insertion order (FIFO), which is the wrong policy here. Scoring one resume
# against N jobs re-reads the same job-description embeddings while inserting a fresh
# batch of resume-section embeddings, so FIFO evicted exactly the entries being reused —
# a 20-resume batch (~8 sections each) overflowed 128 mid-batch and re-encoded the JDs.
_EMBEDDING_CACHE: "OrderedDict[str, Any]" = OrderedDict()
_EMBEDDING_CACHE_MAXSIZE = max(16, int(os.getenv("ATS_EMBEDDING_CACHE_SIZE", "512")))


def _get_text_embedding(text: str) -> Any:
    """
    Get embedding for a text string with SHA-256 based LRU caching.

    Caches up to _EMBEDDING_CACHE_MAXSIZE embeddings keyed on the SHA-256
    hash of the input text. Cache hits avoid the 200-800ms encode() call, and
    move the entry to the most-recently-used end so a burst of one-off inserts
    cannot evict a hot entry.

    Args:
        text: Input text to embed.

    Returns:
        Embedding tensor/array from SentenceTransformer, or None if unavailable.
    """
    model = _get_semantic_model()
    if model is None:
        return None
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    if text_hash in _EMBEDDING_CACHE:
        logger.debug("JD embedding cache hit: %s", text_hash[:8])
        _EMBEDDING_CACHE.move_to_end(text_hash)
        return _EMBEDDING_CACHE[text_hash]
    embedding = model.encode(text, convert_to_tensor=True)
    # Evict least-recently-used if at capacity.
    if len(_EMBEDDING_CACHE) >= _EMBEDDING_CACHE_MAXSIZE:
        _EMBEDDING_CACHE.popitem(last=False)
    _EMBEDDING_CACHE[text_hash] = embedding
    return embedding

# --- C4.3: spaCy NER (lazy load — transformer model is large) ---
_NLP = None

def _get_nlp():
    """Lazy-load spaCy model instantly without network timeouts."""
    global _NLP
    if _NLP is None:
        try:
            import importlib.util
            import spacy
            if importlib.util.find_spec("en_core_web_trf") is not None:
                _NLP = spacy.load("en_core_web_trf")
            elif importlib.util.find_spec("en_core_web_sm") is not None:
                _NLP = spacy.load("en_core_web_sm")
            else:
                _NLP = False
        except Exception as e:
            logging.warning(f"spaCy model not available: {e}. NER features disabled.")
            _NLP = False
    return _NLP if _NLP is not False else None

# --- C4.4: Fuzzy matching ---
try:
    from rapidfuzz import fuzz as _fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logging.warning("rapidfuzz not installed. Falling back to exact keyword matching.")

FUZZY_THRESHOLD = 75  # rapidfuzz scores are 0-100

# --- C4.3: Degree scoring ---
DEGREE_SCORES = {
    # Doctoral
    "phd": 100, "ph.d": 100, "ph.d.": 100, "doctorate": 100, "doctoral": 100,
    "d.phil": 100, "doctor of philosophy": 100,
    # Masters
    "master": 80, "masters": 80, "m.s": 80, "m.s.": 80, "m.sc": 80, "m.sc.": 80,
    "mba": 80, "m.b.a": 80, "m.tech": 80, "m.tech.": 80, "m.e": 80, "m.e.": 80,
    "mca": 80, "m.c.a": 80, "msc": 80, "m.eng": 80, "master of science": 80,
    "master of technology": 80, "master of engineering": 80,
    # Bachelors
    "bachelor": 60, "bachelors": 60, "b.s": 60, "b.s.": 60, "b.sc": 60, "b.sc.": 60,
    "b.tech": 60, "b.tech.": 60, "btech": 60, "b tech": 60,
    "b.e": 60, "b.e.": 60, "be": 60, "b.eng": 60, "b.eng.": 60,
    "bca": 60, "b.c.a": 60, "bsc": 60, "b.com": 60,
    "bachelor of science": 60, "bachelor of technology": 60,
    "bachelor of engineering": 60, "bachelor of arts": 55,
    "honours": 60, "hons": 60,
    # Associate
    "associate": 40, "a.a": 40, "a.s": 40, "a.a.s": 40,
    # Below degree
    "diploma": 30, "advanced diploma": 35, "higher national diploma": 35,
    "certificate": 20, "certification": 20,
}

# Domain validation sets for strict category anti-misclassification
SPOKEN_LANGUAGES_SET: set = {
    'english', 'spanish', 'french', 'german', 'chinese', 'japanese',
    'korean', 'russian', 'arabic', 'portuguese', 'italian', 'hindi',
    'tamil', 'telugu', 'bengali', 'marathi', 'gujarati', 'punjabi', 'kannada', 'malayalam'
}

SOFT_SKILLS_SET: set = {
    'communication', 'teamwork', 'leadership', 'problem solving', 'critical thinking',
    'time management', 'adaptability', 'decision making', 'negotiation', 'emotional intelligence',
    'creativity', 'interpersonal skills', 'conflict resolution', 'active listening',
    'work ethic', 'attention to detail', 'analytical thinking', 'collaboration', 'organization', 'multitasking'
}

DEGREE_TITLES_SET: set = {
    'b.tech', 'btech', 'b.e', 'be', 'b.sc', 'bsc', 'bachelor', 'bachelors',
    'm.tech', 'mtech', 'm.e', 'me', 'm.sc', 'msc', 'master', 'masters',
    'phd', 'ph.d', 'doctorate', 'diploma', 'bca', 'mca', 'b.com', 'm.com'
}

# --- C4.6: Section detection ---
SECTION_HEADERS: Dict[str, List[str]] = {
    "skills": [
        "skills", "technical skills", "core skills", "key skills", "competencies", "tech stack", "technical stack",
        "technologies", "tools", "expertise", "proficiencies", "competencies", "skill set",
        "programming languages", "my technical skills", "technical expertise", "languages & frameworks",
        "it skills", "domain skills", "areas of expertise", "core competencies", "technical proficiencies",
        "skills & abilities", "technical toolkit", "platforms & tools", "programming",
        "core technical skills", "core technical skill", "technical skills & expertise", "key technical skills",
        "technical proficiency", "skills & tools", "tools & technologies",
    ],
    "experience": [
        "experience", "work experience", "employment", "professional experience", "work history", "career",
        "professional background", "relevant experience", "employment history",
        "job history", "work background", "professional journey", "career history",
        "career summary", "work summary", "work profile", "professional work experience",
        "industry experience", "experience summary",
    ],
    "education": [
        "education", "academic", "qualifications", "degrees", "studies",
        "educational background", "academic background", "academic qualifications",
        "academic history", "schooling", "education & training", "scholastic background",
        "academic details", "academic record", "education summary",
    ],
    "projects": [
        "projects", "personal projects", "open source", "portfolio", "side projects",
        "key projects", "notable projects", "project experience",
        "selected projects", "academic projects", "professional projects", "major projects",
        "technical projects", "software projects",
    ],
    "certifications": [
        "certifications", "certificates", "licenses", "accreditations",
        "professional certifications", "credentials", "awards & certifications",
        "certifications & licenses", "courses & certifications",
        "training & certifications", "licenses & certifications", "professional courses",
        "certifications / licenses", "certificates & licenses", "online certifications",
        "professional credentials",
    ],
    "summary": [
        "summary", "profile", "objective", "about me", "professional summary",
        "career objective", "professional profile", "executive summary",
        "personal statement", "overview", "profile summary", "summary of qualifications",
        "career overview", "personal summary",
    ],
    "hobbies": [
        "hobbies", "interests", "activities", "extracurricular",
        "personal interests", "outside interests", "volunteer work", "hobbies & interests",
        "extracurricular activities", "extra curricular activities", "extra-curricular activities",
        "achievements", "awards", "declaration", "publications", "honors & awards",
    ],
    "job_preferences": [
        "job preferences", "career preferences", "preferred location", "target role",
        "expected salary", "availability", "preferences", "career goals",
    ],
}

# Where a skill appears says how well it is evidenced: used in a job or project beats a
# line in a skills list. (This used to rank the skills list highest.)
SECTION_SKILL_WEIGHTS: Dict[str, float] = {
    "experience": 1.5,
    "projects": 1.3,
    "skills": 1.0,
    "certifications": 1.0,
    "education": 0.8,
    "summary": 0.7,
    "hobbies": 0.3,
    "other": 0.5,
}

# How much a required skill is worth depending on where the resume evidences it.
# A skill named inside a job or a project was *demonstrated*; the same skill in a
# comma-separated list was *claimed*. Note the ordering is the reverse of
# SECTION_SKILL_WEIGHTS above, which ranks a bare skills list highest — that
# table belongs to compute_weighted_skills_score, which the live scorer has
# never called.
#
# Every value is <= 1.0 on purpose: this is a discount on claiming, never a
# bonus for demonstrating. No candidate's skills sub-score can rise above what
# the old flat match gave them, which keeps the blast radius of a re-score
# bounded and checkable against production data.
SKILL_EVIDENCE_WEIGHTS: Dict[str, float] = {
    "experience": 1.00,      # proven in a job
    "projects": 1.00,        # proven in a project
    "certifications": 0.90,  # externally examined, but not applied work
    "skills": 0.80,          # claimed in a list
    "other": 0.80,           # headline / pre-header preamble — also a claim
    "summary": 0.75,
    "education": 0.70,
    "job_preferences": 0.40,
    "hobbies": 0.40,
}

# Applied when a skill is credited but cannot be located in any section — a
# spelling the extractor resolved but the text does not contain verbatim
# ("Dockerised" -> docker). Treated as claimed rather than demonstrated: if we
# cannot point at the work, we do not pay for it.
UNLOCATABLE_EVIDENCE_WEIGHT = SKILL_EVIDENCE_WEIGHTS["skills"]

# Evidence weighting needs at least one of these to be meaningful. Without them
# the scorer falls back to the flat match rather than penalising a resume for
# using section headings it does not recognise.
EVIDENCE_CAPABLE_SECTIONS = frozenset({"experience", "projects", "skills"})

# Bumped whenever the rubric changes in a way that makes scores incomparable
# with older ones. Stored in scoring_breakdown so a partially re-scored table
# stays readable and the backfill can tell done from pending.
#   1 — education-weighted 7-factor rubric (education .35 / skills .25 /
#       experience .15 / certification .10 / semantic .05 / project .05 /
#       keyword .05), skills matched flat over the whole document.
#   2 — experience and projects promoted over keyword placement; skills credit
#       scaled by the section evidencing each skill.
ATS_RUBRIC_VERSION = 2

# Factor weights, summing to 1.0. Single source of truth — tests and docs read
# this rather than restating the numbers.
#
# Rationale for version 2: "keyword" is literally *where a word appears* and is
# cut hardest; "semantic" is the other whole-document word-occurrence factor and
# is trimmed to match. Both fund experience and projects, which together move
# from 20% to 28%. Education is held at 35% as an explicit recruiter policy
# decision, not an oversight. skills_match holds its weight because its
# *internals* became evidence-sensitive in this same change — moving the weight
# as well would make the before/after impossible to attribute.
DEFAULT_ATS_WEIGHTS: Dict[str, float] = {
    'education': 0.35,     # degree match + college tier + CGPA
    'skills_match': 0.25,  # required + preferred, discounted by evidence
    'experience': 0.20,    # relevant exp + internships + github/OSS/hackathons
    'certification': 0.10,
    'project': 0.08,
    'semantic': 0.01,
    'keyword': 0.01,
}

# A rubric that does not sum to 1.0 silently rescales every score, so fail at
# import rather than at the first candidate.
assert round(sum(DEFAULT_ATS_WEIGHTS.values()), 6) == 1.0, (
    f"ATS weights must sum to 1.0, got {sum(DEFAULT_ATS_WEIGHTS.values())}"
)

# Enhanced imports for OCR and DOC processing
try:
    import pytesseract
    from PIL import Image
    import io
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logging.warning("OCR not available. Install pytesseract and Pillow for enhanced scanning.")

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    logging.warning("DOCX processing not available. Install python-docx for document support.")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== ENHANCED PYDANTIC MODELS ====================

class EnhancedResumeParseResult(BaseModel):
    """Enhanced resume parsing result with OCR support"""
    text: str
    skills: List[str]
    experience_years: float
    contact_info: Dict[str, str]
    education: List[Dict[str, Any]]
    work_experience: List[Dict[str, Any]]
    certifications: List[str]
    languages: List[str]
    projects: List[Dict[str, Any]]
    confidence_score: float
    file_type: str
    processing_method: str

@dataclass
class PreparedResume:
    """Everything derivable from a resume alone, computed once.

    The first five steps of ``enhanced_score_candidate`` never read the job
    description, yet scoring one resume against N jobs repeated all of them N times —
    including the spaCy transformer pass inside ``enhanced_parse_resume_from_text``.
    A caller with many jobs builds this once and passes it in, so that work happens
    once per resume instead of once per (resume, job) pair.

    ``text`` is the PROCESSED resume (sanitised, cleaned, normalised), because the
    scorer keeps using it after parsing — for keyword match, education, experience and
    JD-overlap. Handing back the caller's raw text here would change scores.
    """
    text: str
    sections: Dict[str, str]
    parsed: "EnhancedResumeParseResult"


class EnhancedATSScoreResult(BaseModel):
    """Enhanced ATS scoring result with detailed breakdown"""
    ats_score: float
    skills_match_score: float
    semantic_score: float
    experience_score: float
    keyword_score: float
    education_score: float
    certification_score: float
    project_score: float
    matched_skills: List[str]
    missing_skills: List[str]
    cluster_matched_skills: List[Dict[str, str]] = []
    extracted_skills: List[str]
    extracted_experience: float
    education_level: str
    certifications: List[str]
    confidence_score: float
    scoring_breakdown: Dict[str, Any]
    recommendations: List[str]
    strengths: List[str]
    weaknesses: List[str]
    rating: str = ""
    hiring_recommendation: str = ""
    # Mandatory requirements (the job's required_skills): shown to HR as "meets X of Y",
    # never grounds for automatic rejection.
    mandatory_met: Optional[int] = None
    mandatory_total: Optional[int] = None
    missing_mandatory: List[str] = []
    # Where the nice-to-haves came from: explicit (HR listed them), inferred (read from the
    # JD text), or none.
    preferred_source: Optional[str] = None
    # Share of the scoring weight that rests on evidence found in the resume (0-1). Low
    # coverage means most of the score could not be assessed; the pipeline sends it to a
    # person rather than ranking it.
    evidence_coverage: Optional[float] = None
    # Everything behind the score, with resume excerpts (see src/services/ats_evidence.py).
    explanation: Dict[str, Any] = {}

# ==================== MODULE-LEVEL HELPER FUNCTIONS ====================

def compute_semantic_score(text_a: str, text_b: str) -> float:
    """
    C4.2 — Compute semantic similarity using Sentence Transformers (all-MiniLM-L6-v2).
    Falls back to TF-IDF cosine similarity if sentence-transformers is unavailable.
    Uses cached embeddings via _get_text_embedding() for performance.
    Returns score in 0-100 range.
    """
    if not text_a or not text_b:
        return 0.0
    if SENTENCE_TRANSFORMERS_AVAILABLE:
        try:
            # FIXED: No embedding cache — use cached embeddings
            emb_a = _get_text_embedding(text_a)
            emb_b = _get_text_embedding(text_b)
            if emb_a is not None and emb_b is not None:
                sim = float(st_util.cos_sim(emb_a, emb_b))
                return round(max(0.0, sim) * 100, 2)
        except Exception as e:
            logger.warning(f"Sentence transformer scoring failed: {e}. Using TF-IDF fallback.")
    # TF-IDF fallback
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        vect = TfidfVectorizer(stop_words="english", max_features=500)
        tfidf = vect.fit_transform([text_a, text_b])
        sim = cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0]
        return round(float(sim) * 100, 2)
    except Exception:
        return 50.0


def compute_weighted_semantic_score(sections: Dict[str, str], jd_text: str) -> float:
    """
    C4.2 — Section-aware semantic score. Weights each section's similarity by importance.
    """
    if not sections or not jd_text:
        return 0.0
    total_weight = 0.0
    weighted_sum = 0.0
    section_semantic_weights = {
        "experience": 1.5, "skills": 1.3, "projects": 1.1,
        "summary": 0.9, "education": 0.7, "certifications": 0.8,
        "hobbies": 0.2, "other": 0.5,
    }
    for section_name, section_text in sections.items():
        if not section_text.strip():
            continue
        weight = section_semantic_weights.get(section_name, 0.5)
        score = compute_semantic_score(section_text, jd_text)
        weighted_sum += score * weight
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return round(weighted_sum / total_weight, 2)



def extract_skills_from_tokens(text: str) -> List[str]:
    """
    C4.3 — Extract skills using spaCy tokens/noun-chunks matched against canonical skills set.
    Falls back to empty list if spaCy unavailable.
    """
    nlp = _get_nlp()
    if nlp is None or not text:
        return []
    found = set()
    try:
        doc = nlp(text[:10000])
        for token in doc:
            t = token.text.lower().strip()
            # Skip single-char tokens — they cause false positives (e.g. "r" in "architecture")
            if len(t) >= 2 and t in CANONICAL_SKILLS_SET:
                found.add(normalize_skill(t))
        for chunk in doc.noun_chunks:
            c = chunk.text.lower().strip()
            if len(c) >= 2 and c in CANONICAL_SKILLS_SET:
                found.add(normalize_skill(c))
    except Exception as e:
        logger.warning(f"spaCy skill extraction failed: {e}")
    return list(found)


_HEADER_CLEAN_RE = re.compile(r'^[#*\-•·▪\d\.\s\[\]\(\)]+|[\s\:\-\|]+$')


def _is_section_header(line: str) -> Optional[str]:
    """
    Determine if a line is a section header line.
    Requires line length <= 55 characters and word count <= 8 to prevent prose/sentences from matching.
    Returns matched section_name or None.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 55:
        return None

    clean_line = _HEADER_CLEAN_RE.sub('', stripped).strip().lower()
    if not clean_line or len(clean_line.split()) > 8:
        return None

    for section_name, headers in SECTION_HEADERS.items():
        for h in headers:
            if clean_line == h:
                return section_name
            if clean_line.startswith(h + ":") or clean_line.startswith(h + " -") or clean_line.startswith(h + " |"):
                return section_name
    return None


def _inline_section_header(line: str) -> Optional[Tuple[str, str]]:
    """A heading written on the same line as its content: "EXPERIENCE 2018-2024 Manager".

    Only an ALL-CAPS heading counts, so prose such as "Experience with Python" is not
    mistaken for one. Returns (section_name, rest_of_line) or None. Without this, a
    resume laid out this way had every line filed under "other", so experience dates,
    certifications and skills used in real work were all missed.
    """
    for section_name, headers in SECTION_HEADERS.items():
        for h in sorted(headers, key=len, reverse=True):
            head, rest = line[:len(h)], line[len(h):]
            content = rest.strip(" :\t-|")
            if head == h.upper() and rest[:1] in (" ", ":", "\t", "-", "|") and content:
                return section_name, content
    return None


def detect_sections(text: str) -> Dict[str, str]:
    """
    Split resume text into named sections by scanning for section header lines.
    Returns dict mapping section_name -> section_text.
    """
    lines = text.split("\n")
    sections: Dict[str, str] = {}
    current_section = "other"
    buffer: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            buffer.append("")
            continue

        matched_section = _is_section_header(line)
        inline = None if matched_section else _inline_section_header(stripped)
        if matched_section or inline:
            if buffer:
                sections[current_section] = sections.get(current_section, "") + "\n".join(buffer) + "\n"
            current_section = matched_section or inline[0]
            buffer = [inline[1]] if inline else []
        else:
            buffer.append(stripped)

    if buffer:
        sections[current_section] = sections.get(current_section, "") + "\n".join(buffer)

    return sections


# Typography ligature map — common PDF rendering artifacts
_LIGATURES: Dict[str, str] = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
}
_LIGATURE_RE = re.compile("|".join(re.escape(k) for k in _LIGATURES))

# Bullet/list-marker characters to normalize to a hyphen
_BULLET_RE = re.compile(r"^[\u2022\u25aa\u25a6\u25e6\u2023\u2192\u2713\u2714\u2605\u2606\u00b7]+\s*", re.MULTILINE)

# Hyphenated line-break: "Py-\nthon" → "Python"
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\n(\w)")


def clean_resume_text(text: str) -> str:
    """
    Normalise raw PDF/DOCX text before NLP processing.
    Fixes: Unicode normalisation, PDF ligatures, hyphenated line-breaks,
    bullet-point markers, and excessive whitespace.
    """
    if not text:
        return text
    # 1. Unicode NFKC — decomposes compatibility characters (half-width, fullwidth, etc.)
    text = unicodedata.normalize("NFKC", text)
    # 2. Replace typographic ligatures (ﬁ → fi, ﬂ → fl, etc.)
    text = _LIGATURE_RE.sub(lambda m: _LIGATURES[m.group(0)], text)
    # 3. Repair hyphenated line breaks ("Py-\nthon" → "Python")
    text = _HYPHEN_BREAK_RE.sub(r"\1\2", text)
    # 4. Normalise bullet markers to a simple hyphen
    text = _BULLET_RE.sub("- ", text)
    # 5. Collapse multiple blank lines to one
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 6. Strip leading/trailing whitespace per line
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


# FIXED: No input sanitization (prompt injection risk)
_PROMPT_INJECTION_LINE_RE = re.compile(
    r'^\s*(?:ignore|disregard|system:|assistant:|\[INST\]|<s>|</s>|<<SYS>>|<\|)',
    re.IGNORECASE,
)
_BRACKET_INSTRUCTION_RE = re.compile(
    r'[\[{<]\s*(?:instruction|system|prompt|ignore|override|hack)[^}\]>]*[\]}>]',
    re.IGNORECASE,
)
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')

_SANITIZE_MAX_CHARS = 8000


def sanitize_resume_text(text: str) -> str:
    """
    Sanitize resume text to mitigate prompt injection risks.

    Applies the following cleaning steps:
    1. Strips all control characters except newline (\\n) and tab (\\t).
    2. Removes lines that begin with known prompt-injection prefixes
       (Ignore, Disregard, System:, Assistant:, [INST], <s>, </s>, etc.).
    3. Removes bracket-wrapped instruction patterns like [instruction],
       {system override}, etc.
    4. Truncates to 8000 characters maximum.

    Never raises an exception — silently cleans and continues.

    Args:
        text: Raw resume text.

    Returns:
        Sanitized text safe for LLM consumption.
    """
    if not text:
        return text
    try:
        # 1. Strip control characters (keep \n=0x0a and \t=0x09)
        text = _CONTROL_CHAR_RE.sub('', text)

        # 2. Remove prompt-injection lines
        lines = text.split('\n')
        clean_lines = [
            line for line in lines
            if not _PROMPT_INJECTION_LINE_RE.match(line)
        ]
        text = '\n'.join(clean_lines)

        # 3. Remove bracket-wrapped instructions
        text = _BRACKET_INSTRUCTION_RE.sub('', text)

        # 4. Truncate
        if len(text) > _SANITIZE_MAX_CHARS:
            text = text[:_SANITIZE_MAX_CHARS]

    except Exception:
        # Never raise — return whatever we have
        pass

    return text


# FIXED: No resume length normalization
def normalize_resume_text(text: str, max_tokens: int = 1200) -> str:
    """
    Normalize resume length to prevent long resumes from inflating scores.

    Splits the resume into sections (Skills, Experience, Summary, Other)
    and allocates a token budget per section. Each section is independently
    truncated to its budget before rejoining.

    Uses simple whitespace tokenization (split on whitespace) — no heavy
    tokenizer dependency.

    Args:
        text: Raw resume text (already cleaned).
        max_tokens: Total token budget across all sections. Default 1200.

    Returns:
        Truncated resume text respecting per-section budgets.
    """
    if not text:
        return text

    # Quick check: if already under budget, return as-is
    tokens = text.split()
    if len(tokens) <= max_tokens:
        return text

    sections = detect_sections(text)

    # Per-section token budgets (must sum to <= max_tokens)
    budget = {
        "skills": 400,
        "experience": 400,
        "summary": 200,
        "education": 100,
        "certifications": 50,
        "projects": 50,
        "other": 0,  # computed as remainder
        "hobbies": 0,
    }
    allocated = sum(v for k, v in budget.items() if k != "other")
    budget["other"] = max(0, max_tokens - allocated)

    truncated_parts: List[str] = []
    for section_name, section_text in sections.items():
        section_budget = budget.get(section_name, budget["other"])
        if section_budget <= 0:
            continue
        section_tokens = section_text.split()
        if len(section_tokens) > section_budget:
            section_text = " ".join(section_tokens[:section_budget])
        # Re-emit the header. `detect_sections` CONSUMES the header line (it is
        # never appended to its buffer), so rejoining bodies alone returned a
        # document with no headers at all — and callers run `detect_sections`
        # again on this result. Every resume over the token budget therefore
        # collapsed to a single "other" section, silently disabling every
        # section-aware path downstream. Each canonical name is itself an exact
        # alias in SECTION_HEADERS, so it is re-recognised on the second pass.
        if section_name != "other":
            truncated_parts.append(section_name.replace("_", " ").upper())
        truncated_parts.append(section_text)

    result = "\n\n".join(truncated_parts)
    return result if result.strip() else text


def _find_cluster_match(required_skill: str, resume_skills_set: set) -> Optional[str]:
    """
    Return the first resume skill that shares ANY cluster with required_skill,
    or None if no cluster relationship exists.
    Example: required="pinecone", resume has "faiss" → returns "faiss" (both vector_database)

    A skill can belong to multiple clusters (e.g. FAISS is in both vector_database
    and embedding_model), so we iterate every cluster containing required_skill
    rather than relying on SKILL_TO_CLUSTER's single-cluster reverse map.
    """
    req_lower = required_skill.lower()
    for members in SKILL_CLUSTERS.values():
        members_lower = {m.lower() for m in members}
        if req_lower not in members_lower:
            continue
        for member_lower in members_lower:
            if member_lower != req_lower and member_lower in resume_skills_set:
                return member_lower
    return None


def _match_credit(required_norm: str, resume_normalized: set) -> Tuple[float, str, str]:
    """Base credit for one required skill against the resume's skill set.

    Returns ``(credit, status, matched_token)`` where credit is 1.0 for a direct
    match, ``CLUSTER_PARTIAL_CREDIT`` for a same-cluster relative, and 0.0 when
    absent. ``matched_token`` is the resume-side skill that satisfied it, which
    the evidence pass needs in order to go looking for it in the text.

    Containment is whole-word in both directions rather than a bare substring
    test. The old ``skill in rs or rs in skill`` credited required "go" to
    anyone who listed "django" or "mongodb", "java" to "javascript", and
    "scale" to "scala". Whole-word containment keeps the case the check existed
    for — required "lambda" satisfied by "aws lambda" — without those.
    """
    if required_norm in resume_normalized:
        return 1.0, "exact", required_norm
    for resume_skill in resume_normalized:
        if (contains_whole_word(resume_skill, required_norm)
                or contains_whole_word(required_norm, resume_skill)):
            return 1.0, "phrase", resume_skill
    cluster_hit = _find_cluster_match(required_norm, resume_normalized)
    if cluster_hit:
        return CLUSTER_PARTIAL_CREDIT, "cluster", cluster_hit
    return 0.0, "missing", ""


def _best_evidence_section(sections: Dict[str, str], terms: List[str]) -> Tuple[str, float]:
    """The strongest section any of ``terms`` appears in, as ``(name, weight)``.

    ``max`` over every section the skill occurs in, never first-seen, so the
    result does not depend on the order sections happen to appear in the
    document. A skill listed under SKILLS *and* used in an EXPERIENCE bullet
    earns the experience weight.
    """
    best_name, best_weight = "", 0.0
    for section_name, section_text in (sections or {}).items():
        if not section_text:
            continue
        weight = SKILL_EVIDENCE_WEIGHTS.get(section_name, UNLOCATABLE_EVIDENCE_WEIGHT)
        if weight <= best_weight:
            continue
        if any(contains_whole_word(section_text, term) for term in terms if term):
            best_name, best_weight = section_name, weight
    return best_name, best_weight


def score_required_skills_with_evidence(
    sections: Dict[str, str],
    resume_skills: List[str],
    required_skills: List[str],
) -> Tuple[float, List[Dict[str, Any]]]:
    """Percentage match for ``required_skills``, discounted by where each is evidenced.

    Returns ``(pct, detail)``. Each detail entry is
    ``{skill, status, section, credit}`` — the required skill, how it matched,
    the strongest section it was found in, and the credit it finally earned.

    The denominator deduplicates normalised skills. A JD listing both "JS" and
    "JavaScript" normalises to one skill, and previously capped a perfect
    candidate at 50 because the numerator deduplicated while the denominator
    did not.
    """
    required_unique: List[str] = list(OrderedDict.fromkeys(
        normalize_skill(s).lower() for s in (required_skills or []) if str(s).strip()
    ))
    if not required_unique:
        return 100.0, []

    resume_normalized = set(normalize_skill(s).lower() for s in (resume_skills or []))
    # The resume's own spelling, so the evidence pass can find text that says
    # "JS" for a skill the scorer normalised to "javascript".
    raw_by_normalised: Dict[str, str] = {}
    for raw in (resume_skills or []):
        raw_by_normalised.setdefault(normalize_skill(raw).lower(), str(raw))

    earned = 0.0
    detail: List[Dict[str, Any]] = []
    for skill in required_unique:
        credit, status, matched = _match_credit(skill, resume_normalized)
        if credit <= 0:
            detail.append({"skill": skill, "status": status, "section": "", "credit": 0.0})
            continue
        terms = [t for t in (matched, raw_by_normalised.get(matched, ""), skill) if t]
        section_name, weight = _best_evidence_section(sections, terms)
        if weight <= 0:
            section_name, weight = "", UNLOCATABLE_EVIDENCE_WEIGHT
        final = credit * weight
        earned += final
        detail.append({
            "skill": skill,
            "status": status,
            "section": section_name,
            "credit": round(final, 3),
        })

    pct = round(min(100.0, (earned / len(required_unique)) * 100), 2)
    return pct, detail


def compute_weighted_skills_score(sections: Dict[str, str], required_skills: List[str]) -> float:
    """
    C4.6 — Calculate skills match score weighted by which section each skill appears in.

    NOT USED BY THE LIVE SCORER — kept for its tests. `enhanced_score_candidate`
    reaches skills through `score_skills_v2`, which uses
    `score_required_skills_with_evidence` above. Note this function's
    SECTION_SKILL_WEIGHTS rank a bare skills list ABOVE experience, the
    opposite of the live rule.
    Exact matches get full section weight; cluster matches (related tech) get 60% credit.
    """
    if not required_skills:
        return 100.0
    required_normalized = [normalize_skill(s).lower() for s in required_skills]
    skill_scores: Dict[str, float] = {}  # skill -> best weighted hit

    for section_name, section_text in sections.items():
        weight = SECTION_SKILL_WEIGHTS.get(section_name, 0.5)
        text_lower = section_text.lower()
        for skill in required_normalized:
            # Exact match
            if skill in text_lower:
                if skill not in skill_scores or skill_scores[skill] < weight:
                    skill_scores[skill] = weight
            # Cluster match — check if any related skill appears in this section
            elif skill not in skill_scores:
                cluster = SKILL_TO_CLUSTER.get(skill)
                if cluster:
                    for member in SKILL_CLUSTERS.get(cluster, []):
                        if member.lower() != skill and member.lower() in text_lower:
                            skill_scores[skill] = weight * CLUSTER_PARTIAL_CREDIT
                            break

    if not skill_scores:
        return 0.0

    # Weighted score: sum(weight_of_best_match) / sum(max_weight) × 100. Normalised by the
    # strongest section (experience), so a skill shown in real work scores highest.
    max_weight = max(SECTION_SKILL_WEIGHTS.values())
    total = sum(skill_scores.values())
    perfect = len(required_normalized) * max_weight
    return round(min(100.0, (total / perfect) * 100), 2)


# ==================== ENHANCED ATS SCORING SERVICE ====================

class EnhancedATSScoringService:
    """Enhanced ATS scoring service with OCR and advanced parsing"""

    def __init__(self):
        # Enhanced tech skills list (used as fallback when spaCy unavailable)
        self.tech_skills = [
            # Programming Languages
            'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'go', 'rust',
            'ruby', 'php', 'swift', 'kotlin', 'scala', 'perl', 'r', 'matlab',
            'objective-c', 'dart', 'lua', 'bash', 'powershell', 'sql',

            # Web Technologies
            'react', 'angular', 'vue', 'svelte', 'nextjs', 'nuxtjs', 'django',
            'flask', 'fastapi', 'spring', 'express', 'laravel', 'rails',
            'nodejs', 'webpack', 'vite', 'babel', 'rollup',

            # Databases
            'postgresql', 'mysql', 'mongodb', 'redis', 'elasticsearch',
            'cassandra', 'dynamodb', 'sqlite', 'oracle', 'sql server',
            'mariadb', 'neo4j', 'influxdb', 'timescaledb',

            # Cloud & DevOps
            'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'terraform',
            'ansible', 'jenkins', 'circleci', 'travis ci',
            'github actions', 'argocd', 'helm', 'prometheus', 'grafana',

            # Tools & Technologies
            'git', 'github', 'gitlab', 'bitbucket', 'jira', 'confluence',
            'linux', 'ubuntu', 'centos', 'windows server', 'nginx', 'apache',
            'vscode', 'intellij', 'pycharm', 'eclipse', 'vim', 'emacs',

            # Data Science & AI
            'machine learning', 'deep learning', 'tensorflow', 'pytorch',
            'keras', 'scikit-learn', 'pandas', 'numpy', 'matplotlib',
            'seaborn', 'jupyter', 'nltk', 'spacy', 'opencv', 'hadoop',
            'spark', 'kafka', 'airflow', 'mlflow', 'dvc',

            # Generative AI & LLM Stack
            'langchain', 'lang chain', 'langgraph', 'lang graph',
            'llm', 'llms', 'large language models', 'generative ai', 'genai',
            'rag', 'retrieval augmented generation', 'prompt engineering',
            'fine-tuning', 'fine tuning', 'embeddings', 'vector embeddings',
            'multi-agent', 'multi agent', 'agentic',
            'ollama', 'openai', 'anthropic', 'hugging face', 'huggingface',
            'gpt', 'gpt-4', 'claude', 'gemini',
            'sentence transformers', 'sentence-transformers',
            'faiss', 'chromadb', 'chroma db', 'pinecone', 'weaviate',
            'qdrant', 'milvus', 'vector database', 'vector db',
            'ocr', 'speech-to-text', 'text-to-speech',
            'lora', 'rlhf', 'peft',

            # Mobile Development
            'ios', 'android', 'react native', 'flutter',
            'xamarin', 'cordova', 'ionic', 'unity',
            'unreal engine', 'godot',

            # Other Technologies
            'rest api', 'graphql', 'microservices', 'serverless',
            'blockchain', 'iot',
            'tableau', 'power bi', 'looker', 'superset',
            'html', 'css', 'html5', 'css3',

            # Digital Marketing
            'seo', 'google ads', 'facebook ads', 'content marketing',
            'email marketing', 'social media marketing', 'hubspot',
            'google analytics', 'copywriting', 'lead generation',
            'a/b testing', 'ppc', 'mailchimp', 'marketo', 'hootsuite',
            'affiliate marketing',

            # HR
            'recruitment', 'talent acquisition', 'performance management',
            'hris', 'labor law', 'employee relations', 'onboarding',
            'payroll management', 'organizational development',
            'succession planning', 'compensation and benefits',
            'sap hr', 'workday',

            # Sales
            'b2b sales', 'crm', 'salesforce', 'negotiation',
            'account management', 'revenue forecasting',
            'pipeline management', 'cold calling',
            'sales strategy', 'business development',

            # Finance & Accounting
            'financial planning', 'budgeting', 'forecasting',
            'financial reporting', 'financial analysis', 'gaap', 'ifrs',
            'bookkeeping', 'accounts payable', 'accounts receivable',
            'tax preparation', 'audit', 'treasury management',
            'quickbooks', 'tally erp', 'sap finance', 'excel',
            'valuation',

            # Design
            'adobe photoshop', 'adobe illustrator', 'figma', 'indesign',
            'branding', 'typography', 'ui/ux design', 'motion graphics',
            'after effects', 'canva', 'sketch', 'wireframing', 'prototyping',

            # Project Management & Leadership
            'stakeholder management', 'risk management', 'resource management',
            'project planning', 'ms project', 'prince2', 'change management',
            'team leadership', 'strategic planning', 'budget management',

            # Operations
            'operations management', 'supply chain', 'logistics',
            'inventory management', 'quality assurance', 'process improvement',
            'lean', 'six sigma',

            # Office Tools
            'microsoft office', 'ms office', 'powerpoint', 'word',
        ]

        # Enhanced ATS scoring weights — see DEFAULT_ATS_WEIGHTS.
        self.weights = dict(DEFAULT_ATS_WEIGHTS)

        # Education level mapping
        self.education_levels = {
            'high school': 1,
            'associate': 2,
            'bachelor': 3,
            'master': 4,
            'phd': 5,
            'postdoc': 6
        }

    def extract_skills(self, text: str) -> List[str]:
        """
        C4.1 + C4.3 — Extract and normalize skills.
        Uses spaCy token matching against canonical skills when available,
        falls back to word-boundary regex match against self.tech_skills.
        All extracted skills are passed through normalize_skill().
        """
        found: set = set()

        # spaCy-based canonical skill extraction
        spacy_skills = extract_skills_from_tokens(text)
        for s in spacy_skills:
            found.add(normalize_skill(s))

        # Fallback / supplemental: whole-word match against self.tech_skills
        # Uses \b word boundaries so "go" doesn't match inside "government",
        # "ar" doesn't match inside "rag"/"language", etc.
        text_lower = text.lower()
        for skill in self.tech_skills:
            pattern = r'\b' + re.escape(skill) + r'\b'
            if re.search(pattern, text_lower):
                found.add(normalize_skill(skill))

        # Filter out soft skills, spoken languages, and single-character noise
        filtered_skills = []
        for s in found:
            s_clean = s.strip()
            if len(s_clean) <= 1:
                continue
            s_low = s_clean.lower()
            if s_low in SOFT_SKILLS_SET or s_low in SPOKEN_LANGUAGES_SET:
                continue
            filtered_skills.append(s_clean)
        return filtered_skills

    def calculate_work_experience_years_from_entries(self, entries: List[Dict[str, Any]]) -> float:
        """
        Calculate total experience years from structured work experience entries.
        Rule: Internships with duration <= 8 months are EXCLUDED from total experience years calculation.
        """
        total_months = 0
        for entry in entries:
            emp_type = (entry.get('employment_type') or '').lower()
            title = (entry.get('job_title') or entry.get('title') or '').lower()
            is_internship = 'intern' in emp_type or 'intern' in title or 'trainee' in title

            duration_m = entry.get('duration_months')
            if duration_m is None and entry.get('start_date'):
                try:
                    s_yr = int(str(entry['start_date'])[:4])
                    if entry.get('end_date'):
                        e_yr = int(str(entry['end_date'])[:4])
                    else:
                        e_yr = datetime.now().year
                    duration_m = max(0, (e_yr - s_yr)) * 12
                except Exception:
                    duration_m = 0

            if duration_m is None:
                duration_m = 0

            # Internship Rule: Internships <= 8 months are excluded from total work experience calculation
            if is_internship and duration_m <= 8:
                continue

            total_months += duration_m
        return round(total_months / 12.0, 1)

    # FIXED: extract_experience_years double-counting & internship exclusion
    def extract_experience_years(self, text: str, sections: Optional[Dict[str, str]] = None) -> float:
        """
        Multi-pattern experience extraction with deduplication and internship filtering.
        Rule: Internships of 0 to 8 months are excluded from total work experience calculation.

        Date ranges are harvested from the work-history sections only. Scanned
        over the whole document, "B.Tech 2018 - 2022" counted as four years of
        professional experience, as did certification validity windows and
        project timelines — and because the result is a `max()` against the
        explicit "N years" phrase, that inflated number usually won. Explicit
        phrases are still read document-wide: they are self-describing and
        normally live in the summary.

        Falls back to whole-document scanning when no work-history section was
        detected, so a resume with unrecognised headings is not silently
        credited with zero experience.
        """
        text_norm = text.replace("–", "-").replace("—", "-")
        if sections is None:
            sections = detect_sections(text)
        work_bodies = [sections.get(name, "") for name in ("experience", "projects")]
        work_text = "\n".join(b for b in work_bodies if b)
        date_range_text = (work_text.replace("–", "-").replace("—", "-")
                           if work_text.strip() else text_norm)

        # --- Explicit year mentions ---
        year_patterns = [
            r'(\d+)\+?\s*years?\s*(?:of\s+)?experience',
            r'experience\s*(?:of\s+)?(\d+)\+?\s*years?',
            r'(\d+)\+?\s*years?\s*(?:of\s+)?(?:work|professional|industry)',
        ]
        year_hits: set = set()
        for pattern in year_patterns:
            for m in re.finditer(pattern, text_norm, re.IGNORECASE):
                # Ensure the mention isn't specifically about an internship <= 8 months
                snippet = text_norm[max(0, m.start() - 60):min(len(text_norm), m.end() + 60)].lower()
                if 'intern' in snippet and int(m.group(1)) == 0:
                    continue
                year_hits.add(int(m.group(1)))
        explicit_years = max(year_hits) if year_hits else 0.0

        # --- Month mentions (added to explicit) ---
        month_pattern = r'(\d+)\+?\s*months?\s*(?:of\s+)?(?:experience|work|internship)'
        month_total = 0.0
        for m in re.finditer(month_pattern, text_norm, re.IGNORECASE):
            months = int(m.group(1))
            snippet = text_norm[max(0, m.start() - 60):min(len(text_norm), m.end() + 60)].lower()
            # If internship <= 8 months, exclude from total work experience
            if 'intern' in snippet and months <= 8:
                continue
            month_total += months
        explicit_years += month_total / 12.0

        # --- Date range extraction ---
        current_year = datetime.now().year
        date_range_pattern = r'(\d{4})\s*[-–—]\s*(\d{4}|present|current|now|till date|to date)'
        date_range_years = 0.0
        for m in re.finditer(date_range_pattern, date_range_text, re.IGNORECASE):
            start_str, end_str = m.group(1), m.group(2).strip().lower()
            snippet = date_range_text[max(0, m.start() - 80):min(len(date_range_text), m.end() + 80)].lower()
            is_intern = 'intern' in snippet or 'trainee' in snippet
            try:
                start = int(start_str)
                end = current_year if end_str not in {str(y) for y in range(1990, current_year + 1)} and not end_str.isdigit() else int(end_str) if end_str.isdigit() else current_year
                duration = end - start
                # Exclude short internships (<= 8 months / same year or 1 yr intern)
                if is_intern and duration <= 0:
                    continue
                if 0 < duration <= 50:
                    date_range_years += duration
            except ValueError:
                logger.warning(f"Could not parse experience date range: {m.group(0)}")

        # Dedup: take whichever source gives the higher total, never sum them
        return round(max(explicit_years, date_range_years), 1)

    def extract_contact_info(self, text: str) -> dict:
        """Extract contact information from text"""
        contact = {}
        email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if email_match:
            contact['email'] = email_match.group()
        phone_match = re.search(r'[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{4,6}', text)
        if phone_match:
            contact['phone'] = phone_match.group()
        return contact

    def _extract_page_layout_aware(self, page) -> str:
        """
        Extract text from a pdfplumber page using column layout awareness.
        Detects 2-column / 3-column / sidebar layouts and reads left column top-to-bottom
        followed by right column top-to-bottom.
        """
        try:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
            if not words:
                return page.extract_text() or ""

            page_width = float(page.width or 612)
            mid_point = page_width * 0.38

            left_words = [w for w in words if w['x1'] <= mid_point + 15]
            right_words = [w for w in words if w['x0'] >= mid_point - 15]
            spanning_words = [w for w in words if w['x0'] < mid_point - 15 and w['x1'] > mid_point + 15]

            # If a clear 2-column / sidebar split exists
            if len(left_words) >= 5 and len(right_words) >= 5 and len(spanning_words) <= len(words) * 0.12:
                def render_column(col_words):
                    sorted_w = sorted(col_words, key=lambda w: (round(w['top'] / 3.5) * 3.5, w['x0']))
                    lines, cur_line, cur_top = [], [], None
                    for w in sorted_w:
                        t = round(w['top'] / 3.5) * 3.5
                        if cur_top is None or abs(t - cur_top) <= 3.5:
                            cur_line.append(w['text'])
                            cur_top = t
                        else:
                            lines.append(" ".join(cur_line))
                            cur_line = [w['text']]
                            cur_top = t
                    if cur_line:
                        lines.append(" ".join(cur_line))
                    return "\n".join(lines)

                return render_column(left_words) + "\n\n" + render_column(right_words)
        except Exception as e:
            logger.debug(f"Layout extraction fallback: {e}")

        return page.extract_text() or ""

    def extract_text_from_pdf_enhanced(self, file_path: str) -> Tuple[str, float]:
        """Enhanced PDF text extraction with OCR and column layout awareness"""
        text = ""
        confidence = 0.0

        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    page_text = self._extract_page_layout_aware(page)
                    if page_text:
                        text += page_text + "\n"
                        confidence += 0.9
                    else:
                        if OCR_AVAILABLE:
                            try:
                                image = page.to_image()
                                ocr_text = pytesseract.image_to_string(image)
                                if ocr_text.strip():
                                    text += ocr_text + "\n"
                                    confidence += 0.7
                            except Exception as e:
                                logger.warning(f"OCR failed for page {page_num}: {e}")
                                confidence += 0.5

            return clean_resume_text(text), min(confidence, 1.0)

        except Exception as e:
            logger.error(f"Enhanced PDF extraction failed: {e}")
            return "", 0.0

    def extract_text_from_docx(self, file_path: str) -> Tuple[str, float]:
        """Enhanced DOCX text extraction"""
        if not DOCX_AVAILABLE:
            return "", 0.0

        try:
            import docx
            doc = docx.Document(file_path)

            text = ""
            confidence = 0.0

            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    text += paragraph.text + "\n"
                    confidence += 0.1

            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            text += cell.text + " "
                            confidence += 0.05

            return clean_resume_text(text), min(confidence, 1.0)

        except Exception as e:
            logger.error(f"DOCX extraction failed: {e}")
            return "", 0.0

    def extract_education_info(self, text: str) -> List[Dict[str, Any]]:
        """
        C4.3 — Extract education using DEGREE_SCORES dict instead of pure regex.
        Returns list of dicts with degree, score, confidence.
        """
        education = []
        text_lower = text.lower()

        for degree_key, score in DEGREE_SCORES.items():
            if degree_key in text_lower:
                education.append({
                    'degree': degree_key,
                    'score': score,
                    'confidence': 0.85
                })

        # Also run original pattern matching for institution names
        institution_pattern = r'(?:University|College|Institute|Institution)\s+of\s+([A-Za-z\s]+)'
        for match in re.findall(institution_pattern, text, re.IGNORECASE):
            education.append({
                'degree': match.strip(),
                'score': 0,
                'confidence': 0.7
            })

        return education

    # FIXED: extract_work_experience weak regex
    def extract_work_experience(self, text: str) -> List[Dict[str, Any]]:
        """
        Extract work experience entries from resume text.

        Uses a three-tier extraction strategy:
        1. Section-header detection: finds the Experience/Work History section and
           parses entries with date ranges and company/title patterns.
        2. spaCy NER (if en_core_web_sm available): extracts ORG + DATE entity
           pairs as work entries.
        3. Regex fallback: the original pattern-based extraction.

        Returns a list of dicts. Each dict always contains at minimum:
            {"company": str, "title": str, "duration_months": int,
             "extracted_text": str, "confidence": float}
        Legacy keys ("years", "text") are preserved for backward compatibility.
        """
        experience: List[Dict[str, Any]] = []

        # --- Tier 1: Section-header detection ---
        experience_section = self._extract_experience_section(text)
        if experience_section:
            tier1 = self._parse_experience_section(experience_section)
            if tier1:
                experience.extend(tier1)

        # --- Tier 2: spaCy NER (ORG + DATE pairs) ---
        if not experience:
            tier2 = self._extract_experience_via_ner(text)
            if tier2:
                experience.extend(tier2)

        # --- Tier 3: Regex fallback ---
        if not experience:
            experience_patterns = [
                r'(\d+)\s*(?:\+|\+?\s*years?)\s*(?:of\s*)?(?:experience|work)',
                r'(\d{4})\s*[-–]\s*(\d{4}|\s*present)\s*(?:at|@)\s+([A-Za-z\s&]+)',
                r'(?:Senior|Junior|Lead|Principal|Staff|Mid-level|Entry-level)\s+(?:Software Engineer|Developer|Manager|Analyst|Consultant)',
            ]
            for pattern in experience_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        experience.append({
                            'years': int(match[0]),
                            'company': match[1] if len(match) > 1 else '',
                            'title': '',
                            'duration_months': int(match[0]) * 12 if match[0].isdigit() else 0,
                            'extracted_text': ' '.join(match),
                            'confidence': 0.5
                        })
                    else:
                        experience.append({
                            'text': match,
                            'company': '',
                            'title': match,
                            'duration_months': 0,
                            'extracted_text': match,
                            'confidence': 0.4
                        })

        return experience

    def _extract_experience_section(self, text: str) -> str:
        """
        Locate the work-experience section by scanning for header lines.

        Looks for lines containing keywords like Experience, Work History,
        Employment, Career, Professional Background (case-insensitive).
        Returns the text from that header to the next detected section header,
        or empty string if no experience section found.
        """
        lines = text.split("\n")
        exp_keywords = {
            "experience", "work history", "employment", "career",
            "professional background", "work experience",
            "employment history", "relevant experience",
        }
        start_idx = None
        end_idx = len(lines)

        for i, line in enumerate(lines):
            stripped = line.strip().lower()
            if not stripped:
                continue
            if start_idx is None:
                # Look for experience header
                if any(stripped.startswith(kw) or stripped == kw for kw in exp_keywords):
                    start_idx = i + 1
            else:
                # Look for next section header (any known section)
                for headers in SECTION_HEADERS.values():
                    if any(stripped.startswith(h) or stripped == h for h in headers):
                        # Make sure it's not another experience-related header
                        if not any(stripped.startswith(kw) or stripped == kw for kw in exp_keywords):
                            end_idx = i
                            break
                if end_idx != len(lines):
                    break

        if start_idx is None:
            return ""
        return "\n".join(lines[start_idx:end_idx])

    def _parse_experience_section(self, section_text: str) -> List[Dict[str, Any]]:
        """
        Parse structured work entries from an experience section.

        Looks for date-range lines (e.g. "2019 - 2023") paired with nearby
        company/title text.
        """
        entries: List[Dict[str, Any]] = []
        current_year = datetime.now().year
        date_pattern = re.compile(
            r'(\d{4})\s*[-–—]\s*(\d{4}|present|current|now|till date|to date)',
            re.IGNORECASE,
        )
        lines = section_text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            m = date_pattern.search(line)
            if m:
                start_year = int(m.group(1))
                end_str = m.group(2).strip().lower()
                end_year = int(end_str) if end_str.isdigit() else current_year
                duration_months = max(0, (end_year - start_year)) * 12

                # Try to grab company/title from the same line or adjacent lines
                context_lines = []
                if i > 0:
                    context_lines.append(lines[i - 1].strip())
                context_lines.append(line)
                if i + 1 < len(lines):
                    context_lines.append(lines[i + 1].strip())
                context = " | ".join(l for l in context_lines if l)

                # Simple heuristic: the longest non-date line is likely company/title
                non_date_parts = [l for l in context_lines if l and not date_pattern.search(l)]
                company = non_date_parts[0] if non_date_parts else ""
                title = non_date_parts[1] if len(non_date_parts) > 1 else ""

                entries.append({
                    "company": company,
                    "title": title,
                    "duration_months": duration_months,
                    "extracted_text": context,
                    "confidence": 0.75,
                })
            i += 1
        return entries

    def _extract_experience_via_ner(self, text: str) -> List[Dict[str, Any]]:
        """
        Use spaCy NER to extract ORG + DATE entity pairs as work entries.

        Gracefully returns empty list if spaCy or en_core_web_sm is unavailable.
        """
        try:
            import spacy
            try:
                nlp = spacy.load("en_core_web_sm")
            except OSError:
                return []
        except ImportError:
            return []

        entries: List[Dict[str, Any]] = []
        doc = nlp(text[:10000])
        orgs = [ent for ent in doc.ents if ent.label_ == "ORG"]
        dates = [ent for ent in doc.ents if ent.label_ == "DATE"]

        # Pair each ORG with the nearest DATE within 200 chars
        used_dates: set = set()
        for org in orgs:
            best_date = None
            best_dist = 200
            for j, date in enumerate(dates):
                if j in used_dates:
                    continue
                dist = abs(org.start_char - date.start_char)
                if dist < best_dist:
                    best_dist = dist
                    best_date = (j, date)
            duration_months = 0
            date_text = ""
            if best_date:
                used_dates.add(best_date[0])
                date_text = best_date[1].text
                # Try to parse year range from date text
                yr_match = re.search(r'(\d{4})\s*[-–—]\s*(\d{4}|present|current|now)', date_text, re.IGNORECASE)
                if yr_match:
                    s = int(yr_match.group(1))
                    e_str = yr_match.group(2).lower()
                    e = int(e_str) if e_str.isdigit() else datetime.now().year
                    duration_months = max(0, (e - s)) * 12

            entries.append({
                "company": org.text,
                "title": "",
                "duration_months": duration_months,
                "extracted_text": f"{org.text} ({date_text})" if date_text else org.text,
                "confidence": 0.65,
            })

        return entries

    def extract_certifications(self, text: str) -> List[str]:
        """Extract certifications from resume text with category validation."""
        certifications = []

        cert_patterns = [
            r'(?:AWS|Amazon Web Services)\s+(?:Certified|Solutions Architect|Developer|Practitioner)',
            r'(?:Google Cloud|GCP)\s+(?:Professional|Associate|Cloud\s+\w+)',
            r'(?:Microsoft|Azure)\s+(?:Certified|Solutions Expert|Administrator|Developer)',
            r'(?:Oracle|PMP|Scrum|Agile)\s+(?:Certified|Professional|Master)',
            r'(?:CCNA|CCNP|CISSP|CISA|CEH|CKA|CKAD|CKS|ITIL)\b',
            r'(?:Certified\s+)?(?:Information\s+Systems\s+Security\s+Professional|CISSP|Scrum\s+Master)',
            r'(?:Certified\s+)[A-Za-z0-9\s\-]+',
        ]

        for pattern in cert_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                m_str = ' '.join(match).strip() if isinstance(match, tuple) else match.strip()
                if m_str:
                    certifications.append(m_str)

        # Extract line items from detected Certifications section if present
        sections = detect_sections(text)
        cert_section_text = sections.get("certifications", "")
        if cert_section_text:
            for raw_line in cert_section_text.split("\n"):
                line = re.sub(r'^[#*\-•·▪\d\.\s\[\]\(\)]+', '', raw_line).strip(" .,-–—|()")
                if 4 <= len(line) <= 120 and re.search(r'[A-Za-z]{3,}', line):
                    certifications.append(line)

        # Filter out false positives (bare technical skills, soft skills, spoken languages, degree titles)
        filtered = []
        seen = set()
        for cert in certifications:
            c_clean = cert.strip()
            c_low = c_clean.lower()
            if not c_clean or len(c_clean) < 3 or c_low in seen:
                continue
            if c_low in SPOKEN_LANGUAGES_SET or c_low in SOFT_SKILLS_SET or c_low in DEGREE_TITLES_SET:
                continue
            # Drop single word bare tech skills (e.g. "python", "sql", "react") unless recognized cert acronym
            if len(c_clean.split()) == 1 and c_low not in {'ccna', 'ccnp', 'cissp', 'cisa', 'ceh', 'ckad', 'cka', 'cks', 'pmp', 'itil'}:
                continue
            seen.add(c_low)
            filtered.append(c_clean)

        return filtered

    def extract_projects(self, text: str) -> List[Dict[str, Any]]:
        """Extract project information from resume text"""
        projects = []

        project_patterns = [
            r'(?:Project|Projects?)\s*[:\-]\s*([A-Za-z0-9\s]+)',
            r'(?:Developed|Built|Created|Designed|Implemented)\s+([A-Za-z0-9\s]+)',
            r'(?:GitHub|GitLab|Bitbucket)\s+(?:repository|repo)\s*([A-Za-z0-9\-_]+)',
        ]

        for pattern in project_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                projects.append({
                    'name': match if isinstance(match, str) else ' '.join(match),
                    'extracted_text': match if isinstance(match, str) else ' '.join(match),
                    'confidence': 0.6
                })

        return projects

    def extract_languages(self, text: str) -> List[str]:
        """Extract languages from resume text"""
        languages = []

        language_list = [
            'english', 'spanish', 'french', 'german', 'chinese', 'japanese',
            'korean', 'russian', 'arabic', 'portuguese', 'italian',
            'hindi', 'tamil', 'telugu', 'bengali', 'marathi', 'gujarati'
        ]

        text_lower = text.lower()
        for lang in language_list:
            if lang in text_lower:
                languages.append(lang.capitalize())

        return list(set(languages))

    def calculate_skills_match(self, resume_skills: List[str], required_skills: List[str]) -> float:
        """
        C4.1 — Normalize both sides before matching.
        Exact/whole-word-phrase match = 1.0 credit. Same-cluster skill = 0.6 credit.

        Position-blind: this is the fallback for resumes whose sections could
        not be detected. `score_required_skills_with_evidence` is the section-
        aware path. Both share `_match_credit`, so they always agree about
        whether a candidate has a skill — only about what it is worth.
        """
        if not required_skills:
            return 100.0
        resume_normalized = set(normalize_skill(s).lower() for s in resume_skills)
        required_unique = list(OrderedDict.fromkeys(
            normalize_skill(s).lower() for s in required_skills if str(s).strip()
        ))
        if not required_unique:
            return 100.0
        total = sum(_match_credit(skill, resume_normalized)[0] for skill in required_unique)
        return round(min(100.0, (total / len(required_unique)) * 100), 2)

    def calculate_semantic_similarity(self, resume_text: str, job_description: str) -> float:
        """C4.2 — Delegates to module-level compute_semantic_score()."""
        return compute_semantic_score(resume_text, job_description)

    def calculate_experience_match(self, resume_years: float, required_years: float) -> float:
        """Calculate experience match score (0-100)"""
        if required_years == 0:
            return 100.0
        ratio = resume_years / required_years
        if ratio >= 1.5:
            return 100.0
        elif ratio >= 1.0:
            return 90.0
        elif ratio >= 0.75:
            return 75.0
        elif ratio >= 0.5:
            return 60.0
        else:
            return round(max(0.0, ratio * 100), 2)

    def calculate_keyword_match(self, resume_text: str, job_description: str) -> float:
        """
        C4.4 — Fuzzy keyword matching using rapidfuzz.partial_ratio.
        Falls back to exact overlap if rapidfuzz unavailable.
        """
        job_words = list(set(re.findall(r'\b[a-zA-Z]{3,}\b', job_description.lower())))
        resume_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', resume_text.lower()))
        if not job_words:
            return 50.0

        if RAPIDFUZZ_AVAILABLE:
            hits = sum(
                1 for jw in job_words
                if any(_fuzz.token_set_ratio(jw, rw) >= FUZZY_THRESHOLD for rw in resume_words)
            )
        else:
            hits = sum(1 for jw in job_words if jw in resume_words)

        overlap = hits / len(job_words)
        return round(min(100.0, overlap * 150), 2)

    def calculate_education_score(self, education: List[Dict[str, Any]], required_level: str = "bachelor") -> float:
        """
        C4.3 — Score based on DEGREE_SCORES dict extracted from education list.
        """
        if not education:
            return 0.0

        max_score = max((edu.get('score', 0) for edu in education), default=0)
        required_score = DEGREE_SCORES.get(required_level.lower(), 60)

        if max_score >= required_score:
            return 100.0
        return round((max_score / required_score) * 100, 2)

    def calculate_certification_score(self, certifications: List[str]) -> float:
        """Calculate certification score — counts certs and gives bonus for recognized ones."""
        if not certifications:
            return 0.0

        base_score = min(len(certifications) * 20, 80)
        relevant_keywords = [
            # Cloud
            'aws', 'azure', 'google cloud', 'gcp', 'alibaba cloud',
            # DevOps / infra
            'kubernetes', 'docker', 'terraform', 'hashicorp', 'red hat', 'rhce', 'rhcsa',
            'linux foundation', 'ckad', 'cka', 'cks', 'argocd',
            # Security
            'cissp', 'cisa', 'cism', 'comptia', 'security+', 'network+', 'ceh', 'ccna', 'ccnp',
            'oscp', 'pentest',
            # Data / ML
            'databricks', 'snowflake', 'tensorflow', 'pytorch', 'deeplearning.ai',
            'machine learning', 'data science', 'aws ml',
            # Project management
            'pmp', 'prince2', 'safe', 'scrum', 'agile', 'csm', 'psm',
            # General
            'oracle', 'java', 'python', 'microsoft', 'cisco',
        ]
        relevant_count = sum(1 for cert in certifications
                             if any(keyword in cert.lower() for keyword in relevant_keywords))
        bonus = min(relevant_count * 10, 20)
        return min(100.0, base_score + bonus)

    def calculate_project_score(self, projects: List[Dict[str, Any]], jd_text: str = "") -> float:
        """
        C4.7 — Semantic project relevance score.
        Each project's extracted_text is compared to the JD using sentence transformers.
        Falls back to count-based score if no JD text provided.
        """
        if not projects:
            return 0.0

        if jd_text:
            project_texts = [p.get('extracted_text', p.get('name', '')) for p in projects]
            project_texts = [t for t in project_texts if t.strip()]
            if project_texts:
                scores = [compute_semantic_score(pt, jd_text) for pt in project_texts]
                avg = sum(scores) / len(scores)
                count_bonus = min(len(project_texts) * 5, 20)
                return round(min(100.0, avg + count_bonus), 2)

        # Count-based fallback (no JD text)
        base_score = min(len(projects) * 15, 60)
        tech_keywords = ['developed', 'built', 'implemented', 'designed', 'created']
        tech_count = sum(1 for proj in projects
                        if any(keyword in proj.get('extracted_text', '').lower()
                              for keyword in tech_keywords))
        bonus = min(tech_count * 10, 40)
        return base_score + bonus

    # ──────────────────────────────────────────────────────────────────────
    # Recruiter rubric — nested sub-scores
    #   Education   = Degree match (40) + College tier (30) + CGPA (30)
    #   Experience  = Relevant exp (50) + Internships (25) + GitHub/OSS/Hack (25)
    #   Skills      = Required (70) + Preferred (30)
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _degree_score_from_text(*texts: str) -> int:
        """Highest DEGREE_SCORES value found across the given strings (0 if none)."""
        best = 0
        for t in texts:
            if not t:
                continue
            low = t.lower()
            for kw, sc in DEGREE_SCORES.items():
                if kw in low and sc > best:
                    best = sc
        return best

    @staticmethod
    def _parse_cgpa(*values) -> Optional[float]:
        """Parse a CGPA / GPA / percentage from any of the given strings.
        Returns a normalized 0.0-1.0 fraction, or None if not found."""
        for v in values:
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            # Percentage e.g. "85%"
            m = re.search(r'(\d{2,3}(?:\.\d+)?)\s*%', s)
            if m:
                pct = float(m.group(1))
                if 0 < pct <= 100:
                    return min(1.0, pct / 100.0)
            # Explicit scale e.g. "8.5/10", "3.8/4", "10/10"
            m = re.search(r'(\d{1,2}(?:\.\d+)?)\s*/\s*(10|5|4)\b', s)
            if m:
                num, den = float(m.group(1)), float(m.group(2))
                if den > 0 and num <= den:
                    return min(1.0, num / den)
            # cgpa/gpa/grade keyword followed by a number
            m = re.search(r'(?:cgpa|gpa|grade)\D{0,8}(\d{1,2}(?:\.\d+)?)', s, re.I)
            if m:
                val = float(m.group(1))
                if val <= 4:
                    return min(1.0, val / 4.0)
                if val <= 10:
                    return min(1.0, val / 10.0)
                if val <= 100:
                    return min(1.0, val / 100.0)
            # Bare number in a short field (structured grade like "8.7")
            if len(s) <= 6:
                m = re.fullmatch(r'(\d{1,2}(?:\.\d+)?)', s)
                if m:
                    val = float(m.group(1))
                    if val <= 4:
                        return min(1.0, val / 4.0)
                    if val <= 10:
                        return min(1.0, val / 10.0)
        return None

    def score_education_v2(self, educations, institution_names, parsed_education,
                           resume_text, required_education):
        """Education sub-score (0-100) = degree(40) + college tier(30) + CGPA(30)."""
        # Degree match (0-40)
        if educations:
            cand_degree = max(
                (self._degree_score_from_text(e.get('education_level', ''), e.get('degree', ''))
                 for e in educations), default=0)
        else:
            cand_degree = max((e.get('score', 0) for e in (parsed_education or [])), default=0)
        if not cand_degree:
            cand_degree = self._degree_score_from_text(resume_text or '')
        req_degree = DEGREE_SCORES.get((required_education or 'bachelor').lower(), 60)
        if cand_degree <= 0:
            degree_sub = 0.0
        elif cand_degree >= req_degree:
            degree_sub = 40.0
        else:
            degree_sub = round(40.0 * cand_degree / req_degree, 1)

        # College tier (0-30)
        names = [e.get('institution_name', '') for e in educations] if educations else (institution_names or [])
        tier = best_tier([n for n in names if n])
        tier_sub = 30.0 if tier == 'tier_1' else 20.0 if tier == 'tier_2' else 0.0

        # CGPA (0-30)
        cgpa_frac = None
        if educations:
            for e in educations:
                cgpa_frac = self._parse_cgpa(e.get('cgpa'), e.get('grade'))
                if cgpa_frac is not None:
                    break
        if cgpa_frac is None:
            cgpa_frac = self._parse_cgpa(resume_text or '')
        cgpa_sub = round(30.0 * cgpa_frac, 1) if cgpa_frac is not None else 0.0

        score = round(min(100.0, degree_sub + tier_sub + cgpa_sub), 2)
        detail = {
            'degree_match': degree_sub, 'degree_max': 40,
            'college_tier': tier_sub, 'college_tier_max': 30, 'tier': tier,
            'cgpa': cgpa_sub, 'cgpa_max': 30, 'cgpa_found': cgpa_frac is not None,
        }
        return score, detail

    def score_experience_v2(self, years, required_years, work_experiences, links_text, resume_text):
        """Experience sub-score (0-100) = relevant(50) + internships(25) + github/OSS/hack(25)."""
        # Relevant experience (0-50)
        if not required_years or required_years <= 0:
            rel = 50.0
        else:
            ratio = (years or 0) / required_years
            if ratio >= 1.0:
                rel = 50.0
            elif ratio >= 0.75:
                rel = 40.0
            elif ratio >= 0.5:
                rel = 30.0
            else:
                rel = round(max(0.0, ratio) * 50.0, 1)

        # Internships (0-25)
        intern_count = 0
        if work_experiences:
            intern_count = sum(
                1 for w in work_experiences
                if 'intern' in (w.get('employment_type', '') or '').lower()
                or 'intern' in (w.get('job_title', '') or '').lower())
        if intern_count == 0 and resume_text and 'intern' in resume_text.lower():
            intern_count = 1
        intern_sub = float(min(intern_count * 15, 25))

        # GitHub / Open-source / Hackathons (0-25)
        blob = ' '.join(filter(None, [links_text or '', resume_text or ''])).lower()
        gh = 15.0 if 'github' in blob else 0.0
        oss = 5.0 if ('open source' in blob or 'open-source' in blob) else 0.0
        hack = 5.0 if 'hackathon' in blob else 0.0
        oss_sub = min(25.0, gh + oss + hack)

        score = round(min(100.0, rel + intern_sub + oss_sub), 2)
        detail = {
            'relevant_experience': rel, 'relevant_max': 50,
            'internships': intern_sub, 'internships_max': 25, 'internship_count': intern_count,
            'github_oss_hackathons': oss_sub, 'github_oss_max': 25,
            'has_github': gh > 0, 'has_oss': oss > 0, 'has_hackathon': hack > 0,
        }
        return score, detail

    def score_skills_v2(self, resume_skills, required_skills, preferred_skills, sections=None):
        """Skills sub-score (0-100) = required(70) + preferred(30).
        When the JD lists no preferred skills, required carries the full 100.

        When ``sections`` is supplied and usable, each skill's credit is scaled
        by where the resume evidences it (SKILL_EVIDENCE_WEIGHTS) — a skill used
        in a job or project is worth more than one sitting in a list.

        The guard matters: a resume whose headings we failed to recognise falls
        back to the flat, position-blind match rather than being marked down for
        its typography. Handled the other way round — scoring unrecognised text
        as "not demonstrated" — deleting your headings would become a way to
        lose points, and weighting it as fully demonstrated would make deleting
        them an exploit.
        """
        evidence_capable = bool(sections) and bool(EVIDENCE_CAPABLE_SECTIONS & set(sections))
        skill_evidence: List[Dict[str, Any]] = []
        flat_req_pct = (self.calculate_skills_match(resume_skills, required_skills) / 100.0
                        if required_skills else 1.0)

        if required_skills and evidence_capable:
            pct, skill_evidence = score_required_skills_with_evidence(
                sections, resume_skills, required_skills)
            req_pct = pct / 100.0
        else:
            req_pct = flat_req_pct

        if preferred_skills:
            if evidence_capable:
                pref_pct = score_required_skills_with_evidence(
                    sections, resume_skills, preferred_skills)[0] / 100.0
            else:
                pref_pct = self.calculate_skills_match(resume_skills, preferred_skills) / 100.0
            req_sub = round(70.0 * req_pct, 1)
            pref_sub = round(30.0 * pref_pct, 1)
            score = round(req_sub + pref_sub, 2)
        else:
            req_sub = round(70.0 * req_pct, 1)
            pref_sub = None
            score = round(100.0 * req_pct, 2)

        demonstrated = sum(1 for e in skill_evidence
                           if e.get('section') in ('experience', 'projects'))
        credited = sum(1 for e in skill_evidence if e.get('credit', 0) > 0)
        detail = {
            'required_skills': req_sub, 'required_max': 70,
            'preferred_skills': pref_sub, 'preferred_max': 30,
            'has_preferred': bool(preferred_skills),
            # False means the resume had no recognisable sections and was scored
            # flat — so a recruiter can tell a weak resume from an unparsed one.
            'evidence_weighted': evidence_capable,
            'demonstrated_count': demonstrated,
            'listed_only_count': credited - demonstrated,
            # The undiscounted score, so the size of the discount is visible.
            'required_flat_pct': round(flat_req_pct * 100, 2),
            'skill_evidence': skill_evidence,
        }
        return score, detail

    def extract_preferred_skills(self, jd_text: str, exclude: Optional[List[str]] = None) -> List[str]:
        """Skills mentioned under a preferred / nice-to-have / desirable section of the JD,
        minus any that are mandatory (`exclude`) — a required skill must not also earn
        preferred credit."""
        if not jd_text:
            return []
        low = jd_text.lower()
        markers = ['preferred', 'nice to have', 'nice-to-have', 'good to have',
                   'desirable', 'plus', 'bonus', 'advantage', 'added advantage']
        hits = [low.find(m) for m in markers if low.find(m) >= 0]
        if not hits:
            return []
        segment = jd_text[min(hits):min(hits) + 600]
        excluded = {normalize_skill(x).lower() for x in (exclude or [])}
        return [sk for sk in self.extract_skills(segment) if normalize_skill(sk).lower() not in excluded]

    @staticmethod
    def _rating_and_recommendation(score: float):
        """Map a final 0-100 score to a recruiter rating + hiring recommendation."""
        if score >= 85:
            return "Excellent", "Strong hire — fast-track to interview"
        if score >= 70:
            return "Very Good", "Recommended — shortlist for interview"
        if score >= 55:
            return "Good", "Consider — review manually before deciding"
        if score >= 40:
            return "Average", "Weak match — only if the pipeline is thin"
        return "Poor", "Not recommended for this role"

    def _strengths_weaknesses_v2(self, edu, exp, skl, matched_skills, missing_skills,
                                 certification_score):
        """Recruiter-friendly strengths / weaknesses derived from the sub-scores."""
        strengths, weaknesses = [], []

        if edu['tier'] == 'tier_1':
            strengths.append("Graduated from a Tier-1 institute")
        elif edu['tier'] == 'tier_2':
            strengths.append("Graduated from a Tier-2 institute")
        if edu['degree_match'] >= 40:
            strengths.append("Meets or exceeds the required degree level")
        elif edu['degree_match'] < 20:
            weaknesses.append("Degree level is below the role requirement")
        if edu['cgpa_found'] and edu['cgpa'] >= 24:
            strengths.append("Strong academic record (high CGPA)")
        elif not edu['cgpa_found']:
            weaknesses.append("No CGPA/GPA found on the resume")

        if matched_skills:
            strengths.append(f"Matches {len(matched_skills)} required skill(s)")
        if missing_skills:
            weaknesses.append(f"Missing {len(missing_skills)} required skill(s): "
                              f"{', '.join(missing_skills[:5])}")
        if not skl['has_preferred']:
            pass
        elif skl['preferred_skills'] is not None and skl['preferred_skills'] >= 24:
            strengths.append("Covers most preferred/nice-to-have skills")

        if exp['relevant_experience'] >= 50:
            strengths.append("Has the required years of relevant experience")
        elif exp['relevant_experience'] < 30:
            weaknesses.append("Less relevant experience than required")
        if exp['internship_count'] > 0:
            strengths.append(f"Completed {exp['internship_count']} internship(s)")
        if exp['has_github']:
            strengths.append("Active GitHub / open-source presence")
        if exp['has_hackathon']:
            strengths.append("Hackathon participation")

        if certification_score >= 60:
            strengths.append("Holds relevant professional certifications")
        elif certification_score == 0:
            weaknesses.append("No certifications found")

        return strengths, weaknesses

    def enhanced_parse_resume(self, file_path: str) -> EnhancedResumeParseResult:
        """Enhanced resume parsing with OCR and detailed extraction"""
        file_extension = Path(file_path).suffix.lower()

        if file_extension == '.pdf':
            text, confidence = self.extract_text_from_pdf_enhanced(file_path)
            processing_method = "pdf_enhanced"
        elif file_extension in ['.docx', '.doc']:
            text, confidence = self.extract_text_from_docx(file_path)
            processing_method = "docx_enhanced"
        else:
            raise ValueError(f"Unsupported file format: {file_extension}")

        if not text:
            raise ValueError("No text extracted from resume")

        skills = self.extract_skills(text)
        experience_years = self.extract_experience_years(text)
        contact_info = self.extract_contact_info(text)
        education = self.extract_education_info(text)
        work_experience = self.extract_work_experience(text)
        certifications = self.extract_certifications(text)
        projects = self.extract_projects(text)
        languages = self.extract_languages(text)

        return EnhancedResumeParseResult(
            text=text,
            skills=skills,
            experience_years=experience_years,
            contact_info=contact_info,
            education=education,
            work_experience=work_experience,
            certifications=certifications,
            languages=languages,
            projects=projects,
            confidence_score=confidence,
            file_type=file_extension,
            processing_method=processing_method
        )

    def prepare_resume(self, resume_text: str) -> PreparedResume:
        """Run the resume-only half of scoring once.

        Holds the same calls, in the same order, with the same arguments that
        ``enhanced_score_candidate`` used inline, so a prepared result scores
        identically to an unprepared one. See ``PreparedResume``.
        """
        # Sanitize before any processing, then repair the raw text (ligatures,
        # line breaks, bullets), then apply the token budget.
        text = sanitize_resume_text(resume_text)
        text = clean_resume_text(text)
        text = normalize_resume_text(text)
        return PreparedResume(
            text=text,
            # C4.6 — sections drive section-aware semantic scoring.
            sections=detect_sections(text),
            parsed=self.enhanced_parse_resume_from_text(text),
        )

    def enhanced_score_candidate(
        self,
        resume_text: str,
        job_description: str,
        required_skills: List[str],
        required_experience: int,
        required_education: str = "bachelor",
        custom_weights: Optional[Dict[str, float]] = None,
        institution_names: Optional[List[str]] = None,
        permanent_pincode: Optional[str] = None,
        educations: Optional[List[Dict[str, Any]]] = None,
        work_experiences: Optional[List[Dict[str, Any]]] = None,
        links_text: Optional[str] = None,
        preferred_skills: Optional[List[str]] = None,
        prepared: Optional[PreparedResume] = None,
        known_experience_years: Optional[float] = None,
    ) -> EnhancedATSScoreResult:
        """
        Recruiter-rubric ATS scoring.

        Weighted factors (sum to 1.0): education 35%, skills 25%, experience 20%,
        certification 10%, project 8%, semantic 1%, keyword 1% — see
        DEFAULT_ATS_WEIGHTS. Education, skills, and experience are themselves
        composed of sub-scores (see score_*_v2).

        Skills credit is scaled by where the resume evidences each skill, so a
        skill used in a job or project outweighs the same skill in a list. See
        score_skills_v2 and SKILL_EVIDENCE_WEIGHTS.

        Structured inputs (``educations``, ``work_experiences``, ``links_text``,
        ``preferred_skills``) make the sub-scores reliable; when absent the scorer
        falls back to extracting them from ``resume_text`` / ``job_description``.

        A rural permanent-address affirmative bonus (+3, capped at 100) is added
        from ``permanent_pincode``. College tier is part of the education score.
        """
        weights = custom_weights if custom_weights else self.weights

        # Resume-only preprocessing: sanitise, clean, normalise, detect sections, parse.
        # None of it reads the job description, so a caller scoring one resume against
        # many jobs passes `prepared` and pays for it once instead of once per job.
        prep = prepared if prepared is not None else self.prepare_resume(resume_text)
        # Evidence is looked for in the FULL text: the processed text is cut to a token
        # budget, and a skill mentioned after the cut used to count as missing.
        from src.services import ats_evidence as ev
        evidence_text = clean_resume_text((resume_text or "")[:ev.FULL_TEXT_MAX_CHARS])
        units = ev.section_units(detect_sections(evidence_text))
        resume_text = prep.text
        sections = prep.sections
        resume_result = prep.parsed

        # ── Secondary factors (semantic / keyword / certification / project) ──
        if len(sections) > 1:
            semantic_score = compute_weighted_semantic_score(sections, job_description)
        else:
            semantic_score = self.calculate_semantic_similarity(resume_text, job_description)
        keyword_score = self.calculate_keyword_match(resume_text, job_description)
        certification_score = self.calculate_certification_score(resume_result.certifications)
        project_score = self.calculate_project_score(resume_result.projects, jd_text=job_description)

        # ── Skills = Required (70) + Preferred (30) ──
        # None = HR never listed nice-to-haves, so infer them from the JD text; an explicit
        # list (even an empty one) is used as given. Either way a mandatory skill is never
        # also counted as preferred.
        required_keys = {normalize_skill(x).lower() for x in required_skills}
        if preferred_skills is None:
            preferred_skills = self.extract_preferred_skills(job_description, exclude=required_skills)
            preferred_source = "inferred" if preferred_skills else "none"
        else:
            preferred_skills = [p for p in preferred_skills if normalize_skill(p).lower() not in required_keys]
            preferred_source = "explicit" if preferred_skills else "none"
        # Each required / preferred skill: demonstrated, listed, related or not found,
        # with excerpts. One pass decides both the score and the matched/missing lists.
        extracted_set = {normalize_skill(x).lower() for x in resume_result.skills}
        required_checks = [ev.classify_skill(sk, units, extracted_set) for sk in required_skills]
        preferred_checks = [ev.classify_skill(sk, units, extracted_set) for sk in (preferred_skills or [])]
        skills_score, skills_detail = ev.skills_score(required_checks, preferred_checks)

        # ── Education = Degree (40) + College tier (30) + CGPA (30); unknown parts excluded ──
        _, edu_v2 = self.score_education_v2(
            educations, institution_names, resume_result.education, resume_text, required_education)
        inst_names = [e.get('institution_name') for e in educations] if educations else (institution_names or [])
        education_score, education_detail = ev.education_score(
            degree_sub=edu_v2['degree_match'], cand_degree=edu_v2['degree_match'],
            tier=edu_v2['tier'], has_institution=any(inst_names),
            cgpa_sub=edu_v2['cgpa'], cgpa_found=edu_v2['cgpa_found'], units=units)

        # ── Experience = Relevant (50) + Internships (25) + GitHub/OSS/Hack (25) ──
        years, years_method, years_excerpts = ev.experience_years_evidence(
            units, resume_result.experience_years, known_experience_years)
        experience_score, experience_detail = ev.experience_score(
            years, required_experience, work_experiences, links_text, units)
        experience_detail['years_method'] = years_method
        experience_detail['years_evidence'] = years_excerpts

        # Composite: weighted over the factors with evidence; a factor with nothing to go
        # on (all of education unknown, say) is left out and the rest re-weighted, rather
        # than dragging the score down as a zero.
        factor_scores = {
            'education': education_score, 'skills_match': skills_score, 'experience': experience_score,
            'certification': certification_score, 'semantic': semantic_score,
            'project': project_score, 'keyword': keyword_score,
        }
        ats_score, evidence_coverage, factor_rows = ev.combine(factor_scores, weights)

        # College tier is now part of the education score (no separate tier bonus).
        # Rural permanent address remains a small affirmative bonus, capped at 100.
        college_tier = education_detail.get('tier')
        rural = bool(permanent_pincode) and is_rural(permanent_pincode)
        rural_bonus = 3.0 if rural else 0.0
        total_bonus = rural_bonus

        ats_score = min(100.0, ats_score + total_bonus)

        # Resume<->JD verbatim-overlap (copy / plagiarism) detection. This is
        # distinct from the semantic factor: it flags large blocks of identical
        # text (a pasted JD), which would otherwise game skills/semantic/keyword.
        plagiarism = jd_copy_overlap(resume_text, job_description)
        if JD_COPY_PENALTY > 0 and plagiarism["flagged"]:
            ats_score = max(0.0, ats_score - JD_COPY_PENALTY)

        # Matched / related / missing come from the same evidence that set the score, so
        # the lists can no longer disagree with it (they used to use exact set matching
        # while the score used substring matching).
        required_normalized = [normalize_skill(s).lower() for s in required_skills]
        resume_normalized = [normalize_skill(s).lower() for s in resume_result.skills]
        matched_skills = [normalize_skill(c['skill']).lower() for c in required_checks
                          if c['level'] in ('demonstrated', 'listed')]
        cluster_matched_skills: List[Dict[str, str]] = [
            {"required": normalize_skill(c['skill']).lower(), "found": c.get('related_to'),
             "cluster": SKILL_TO_CLUSTER.get(normalize_skill(c['skill']).lower(), "")}
            for c in required_checks if c['level'] == 'related']
        missing_skills: List[str] = [normalize_skill(c['skill']).lower() for c in required_checks
                                     if c['level'] == 'not_found']

        rating, hiring_recommendation = self._rating_and_recommendation(ats_score)

        recommendations = self._generate_recommendations(
            skills_score, experience_score, education_score,
            missing_skills, resume_result.certifications
        )

        strengths, weaknesses = _evidence_strengths_weaknesses(
            required_checks, preferred_checks, education_detail, experience_detail, certification_score)

        education_level = self._determine_education_level(resume_result.education)

        # Preferred skills the candidate is missing (for the dashboard).
        missing_preferred = [c['skill'] for c in preferred_checks if c['level'] == 'not_found']

        # Supporting resume excerpts per factor. Presentation only — it reads
        # values already computed above and cannot change a score, so a failure
        # here costs the excerpts, never the scoring run.
        try:
            evidence = collect_evidence(
                resume_text=resume_text,
                sections=sections,
                job_description=job_description,
                matched_skills=matched_skills,
                cluster_matched_skills=cluster_matched_skills,
                preferred_skills=preferred_skills or [],
                resume_skills=resume_result.skills,
                education=resume_result.education,
                work_experience=resume_result.work_experience,
                certifications=resume_result.certifications,
                projects=resume_result.projects,
                normalize=normalize_skill,
            )
        except Exception as e:
            logger.warning(f"ATS evidence collection failed, scoring unaffected: {e}")
            evidence = {}

        scoring_breakdown = {
            'rubric_version': ATS_RUBRIC_VERSION,
            'weights_used': weights,
            'evidence': evidence,
            'individual_scores': {
                'education': education_score,
                'skills_match': skills_score,
                'experience': experience_score,
                'certification': certification_score,
                'semantic': semantic_score,
                'project': project_score,
                'keyword': keyword_score,
            },
            'weighted_contributions': {r['key']: r['contribution'] for r in factor_rows},
            'sub_scores': {
                'education': education_detail,
                'experience': experience_detail,
                'skills': skills_detail,
            },
            'rating': rating,
            'hiring_recommendation': hiring_recommendation,
            'strengths': strengths,
            'weaknesses': weaknesses,
            'preferred_skills': preferred_skills or [],
            'preferred_source': preferred_source,
            'missing_preferred_skills': missing_preferred,
            'mandatory': {
                'met': len(matched_skills),
                'total': len(required_checks),
                'missing': [c['skill'] for c in required_checks if c['level'] not in ('demonstrated', 'listed')],
            },
            'factors': factor_rows,
            'evidence_coverage': evidence_coverage,
            'unknown_factors': [r['key'] for r in factor_rows if r['status'] == 'unknown_excluded'],
            'parsing_confidence': resume_result.confidence_score,
            'processing_method': resume_result.processing_method,
            'sections_detected': list(sections.keys()),
            'cluster_matched_count': len(cluster_matched_skills),
            'bonus': {
                'college_tier': college_tier,
                'rural': rural,
                'rural_bonus': rural_bonus,
                'total_bonus': total_bonus,
            },
            'plagiarism': plagiarism,
        }

        explanation = ev.cap_explanation({
            "schema": 1,
            "scoring_rules_version": SCORING_RULES_VERSION,
            "mandatory": {"met": len(matched_skills), "total": len(required_checks), "items": required_checks},
            "preferred": {"source": preferred_source,
                          "met": sum(1 for c in preferred_checks if c['level'] in ('demonstrated', 'listed')),
                          "total": len(preferred_checks), "items": preferred_checks},
            "experience": experience_detail,
            "education": education_detail,
            "factors": factor_rows,
            "evidence_coverage": evidence_coverage,
            "bonuses": {"rural": rural, "rural_bonus": rural_bonus},
            "plagiarism": plagiarism,
        })
        return EnhancedATSScoreResult(
            mandatory_met=len(matched_skills),
            mandatory_total=len(required_checks),
            missing_mandatory=[c['skill'] for c in required_checks if c['level'] not in ('demonstrated', 'listed')],
            preferred_source=preferred_source,
            evidence_coverage=evidence_coverage,
            explanation=explanation,
            ats_score=round(ats_score, 2),
            skills_match_score=round(skills_score, 2),
            semantic_score=round(semantic_score, 2),
            experience_score=round(experience_score or 0.0, 2),
            keyword_score=round(keyword_score, 2),
            education_score=round(education_score or 0.0, 2),
            certification_score=round(certification_score, 2),
            project_score=round(project_score, 2),
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            cluster_matched_skills=cluster_matched_skills,
            extracted_skills=resume_result.skills,
            extracted_experience=resume_result.experience_years,
            education_level=education_level,
            certifications=resume_result.certifications,
            confidence_score=resume_result.confidence_score,
            scoring_breakdown=scoring_breakdown,
            recommendations=recommendations,
            strengths=strengths,
            weaknesses=weaknesses,
            rating=rating,
            hiring_recommendation=hiring_recommendation,
        )

    def enhanced_parse_resume_from_text(self, text: str) -> EnhancedResumeParseResult:
        """Parse resume from already extracted text"""
        skills = self.extract_skills(text)
        experience_years = self.extract_experience_years(text)
        contact_info = self.extract_contact_info(text)
        education = self.extract_education_info(text)
        work_experience = self.extract_work_experience(text)
        certifications = self.extract_certifications(text)
        projects = self.extract_projects(text)
        languages = self.extract_languages(text)

        return EnhancedResumeParseResult(
            text=text,
            skills=skills,
            experience_years=experience_years,
            contact_info=contact_info,
            education=education,
            work_experience=work_experience,
            certifications=certifications,
            languages=languages,
            projects=projects,
            confidence_score=0.8,
            file_type="text",
            processing_method="text_input"
        )

    def _generate_recommendations(
        self, skills_score: float, experience_score: float, education_score: float,
        missing_skills: List[str], certifications: List[str]
    ) -> List[str]:
        """Generate personalized recommendations"""
        recommendations = []

        if skills_score < 70:
            recommendations.append(f"Consider learning missing skills: {', '.join(missing_skills[:3])}")

        # None = not stated on the resume: nothing to recommend from an absence.
        if experience_score is not None and experience_score < 70:
            recommendations.append("Gain more relevant work experience through internships or projects")

        if education_score is not None and education_score < 70:
            recommendations.append("Consider pursuing additional certifications or advanced degrees")

        if not certifications:
            recommendations.append("Obtain industry-recognized certifications to strengthen your profile")

        return recommendations

    def _generate_strengths_weaknesses(
        self, matched_skills: List[str], missing_skills: List[str],
        experience_years: float, required_experience: int,
        education: List[Dict], certifications: List[str]
    ) -> Tuple[List[str], List[str]]:
        """Generate strengths and weaknesses analysis"""
        strengths = []
        weaknesses = []

        if matched_skills:
            strengths.append(f"Strong in {len(matched_skills)} key technologies")

        if experience_years >= required_experience:
            strengths.append("Sufficient experience for the role")

        if education:
            strengths.append("Relevant educational background")

        if certifications:
            strengths.append(f"Holds {len(certifications)} professional certifications")

        if missing_skills:
            weaknesses.append(f"Missing {len(missing_skills)} required skills")

        if experience_years < required_experience:
            weaknesses.append("Insufficient experience for the role")

        if not certifications:
            weaknesses.append("No professional certifications found")

        return strengths, weaknesses

    def _determine_education_level(self, education: List[Dict[str, Any]]) -> str:
        """Determine highest education level"""
        if not education:
            return "Not specified"

        max_score = 0
        result_level = "Not specified"

        for edu in education:
            degree = edu.get('degree', '').lower()
            score = edu.get('score', 0)
            if score > max_score:
                max_score = score
                result_level = degree.title()
            # Also check via education_levels for backward compat
            for level_name, level_value in self.education_levels.items():
                if level_name in degree and DEGREE_SCORES.get(level_name, 0) > max_score:
                    max_score = DEGREE_SCORES.get(level_name, 0)
                    result_level = level_name.title()

        return result_level

    def get_score_interpretation(self, ats_score: float) -> Dict[str, Any]:
        """Get detailed score interpretation"""
        if ats_score >= 90:
            return {
                'level': 'Excellent',
                'recommendation': 'Strongly Recommend - Immediate Interview',
                'color': '#10B981',
                'priority': 'High',
                'description': 'Outstanding match - Top candidate for this position'
            }
        elif ats_score >= 80:
            return {
                'level': 'Very Good',
                'recommendation': 'Recommend - Schedule Interview Soon',
                'color': '#3B82F6',
                'priority': 'High',
                'description': 'Strong candidate - Should advance to next round'
            }
        elif ats_score >= 70:
            return {
                'level': 'Good',
                'recommendation': 'Consider - Worth Interviewing',
                'color': '#F59E0B',
                'priority': 'Medium',
                'description': 'Qualified candidate - May have some gaps'
            }
        elif ats_score >= 60:
            return {
                'level': 'Fair',
                'recommendation': 'Borderline - Review Carefully',
                'color': '#F97316',
                'priority': 'Low',
                'description': 'Potential candidate - Significant gaps exist'
            }
        else:
            return {
                'level': 'Poor',
                'recommendation': 'Do Not Recommend - Reject',
                'color': '#EF4444',
                'priority': 'Very Low',
                'description': 'Does not meet position requirements'
            }

# ==================== GLOBAL ENHANCED ATS SERVICE INSTANCE ====================

enhanced_ats_service = EnhancedATSScoringService()

# ==================== CONVENIENCE FUNCTIONS ====================

def enhanced_parse_resume_file(file_path: str) -> EnhancedResumeParseResult:
    """Parse resume file using enhanced ATS service"""
    return enhanced_ats_service.enhanced_parse_resume(file_path)

def enhanced_score_candidate_ats(
    resume_text: str,
    job_description: str,
    required_skills: List[str],
    required_experience: int,
    required_education: str = "bachelor"
) -> EnhancedATSScoreResult:
    """Score candidate using enhanced ATS service"""
    return enhanced_ats_service.enhanced_score_candidate(
        resume_text, job_description, required_skills,
        required_experience, required_education
    )

def get_enhanced_ats_interpretation(ats_score: float) -> Dict[str, Any]:
    """Get enhanced score interpretation"""
    return enhanced_ats_service.get_score_interpretation(ats_score)


def current_model_versions(llm_model: Optional[str] = None) -> Dict[str, str]:
    """Which models this process scores with, for provenance. Records what was actually
    used: the semantic factor falls back to TF-IDF when sentence-transformers is absent,
    and scores differ between the two."""
    nlp = _NLP if _NLP else None
    versions = {
        "semantic": "all-MiniLM-L6-v2" if SENTENCE_TRANSFORMERS_AVAILABLE else "tfidf",
        "nlp": (f"{nlp.meta.get('name', '?')}-{nlp.meta.get('version', '?')}" if nlp is not None
                and hasattr(nlp, "meta") else "none"),
    }
    if llm_model:
        versions["llm"] = llm_model
    return versions



def _evidence_strengths_weaknesses(required, preferred, edu, exp, certification_score):
    """Strengths and gaps that say what was found, quoting it where possible, and that say
    "not found on the resume" rather than "lacks" — absence of evidence is not proof."""
    strengths, weaknesses = [], []
    shown = [c for c in required if c["level"] == "demonstrated"]
    for c in shown[:3]:
        quote = c["excerpts"][0]["excerpt"] if c["excerpts"] else ""
        strengths.append(f"{c['skill']} used in real work: \u201c{quote}\u201d" if quote else f"{c['skill']} used in real work")
    listed = [c["skill"] for c in required if c["level"] == "listed"]
    if listed:
        strengths.append(f"Lists {', '.join(listed[:5])} (not shown in an experience or project entry)")
    related = [c for c in required if c["level"] == "related"]
    for c in related[:3]:
        weaknesses.append(f"{c['skill']}: not found; has the related {c.get('related_to')}")
    missing = [c["skill"] for c in required if c["level"] == "not_found"]
    if missing:
        weaknesses.append(f"Not found on the resume: {', '.join(missing[:5])}")
    pref_found = [c["skill"] for c in preferred if c["level"] in ("demonstrated", "listed")]
    if pref_found:
        strengths.append(f"Nice-to-haves found: {', '.join(pref_found[:5])}")

    if edu.get("tier") in ("tier_1", "tier_2"):
        strengths.append(f"Graduated from a {edu['tier'].replace('_', '-').title()} institute")
    if edu.get("degree_status") == "met":
        strengths.append("Meets the required degree level")
    elif edu.get("degree_status") == "below":
        weaknesses.append("Degree level is below the role requirement")
    elif edu.get("degree_status") == "unknown":
        weaknesses.append("Degree not stated on the resume: verify")
    if edu.get("cgpa_status") == "unknown":
        weaknesses.append("CGPA not stated on the resume (not counted)")

    status = exp.get("relevant_status")
    if status == "met":
        strengths.append(f"About {exp.get('years_found'):g} years of experience, meeting the {exp.get('required_years')} required")
    elif status == "below":
        weaknesses.append(f"About {exp.get('years_found'):g} years of experience; {exp.get('required_years')} required")
    elif status == "unknown":
        weaknesses.append("Years of experience not stated on the resume: verify")
    if exp.get("internship_count"):
        strengths.append(f"{exp['internship_count']} internship(s) listed")
    if exp.get("has_github"):
        strengths.append("GitHub profile linked")
    if exp.get("has_hackathon"):
        strengths.append("Hackathon participation described")
    if certification_score >= 60:
        strengths.append("Holds relevant professional certifications")
    return strengths, weaknesses
