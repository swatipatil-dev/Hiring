"""
LLM Profile Section Extractor  (candidate resume auto-fill)
-----------------------------------------------------------
Step 1 — the section-anchored regex extractors in ``candidate_profile_routes`` read
          work history, education, certifications and projects off the resume.
Step 2 — Gemini fills the sections they could not actually read.

Same contract as ``llm_resume_extractor`` (the Talent Pool gap-filler), applied to
list-shaped sections instead of flat scalar fields:

  * The model is offered a section only when the parser produced **nothing**, or
    produced rows that are demonstrably junk (see ``fillable_sections``). A section
    that genuinely parsed is never touched — merging model output into a real list
    would leave the candidate unable to tell which rows came off their own resume,
    and a duplicated job is worse than a missing one.
  * Replacement is all-or-nothing per section, never a merge, for the same reason.
  * Every item is validated against the real column widths before it is offered.
  * Any failure returns {} and the candidate simply sees the regex result.

Nothing here is persisted. ``/import/parse`` writes nothing; the candidate reviews,
edits and ticks each item, and only then does ``/import/apply`` insert rows.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from src.services.llm_resume_extractor import (
    LLM_RESUME_TEXT_CHARS,
    _clean_str,
    _enabled,
    _parse_json,
)

logger = logging.getLogger(__name__)


SECTIONS = ("work_experience", "education", "certifications", "projects")

# Per-section item caps, matching the regex helpers in candidate_profile_routes so a
# runaway response cannot flood the review form with more rows than the parser could.
_CAPS = {
    "work_experience": 15,
    "education": 10,
    "certifications": 20,
    "projects": 15,
}

# Column widths on the tables /import/apply writes into (models.py). A value that
# would be truncated by MySQL is rejected here rather than silently shortened.
_W_TITLE = 255          # candidate_work_experience.job_title
_W_COMPANY = 255        # candidate_work_experience.company_name
_W_DEGREE = 100         # candidate_education.degree
_W_INSTITUTION = 255    # candidate_education.institution_name
_W_CERT = 255           # candidate_certifications.certificate_name
_W_PROJECT = 255        # candidate_projects.project_title

# Model-inferred: plausible but unverified, so it sits below the review screen's
# pre-tick threshold and the candidate opts in deliberately. Same value the route
# gives its own unanchored extractions — see CONF_INFERRED in
# candidate_profile_routes, which documents the scale.
_LLM_CONFIDENCE = 0.5

_ASK = {
    "work_experience":
        '"work_experience": [{"title": job title, "company": employer name}] — '
        'every role held, most recent first',
    "education":
        '"education": [{"degree": qualification, "institution": school or college name}] — '
        'every qualification',
    "certifications":
        '"certifications": [certificate name as a plain string] — professional '
        'certifications only, not degrees or skills',
    "projects":
        '"projects": [{"name": project title}] — personal, academic or side projects',
}


# ── What counts as "the parser didn't really read this" ───────────────────────
# A section can be present and still be unusable. These are the failure shapes the
# regex extractors actually produce on a resume with no recognisable headings —
# observed on real output, not hypothetical:
#
#   certifications: unrelated lines glued together, e.g.
#     "Certified\nOutside of work I keep a couple of things\nwritten in Python..."
#   education:      qualification fragments scraped out of prose with no institution,
#     e.g. degree="be" / "associate" with institution=""
#
# Replacing such a section is not "topping up" — there is nothing there worth
# preserving. The replacement is still all-or-nothing, and still badged as AI in the
# review screen and unticked, so parser output stays distinguishable from model
# output. And if the model returns nothing that survives validation, the section is
# left exactly as the parser produced it.

_MAX_CERT_WORDS = 12          # a certification name; longer reads as a sentence
_MAX_CERT_CHARS = 200
_MAX_PROJECT_CHARS = 80
# Bare qualification words carrying no information on their own.
_DEGREE_STOPWORDS = {
    "be", "b", "me", "ba", "bs", "ms", "msc", "bsc", "associate", "bachelor",
    "bachelors", "master", "masters", "diploma", "degree", "graduate", "graduation",
}
# A section is treated as unread when at least this share of its rows are junk. Not
# "every row": the observed breakage mixes one plausible value in with fragments, and
# demanding unanimity would let that one row protect the rest. Not "any row" either —
# a single bad row among good ones is the candidate's to delete, not ours to discard.
_JUNK_SHARE = 0.5


def _work_is_weak(item: Any) -> bool:
    if not isinstance(item, dict):
        return True
    return not (item.get("company") or "").strip()


def _education_is_weak(item: Any) -> bool:
    if not isinstance(item, dict):
        return True
    if (item.get("institution") or "").strip():
        return False          # an institution name is real information; keep it
    degree = (item.get("degree") or "").strip().strip(".").lower()
    return (not degree) or degree in _DEGREE_STOPWORDS or len(degree) <= 3


def _certification_is_weak(item: Any) -> bool:
    if not isinstance(item, str):
        return True
    value = item.strip()
    if not value or "\n" in value:            # glued-together lines
        return True
    return len(value) > _MAX_CERT_CHARS or len(value.split()) > _MAX_CERT_WORDS


def _project_is_weak(item: Any) -> bool:
    name = item.get("name") if isinstance(item, dict) else item
    if not isinstance(name, str) or not name.strip():
        return True
    value = name.strip()
    # A lowercase opener means a wrapped mid-sentence fragment, not a project title.
    return "\n" in value or len(value) > _MAX_PROJECT_CHARS or value[:1].islower()


_IS_WEAK = {
    "work_experience": _work_is_weak,
    "education": _education_is_weak,
    "certifications": _certification_is_weak,
    "projects": _project_is_weak,
}


def missing_sections(parsed: Optional[Dict[str, Any]]) -> List[str]:
    """Sections the regex extractors produced nothing at all for."""
    parsed = parsed or {}
    return [s for s in SECTIONS if not parsed.get(s)]


def broken_sections(parsed: Optional[Dict[str, Any]]) -> List[str]:
    """Sections that produced rows, but rows that are mostly junk."""
    parsed = parsed or {}
    out = []
    for section in SECTIONS:
        items = parsed.get(section) or []
        if not items:
            continue                      # empty is missing_sections' business
        weak = sum(1 for i in items if _IS_WEAK[section](i))
        if weak / len(items) >= _JUNK_SHARE:
            out.append(section)
    return out


def fillable_sections(parsed: Optional[Dict[str, Any]]) -> List[str]:
    """Sections the model may be asked about: empty ones, and unusable ones.

    A section that genuinely parsed is never offered here, which is what keeps the
    model from quietly appending to a list the candidate can see came off their CV.
    """
    empty = set(missing_sections(parsed))
    broken = set(broken_sections(parsed))
    return [s for s in SECTIONS if s in empty or s in broken]


def _build_prompt(text: str, missing: List[str]) -> str:
    wanted = "\n".join(f"  {_ASK[s]}" for s in missing)
    return f"""You are extracting factual sections from a resume. Extract ONLY these:

{wanted}

Rules:
- Copy values verbatim from the resume. Do not rephrase, expand or normalise them.
- Include an entry only if the resume actually states it. Never guess, never infer
  from context, and never use any outside knowledge about a company or school.
- If the resume contains nothing for a section, return an empty list for it.
- Do not return any section that is not listed above.
- Reply with a single JSON object and nothing else. No prose, no markdown fence.

RESUME:
{text[:LLM_RESUME_TEXT_CHARS]}

JSON:"""


def _name_of(raw: Any, *keys: str) -> Optional[str]:
    """Pull a name out of either a bare string or an object.

    Models answer a list of plain names about as often as a list of objects, however
    the example is written — accepting both costs nothing and saves a retry.
    """
    if isinstance(raw, str):
        return _clean_str(raw)
    if isinstance(raw, dict):
        for k in keys:
            got = _clean_str(raw.get(k))
            if got:
                return got
    return None


def _pair(raw: Any, key_a: str, max_a: int, key_b: str, max_b: int) -> Optional[Dict[str, Any]]:
    """Validate a two-field object (title/company, degree/institution).

    Kept if at least one side survives — a job with a title but no employer is still
    worth offering, and the candidate can type the rest.
    """
    if not isinstance(raw, dict):
        return None
    a = _clean_str(raw.get(key_a))
    b = _clean_str(raw.get(key_b))
    if a and len(a) > max_a:
        a = None
    if b and len(b) > max_b:
        b = None
    if not a and not b:
        return None
    # `confidence` matches the shape the parser emits, so the review screen's existing
    # threshold logic reads a real number rather than undefined.
    return {key_a: a or "", key_b: b or "", "confidence": _LLM_CONFIDENCE}


def _validate(data: Dict[str, Any], missing: List[str]) -> Dict[str, List[Any]]:
    """Keep only sections that were asked for, and only items safe to store.

    The `missing` filter is what makes "fill empty sections only" structurally true
    rather than merely requested in the prompt.
    """
    allowed = set(missing)
    out: Dict[str, List[Any]] = {}

    for section, items in (data or {}).items():
        if section not in allowed or not isinstance(items, list):
            continue
        cap = _CAPS.get(section, 10)

        kept: List[Any] = []
        for raw in items:
            if section == "work_experience":
                item = _pair(raw, "title", _W_TITLE, "company", _W_COMPANY)
            elif section == "education":
                item = _pair(raw, "degree", _W_DEGREE, "institution", _W_INSTITUTION)
            elif section == "certifications":
                name = _name_of(raw, "name", "title", "certificate_name")
                item = name if name and len(name) <= _W_CERT else None
            elif section == "projects":
                name = _name_of(raw, "name", "title", "project_title")
                item = ({"name": name, "confidence": _LLM_CONFIDENCE}
                        if name and len(name) <= _W_PROJECT else None)
            else:
                item = None

            if item is not None:
                kept.append(item)
                if len(kept) >= cap:
                    break

        if kept:
            out[section] = kept

    return out


def extract_sections_sync(text: str, parsed: Optional[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Fill sections the parser left empty. Returns {} on any failure.

    Synchronous; ``extract_sections`` is the wrapper to use from a request coroutine.
    """
    if not text or not text.strip():
        return {}
    missing = fillable_sections(parsed)
    if not missing or not _enabled():
        return {}

    try:
        from src.services.gemini_service import _generate_content
        raw = _generate_content(
            _build_prompt(text, missing),
            temperature=0.0,           # factual copy-out, not generation
            top_p=1.0,
            # Lists run longer than the pool extractor's flat fields.
            max_output_tokens=1200,
            # Thinking tokens come out of max_output_tokens on Gemini 2.5 and would
            # truncate this JSON mid-list. See gemini_service._generate_content.
            thinking_budget=0,
        )
    except Exception as e:
        logger.warning(f"LLM profile extraction failed: {e} — keeping parser-only sections")
        return {}

    filled = _validate(_parse_json(raw), missing)
    if filled:
        logger.info(
            "LLM profile extraction filled: %s",
            {k: len(v) for k, v in sorted(filled.items())},
        )
    return filled


async def extract_sections(text: str, parsed: Optional[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Async wrapper. Offloads the blocking SDK call so the event loop keeps serving
    other requests — the same approach gemini_service.generate_text uses."""
    if not text or not text.strip():
        return {}
    if not fillable_sections(parsed) or not _enabled():
        return {}          # skip the thread hop entirely when there is nothing to do
    return await asyncio.to_thread(extract_sections_sync, text, parsed)
