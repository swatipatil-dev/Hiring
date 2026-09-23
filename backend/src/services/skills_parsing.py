"""One reader for the skill columns, which hold four different formats.

``job_postings.required_skills`` and its siblings are ``Text`` columns that
different write paths have filled differently over time:

- a JSON array, ``'["Python", "React"]'`` — what ``json.dumps`` produces
- a Python repr, ``"['Python', 'React']"`` — what assigning a raw ``list`` to a
  ``Text`` column produces, via SQLAlchemy's ``str()`` coercion
- a bare comma-separated string, ``"Python, React"`` — older data and hand edits
- ``NULL`` / empty

Reading these was duplicated byte-identically in four modules, and one service
imported the copy that happened to live in a *route* module. This is that
function, once, with the repr case actually handled: previously
``"['Python', 'React']"`` failed ``json.loads`` on the single quotes, fell
through to the comma split, and yielded ``["['Python'", "'React']"]`` — tokens
that match no skill and silently cost the candidate every requirement on the job.

A malformed value must never take out the page that reads it, so every failure
path returns a list rather than raising.
"""

from __future__ import annotations

import ast
import json
from typing import Any, List, Optional


def parse_skills(raw_skills: Any) -> List[str]:
    """Skills as a list of strings, whatever format the column holds.

    Returns ``[]`` for NULL, empty, or unparseable input — never raises.
    """
    if isinstance(raw_skills, list):
        return [str(s).strip() for s in raw_skills if str(s).strip()]
    if not isinstance(raw_skills, str) or not raw_skills.strip():
        return []

    text = raw_skills.strip()

    # JSON first: what every correct writer produces.
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    else:
        if isinstance(parsed, list):
            return [str(s).strip() for s in parsed if str(s).strip()]
        if isinstance(parsed, str) and parsed.strip():
            return [parsed.strip()]
        # Valid JSON that is not a list of skills — an object, `null`, a number.
        # Falling through to the comma split here would return the raw document
        # as a single "skill" (`{"skills": ["Python"]}` or `null`), which is
        # worse than admitting we found nothing.
        return []

    # Python repr: a raw list assigned to a Text column. literal_eval parses
    # literals only and cannot execute code, so this is safe on stored data.
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple)):
                return [str(s).strip() for s in parsed if str(s).strip()]
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass

    # Comma-separated, the last resort and the oldest format.
    return [s.strip() for s in text.split(",") if s.strip()]


def dump_skills(skills: Any) -> str | None:
    """Serialise a skill list for storage, or None for "not set".

    ``None`` and ``[]`` are different on purpose where a column uses NULL as an
    "unset" sentinel: pass ``None`` to leave it unset, ``[]`` to record an
    explicit empty list.
    """
    if skills is None:
        return None
    if isinstance(skills, str):
        skills = parse_skills(skills)
    return json.dumps([str(s).strip() for s in skills if str(s).strip()])


def parse_skills_or_none(raw_skills: Any) -> Optional[List[str]]:
    """Skills as a list, or None when the column is NULL.

    The distinction is load-bearing for job postings: NULL means nobody has
    separated mandatory from preferred on this job, and the scorer leaves such
    jobs alone. An empty list means someone looked and left it empty. Callers
    that flatten NULL to [] silently opt every legacy job into the new scoring.
    """
    if raw_skills is None:
        return None
    if isinstance(raw_skills, str) and not raw_skills.strip():
        return None
    return parse_skills(raw_skills)
