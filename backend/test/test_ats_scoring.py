"""
Unit tests for the enhanced ATS scoring engine (Branch C).

All tests run without GPU or large models — heavy models are mocked.

Run with:
    pytest test/test_ats_scoring.py -v
"""

import sys
import types
from unittest.mock import MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Helpers: inject fake modules for testing both installed/uninstalled states
# ---------------------------------------------------------------------------

def _make_fake_sentence_transformers():
    st = types.ModuleType("sentence_transformers")
    st.__version__ = "2.6.0"

    class FakeModel:
        def encode(self, texts, convert_to_tensor=False):
            import numpy as np
            return [np.array([1.0, 0.0, 0.0]) for _ in texts]

    class FakeUtil:
        @staticmethod
        def cos_sim(a, b):
            mock = MagicMock()
            mock.__float__ = lambda _: 0.85
            mock.item = lambda: 0.85
            return mock

    st.SentenceTransformer = FakeModel
    st.util = FakeUtil()
    return st


def _make_fake_rapidfuzz():
    rf = types.ModuleType("rapidfuzz")
    rf.__version__ = "3.5.0"
    fuzz_mod = types.ModuleType("rapidfuzz.fuzz")

    def partial_ratio(a, b):
        if not a or not b:
            return 0
        common = set(a.lower()) & set(b.lower())
        return (2 * len(common)) / (len(a) + len(b)) * 100

    def token_set_ratio(a, b):
        if not a or not b:
            return 0
        tokens_a = set(a.lower().split())
        tokens_b = set(b.lower().split())
        common = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return (len(common) / len(union)) * 100 if union else 0

    fuzz_mod.partial_ratio = partial_ratio
    fuzz_mod.token_set_ratio = token_set_ratio
    rf.fuzz = fuzz_mod
    # Deliberately NOT installed into sys.modules: the only caller patches
    # enhanced_ats_service._fuzz directly, and a session-wide fake leaks into
    # every later test that imports the real rapidfuzz.
    return rf


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def service():
    from src.services.enhanced_ats_service import EnhancedATSScoringService
    return EnhancedATSScoringService()


@pytest.fixture(scope="module")
def detect_sections_fn():
    from src.services.enhanced_ats_service import detect_sections
    return detect_sections


@pytest.fixture(scope="module")
def normalize_skill_fn():
    from src.services.enhanced_ats_service import normalize_skill
    return normalize_skill


# ---------------------------------------------------------------------------
# 1. Section detection
# ---------------------------------------------------------------------------

class TestSectionDetection:

    def test_detects_skills_section_exact(self, detect_sections_fn):
        sections = detect_sections_fn("SKILLS\npython\njava\n")
        assert "skills" in sections
        assert "python" in sections["skills"].lower()

    def test_detects_skills_alias_my_technical_skills(self, detect_sections_fn):
        sections = detect_sections_fn("MY TECHNICAL SKILLS\npython\njava\n")
        assert "skills" in sections, "'MY TECHNICAL SKILLS' alias not detected"

    def test_detects_experience_alias_work_history(self, detect_sections_fn):
        sections = detect_sections_fn("WORK HISTORY\n2020-2023 Software Engineer at Acme\n")
        assert "experience" in sections, "'WORK HISTORY' alias not detected as experience"

    def test_detects_experience_alias_professional_background(self, detect_sections_fn):
        sections = detect_sections_fn("PROFESSIONAL BACKGROUND\n2019-2022 at TechCorp\n")
        assert "experience" in sections, "'PROFESSIONAL BACKGROUND' alias not detected"

    def test_detects_education_alias_educational_background(self, detect_sections_fn):
        sections = detect_sections_fn("EDUCATIONAL BACKGROUND\nB.Tech in Computer Science\n")
        assert "education" in sections, "'EDUCATIONAL BACKGROUND' alias not detected"

    def test_detects_projects_alias_key_projects(self, detect_sections_fn):
        sections = detect_sections_fn("KEY PROJECTS\n- Built a data pipeline\n")
        assert "projects" in sections, "'KEY PROJECTS' alias not detected"

    def test_unknown_section_goes_to_other(self, detect_sections_fn):
        sections = detect_sections_fn("REFERENCES\nJohn Smith, CEO\n")
        assert "other" in sections

    def test_multi_section_resume(self, detect_sections_fn):
        text = "SKILLS\npython sql\nEXPERIENCE\n2018-2022 at Acme\nEDUCATION\nBachelor of Science\n"
        sections = detect_sections_fn(text)
        assert "skills" in sections
        assert "experience" in sections
        assert "education" in sections

    def test_skill_content_not_in_wrong_section(self, detect_sections_fn):
        text = "EDUCATION\nBachelor of Science\nSKILLS\npython\n"
        sections = detect_sections_fn(text)
        assert "python" in sections.get("skills", "")
        assert "python" not in sections.get("education", "")


# ---------------------------------------------------------------------------
# 2. Fuzzy keyword matching
# ---------------------------------------------------------------------------

class TestKeywordMatching:

    def test_returns_float_in_range(self, service):
        score = service.calculate_keyword_match(
            resume_text="Python developer with Django and PostgreSQL",
            job_description="Python Django PostgreSQL developer"
        )
        assert 0.0 <= score <= 100.0

    def test_perfect_overlap_scores_high(self, service):
        text = "python django postgresql"
        score = service.calculate_keyword_match(text, text)
        assert score > 80.0, f"Identical text scored too low: {score}"

    def test_empty_job_description_returns_50(self, service):
        score = service.calculate_keyword_match("python developer", "")
        assert score == 50.0

    def test_no_overlap_scores_low(self, service):
        score = service.calculate_keyword_match(
            "carpentry woodworking joinery",
            "python machine learning tensorflow"
        )
        assert score < 30.0, f"Non-overlapping text scored too high: {score}"

    def test_rapidfuzz_path_runs_without_error(self):
        fake_rf = _make_fake_rapidfuzz()
        with patch("src.services.enhanced_ats_service.RAPIDFUZZ_AVAILABLE", True), \
             patch("src.services.enhanced_ats_service._fuzz", fake_rf.fuzz, create=True):
            from src.services.enhanced_ats_service import EnhancedATSScoringService
            svc = EnhancedATSScoringService()
            score = svc.calculate_keyword_match("Python developer", "Python developer")
            assert 0.0 <= score <= 100.0

    def test_fallback_exact_when_rapidfuzz_unavailable(self):
        with patch("src.services.enhanced_ats_service.RAPIDFUZZ_AVAILABLE", False):
            from src.services.enhanced_ats_service import EnhancedATSScoringService
            svc = EnhancedATSScoringService()
            score = svc.calculate_keyword_match("python developer", "python developer")
            assert score > 0.0


# ---------------------------------------------------------------------------
# 3. Skill normalization
# ---------------------------------------------------------------------------

class TestSkillNormalization:

    def test_known_alias_resolves(self, normalize_skill_fn):
        from src.services.enhanced_ats_service import ALIAS_MAP
        if not ALIAS_MAP:
            pytest.skip("ALIAS_MAP is empty — check skill_aliases.json")
        alias = next(iter(ALIAS_MAP))
        assert normalize_skill_fn(alias) == ALIAS_MAP[alias]

    def test_unknown_token_returned_as_is(self, normalize_skill_fn):
        assert normalize_skill_fn("xyznonsenseskill") == "xyznonsenseskill"

    def test_case_insensitive_lookup(self, normalize_skill_fn):
        from src.services.enhanced_ats_service import ALIAS_MAP
        if not ALIAS_MAP:
            pytest.skip("ALIAS_MAP is empty")
        alias = next(iter(ALIAS_MAP))
        assert normalize_skill_fn(alias.upper()) == normalize_skill_fn(alias.lower())

    def test_whitespace_stripped(self, normalize_skill_fn):
        assert normalize_skill_fn("  python  ") == normalize_skill_fn("python")


# ---------------------------------------------------------------------------
# 4. Scoring factor ranges (each must return 0–100)
# ---------------------------------------------------------------------------

class TestScoringFactorRanges:

    def test_skills_match_range(self, service):
        score = service.calculate_skills_match(["python", "sql"], ["python", "java", "sql"])
        assert 0.0 <= score <= 100.0

    def test_skills_match_empty_required(self, service):
        assert service.calculate_skills_match(["python"], []) == 100.0

    def test_experience_match_range(self, service):
        for resume_yrs, req_yrs in [(0, 5), (3, 3), (10, 5), (0, 0)]:
            score = service.calculate_experience_match(resume_yrs, req_yrs)
            assert 0.0 <= score <= 100.0, f"Out of range for {resume_yrs}/{req_yrs}: {score}"

    def test_experience_match_zero_required(self, service):
        assert service.calculate_experience_match(0, 0) == 100.0

    def test_education_score_range(self, service):
        edu_list = [{"degree": "bachelor", "score": 60, "confidence": 0.9}]
        score = service.calculate_education_score(edu_list, "bachelor")
        assert 0.0 <= score <= 100.0

    def test_education_score_empty(self, service):
        assert service.calculate_education_score([], "bachelor") == 0.0

    def test_certification_score_range(self, service):
        score = service.calculate_certification_score(["AWS Certified Developer"])
        assert 0.0 <= score <= 100.0

    def test_certification_score_empty(self, service):
        assert service.calculate_certification_score([]) == 0.0

    def test_keyword_score_range(self, service):
        score = service.calculate_keyword_match("python sql django", "python developer sql")
        assert 0.0 <= score <= 100.0

    def test_project_score_range(self, service):
        projects = [{"name": "Pipeline", "extracted_text": "built a data pipeline using python"}]
        score = service.calculate_project_score(projects, jd_text="data engineering python pipeline")
        assert 0.0 <= score <= 100.0

    def test_project_score_empty(self, service):
        assert service.calculate_project_score([]) == 0.0


# ---------------------------------------------------------------------------
# 5. Fallback behavior when models unavailable
# ---------------------------------------------------------------------------

class TestFallbackBehavior:

    def test_semantic_score_tfidf_fallback(self):
        with patch("src.services.enhanced_ats_service._get_semantic_model", return_value=None):
            from src.services.enhanced_ats_service import compute_semantic_score
            score = compute_semantic_score(
                "Python developer with 5 years experience",
                "Python developer senior position"
            )
            assert 0.0 <= score <= 100.0

    def test_spacy_fallback_returns_empty_list(self):
        with patch("src.services.enhanced_ats_service._get_nlp", return_value=None):
            from src.services.enhanced_ats_service import extract_skills_from_tokens
            result = extract_skills_from_tokens("Python developer with React experience")
            assert result == []

    def test_enhanced_score_candidate_runs_without_models(self):
        with patch("src.services.enhanced_ats_service._get_semantic_model", return_value=None), \
             patch("src.services.enhanced_ats_service.RAPIDFUZZ_AVAILABLE", False), \
             patch("src.services.enhanced_ats_service._get_nlp", return_value=None):
            from src.services.enhanced_ats_service import EnhancedATSScoringService
            svc = EnhancedATSScoringService()
            result = svc.enhanced_score_candidate(
                resume_text="SKILLS\npython sql\nEXPERIENCE\n3 years of experience\n",
                job_description="Python SQL developer needed",
                required_skills=["python", "sql"],
                required_experience=3,
            )
            assert 0.0 <= result.ats_score <= 100.0
            assert 0.0 <= result.skills_match_score <= 100.0
            assert 0.0 <= result.semantic_score <= 100.0

    def test_section_detection_no_headers_fallback(self):
        from src.services.enhanced_ats_service import EnhancedATSScoringService
        svc = EnhancedATSScoringService()
        result = svc.enhanced_score_candidate(
            resume_text="No headers here just raw text with python and sql skills and 3 years of experience",
            job_description="Python SQL developer",
            required_skills=["python", "sql"],
            required_experience=2,
        )
        assert isinstance(result.ats_score, float)
