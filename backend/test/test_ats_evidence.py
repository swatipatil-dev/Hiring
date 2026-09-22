"""Tests for per-factor resume excerpts (src/services/ats_evidence.py).

The contract worth protecting is that an excerpt is *the resume's own words*.
An excerpt that paraphrases, or that quotes a line not actually containing the
thing it claims to prove, is worse than no excerpt: it lends a wrong score the
appearance of justification.
"""

import pytest

from src.services.ats_evidence import (
    MAX_EXCERPTS_PER_FACTOR,
    collect_evidence,
    _find_line,
    _is_heading,
    _rank_jd_keywords,
    _tidy,
)

RESUME = """SKILLS
Python, JS, Docker, PostgreSQL

EXPERIENCE
Senior Backend Engineer at Acme Corp (2020-2024)
Built and scaled a Python microservice handling 40k requests per second.

EDUCATION
B.Tech in Computer Science, IIT Bombay, CGPA 8.7

CERTIFICATIONS
AWS Certified Solutions Architect
"""

SECTIONS = {
    "skills": "Python, JS, Docker, PostgreSQL",
    "experience": (
        "Senior Backend Engineer at Acme Corp (2020-2024)\n"
        "Built and scaled a Python microservice handling 40k requests per second."
    ),
    "education": "B.Tech in Computer Science, IIT Bombay, CGPA 8.7",
}

JD = (
    "We need a Python engineer to build scalable backend microservice systems. "
    "Python and Docker experience required. Backend microservice design is central."
)


def evidence(**overrides):
    kwargs = dict(
        resume_text=RESUME,
        sections=SECTIONS,
        job_description=JD,
        matched_skills=["python", "docker"],
        resume_skills=["Python", "JS", "Docker", "PostgreSQL"],
        education=[{"degree": "b.tech", "score": 60, "confidence": 0.85}],
        work_experience=[{
            "company": "Acme Corp",
            "title": "Senior Backend Engineer",
            "duration_months": 48,
            "extracted_text": "Senior Backend Engineer at Acme Corp",
        }],
        certifications=["AWS Certified Solutions Architect"],
        projects=[],
    )
    kwargs.update(overrides)
    return collect_evidence(**kwargs)


class TestQuotesAreVerbatim:
    def test_every_quote_appears_in_the_resume(self):
        """The whole point: an excerpt is text the candidate actually wrote."""
        normalised_resume = " ".join(RESUME.split()).lower()
        for factor, excerpts in evidence().items():
            for item in excerpts:
                quote = item["quote"].rstrip("…").strip().lower()
                assert quote in normalised_resume, f"{factor} quoted text not in resume: {quote!r}"

    def test_every_excerpt_has_the_three_fields(self):
        for excerpts in evidence().values():
            for item in excerpts:
                assert set(item) == {"quote", "section", "supports"}
                assert item["quote"] and item["supports"]

    def test_sections_are_attributed_from_the_real_section_map(self):
        skills = evidence()["skills_match"]
        assert skills and skills[0]["section"] == "skills"


class TestSkillEvidence:
    def test_skills_sharing_one_line_are_all_reported(self):
        """Regression: dedupe by quote once dropped every skill after the first."""
        supports = evidence()["skills_match"][0]["supports"]
        assert "Python" in supports and "Docker" in supports

    def test_a_shared_line_is_quoted_only_once(self):
        quotes = [e["quote"] for e in evidence()["skills_match"]]
        assert len(quotes) == len(set(quotes))

    def test_alias_in_resume_is_found_from_normalised_skill(self):
        """The scorer matches "javascript"; the resume only ever says "JS"."""
        result = evidence(
            matched_skills=["javascript"],
            normalize=lambda s: {"JS": "javascript"}.get(s, s),
        )
        assert result["skills_match"], "alias spelling should still be locatable"
        assert "JS" in result["skills_match"][0]["quote"]

    def test_unmatched_preferred_skill_is_not_quoted(self):
        result = evidence(matched_skills=[], preferred_skills=["rust"])
        assert result["skills_match"] == []


class TestWholeWordMatching:
    def test_short_skill_does_not_match_inside_a_longer_word(self):
        """"R" must not match "your"; "go" must not match "Google"."""
        lines = ["Worked on your Google Cloud migration"]
        assert _find_line(lines, "r", whole_word=True) is None
        assert _find_line(lines, "go", whole_word=True) is None

    def test_dotted_degree_token_still_matches(self):
        assert _find_line(["B.Tech in Computer Science"], "b.tech", whole_word=True) is not None

    def test_phantom_degree_from_substring_yields_no_excerpt(self):
        """"Bombay" contains "mba"; that parser artefact must not be dressed
        up as evidence of a master's degree."""
        result = evidence(education=[{"degree": "mba", "score": 80, "confidence": 0.85}])
        assert result["education"] == []

    def test_genuine_degree_is_quoted(self):
        result = evidence(education=[{"degree": "b.tech", "score": 60, "confidence": 0.85}])
        assert len(result["education"]) == 1
        assert "B.Tech" in result["education"][0]["quote"]


class TestKeywordEvidence:
    def test_bare_section_header_is_not_used_as_proof(self):
        quotes = [e["quote"].lower() for e in evidence()["keyword"]]
        assert "experience" not in quotes
        assert "certifications" not in quotes

    def test_keywords_rank_by_frequency_in_the_jd(self):
        ranked = _rank_jd_keywords("alpha alpha alpha beta beta gamma")
        assert ranked[:2] == ["alpha", "beta"]

    def test_stopwords_are_not_ranked(self):
        assert "with" not in _rank_jd_keywords("with with with alpha")

    @pytest.mark.parametrize("line,expected", [
        ("EXPERIENCE", True),
        ("Certifications:", True),
        ("Senior Backend Engineer at Acme Corp", False),
        ("Python, JS, Docker, PostgreSQL", False),
    ])
    def test_heading_detection(self, line, expected):
        assert _is_heading(line) is expected


class TestLimitsAndShape:
    def test_all_seven_factor_keys_are_always_present(self):
        assert set(evidence()) == {
            "education", "skills_match", "experience",
            "certification", "semantic", "project", "keyword",
        }

    def test_no_factor_exceeds_the_cap(self):
        many = [f"skill{i}" for i in range(40)]
        resume = "SKILLS\n" + "\n".join(f"skill{i} experience line here" for i in range(40))
        result = collect_evidence(
            resume_text=resume, sections={}, job_description=JD,
            matched_skills=many, resume_skills=many,
        )
        for factor, excerpts in result.items():
            assert len(excerpts) <= MAX_EXCERPTS_PER_FACTOR, factor

    def test_long_quote_is_truncated_with_an_ellipsis(self):
        assert _tidy("word " * 200).endswith("…")

    def test_factor_with_no_support_is_an_empty_list_not_missing(self):
        assert evidence(projects=[])["project"] == []


class TestDegradesQuietly:
    def test_empty_inputs_do_not_raise(self):
        result = collect_evidence(resume_text="", sections={}, job_description="")
        assert all(v == [] for v in result.values())

    def test_malformed_entries_are_skipped(self):
        result = collect_evidence(
            resume_text=RESUME, sections=SECTIONS, job_description=JD,
            education=["not a dict"], work_experience=[None], projects=["bad"],
            cluster_matched_skills=["bad"],
        )
        assert result["education"] == [] and result["experience"] == []

    def test_a_failing_normaliser_does_not_lose_evidence(self):
        def boom(_):
            raise ValueError("normaliser exploded")

        result = evidence(normalize=boom)
        assert result["skills_match"], "evidence should survive a bad normaliser"
