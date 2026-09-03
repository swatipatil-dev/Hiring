"""
LLM Resume Field Extractor
--------------------------
Step 1 — ``resume_profile_parser.parse_resume_profile`` (regex/keyword heuristics)
          produces the profile. It returns None rather than guessing, so unusual
          layouts leave full_name / college / job_role blank.
Step 2 — Gemini fills ONLY the fields step 1 left blank.

The LLM is strictly additive. It is asked only about missing fields, and any key it
returns for a field that already has a value is discarded before validation — so this
can improve coverage but can never overwrite a correct deterministic result. When the
feature is disabled, credentials are absent, there are no gaps, or anything goes wrong,
every entry point returns ``{}`` and the caller keeps today's behaviour.

Same shape as ``llm_ats_service``: deterministic engine first, LLM enriches, degrade
quietly.
"""

import os
import re
import json
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────
# No-op without GEMINI_API_KEY / GOOGLE_CLOUD_PROJECT regardless of this flag.
LLM_RESUME_EXTRACTION = os.getenv("RESUME_LLM_EXTRACTION", "true").strip().lower() == "true"
# Bounds the fan-out when a bulk upload has many resumes with gaps.
LLM_RESUME_CONCURRENCY = max(1, int(os.getenv("RESUME_LLM_CONCURRENCY", "5")))
# Identity fields cluster at the top of a resume; sending the whole document would
# multiply cost and latency for no gain.
LLM_RESUME_TEXT_CHARS = max(500, int(os.getenv("RESUME_LLM_TEXT_CHARS", "6000")))

# The fields resume_pool stores. Mirrors parse_resume_profile's return keys.
PROFILE_FIELDS = (
    "full_name", "email", "phone", "college",
    "year_of_passing", "job_role", "highest_degree",
)

# VARCHAR widths on ResumePoolEntry (models.py). A value that would trigger a MySQL
# truncation error has to be rejected here, not discovered at commit().
_MAX_LEN = {
    "full_name": 255, "email": 255, "phone": 50,
    "college": 255, "job_role": 255, "highest_degree": 100,
}

# Same pattern enhanced_ats_service.extract_contact_info uses, so an LLM-supplied
# address is held to exactly the standard a parsed one would have been.
_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')

# Models like to answer "not found" instead of null however firmly they are asked.
_PLACEHOLDERS = {
    "", "n/a", "na", "none", "null", "nil", "unknown", "not found", "not given",
    "not specified", "not mentioned", "not available", "not provided", "-", "--",
}

_FIELD_HINTS = {
    "full_name": "the candidate's own full name, as written on the resume",
    "email": "the candidate's email address, copied exactly",
    "phone": "the candidate's phone number, copied exactly",
    "college": "the university or college they studied at (the institution name only)",
    "year_of_passing": "the 4-digit year they graduated, as a number",
    "job_role": "their current or most recent job title, or the role they are targeting",
    "highest_degree": "their highest qualification, e.g. B.Tech, MCA, MBA, B.Sc",
}


def missing_fields(profile: Optional[Dict[str, Any]]) -> List[str]:
    """Profile keys with no usable value — the only fields the LLM may be asked about."""
    profile = profile or {}
    return [f for f in PROFILE_FIELDS if not profile.get(f)]


def profile_source_map(entry) -> Dict[str, str]:
    """Read ResumePoolEntry.profile_source (JSON in a TEXT column) into a dict.

    Rows written before gap-filling existed hold NULL, and a field absent from the map
    means the value was produced deterministically — so {} is the correct answer for
    them, not an error.
    """
    raw = getattr(entry, "profile_source", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if v}


def _build_prompt(text: str, missing: List[str]) -> str:
    wanted = "\n".join(f'  "{f}": {_FIELD_HINTS[f]}' for f in missing)
    return f"""You are extracting factual fields from a resume. Extract ONLY these fields:

{wanted}

Rules:
- Copy values verbatim from the resume. Do not rephrase, expand or normalise them.
- If the resume does not clearly state a field, return null for it. Never guess,
  infer from context, or use any outside knowledge.
- Do not return a field that is not listed above.
- Reply with a single JSON object and nothing else. No prose, no markdown fence.

RESUME:
{text[:LLM_RESUME_TEXT_CHARS]}

JSON:"""


def _parse_json(raw: str) -> Dict[str, Any]:
    """Pull a JSON object out of model output; tolerate fences and stray prose."""
    if not raw:
        return {}
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _clean_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = re.sub(r"\s+", " ", value).strip().strip(".,;:|-").strip()
    return None if v.lower() in _PLACEHOLDERS else (v or None)


def _validate(data: Dict[str, Any], missing: List[str]) -> Dict[str, Any]:
    """Keep only values that are safe to store.

    The `missing` filter is what makes "fill blanks only" structurally true rather
    than merely requested in the prompt — a model that volunteers an already-filled
    field cannot reach the database.
    """
    allowed = set(missing)
    out: Dict[str, Any] = {}

    for field, value in (data or {}).items():
        if field not in allowed or value is None:
            continue

        if field == "year_of_passing":
            try:
                year = int(str(value).strip())
            except (TypeError, ValueError):
                continue
            # A stray "2 years experience" or a phone fragment must not land here.
            if 1950 <= year <= datetime.utcnow().year + 6:
                out[field] = year
            continue

        text = _clean_str(value)
        if not text or len(text) > _MAX_LEN[field]:
            continue

        if field == "email":
            if not _EMAIL_RE.match(text):
                continue
        elif field == "full_name":
            # Guard against the model returning a sentence or a contact line.
            if ("@" in text or any(ch.isdigit() for ch in text)
                    or not any(ch.isalpha() for ch in text)
                    or len(text.split()) > 6):
                continue

        out[field] = text

    return out


def _enabled() -> bool:
    """True if a call should even be attempted. check_health() is credential-only —
    no network round-trip — so this is cheap to call per resume."""
    if not LLM_RESUME_EXTRACTION:
        return False
    try:
        from src.services.gemini_service import sdk_available, credentials_configured
        return sdk_available() and credentials_configured()
    except Exception:
        return False


def extract_gaps_sync(text: str, profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fill the blanks in `profile` from `text`. Returns {} on any failure.

    Synchronous — for resume_pool_import and scripts, which are not async. Blocking,
    so do not call it directly from a request coroutine; use extract_gaps() there.
    """
    if not text or not text.strip():
        return {}
    missing = missing_fields(profile)
    if not missing or not _enabled():
        return {}

    try:
        from src.services.gemini_service import _generate_content
        raw = _generate_content(
            _build_prompt(text, missing),
            temperature=0.0,          # factual copy-out, not generation
            top_p=1.0,
            max_output_tokens=300,
            # Thinking tokens come out of max_output_tokens on Gemini 2.5 and would
            # truncate this JSON mid-string. See gemini_service._generate_content.
            thinking_budget=0,
        )
    except Exception as e:
        logger.warning(f"LLM resume extraction failed: {e} — keeping regex-only profile")
        return {}

    fills = _validate(_parse_json(raw), missing)
    if fills:
        logger.info(f"LLM resume extraction filled: {sorted(fills)}")
    return fills


async def extract_gaps(text: str, profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Async wrapper. Offloads the blocking SDK call so the event loop keeps serving
    other requests — the same approach gemini_service.generate_text uses."""
    if not text or not text.strip():
        return {}
    if not missing_fields(profile) or not _enabled():
        return {}          # skip the thread hop entirely when there is nothing to do
    return await asyncio.to_thread(extract_gaps_sync, text, profile)
