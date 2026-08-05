"""
Unit tests for the resume<->JD verbatim-copy (plagiarism) detector.

Run with:
    pytest test/test_jd_overlap.py -v
"""

import pytest

from src.services.jd_overlap import jd_copy_overlap, JD_COPY_THRESHOLD
from src.services.enhanced_ats_service import EnhancedATSScoringService


JD = (
    "We are looking for a senior backend engineer to design and build scalable "
    "REST APIs using Python and Django. You will own the data layer, optimize "
    "PostgreSQL queries, write automated tests, and mentor junior developers. "
    "Experience with Docker, Kubernetes, and CI/CD pipelines is strongly preferred."
)


def test_verbatim_paste_is_flagged():
    # Candidate pasted the entire JD into their resume.
    result = jd_copy_overlap(JD, JD)
    assert result["jd_overlap"] >= 0.99
    assert result["flagged"] is True
    assert result["longest_copied_run"] >= 20


def test_genuine_relevant_resume_not_flagged():
    # Topically relevant but independently written — high semantic overlap, but
    # almost no verbatim 5-word phrases in common.
    resume = (
        "Backend developer with six years shipping production services. Built "
        "order-management microservices in Flask, tuned Postgres indexes that cut "
        "p95 latency by 40%, and led migration to containerized deployments on AWS. "
        "Comfortable owning on-call, code review, and onboarding new hires."
    )
    result = jd_copy_overlap(resume, JD)
    assert result["jd_overlap"] < JD_COPY_THRESHOLD
    assert result["flagged"] is False


def test_partial_paste_is_flagged():
    # Real resume plus two sentences lifted verbatim from the JD.
    resume = (
        "Software engineer with strong fundamentals. "
        "You will own the data layer, optimize PostgreSQL queries, write automated "
        "tests, and mentor junior developers. "
        "Experience with Docker, Kubernetes, and CI/CD pipelines is strongly preferred."
    )
    result = jd_copy_overlap(resume, JD)
    assert result["flagged"] is True
    assert result["longest_copied_run"] >= 5


@pytest.mark.parametrize("a,b", [
    ("", JD),
    (JD, ""),
    ("too short", "also short text"),
    (None, None),
])
def test_empty_or_short_inputs(a, b):
    result = jd_copy_overlap(a, b)
    assert result["flagged"] is False
    assert result["jd_overlap"] == 0.0
    assert result["longest_copied_run"] == 0


# ── Scorer integration ─────────────────────────────────────────────────────────

def test_scorer_records_plagiarism_block():
    svc = EnhancedATSScoringService()
    # Resume = the JD pasted verbatim.
    flagged = svc.enhanced_score_candidate(
        resume_text=JD,
        job_description=JD,
        required_skills=["python", "django"],
        required_experience=3,
    )
    plag = flagged.scoring_breakdown["plagiarism"]
    assert plag["flagged"] is True
    assert plag["jd_overlap"] >= 0.99

    # A clean resume on the same role should not be flagged.
    clean = svc.enhanced_score_candidate(
        resume_text="Experienced engineer skilled in Python and Django building APIs.",
        job_description=JD,
        required_skills=["python", "django"],
        required_experience=3,
    )
    assert clean.scoring_breakdown["plagiarism"]["flagged"] is False
