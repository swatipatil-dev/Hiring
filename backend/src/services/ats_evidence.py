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
from typing import Dict, List, Optional, Tuple

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
