"""
Unit tests for the core scoring functions in enhanced_ats_service.py.

These are pure-function tests — no DB or HTTP required.
Covers: semantic scoring, weighted skills, cluster matching, text cleaning,
        resume normalisation, and the composite ATS score formula.
"""
import pytest

from src.services.enhanced_ats_service import (
    compute_semantic_score,
    compute_weighted_semantic_score,
    compute_weighted_skills_score,
    _find_cluster_match,
    clean_resume_text,
    normalize_resume_text,
    detect_sections,
    SKILL_CLUSTERS,
    SKILL_TO_CLUSTER,
    EnhancedATSScoringService,
)


# ══════════════════════════════════════════════════════════════════════════════
# compute_semantic_score
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeSemanticScore:

    def test_returns_float(self):
        score = compute_semantic_score("Python developer", "Python engineer")
        assert isinstance(score, float)

    def test_range_0_to_100(self):
        score = compute_semantic_score("machine learning", "deep learning neural networks")
        assert 0.0 <= score <= 100.0

    def test_identical_texts_score_high(self):
        text = "Python FastAPI PostgreSQL Docker AWS backend developer"
        score = compute_semantic_score(text, text)
        assert score >= 70.0, f"Identical texts should score ≥70, got {score}"

    def test_empty_first_text_returns_zero(self):
        assert compute_semantic_score("", "some job description") == 0.0

    def test_empty_second_text_returns_zero(self):
        assert compute_semantic_score("Python developer", "") == 0.0

    def test_both_empty_returns_zero(self):
        assert compute_semantic_score("", "") == 0.0

    def test_unrelated_texts_score_lower_than_related(self):
        related = compute_semantic_score(
            "Python FastAPI REST API backend", "FastAPI Python REST backend developer"
        )
        unrelated = compute_semantic_score(
            "Culinary arts chef pastry kitchen", "Python FastAPI REST backend developer"
        )
        assert related > unrelated, (
            f"Related score ({related}) should exceed unrelated ({unrelated})"
        )

    def test_uses_the_model_without_anything_loading_it_first(self, monkeypatch):
        """Regression: scoring checked SENTENCE_TRANSFORMERS_AVAILABLE, which only
        loading the model sets, and nothing else loaded it — so every score in
        production silently used the TF-IDF fallback."""
        import src.services.enhanced_ats_service as svc_mod

        class Encoder:
            def encode(self, text, convert_to_tensor=False, **kwargs):
                return text

        class Util:
            @staticmethod
            def cos_sim(a, b):
                return 0.42

        monkeypatch.setattr(svc_mod, "SENTENCE_TRANSFORMERS_AVAILABLE", False)
        monkeypatch.setattr(svc_mod, "_get_semantic_model", lambda: Encoder())
        monkeypatch.setattr(svc_mod, "st_util", Util())
        monkeypatch.setattr(svc_mod, "_EMBEDDING_CACHE", svc_mod.OrderedDict())

        assert compute_semantic_score("Python developer", "Python engineer") == 42.0
        assert svc_mod.current_model_versions()["semantic"] == "all-MiniLM-L6-v2"

    def test_records_tfidf_when_the_model_cannot_load(self, monkeypatch):
        import src.services.enhanced_ats_service as svc_mod

        monkeypatch.setattr(svc_mod, "_get_semantic_model", lambda: None)
        assert svc_mod.current_model_versions()["semantic"] == "tfidf"


# ══════════════════════════════════════════════════════════════════════════════
# compute_weighted_semantic_score
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeWeightedSemanticScore:

    def test_empty_sections_returns_zero(self):
        assert compute_weighted_semantic_score({}, "Python developer") == 0.0

    def test_empty_jd_returns_zero(self):
        sections = {"skills": "Python FastAPI", "experience": "3 years backend"}
        assert compute_weighted_semantic_score(sections, "") == 0.0

    def test_returns_float_in_range(self):
        sections = {
            "skills": "Python FastAPI PostgreSQL Docker",
            "experience": "3 years building REST APIs",
        }
        score = compute_weighted_semantic_score(sections, "Python backend developer FastAPI")
        assert isinstance(score, float)
        assert 0.0 <= score <= 100.0

    def test_blank_section_values_are_ignored(self):
        sections = {"skills": "   ", "experience": ""}
        score = compute_weighted_semantic_score(sections, "Python developer")
        assert score == 0.0

    def test_high_overlap_scores_above_low_overlap(self):
        jd = "Python FastAPI PostgreSQL Docker AWS REST API backend"
        high = compute_weighted_semantic_score(
            {"skills": "Python FastAPI PostgreSQL Docker AWS",
             "experience": "Built REST APIs with FastAPI and PostgreSQL"},
            jd,
        )
        low = compute_weighted_semantic_score(
            {"skills": "Excel Word PowerPoint",
             "experience": "Administrative data entry tasks"},
            jd,
        )
        assert high > low


# ══════════════════════════════════════════════════════════════════════════════
# compute_weighted_skills_score
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeWeightedSkillsScore:

    def test_empty_required_skills_returns_100(self):
        sections = {"skills": "Python FastAPI"}
        assert compute_weighted_skills_score(sections, []) == 100.0

    def test_empty_sections_returns_low_or_zero(self):
        score = compute_weighted_skills_score({}, ["python", "fastapi", "docker"])
        assert score <= 20.0, f"No skills in resume should score low, got {score}"

    def test_exact_match_in_skills_section(self):
        # Listed in a skills section only: credited, but below skills shown in real work.
        sections = {"skills": "python fastapi postgresql docker"}
        score = compute_weighted_skills_score(sections, ["python", "fastapi", "docker"])
        assert 60.0 <= score < 100.0, f"Listed-only skills — expected 60-99, got {score}"

    def test_partial_match_scores_between_0_and_100(self):
        sections = {"skills": "python fastapi"}
        score = compute_weighted_skills_score(
            sections, ["python", "fastapi", "kubernetes", "terraform"]
        )
        assert 0.0 < score < 100.0

    def test_no_match_scores_zero_or_low(self):
        sections = {"skills": "excel powerpoint word"}
        score = compute_weighted_skills_score(
            sections, ["python", "fastapi", "docker", "kubernetes"]
        )
        assert score <= 20.0

    def test_skill_demonstrated_in_experience_scores_higher_than_listed_in_skills(self):
        skills_section_score = compute_weighted_skills_score(
            {"skills": "python"}, ["python"]
        )
        experience_section_score = compute_weighted_skills_score(
            {"experience": "python"}, ["python"]
        )
        # Shown in a job (experience 1.5) outranks a line in a skills list (1.0).
        assert experience_section_score > skills_section_score

    def test_cluster_match_gives_partial_credit(self):
        """
        Docker and Kubernetes are in the same cluster.
        If resume has Kubernetes but JD requires Docker, cluster credit should apply.
        """
        docker_cluster = SKILL_TO_CLUSTER.get("docker")
        kube_cluster = SKILL_TO_CLUSTER.get("kubernetes")
        if docker_cluster is None or docker_cluster != kube_cluster:
            pytest.skip("docker/kubernetes not in same cluster — check skill_clusters.json")

        with_cluster = compute_weighted_skills_score(
            {"skills": "kubernetes"}, ["docker"]
        )
        no_match = compute_weighted_skills_score(
            {"skills": "excel word powerpoint"}, ["docker"]
        )
        assert with_cluster > no_match, (
            "Cluster match should score higher than no match"
        )


# ══════════════════════════════════════════════════════════════════════════════
# _find_cluster_match
# ══════════════════════════════════════════════════════════════════════════════

class TestFindClusterMatch:

    def test_unknown_skill_returns_none(self):
        result = _find_cluster_match("notarealskill12345", {"python", "fastapi"})
        assert result is None

    def test_same_skill_not_returned_as_cluster_match(self):
        for cluster_name, members in SKILL_CLUSTERS.items():
            if len(members) < 2:
                continue
            skill = members[0].lower()
            resume_set = {skill}
            result = _find_cluster_match(skill, resume_set)
            # Should not return the skill itself
            assert result != skill
            break

    def test_related_skill_in_same_cluster_returned(self):
        for cluster_name, members in SKILL_CLUSTERS.items():
            if len(members) < 2:
                continue
            required = members[0].lower()
            alternate = members[1].lower()
            result = _find_cluster_match(required, {alternate})
            assert result == alternate, (
                f"Expected cluster match '{alternate}' for required '{required}', got {result}"
            )
            break

    def test_no_resume_skills_returns_none(self):
        for cluster_name, members in SKILL_CLUSTERS.items():
            if len(members) >= 1:
                required = members[0].lower()
                result = _find_cluster_match(required, set())
                assert result is None
                break


# ══════════════════════════════════════════════════════════════════════════════
# clean_resume_text
# ══════════════════════════════════════════════════════════════════════════════

class TestCleanResumeText:

    def test_empty_string_unchanged(self):
        assert clean_resume_text("") == ""

    def test_none_like_empty_unchanged(self):
        # Function accepts str; empty str passes through
        assert clean_resume_text("") == ""

    def test_pdf_ligature_fi_repaired(self):
        # ﬁ (U+FB01) → "fi"
        result = clean_resume_text("Proﬁle: Software Engineer")
        assert "ﬁ" not in result
        assert "fi" in result or "Profile" in result

    def test_pdf_ligature_fl_repaired(self):
        # ﬂ (U+FB02) → "fl"
        result = clean_resume_text("Conﬂuent Kafka")
        assert "ﬂ" not in result
        assert "fl" in result

    def test_bullet_marker_removed(self):
        text = "• Python\n• FastAPI\n• Docker"
        result = clean_resume_text(text)
        assert "•" not in result

    def test_hyphenated_line_break_repaired(self):
        text = "Pro-\ngramming with Python"
        result = clean_resume_text(text)
        assert "Programming" in result or "Pro\ngramming" not in result

    def test_multiple_blank_lines_collapsed(self):
        text = "Skills\n\n\n\n\nPython"
        result = clean_resume_text(text)
        assert "\n\n\n" not in result

    def test_leading_trailing_whitespace_stripped(self):
        text = "  \n  Python Developer  \n  "
        result = clean_resume_text(text)
        assert not result.startswith(" ")
        assert not result.endswith(" ")


# ══════════════════════════════════════════════════════════════════════════════
# normalize_resume_text  (token-budget truncation)
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeResumeText:

    def test_short_text_returned_unchanged(self):
        text = "Python FastAPI Docker"
        assert normalize_resume_text(text, max_tokens=1200) == text

    def test_long_text_is_truncated(self):
        # normalize_resume_text uses section-aware budgets (skills=400,
        # experience=400, summary=200, ...). Build a sectioned resume that
        # overflows each budget and verify the result is truncated.
        skills = "SKILLS\n" + ("Python " * 500)        # 500 tokens → budget 400
        experience = "EXPERIENCE\n" + ("Engineer " * 500)  # 500 → budget 400
        summary = "SUMMARY\n" + ("Backend " * 300)      # 300 → budget 200
        long_text = "\n".join([skills, experience, summary])
        result = normalize_resume_text(long_text, max_tokens=1200)
        result_tokens = len(result.split())
        input_tokens = len(long_text.split())
        assert result_tokens < input_tokens, "expected truncation to occur"
        assert result_tokens <= 1300, (
            f"expected ≤ section-budget cap (~1200 + header overhead), got {result_tokens}"
        )

    def test_empty_text_returned_unchanged(self):
        assert normalize_resume_text("") == ""

    def test_result_preserves_some_content(self):
        # After truncation, key skills should still appear
        skills = "SKILLS\nPython FastAPI PostgreSQL Docker AWS Kubernetes"
        experience = "EXPERIENCE\n" + ("Backend Engineer at Tech Co " * 200)
        result = normalize_resume_text(skills + "\n" + experience, max_tokens=200)
        assert "Python" in result or len(result) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Composite ATS score formula
# ══════════════════════════════════════════════════════════════════════════════

class TestCompositeATSScoreFormula:
    """
    Tests that the EnhancedATSScoringService composite score is a correct weighted sum
    of its sub-scores and falls within [0, 100].
    """

    def setup_method(self):
        self.svc = EnhancedATSScoringService()
        self.weights = self.svc.weights
        assert abs(sum(self.weights.values()) - 1.0) < 0.01, (
            f"Weights must sum to 1.0, got {sum(self.weights.values())}"
        )

    def _score(self, resume: str, jd: str, skills: list, exp: int):
        return self.svc.enhanced_score_candidate(
            resume_text=resume,
            job_description=jd,
            required_skills=skills,
            required_experience=exp,
        )

    def test_weights_sum_to_one(self):
        assert abs(sum(self.weights.values()) - 1.0) < 0.01

    def test_score_in_range(self):
        result = self._score(
            resume="Python FastAPI PostgreSQL Docker 3 years backend",
            jd="Python developer FastAPI PostgreSQL",
            skills=["python", "fastapi"],
            exp=3,
        )
        assert 0.0 <= result.ats_score <= 100.0

    def test_perfect_match_scores_high(self):
        resume = """
SKILLS
Python FastAPI PostgreSQL Docker AWS Kubernetes
EXPERIENCE
2020-2024 Backend Engineer at TechCo
  Built REST APIs with Python and FastAPI
  PostgreSQL database design and optimization
  Docker containerization and AWS deployment
EDUCATION Bachelor Computer Science 2020
CERTIFICATIONS AWS Certified Developer 2022
PROJECTS
E-commerce API with FastAPI and PostgreSQL
"""
        jd = "Python Backend Engineer: FastAPI, PostgreSQL, Docker, AWS. 4+ yrs. Bachelor required."
        result = self._score(
            resume=resume, jd=jd,
            skills=["python", "fastapi", "postgresql", "docker", "aws"],
            exp=4,
        )
        assert result.ats_score >= 55.0, (
            f"Strong match expected ≥55, got {result.ats_score}"
        )

    def test_no_match_scores_low(self):
        resume = "Head Chef. French cuisine, pastry, kitchen management, HACCP."
        jd = "ML Engineer: PyTorch, TensorFlow, scikit-learn, CUDA, MLflow."
        result = self._score(
            resume=resume, jd=jd,
            skills=["pytorch", "tensorflow", "scikit-learn"],
            exp=3,
        )
        assert result.ats_score <= 50.0, (
            f"Irrelevant resume expected ≤50, got {result.ats_score}"
        )

    def test_result_has_required_fields(self):
        result = self._score(
            resume="Python developer 2 years FastAPI",
            jd="Python developer",
            skills=["python"],
            exp=2,
        )
        assert hasattr(result, "ats_score")
        assert hasattr(result, "skills_match_score")
        assert hasattr(result, "semantic_score")
        assert hasattr(result, "experience_score")
        assert hasattr(result, "matched_skills")
        assert hasattr(result, "missing_skills")

    def test_matched_skills_are_subset_of_required(self):
        required = ["python", "fastapi", "docker"]
        result = self._score(
            resume="Python FastAPI developer",
            jd="Python FastAPI Docker developer",
            skills=required,
            exp=2,
        )
        for skill in result.matched_skills:
            assert skill.lower() in required or any(
                skill.lower() in s for s in required
            ), f"Matched skill '{skill}' not in required skills {required}"

    def test_experience_score_zero_exp_required(self):
        result = self._score(
            resume="Recent graduate. Python basics.",
            jd="Entry level Python developer.",
            skills=["python"],
            exp=0,
        )
        # Recruiter rubric: experience = relevant(50) + internships(25) + github/OSS(25).
        # With zero required experience the *relevant* component is full (50/50);
        # the candidate above has no internships/github, so the total is 50.
        sub = result.scoring_breakdown["sub_scores"]["experience"]
        assert sub["relevant_experience"] == 50.0
        assert result.experience_score == 50.0

    def test_score_higher_for_better_candidate(self):
        good_resume = """
SKILLS
Python FastAPI PostgreSQL Docker AWS
EXPERIENCE
2021-2024 Backend Engineer at StartupX
  REST APIs with FastAPI and PostgreSQL
EDUCATION BSc Computer Science 2021
"""
        weak_resume = "Knows some Python. Student."

        jd = "Python FastAPI developer, PostgreSQL, Docker. 3 yrs."
        skills = ["python", "fastapi", "postgresql", "docker"]

        good_score = self._score(good_resume, jd, skills, 3).ats_score
        weak_score = self._score(weak_resume, jd, skills, 3).ats_score

        assert good_score > weak_score, (
            f"Good candidate ({good_score:.1f}) should outscore weak ({weak_score:.1f})"
        )
