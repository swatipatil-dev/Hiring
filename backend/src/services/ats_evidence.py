"""Supporting resume excerpts for each ATS factor score.

``scoring_breakdown`` already reports that a candidate scored 72 on skills.
This module reports *why*: the lines of their own resume that earned it. A
recruiter defending a shortlist quotes the resume, not the number.

Nothing here can move a score. Every excerpt is a verbatim span of the resume
text the scorer already processed, located from values
``enhanced_score_candidate`` has finished computing. Evidence collection is
purely additive, and the caller treats a failure here as "no excerpts" rather
than a failed scoring run.

This module deliberately imports nothing from ``enhanced_ats_service`` — that
module imports this one, and the skill normaliser it needs arrives as the
``normalize`` argument instead.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.services.ats_text import whole_word_pattern

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
