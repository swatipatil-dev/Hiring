"""Is this resume text good enough to score?

A resume that could not be read used to be scored anyway: empty or garbled text went
through the scorer, came out near 0, and the candidate could then be rejected by a
top-N shortlist without anyone knowing their resume was never actually read. This module
decides whether the text is trustworthy. If it isn't, the result is marked
`needs_review`: it keeps a number (the ATS list requires one) but is held out of every
automatic reject and shortlist, and HR sees "Needs manual review" instead of a low score.
"""
from __future__ import annotations

import re
from typing import Optional

MIN_WORDS = 80                 # fewer than this cannot support a fair score
SCANNED_CHARS_PER_PAGE = 100   # a PDF with pages but less text than this is probably an image
GARBLED_NON_ALNUM_RATIO = 0.30
LOW_EVIDENCE_COVERAGE = 0.5    # below this share of scoring weight with evidence → a person decides

# Issues that alone mean the text cannot be trusted.
BLOCKING = {"extraction_error", "no_text", "likely_scanned", "too_short", "garbled", "scoring_error"}

ISSUE_TEXT = {
    "extraction_error": "The file could not be read.",
    "no_text": "No text could be extracted from the resume.",
    "likely_scanned": "The resume looks like a scanned image; its text could not be read.",
    "too_short": "Very little text was extracted, so the score would not be reliable.",
    "garbled": "The extracted text is garbled (often a font-encoding problem in the PDF).",
    "scoring_error": "Scoring failed on this resume.",
    "low_evidence": "Too little of the scoring could be based on evidence found in the resume.",
}


def assess(text: Optional[str], extraction: Optional[dict] = None, source: str = "resume") -> dict:
    """Return {status: ok|needs_review, confidence: 0-1, issues: [...], word_count}.

    `source="profile"` is text HireFlow built from the candidate's structured profile. It
    was never parsed from a file, so the file checks (scanned, garbled, too short) do not
    apply: a short profile is a sparse profile, not an unreadable one. Only "no text"
    stops it being scored.
    """
    extraction = extraction or {}
    text = text or ""
    issues = []
    words = re.findall(r"[A-Za-z][A-Za-z+#.\-]*", text)
    word_count = len(words)

    if extraction.get("error"):
        issues.append("extraction_error")
    if not text.strip():
        issues.append("no_text")
    elif source == "profile":
        pass
    else:
        pages = extraction.get("pages")
        if pages and len(text) / max(pages, 1) < SCANNED_CHARS_PER_PAGE:
            issues.append("likely_scanned")
        if word_count < MIN_WORDS:
            issues.append("too_short")
        non_space = [c for c in text if not c.isspace()]
        odd = sum(1 for c in non_space if not c.isalnum() and c not in ".,;:()/-+#&@'%")
        if "(cid:" in text or (non_space and odd / len(non_space) > GARBLED_NON_ALNUM_RATIO):
            issues.append("garbled")

    base = extraction.get("confidence")
    confidence = float(base) if base is not None else (1.0 if text.strip() else 0.0)
    confidence -= 0.25 * sum(1 for i in issues if i in BLOCKING)
    confidence = round(max(0.0, min(1.0, confidence)), 3)
    status = "needs_review" if (set(issues) & BLOCKING or confidence < 0.5) else "ok"
    return {"status": status, "confidence": confidence, "issues": issues, "word_count": word_count}


def describe(issues) -> list[str]:
    return [ISSUE_TEXT.get(i, i.replace("_", " ")) for i in (issues or [])]
