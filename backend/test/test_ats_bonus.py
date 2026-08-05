"""
Unit tests for the ATS affirmative bonuses:
  - college tier (Tier 1 -> +5, Tier 2 -> +3)
  - rural permanent address (+3)

Run with:
    pytest test/test_ats_bonus.py -v
"""

import pytest

from src.services.institution_tiers import classify_institution, best_tier
from src.services import pincode_region
from src.services.enhanced_ats_service import EnhancedATSScoringService


# ── College tier classifier ───────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Indian Institute of Technology Bombay", "tier_1"),
    ("IIT Madras", "tier_1"),
    ("National Institute of Technology, Trichy", "tier_1"),
    ("NIT Warangal", "tier_1"),
    ("IIIT Hyderabad", "tier_1"),
    ("BITS Pilani", "tier_1"),
    ("Indian Institute of Science", "tier_1"),
    ("Manipal Institute of Technology", "tier_2"),
    ("Thapar University", "tier_2"),
    ("SRM Institute of Science and Technology", "tier_2"),
    ("Some Random Local College", None),
    ("", None),
    (None, None),
])
def test_classify_institution(name, expected):
    assert classify_institution(name) == expected


def test_best_tier_prefers_tier_1():
    names = ["Some Local College", "Manipal Institute of Technology", "IIT Delhi"]
    assert best_tier(names) == "tier_1"


def test_best_tier_falls_back_to_tier_2():
    assert best_tier(["Some Local College", "Thapar University"]) == "tier_2"


def test_best_tier_none():
    assert best_tier(["Local College A", "Local College B"]) is None
    assert best_tier([]) is None
    assert best_tier(None) is None


# ── Rural pincode classifier ───────────────────────────────────────────────────

def test_is_rural_from_dataset():
    pincode_region.reload_dataset()
    assert pincode_region.is_rural("576213") is True       # village-dominated pincode
    assert pincode_region.is_rural("560001") is False      # Bangalore GPO -> urban
    assert pincode_region.is_rural("000000") is False      # not in dataset -> unknown
    assert pincode_region.is_rural(None) is False
    assert pincode_region.is_rural("not-a-pincode") is False


def test_classify_pincode_extracts_digits():
    pincode_region.reload_dataset()
    assert pincode_region.classify_pincode(" 576213 ") == "rural"
    assert pincode_region.classify_pincode("PIN: 560001") == "urban"
    assert pincode_region.classify_pincode("12") == "unknown"


# ── End-to-end bonus on the scorer ─────────────────────────────────────────────

_SCORE_KWARGS = dict(
    resume_text="Python developer with 3 years experience building Django APIs.",
    job_description="Looking for a Python Django backend developer.",
    required_skills=["python", "django"],
    required_experience=2,
)


def test_tier1_lifts_education_score_and_rural_is_a_bonus():
    """Under the recruiter rubric, college tier is part of the education score
    (Tier 1 -> +30 of education's 100), and rural is a separate +3 bonus."""
    svc = EnhancedATSScoringService()
    plain = svc.enhanced_score_candidate(
        **_SCORE_KWARGS,
        educations=[{"institution_name": "Some Local College", "degree": "B.Tech", "grade": "7.0"}],
    )
    tier1 = svc.enhanced_score_candidate(
        **_SCORE_KWARGS,
        educations=[{"institution_name": "IIT Bombay", "degree": "B.Tech", "grade": "7.0"}],
        permanent_pincode="576213",  # village-dominated rural pincode
    )
    # Tier 1 adds 30 points to the education sub-score (college_tier component).
    edu_plain = plain.scoring_breakdown["sub_scores"]["education"]
    edu_tier1 = tier1.scoring_breakdown["sub_scores"]["education"]
    assert edu_plain["college_tier"] == 0.0
    assert edu_tier1["college_tier"] == 30.0
    # Rural remains a small additive bonus (no separate tier bonus anymore).
    bonus = tier1.scoring_breakdown["bonus"]
    assert bonus["college_tier"] == "tier_1"
    assert bonus["rural"] is True
    assert bonus["rural_bonus"] == 3.0
    assert bonus["total_bonus"] == 3.0
    assert "tier_bonus" not in bonus
    assert tier1.ats_score <= 100.0


def test_no_bonus_when_inputs_absent():
    svc = EnhancedATSScoringService()
    result = svc.enhanced_score_candidate(**_SCORE_KWARGS)
    bonus = result.scoring_breakdown["bonus"]
    assert bonus["total_bonus"] == 0.0
    assert bonus["college_tier"] is None
    assert bonus["rural"] is False


def test_final_score_capped_at_100():
    svc = EnhancedATSScoringService()
    result = svc.enhanced_score_candidate(
        **_SCORE_KWARGS,
        institution_names=["IIT Bombay"],
        permanent_pincode="576213",
    )
    assert 0.0 <= result.ats_score <= 100.0
