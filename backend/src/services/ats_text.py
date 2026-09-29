"""Text primitives shared by the ATS scorer and the evidence extractor.

Both need to answer "does this resume text contain this term?" and they must
answer it the *same* way. When they disagree the recruiter sees the incoherent
case: a skill credited in the score with nothing quoted behind it, or a quote
attached to a skill that scored zero.

Whole-word matching is the whole point. A bare substring test credits required
"go" to anyone who wrote "django" or "mongodb", "java" to anyone with
"javascript", and "r" to essentially everyone.

This module holds no state and imports nothing from the scorer, so either side
can use it without an import cycle.
"""

from __future__ import annotations

import functools
import re
from typing import Iterable, Optional, Pattern

# Compiled patterns are cached: scoring one resume against N jobs asks about the
# same handful of skills repeatedly, and a JD keyword sweep re-asks for hundreds
# of terms per candidate.
_CACHE_SIZE = 2048


@functools.lru_cache(maxsize=_CACHE_SIZE)
def whole_word_pattern(term: str) -> Optional[Pattern]:
    """A case-insensitive pattern matching ``term`` only as a whole word.

    ``(?<!\\w)`` / ``(?!\\w)`` rather than ``\\b`` because skill names routinely
    start or end with a non-word character — ``\\b`` is a *transition* assertion,
    so ``r"\\bc++\\b"`` can never match "c++" (there is no word character after
    the final "+" for the boundary to sit against). The lookarounds assert
    "not flanked by a word character", which behaves correctly for "c++",
    "b.tech", ".net" and "node.js" alike.

    Returns None for a term with no matchable content, so callers can treat
    "unmatchable" and "not present" identically.
    """
    cleaned = (term or "").strip()
    if not cleaned:
        return None
    return re.compile(r"(?<!\w)" + re.escape(cleaned) + r"(?!\w)", re.IGNORECASE)


def contains_whole_word(text: str, term: str) -> bool:
    """True when ``term`` appears in ``text`` as a whole word."""
    if not text:
        return False
    pattern = whole_word_pattern(term)
    return bool(pattern and pattern.search(text))


def contains_any_whole_word(text: str, terms: Iterable[str]) -> bool:
    """True when any of ``terms`` appears in ``text`` as a whole word."""
    return any(contains_whole_word(text, term) for term in terms)
