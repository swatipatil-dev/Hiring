"""
Tests for the recruiter-rubric ATS scoring:
  weights: education 35 / skills 25 / experience 15 / certification 10 / semantic 5 / project 5 / keyword 5
  education  = degree(40) + tier(30) + cgpa(30)
  experience = relevant(50) + internships(25) + github/oss/hackathon(25)
  skills     = required(70) + preferred(30)

Run with:
    pytest test/test_ats_rubric.py -v
"""

import pytest

from src.services.enhanced_ats_service import EnhancedATSScoringService

svc = EnhancedATSScoringService()

JD = "Looking for a Python Django backend developer. Preferred: Kubernetes, AWS."
BASE = dict(
    resume_text="Python Django developer building REST APIs.",
    job_description=JD,
    required_skills=["python", "django"],
    required_experience=2,
)


def test_weights_match_rubric():
    w = svc.weights
    assert w["education"] == 0.35
    assert w["skills_match"] == 0.25
    assert w["experience"] == 0.15
    assert w["certification"] == 0.10
    assert w["semantic"] == 0.05
    assert w["project"] == 0.05
    assert w["keyword"] == 0.05
    assert round(sum(w.values()), 6) == 1.0


def test_education_subscore_components():
    # Tier-1 degree-meeting candidate with strong CGPA -> near 100 education.
    score, d = svc.score_education_v2(
        educations=[{"education_level": "Bachelor's Degree", "degree": "B.Tech",
                     "institution_name": "IIT Bombay", "grade": "9.0"}],
        institution_names=None, parsed_education=None, resume_text="",
        required_education="bachelor",
    )
    assert d["degree_match"] == 40.0          # B.Tech meets required bachelor
    assert d["college_tier"] == 30.0          # IIT -> tier 1
    assert d["cgpa"] == 27.0                  # 9.0/10 * 30
    assert score == 97.0


def test_education_missing_cgpa_and_unknown_college():
    score, d = svc.score_education_v2(
        educations=[{"education_level": "Bachelor's Degree", "degree": "B.Sc",
                     "institution_name": "Local College", "grade": ""}],
        institution_names=None, parsed_education=None, resume_text="",
        required_education="bachelor",
    )
    assert d["college_tier"] == 0.0
    assert d["cgpa"] == 0.0
    assert d["cgpa_found"] is False


def test_experience_subscore_components():
    score, d = svc.score_experience_v2(
        years=3, required_years=2,
        work_experiences=[{"employment_type": "Internship", "job_title": "SWE Intern"}],
        links_text="github.com/foo", resume_text="Won a hackathon.",
    )
    assert d["relevant_experience"] == 50.0   # meets requirement
    assert d["internships"] == 15.0           # 1 internship
    assert d["has_github"] is True
    assert d["has_hackathon"] is True
    assert d["github_oss_hackathons"] == 20.0  # 15 github + 5 hackathon


def test_skills_required_and_preferred_split():
    # Has both required, neither preferred.
    score, d = svc.score_skills_v2(
        resume_skills=["python", "django"],
        required_skills=["python", "django"],
        preferred_skills=["kubernetes", "aws"],
    )
    assert d["required_skills"] == 70.0
    assert d["preferred_skills"] == 0.0
    assert score == 70.0


def test_skills_no_preferred_means_required_carries_full_100():
    score, d = svc.score_skills_v2(
        resume_skills=["python", "django"],
        required_skills=["python", "django"],
        preferred_skills=[],
    )
    assert score == 100.0
    assert d["has_preferred"] is False


@pytest.mark.parametrize("score,rating", [
    (90, "Excellent"), (75, "Very Good"), (60, "Good"), (45, "Average"), (20, "Poor"),
])
def test_rating_bands(score, rating):
    r, rec = svc._rating_and_recommendation(score)
    assert r == rating
    assert rec  # non-empty recommendation


def test_end_to_end_breakdown_shape():
    res = svc.enhanced_score_candidate(
        **BASE,
        educations=[{"education_level": "Bachelor's Degree", "degree": "B.Tech",
                     "institution_name": "IIT Bombay", "grade": "8.5"}],
        work_experiences=[{"employment_type": "Internship", "job_title": "Intern"}],
        links_text="github.com/foo",
    )
    sb = res.scoring_breakdown
    assert set(sb["individual_scores"]) == {
        "education", "skills_match", "experience", "certification",
        "semantic", "project", "keyword"}
    assert "education" in sb["sub_scores"]
    assert "experience" in sb["sub_scores"]
    assert "skills" in sb["sub_scores"]
    assert res.rating in {"Excellent", "Very Good", "Good", "Average", "Poor"}
    assert res.hiring_recommendation
    assert isinstance(res.strengths, list) and isinstance(res.weaknesses, list)
    # Preferred skills auto-extracted from the JD ("Preferred: Kubernetes, AWS").
    assert "kubernetes" in [s.lower() for s in sb["missing_preferred_skills"]]
    assert 0.0 <= res.ats_score <= 100.0
