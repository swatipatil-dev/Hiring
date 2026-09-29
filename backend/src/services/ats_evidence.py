"""Evidence behind a resume score: what was found, where, and in the resume's own words.

The scorer used to count a skill if its name appeared anywhere, gave the most credit to a
bare skills list, awarded points for the words "github", "intern" or "hackathon" wherever
they appeared, and scored anything it could not find (years of experience, a degree, a
CGPA) as zero — indistinguishable from "does not meet the requirement". This module
replaces those rules:

- Each required or preferred skill is looked for in the full resume text, by its name and
  known aliases, on word boundaries (so "java" is not found inside "javascript"). Where it
  is found decides the credit: DEMONSTRATED in an experience or project entry counts most,
  LISTED only in a skills list counts less, a RELATED skill from the same family counts
  partially, and NOT FOUND counts nothing. Every finding carries a short excerpt.
- Education and experience components are met / not met / UNKNOWN. Unknown components
  are left out of the calculation (the remaining ones are re-weighted) and shown as
  "not stated on the resume — verify", instead of silently scoring zero.
- Internships, GitHub, open source and hackathons are credited only from real evidence:
  an internship entry, a github.com profile URL, a line in an experience/project/
  achievement section.

Pure functions, deterministic, no I/O.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.services.ats_text import whole_word_pattern

EVIDENCE_CREDIT = {"demonstrated": 1.0, "listed": 0.75, "related": 0.5, "not_found": 0.0}
DEMONSTRATION_SECTIONS = ("experience", "projects")
ACHIEVEMENT_SECTIONS = ("hobbies",)          # the "hobbies" key also covers achievements/awards
EXCERPT_MAX_CHARS = 180
EXCERPTS_PER_ITEM = 2
EXPLANATION_MAX_BYTES = 24_000
FULL_TEXT_MAX_CHARS = 30_000
LOW_COVERAGE = 0.5

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
_URL = re.compile(r"https?://\S+|www\.\S+")
_GITHUB_PROFILE = re.compile(r"github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}", re.I)
_INTERN = re.compile(r"\bintern(ship)?s?\b", re.I)
_OSS = re.compile(r"\bopen[- ]source\b", re.I)
_HACKATHON = re.compile(r"\bhackathons?\b", re.I)


# ── text units ───────────────────────────────────────────────────────────────

def section_units(sections: Dict[str, str]) -> List[Tuple[str, str]]:
    """(section, unit) pairs: each non-empty line, with long lines split into sentences."""
    out = []
    for name, body in (sections or {}).items():
        for line in (body or "").split("\n"):
            line = line.strip(" \t-•*·")
            if not line:
                continue
            parts = re.split(r"(?<=[.;])\s+(?=[A-Z])", line) if len(line) > 240 else [line]
            out.extend((name, p.strip()) for p in parts if p.strip())
    return out


def _redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    return _URL.sub(lambda m: m.group(0) if "github.com" in m.group(0).lower() else "[link]", text)


def make_excerpt(unit: str, start: int, end: int) -> str:
    """A window of the unit around [start, end), at most EXCERPT_MAX_CHARS, contacts redacted."""
    if len(unit) <= EXCERPT_MAX_CHARS:
        return _redact(unit)
    half = (EXCERPT_MAX_CHARS - (end - start)) // 2
    a = max(0, start - half)
    b = min(len(unit), a + EXCERPT_MAX_CHARS)
    a = max(0, b - EXCERPT_MAX_CHARS)
    snippet = unit[a:b].strip()
    return _redact(("…" if a > 0 else "") + snippet + ("…" if b < len(unit) else ""))


# ── skills ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _reverse_aliases() -> Dict[str, List[str]]:
    from src.services.enhanced_ats_service import ALIAS_MAP
    rev: Dict[str, List[str]] = {}
    for alias, canonical in ALIAS_MAP.items():
        rev.setdefault(str(canonical).lower(), []).append(str(alias).lower())
    return rev


@lru_cache(maxsize=4096)
def skill_pattern(skill: str) -> re.Pattern:
    """The skill, its canonical form and aliases, matched as whole terms.

    Look-arounds instead of \\b so terms like c++, c#, .net and node.js work, and so
    "java" does not match inside "javascript".
    """
    from src.services.enhanced_ats_service import normalize_skill
    canonical = normalize_skill(skill).lower()
    variants = {skill.lower().strip(), canonical}
    variants.update(_reverse_aliases().get(canonical, []))
    variants = sorted((v for v in variants if v), key=len, reverse=True)
    alt = "|".join(re.escape(v) for v in variants)
    return re.compile(rf"(?<![\w+#.])(?:{alt})(?![\w+#]|\.[a-z])", re.I)


def classify_skill(skill: str, units: List[Tuple[str, str]], extracted: set) -> dict:
    """Where (if anywhere) the resume shows this skill, with excerpts."""
    from src.services.enhanced_ats_service import _find_cluster_match
    pat = skill_pattern(skill)
    demonstrated, listed = [], []
    for section, unit in units:
        m = pat.search(unit)
        if not m:
            continue
        item = {"section": section, "excerpt": make_excerpt(unit, m.start(), m.end())}
        (demonstrated if section in DEMONSTRATION_SECTIONS else listed).append(item)
    if demonstrated:
        return {"skill": skill, "level": "demonstrated", "excerpts": demonstrated[:EXCERPTS_PER_ITEM]}
    if listed:
        return {"skill": skill, "level": "listed", "excerpts": listed[:1]}
    related = _find_cluster_match(skill.lower(), extracted)
    if related:
        rel_pat = skill_pattern(related)
        ex = next(({"section": s, "excerpt": make_excerpt(u, m.start(), m.end())}
                   for s, u in units for m in [rel_pat.search(u)] if m), None)
        return {"skill": skill, "level": "related", "related_to": related, "excerpts": [ex] if ex else []}
    return {"skill": skill, "level": "not_found", "excerpts": []}


def skills_score(required: List[dict], preferred: List[dict]) -> Tuple[float, dict]:
    """Skills sub-score (0-100) = required(70) + preferred(30), credit by evidence level.
    With no preferred skills, required carries the full 100 (as before)."""
    def pct(checks):
        return sum(EVIDENCE_CREDIT[c["level"]] for c in checks) / len(checks) if checks else 1.0
    req = pct(required)
    if preferred:
        req_sub, pref_sub = round(70.0 * req, 1), round(30.0 * pct(preferred), 1)
        score = round(req_sub + pref_sub, 2)
    else:
        req_sub, pref_sub = round(70.0 * req, 1), None
        score = round(100.0 * req, 2)
    counts = {lvl: sum(1 for c in required if c["level"] == lvl) for lvl in EVIDENCE_CREDIT}
    return score, {
        "required_skills": req_sub, "required_max": 70,
        "preferred_skills": pref_sub, "preferred_max": 30,
        "has_preferred": bool(preferred), "evidence_counts": counts,
    }


# ── experience ───────────────────────────────────────────────────────────────

_YEAR_MENTION = [
    re.compile(r"(\d+(?:\.\d+)?)\+?\s*years?\s*(?:of\s+)?(?:experience|work|professional|industry)", re.I),
    re.compile(r"experience\s*(?:of\s+)?(\d+(?:\.\d+)?)\+?\s*years?", re.I),
]
_DATE_RANGE = re.compile(r"(19|20)\d{2}\s*[-–—to]+\s*((19|20)\d{2}|present|current|now|till date|to date)", re.I)


def experience_years_evidence(units, extracted_years: Optional[float],
                              known_years: Optional[float]) -> Tuple[Optional[float], str, List[dict]]:
    """(years or None, how it was found, excerpts). None means NOT STATED — not zero."""
    if known_years is not None:
        return float(known_years), "profile", []
    excerpts = []
    for section, unit in units:
        for p in _YEAR_MENTION:
            m = p.search(unit)
            if m:
                excerpts.append({"section": section, "excerpt": make_excerpt(unit, m.start(), m.end())})
                break
        else:
            if section == "experience":
                m = _DATE_RANGE.search(unit)
                if m:
                    excerpts.append({"section": section, "excerpt": make_excerpt(unit, m.start(), m.end())})
    if not excerpts:
        return None, "not_found", []
    return float(extracted_years or 0.0), "resume", excerpts[:EXCERPTS_PER_ITEM]


def experience_score(years: Optional[float], required_years: int, work_experiences, links_text: str,
                     units) -> Tuple[Optional[float], dict]:
    """Experience (0-100) = relevant(50) + internships(25) + github/OSS/hackathons(25).
    Relevant experience is UNKNOWN when years are not stated; it is then excluded and the
    remaining 50 points are scaled to 100."""
    parts = {}
    if not required_years or required_years <= 0:
        rel, rel_status = 50.0, "not_required"
    elif years is None:
        rel, rel_status = None, "unknown"
    else:
        ratio = years / required_years
        rel = 50.0 if ratio >= 1 else 40.0 if ratio >= 0.75 else 30.0 if ratio >= 0.5 else round(max(0.0, ratio) * 50, 1)
        rel_status = "met" if ratio >= 1 else "below"
    parts["relevant"] = (rel, 50)

    intern_ex = []
    for w in work_experiences or []:
        text = f"{w.get('employment_type') or ''} {w.get('job_title') or ''}"
        if _INTERN.search(text):
            intern_ex.append({"section": "experience", "excerpt": _redact(text.strip())})
    if not intern_ex:
        for section, unit in units:
            if section in DEMONSTRATION_SECTIONS:
                m = _INTERN.search(unit)
                if m:
                    intern_ex.append({"section": section, "excerpt": make_excerpt(unit, m.start(), m.end())})
    intern_count = min(len(intern_ex), 2)
    parts["internships"] = (float(min(intern_count * 15, 25)), 25)

    gh = None
    m = _GITHUB_PROFILE.search(links_text or "")
    if m:
        gh = {"section": "links", "excerpt": m.group(0)}
    else:
        for section, unit in units:
            m = _GITHUB_PROFILE.search(unit)
            if m:
                gh = {"section": section, "excerpt": make_excerpt(unit, m.start(), m.end())}
                break
    evidence_units = [(s, u) for s, u in units if s in DEMONSTRATION_SECTIONS + ACHIEVEMENT_SECTIONS]
    oss = next(({"section": s, "excerpt": make_excerpt(u, m.start(), m.end())}
                for s, u in evidence_units for m in [_OSS.search(u)] if m), None)
    hack = next(({"section": s, "excerpt": make_excerpt(u, m.start(), m.end())}
                 for s, u in evidence_units for m in [_HACKATHON.search(u)] if m), None)
    parts["github_oss_hackathons"] = (min(25.0, (15.0 if gh else 0) + (5.0 if oss else 0) + (5.0 if hack else 0)), 25)

    known = [(v, mx) for v, mx in parts.values() if v is not None]
    score = round(100.0 * sum(v for v, _ in known) / sum(mx for _, mx in known), 2) if known else None
    return score, {
        "relevant_experience": rel, "relevant_max": 50, "relevant_status": rel_status,
        "years_found": years, "required_years": required_years,
        "internships": parts["internships"][0], "internships_max": 25, "internship_count": intern_count,
        "internship_evidence": intern_ex[:EXCERPTS_PER_ITEM],
        "github_oss_hackathons": parts["github_oss_hackathons"][0], "github_oss_max": 25,
        "has_github": gh is not None, "has_oss": oss is not None, "has_hackathon": hack is not None,
        "github_evidence": gh, "oss_evidence": oss, "hackathon_evidence": hack,
    }


# ── education ────────────────────────────────────────────────────────────────

def education_score(degree_sub: float, cand_degree: float, tier: Optional[str], has_institution: bool,
                    cgpa_sub: float, cgpa_found: bool, units) -> Tuple[Optional[float], dict]:
    """Education (0-100) = degree(40) + tier(30) + CGPA(30), each met / below / unknown.
    Unknown parts are excluded and the rest scaled; all unknown → None (factor excluded)."""
    from src.services.enhanced_ats_service import DEGREE_TITLES_SET
    parts, status = {}, {}
    if cand_degree and cand_degree > 0:
        parts["degree"] = (degree_sub, 40)
        status["degree"] = "met" if degree_sub >= 40 else "below"
    else:
        status["degree"] = "unknown"
    if tier:
        parts["tier"] = ((30.0 if tier == "tier_1" else 20.0 if tier == "tier_2" else 0.0), 30)
        status["tier"] = tier
    elif has_institution:
        parts["tier"] = (0.0, 30)
        status["tier"] = "not_listed"
    else:
        status["tier"] = "unknown"
    if cgpa_found:
        parts["cgpa"] = (cgpa_sub, 30)
        status["cgpa"] = "found"
    else:
        status["cgpa"] = "unknown"

    degree_ex = None
    titles = sorted(DEGREE_TITLES_SET, key=len, reverse=True)
    deg_pat = re.compile(r"(?<![\w.])(?:" + "|".join(re.escape(t) for t in titles) + r")(?![\w])", re.I)
    for section, unit in units:
        if section == "education":
            m = deg_pat.search(unit)
            if m:
                degree_ex = {"section": section, "excerpt": make_excerpt(unit, m.start(), m.end())}
                break

    score = round(100.0 * sum(v for v, _ in parts.values()) / sum(mx for _, mx in parts.values()), 2) \
        if parts else None
    return score, {
        "degree_match": parts.get("degree", (None,))[0], "degree_max": 40, "degree_status": status["degree"],
        "degree_evidence": degree_ex,
        "college_tier": parts.get("tier", (None,))[0], "college_tier_max": 30, "tier": tier,
        "tier_status": status["tier"],
        "cgpa": parts.get("cgpa", (None,))[0], "cgpa_max": 30, "cgpa_found": cgpa_found,
        "cgpa_status": status["cgpa"],
    }


# ── composite + explanation ─────────────────────────────────────────────────

FACTOR_LABELS = {
    "education": "Education", "skills_match": "Skills", "experience": "Experience",
    "certification": "Certifications", "semantic": "Overall relevance", "project": "Projects",
    "keyword": "Job-description keywords",
}


def combine(factor_scores: Dict[str, Optional[float]], weights: Dict[str, float]) -> Tuple[float, float, list]:
    """Weighted composite over the factors with evidence; unknown ones are dropped and the
    rest re-weighted. Returns (score, evidence_coverage, factor rows)."""
    total_w = sum(weights[k] for k in factor_scores)
    known_w = sum(weights[k] for k, v in factor_scores.items() if v is not None)
    rows, score = [], 0.0
    for k, v in factor_scores.items():
        eff = (weights[k] / known_w) if (v is not None and known_w) else 0.0
        raw = v * eff if v is not None else 0.0
        score += raw                      # summed unrounded; only the display is rounded
        contribution = round(raw, 2)
        rows.append({"key": k, "label": FACTOR_LABELS.get(k, k), "score": v, "weight": weights[k],
                     "effective_weight": round(eff, 4), "contribution": contribution,
                     "status": "scored" if v is not None else "unknown_excluded"})
    coverage = round(known_w / total_w, 3) if total_w else 0.0
    return round(score, 2), coverage, rows


def cap_explanation(expl: dict, max_bytes: int = EXPLANATION_MAX_BYTES) -> dict:
    """Trim excerpts until the JSON fits: second excerpts first, then shorten, then drop."""
    def size():
        return len(json.dumps(expl, ensure_ascii=False).encode("utf-8"))

    def items():
        for group in ("mandatory", "preferred"):
            yield from (expl.get(group) or {}).get("items", [])

    if size() <= max_bytes:
        return expl
    for it in items():
        it["excerpts"] = it.get("excerpts", [])[:1]
    if size() <= max_bytes:
        return expl
    for it in items():
        for ex in it.get("excerpts", []):
            if ex and len(ex.get("excerpt", "")) > 100:
                ex["excerpt"] = ex["excerpt"][:99] + "…"
    if size() <= max_bytes:
        return expl
    for it in (expl.get("preferred") or {}).get("items", []):
        it["excerpts"] = []
    if size() <= max_bytes:
        return expl
    for it in (expl.get("mandatory") or {}).get("items", [])[5:]:
        it["excerpts"] = []
    return expl

# ══════════════════════════════════════════════════════════════════════════
# PER-FACTOR RESUME EXCERPTS — collect_evidence()
#
# Supporting resume excerpts for each ATS factor score.
#
# ``scoring_breakdown`` already reports that a candidate scored 72 on skills.
# This module reports *why*: the lines of their own resume that earned it. A
# recruiter defending a shortlist quotes the resume, not the number.
#
# Nothing here can move a score. Every excerpt is a verbatim span of the resume
# text the scorer already processed, located from values
# ``enhanced_score_candidate`` has finished computing. Evidence collection is
# purely additive, and the caller treats a failure here as "no excerpts" rather
# than a failed scoring run.
#
# This module deliberately imports nothing from ``enhanced_ats_service`` — that
# module imports this one, and the skill normaliser it needs arrives as the
# ``normalize`` argument instead.
# ══════════════════════════════════════════════════════════════════════════

# A factor shows at most this many excerpts. Recruiters skim, so the first few
# lines of proof are the ones that get read; uncapped, a skills-heavy resume
# turns the dashboard expander into a wall of text.
MAX_EXCERPTS_PER_FACTOR = 4

# Long enough for a bullet plus its context, short enough to sit in a card.
MAX_QUOTE_CHARS = 240

# A sentence shorter than this is a heading or a fragment, not evidence.
MIN_SENTENCE_CHARS = 40

_WS_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_LEADING_MARKER_RE = re.compile(r"^[\s\-•▪◦‣*|:]+")

# Words that survive a 4-character filter but say nothing about a role. Kept
# small on purpose: this only ranks which keyword to *quote*, so a miss costs
# a slightly duller excerpt, never a wrong score.
_KEYWORD_STOPWORDS = frozenset("""
this that with from have has had will would should could their there these those
your you our are was were been being able about into over under more most other
such than then them they when what which while who whom whose role work works
team teams year years plus etc and the for
""".split())


def _tidy(text: str) -> str:
    """Collapse whitespace, strip list markers, and cap a quote's length."""
    cleaned = _WS_RE.sub(" ", text or "").strip()
    cleaned = _LEADING_MARKER_RE.sub("", cleaned).strip()
    if len(cleaned) > MAX_QUOTE_CHARS:
        cleaned = cleaned[:MAX_QUOTE_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return cleaned


def _resume_lines(text: str) -> List[str]:
    """Non-blank resume lines, stripped, in document order."""
    return [ln.strip() for ln in (text or "").split("\n") if ln.strip()]


def _section_of(sections: Optional[Dict[str, str]], line: str) -> str:
    """Name the resume section a line sits in, or "" when unattributable.

    ``detect_sections`` stores each section's body as its stripped lines joined
    by newlines, so a stripped line is a plain substring of its own section.
    """
    if not line or not sections:
        return ""
    for name, body in sections.items():
        if body and line in body:
            return name
    return ""


def _find_line(lines: Sequence[str], needle: str, *, whole_word: bool = False) -> Optional[str]:
    """The shortest resume line containing ``needle``, case-insensitively.

    Shortest wins because a skill named on its own bullet proves more, and
    reads better as a quote, than the same skill buried in a dense paragraph.

    ``whole_word`` matters more than it looks: skills and keywords are matched
    this way so that "R" does not match "your" and "go" does not match "google".
    """
    term = (needle or "").strip()
    if not term:
        return None
    if whole_word:
        # Shared with the scorer, so a credited skill and a quoted skill are
        # always decided by the same rule (src/services/ats_text.py).
        pattern = whole_word_pattern(term)
        if pattern is None:
            return None
        hits = [ln for ln in lines if pattern.search(ln)]
    else:
        lowered = term.lower()
        hits = [ln for ln in lines if lowered in ln.lower()]
    return min(hits, key=len) if hits else None


def _excerpt(line: str, sections: Optional[Dict[str, str]], supports: str) -> Optional[Dict[str, str]]:
    """Build one excerpt record, or None when the line carries no usable text."""
    quote = _tidy(line)
    if not quote:
        return None
    return {"quote": quote, "section": _section_of(sections, (line or "").strip()), "supports": supports}


def _is_heading(line: str) -> bool:
    """True for a bare section header, which is a label rather than evidence."""
    stripped = (line or "").strip().rstrip(":")
    if not stripped:
        return True
    words = stripped.split()
    return len(words) <= 2 and (stripped.isupper() or stripped.istitle())


def _append(
    bucket: List[Dict[str, str]],
    seen: Dict[str, Dict[str, str]],
    excerpt: Optional[Dict[str, str]],
) -> None:
    """Add an excerpt, merging into an existing quote of the same line.

    One resume line routinely proves several things at once — a skills line
    naming four required skills is the common case. Merging keeps all four
    facts while quoting the line once; the earlier behaviour of dropping the
    duplicate silently lost every skill after the first.
    """
    if not excerpt:
        return
    key = excerpt["quote"].lower()
    existing = seen.get(key)
    if existing is not None:
        if excerpt["supports"] not in existing["supports"]:
            existing["supports"] = f"{existing['supports']}; {excerpt['supports']}"
        return
    # Checked only for genuinely new quotes, so a full factor still merges
    # further facts into the lines it is already showing.
    if len(bucket) >= MAX_EXCERPTS_PER_FACTOR:
        return
    seen[key] = excerpt
    bucket.append(excerpt)


def _rank_jd_keywords(job_description: str) -> List[str]:
    """JD words worth quoting, most role-central first.

    Ranked by frequency in the JD: a word the posting repeats is what the role
    is actually about. The scorer's own keyword factor counts every word of 3+
    characters with fuzzy matching; this is a *presentation* ranking over that
    same vocabulary, not a recomputation of the score.
    """
    words = re.findall(r"\b[a-zA-Z]{4,}\b", (job_description or "").lower())
    counts: Dict[str, int] = {}
    for word in words:
        if word in _KEYWORD_STOPWORDS:
            continue
        counts[word] = counts.get(word, 0) + 1
    return sorted(counts, key=lambda w: (-counts[w], -len(w), w))


def _top_semantic_sentences(resume_text: str, job_description: str, limit: int) -> List[str]:
    """Resume sentences with the most lexical overlap with the JD.

    The semantic factor itself scores with sentence-transformer embeddings when
    they are available, and an embedding is not decomposable into the sentences
    that produced it. So these are ranked by TF-IDF cosine instead: they
    illustrate *where* the resume meets the JD rather than reproducing the
    factor's arithmetic, and the UI labels them accordingly.
    """
    sentences = [
        s.strip() for s in _SENTENCE_RE.split(resume_text or "")
        if len(s.strip()) >= MIN_SENTENCE_CHARS
    ]
    if not sentences or not (job_description or "").strip():
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        matrix = TfidfVectorizer(stop_words="english", max_features=2000).fit_transform(
            [job_description] + sentences
        )
        sims = cosine_similarity(matrix[0:1], matrix[1:])[0]
    except Exception:
        # sklearn missing, or a degenerate vocabulary (an all-stopword input
        # raises). Evidence is optional; fall back to document order.
        return sentences[:limit]
    ranked = sorted(zip(sentences, sims), key=lambda pair: pair[1], reverse=True)
    return [sentence for sentence, sim in ranked[:limit] if sim > 0]


def collect_evidence(
    *,
    resume_text: str,
    sections: Optional[Dict[str, str]] = None,
    job_description: str = "",
    matched_skills: Sequence[str] = (),
    cluster_matched_skills: Sequence[Dict[str, str]] = (),
    preferred_skills: Sequence[str] = (),
    resume_skills: Sequence[str] = (),
    education: Sequence[Dict[str, Any]] = (),
    work_experience: Sequence[Dict[str, Any]] = (),
    certifications: Sequence[str] = (),
    projects: Sequence[Dict[str, Any]] = (),
    normalize: Optional[Callable[[str], str]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Map each ATS factor key to the resume excerpts supporting its score.

    Keys match ``scoring_breakdown['individual_scores']``. A factor with no
    locatable proof maps to an empty list, which is itself worth showing: a
    score with nothing quotable behind it is one a recruiter should not lean on.

    Each excerpt is ``{"quote", "section", "supports"}`` — the resume's own
    words, the section they came from, and what they are evidence *of*.
    """
    lines = _resume_lines(resume_text)
    evidence: Dict[str, List[Dict[str, str]]] = {
        key: [] for key in
        ("education", "skills_match", "experience", "certification", "semantic", "project", "keyword")
    }
    seen: Dict[str, Dict[str, Dict[str, str]]] = {key: {} for key in evidence}

    def add(factor: str, line: Optional[str], supports: str) -> None:
        if line:
            _append(evidence[factor], seen[factor], _excerpt(line, sections, supports))

    def normalised(value: Any) -> str:
        """Best-effort normalisation; a normaliser failure must not lose evidence."""
        try:
            return normalize(str(value)).lower() if normalize else str(value).lower()
        except Exception:
            return str(value).lower()

    # Skills — the scorer matches on normalised forms ("javascript"), but the
    # resume may only ever write the alias ("JS"), so map back to the spelling
    # actually on the page before going looking for it.
    raw_by_normalised: Dict[str, str] = {}
    for raw in resume_skills or ():
        raw_by_normalised.setdefault(normalised(raw), str(raw))

    for skill in matched_skills or ():
        spelling = raw_by_normalised.get(str(skill).lower(), str(skill))
        line = (
            _find_line(lines, spelling, whole_word=True)
            or _find_line(lines, str(skill), whole_word=True)
        )
        add("skills_match", line, f"Required skill: {spelling}")

    for hit in cluster_matched_skills or ():
        if not isinstance(hit, dict):
            continue
        found = str(hit.get("found", ""))
        required = str(hit.get("required", ""))
        spelling = raw_by_normalised.get(found.lower(), found)
        add(
            "skills_match",
            _find_line(lines, spelling, whole_word=True),
            f"Required skill {required} — matched via related skill {spelling}",
        )

    for skill in preferred_skills or ():
        key = normalised(skill)
        if key not in raw_by_normalised:
            continue  # candidate does not have it; there is nothing to quote
        spelling = raw_by_normalised[key]
        add("skills_match", _find_line(lines, spelling, whole_word=True), f"Preferred skill: {spelling}")

    # Education — each entry carries the degree keyword that matched, which is
    # the substring to hunt for in the resume.
    for entry in education or ():
        if not isinstance(entry, dict):
            continue
        degree = str(entry.get("degree", "")).strip()
        if not degree:
            continue
        label = "Degree" if entry.get("score") else "Institution"
        # Whole-word only. ``extract_education_info`` matches degree keywords as
        # bare substrings, so it reports an "mba" for anyone who studied in
        # Bombay and a "be" for anyone who lists Kubernetes. Quoting the line
        # that "proved" such a degree would dress up a parser artefact as
        # evidence, so a phantom degree simply yields no excerpt — and a factor
        # scoring well with nothing quotable behind it is the signal to look.
        add("education", _find_line(lines, degree, whole_word=True), f"{label}: {degree}")

    # Experience — quote the resume line an entry came from, not the parser's
    # reconstruction of it.
    for entry in work_experience or ():
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("extracted_text") or entry.get("text") or "").strip()
        title = str(entry.get("title") or "").strip()
        company = str(entry.get("company") or "").strip()
        line = (_find_line(lines, raw) if raw else None) or raw or None
        if not line and company:
            line = _find_line(lines, company)
        role = " at ".join(part for part in (title, company) if part) or "Work history entry"
        months = entry.get("duration_months")
        if isinstance(months, (int, float)) and months > 0:
            role = f"{role} ({int(months)} months)"
        add("experience", line, role)

    for cert in certifications or ():
        name = str(cert).strip()
        if name:
            add("certification", _find_line(lines, name) or name, f"Certification: {name}")

    for project in projects or ():
        if not isinstance(project, dict):
            continue
        raw = str(project.get("extracted_text") or project.get("name") or "").strip()
        if raw:
            add("project", _find_line(lines, raw) or raw, f"Project: {_tidy(raw)}")

    for word in _rank_jd_keywords(job_description):
        if len(evidence["keyword"]) >= MAX_EXCERPTS_PER_FACTOR:
            break
        line = _find_line(lines, word, whole_word=True)
        # A JD keyword that only appears as the resume's own section header
        # ("EXPERIENCE") is an artefact of the layout, not a match worth showing.
        if not line or _is_heading(line):
            continue
        add("keyword", line, f"JD keyword: {word}")

    for sentence in _top_semantic_sentences(resume_text, job_description, MAX_EXCERPTS_PER_FACTOR):
        add("semantic", sentence, "Closest match to the job description")

    return evidence
