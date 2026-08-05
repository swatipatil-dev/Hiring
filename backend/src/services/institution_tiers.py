"""
College / university tier classification for ATS bonus scoring.

The ATS engine awards a small affirmative bonus to candidates who studied at
reputed institutions. Classification is done with a built-in curated list,
matched against the free-text ``institution_name`` the candidate enters on
their education entries.

- ``classify_institution(name)`` → ``"tier_1"`` | ``"tier_2"`` | ``None``
- ``best_tier(names)``          → the highest tier across a candidate's schools

Matching is intentionally tolerant of spelling/spacing variants: names are
normalized (lower-cased, punctuation stripped, whitespace collapsed) and then
checked against substring patterns. Tier 1 patterns win over Tier 2 on conflict.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

# ── Tier 1 — premier national institutes ──────────────────────────────────────
# Matched as normalized substrings, so both the full name and the common
# acronym are covered (e.g. "indian institute of technology" and "iit").
TIER_1_PATTERNS = [
    "indian institute of technology",
    "indian institutes of technology",
    "national institute of technology",
    "indian institute of information technology",
    "indian institute of science",
    "indian institute of management",
    "indian school of business",
    "all india institute of medical sciences",
    "indian statistical institute",
    "birla institute of technology and science",
    "bits pilani",
    "national law school of india",
    "tata institute of fundamental research",
    "iit ",
    "iit-",
    " nit ",
    "nit ",
    "iiit",
    "iisc",
    "aiims",
    "bits ",
    " iim ",
    "iim ",
    "iim-",
    "xlri",
    "nift",
    "vit vellore",
    "vellore institute of technology",
]

# ── Tier 2 — reputed state / private universities ─────────────────────────────
TIER_2_PATTERNS = [
    "delhi technological university",
    "netaji subhas university of technology",
    "college of engineering pune",
    "jadavpur university",
    "anna university",
    "manipal institute of technology",
    "manipal academy of higher education",
    "thapar institute",
    "thapar university",
    "birla institute of technology mesra",
    "bit mesra",
    "pes university",
    "rv college of engineering",
    "ramaiah institute of technology",
    "bms college of engineering",
    "siddaganga institute of technology",
    "amrita vishwa vidyapeetham",
    "sastra",
    "ssn college of engineering",
    "psg college of technology",
    "coimbatore institute of technology",
    "vjti",
    "veermata jijabai technological institute",
    "sardar patel institute of technology",
    "k j somaiya",
    "dwarkadas j sanghvi",
    "pune institute of computer technology",
    "vishwakarma institute of technology",
    "symbiosis institute",
    "christ university",
    "srm institute",
    "srm university",
    "amity university",
    "lovely professional university",
    "chandigarh university",
    "kiit",
    "kalinga institute of industrial technology",
    "graphic era",
    "jaypee institute",
    "shiv nadar university",
    "bennett university",
]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize(name: str) -> str:
    """Lower-case, replace any run of non-alphanumerics with a single space, and
    pad with surrounding spaces so word-boundary patterns like ``" nit "`` match."""
    if not name:
        return ""
    n = _NON_ALNUM.sub(" ", name.lower()).strip()
    return f" {n} "


def _matches_any(normalized: str, patterns: Iterable[str]) -> bool:
    return any(p in normalized for p in patterns)


def classify_institution(name: Optional[str]) -> Optional[str]:
    """Return ``"tier_1"``, ``"tier_2"`` or ``None`` for an institution name."""
    if not name:
        return None
    normalized = _normalize(name)
    if _matches_any(normalized, TIER_1_PATTERNS):
        return "tier_1"
    if _matches_any(normalized, TIER_2_PATTERNS):
        return "tier_2"
    return None


def best_tier(names: Optional[List[str]]) -> Optional[str]:
    """Highest tier across all of a candidate's institutions.

    A Tier-1 match anywhere wins; otherwise a Tier-2 match; otherwise ``None``.
    """
    if not names:
        return None
    found_tier_2 = False
    for name in names:
        tier = classify_institution(name)
        if tier == "tier_1":
            return "tier_1"
        if tier == "tier_2":
            found_tier_2 = True
    return "tier_2" if found_tier_2 else None
