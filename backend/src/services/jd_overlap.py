"""
Resume <-> Job-Description verbatim-overlap (copy / plagiarism) detector.

This is deliberately DIFFERENT from semantic similarity. Semantic similarity is
*supposed* to be high for a genuinely relevant candidate. Large blocks of
*identical* text, on the other hand, indicate that the candidate pasted the job
description into their resume to game the keyword / semantic / skills factors.

We measure literal copying with word **shingles** (contiguous n-word sequences)
and report the *containment* of the JD inside the resume:

    jd_overlap = |resume_shingles ∩ jd_shingles| / |jd_shingles|

i.e. the fraction of the JD's distinctive phrases that appear verbatim in the
resume. A full JD paste pushes this toward 1.0; an independently written resume
that merely covers the same topics stays low (because exact 5-word runs rarely
coincide by chance).

Public API:
    jd_copy_overlap(resume_text, jd_text) -> {
        "jd_overlap": float,          # 0.0 - 1.0 containment
        "flagged": bool,              # jd_overlap >= JD_COPY_THRESHOLD
        "longest_copied_run": int,    # longest contiguous shared phrase (words)
        "threshold": float,
    }
"""

from __future__ import annotations

import re
from typing import Dict, List

# ── Tunable constants ─────────────────────────────────────────────────────────
SHINGLE_N = 5            # phrase length (words) that counts as "verbatim"
JD_COPY_THRESHOLD = 0.20  # flag when >=20% of the JD's 5-word phrases appear in the resume

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase word tokens, punctuation stripped."""
    if not text:
        return []
    return _WORD_RE.findall(text.lower())


def _shingles(tokens: List[str], n: int) -> set[str]:
    """Set of contiguous n-word phrases joined by spaces."""
    if len(tokens) < n:
        return set()
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _longest_common_run(a: List[str], b_shingles: set[str], n: int) -> int:
    """
    Length (in words) of the longest contiguous run of `a` whose every n-gram is
    present in `b_shingles`. Approximates the longest verbatim copied phrase.
    """
    if len(a) < n or not b_shingles:
        return 0
    best = 0
    current = 0  # number of consecutive matching n-grams
    for i in range(len(a) - n + 1):
        if " ".join(a[i:i + n]) in b_shingles:
            current += 1
            best = max(best, current)
        else:
            current = 0
    # `best` consecutive n-grams cover best + n - 1 words.
    return best + n - 1 if best else 0


def jd_copy_overlap(resume_text: str, jd_text: str, n: int = SHINGLE_N) -> Dict[str, object]:
    """Measure how much of the JD appears verbatim in the resume. Never raises."""
    empty = {"jd_overlap": 0.0, "flagged": False, "longest_copied_run": 0,
             "threshold": JD_COPY_THRESHOLD}

    resume_tokens = _tokenize(resume_text)
    jd_tokens = _tokenize(jd_text)
    jd_shingles = _shingles(jd_tokens, n)
    if not jd_shingles:
        return empty

    resume_shingles = _shingles(resume_tokens, n)
    common = jd_shingles & resume_shingles
    jd_overlap = round(len(common) / len(jd_shingles), 4)
    longest = _longest_common_run(resume_tokens, jd_shingles, n)

    return {
        "jd_overlap": jd_overlap,
        "flagged": jd_overlap >= JD_COPY_THRESHOLD,
        "longest_copied_run": longest,
        "threshold": JD_COPY_THRESHOLD,
    }
