"""Tests for evidence-weighted skill scoring.

The property under protection: a required skill *demonstrated* in a job or a
project is worth more than the same skill *claimed* in a comma-separated list.
Before this rubric, a resume that was nothing but a skills block scored the
same on the skills factor as one that shipped production systems using them.

The second property, equally important, is that nobody is punished for
formatting. A resume whose headings we fail to recognise must score exactly
what the old flat matcher gave it — never less.
"""

import pytest

from src.services.enhanced_ats_service import (
    DEFAULT_ATS_WEIGHTS,
    SKILL_EVIDENCE_WEIGHTS,
    EnhancedATSScoringService,
    detect_sections,
    normalize_resume_text,
    score_required_skills_with_evidence,
)

svc = EnhancedATSScoringService()

REQ = ["python", "docker", "aws"]
RESUME_SKILLS = ["Python", "Docker", "AWS"]

JD = (
    "Senior backend engineer. Build scalable services with Python and FastAPI on "
    "PostgreSQL, containerised with Docker and deployed to AWS."
)
PAIR_REQ = ["python", "fastapi", "postgresql", "docker", "aws"]
_EDU = "EDUCATION\nB.Tech Computer Science, IIT Kanpur, CGPA 8.5\n"

LISTER = """SKILLS
Python, FastAPI, PostgreSQL, Docker, AWS, Kubernetes, Redis, Kafka

EXPERIENCE
Software Engineer at Acme Corp, 2020 - 2024
Worked on various backend systems and collaborated with the team.
Participated in code reviews and agile ceremonies.
""" + _EDU

DEMONSTRATOR = """SKILLS
Backend engineering

EXPERIENCE
Software Engineer at Acme Corp, 2020 - 2024
Built the billing API in Python with FastAPI against PostgreSQL.
Containerised the service with Docker and deployed it to AWS.

PROJECTS
Ledger service written in Python, packaged with Docker, hosted on AWS.
""" + _EDU


def pct(sections, required=REQ, resume=RESUME_SKILLS):
    return score_required_skills_with_evidence(sections, resume, required)[0]


class TestDemonstratedBeatsListed:
    def test_experience_outscores_a_skills_list(self):
        listed = pct({"skills": "Python, Docker, AWS"})
        proven = pct({"experience": "Built the billing API in Python, deployed on AWS via Docker"})
        assert proven > listed

    def test_projects_rank_equal_to_experience(self):
        """The user named both; neither is the lesser evidence."""
        assert SKILL_EVIDENCE_WEIGHTS["projects"] == SKILL_EVIDENCE_WEIGHTS["experience"]
        experience = pct({"experience": "Python Docker AWS in production"})
        projects = pct({"projects": "Python Docker AWS in production"})
        assert experience == projects

    def test_evidence_table_ranks_proof_above_claims(self):
        w = SKILL_EVIDENCE_WEIGHTS
        assert w["experience"] > w["skills"]
        assert w["projects"] > w["skills"]
        assert w["skills"] > w["hobbies"]


class TestOrderIndependence:
    def test_section_order_does_not_change_the_score(self):
        """Regression: taking the first-seen section made the score depend on
        the order sections happened to appear in the document."""
        skills_first = pct({"skills": "Python Docker AWS", "experience": "used Python Docker AWS daily"})
        exp_first = pct({"experience": "used Python Docker AWS daily", "skills": "Python Docker AWS"})
        assert skills_first == exp_first

    def test_a_skill_in_both_sections_earns_the_stronger_one(self):
        both = pct({"skills": "Python Docker AWS", "experience": "used Python Docker AWS daily"})
        experience_only = pct({"experience": "used Python Docker AWS daily"})
        assert both == experience_only


class TestMatching:
    def test_duplicate_required_skills_do_not_halve_the_score(self):
        """A JD naming both "JS" and "JavaScript" is one skill, not two."""
        assert svc.calculate_skills_match(["JavaScript"], ["JS", "JavaScript"]) == 100.0

    @pytest.mark.parametrize("resume,required", [
        (["django", "mongodb"], ["go"]),
        (["javascript"], ["java"]),
        (["scala"], ["scale"]),
    ])
    def test_substring_does_not_credit_an_unrelated_skill(self, resume, required):
        assert svc.calculate_skills_match(resume, required) == 0.0

    def test_whole_word_phrase_containment_still_counts(self):
        """The case the substring check existed for must survive."""
        assert svc.calculate_skills_match(["aws lambda"], ["lambda"]) == 100.0

    def test_evidence_path_agrees_with_flat_path_about_presence(self):
        assert pct({"experience": "django and mongodb work"}, required=["go"], resume=["django", "mongodb"]) == 0.0


class TestNoFormattingPenalty:
    def test_headerless_resume_scores_exactly_the_flat_value(self):
        """A resume we could not segment must score what it always did."""
        resume_skills, required = RESUME_SKILLS, REQ
        flat = svc.calculate_skills_match(resume_skills, required)
        score, detail = svc.score_skills_v2(resume_skills, required, [], sections={"other": "Python Docker AWS"})
        assert detail["evidence_weighted"] is False
        assert score == pytest.approx(flat)

    def test_no_sections_at_all_falls_back(self):
        score, detail = svc.score_skills_v2(RESUME_SKILLS, REQ, [], sections=None)
        assert detail["evidence_weighted"] is False
        assert score == pytest.approx(svc.calculate_skills_match(RESUME_SKILLS, REQ))


class TestNeverInflates:
    """Evidence weights are all <= 1.0, so the discount can only reduce.

    This is the invariant that bounds the blast radius of a re-score, and it is
    checked against production data before any backfill is applied.
    """

    @pytest.mark.parametrize("sections", [
        {"skills": "Python, Docker, AWS"},
        {"experience": "Python Docker AWS"},
        {"projects": "Python Docker AWS"},
        {"hobbies": "Python Docker AWS"},
        {"education": "Python Docker AWS"},
        {"skills": "Python", "experience": "Docker", "projects": "AWS"},
    ])
    def test_evidence_score_never_exceeds_flat_score(self, sections):
        flat = svc.calculate_skills_match(RESUME_SKILLS, REQ)
        assert pct(sections) <= flat + 1e-9


class TestBreakdownShape:
    def test_detail_reports_where_each_skill_was_found(self):
        _, detail = svc.score_skills_v2(
            RESUME_SKILLS, REQ, [],
            sections={"skills": "Python", "experience": "Docker and AWS in production"},
        )
        assert detail["evidence_weighted"] is True
        by_skill = {e["skill"]: e for e in detail["skill_evidence"]}
        assert by_skill["python"]["section"] == "skills"
        assert by_skill["docker"]["section"] == "experience"
        assert detail["demonstrated_count"] == 2
        assert detail["listed_only_count"] == 1

    def test_flat_score_is_reported_alongside_the_discounted_one(self):
        _, detail = svc.score_skills_v2(RESUME_SKILLS, REQ, [], sections={"skills": "Python Docker AWS"})
        assert detail["required_flat_pct"] == 100.0
        assert detail["required_skills"] < 100.0  # discounted below the /100 max

    def test_missing_skill_is_recorded_with_zero_credit(self):
        _, detail = svc.score_skills_v2(["Python"], ["python", "rust"], [], sections={"skills": "Python"})
        rust = [e for e in detail["skill_evidence"] if e["skill"] == "rust"][0]
        assert rust["status"] == "missing" and rust["credit"] == 0.0


class TestSectionsSurviveNormalisation:
    def test_a_long_resume_keeps_its_sections(self):
        """Without this the whole feature no-ops on exactly the senior,
        project-heavy resumes it exists to reward.

        `normalize_resume_text` rebuilt the document from section bodies, and
        `detect_sections` consumes the header line — so every resume over the
        token budget collapsed to a single "other" bucket.
        """
        long_resume = (
            "SUMMARY\nBackend engineer.\n\nSKILLS\nPython, Docker\n\nEXPERIENCE\n"
            + "\n".join(
                f"Built service {i} using Python and Docker in production at scale for many users."
                for i in range(120)
            )
            + "\n\nEDUCATION\nB.Tech Computer Science\n"
        )
        assert len(long_resume.split()) > 1200, "fixture must exceed the token budget"
        sections = detect_sections(normalize_resume_text(long_resume))
        assert len(sections) > 1
        assert "experience" in sections and "skills" in sections

    def test_prepared_resume_keeps_sections_for_a_long_resume(self):
        long_resume = (
            "SKILLS\nPython, Docker\n\nEXPERIENCE\n"
            + "\n".join(f"Shipped Python service {i} with Docker to production." for i in range(200))
        )
        prepared = svc.prepare_resume(long_resume)
        assert "experience" in prepared.sections


class TestExperienceYearsScoping:
    def test_education_dates_are_not_counted_as_work_experience(self):
        resume = (
            "EXPERIENCE\nEngineer at Acme 2022 - 2024\n\n"
            "EDUCATION\nB.Tech Computer Science 2018 - 2022\n"
        )
        assert svc.extract_experience_years(resume) == 2.0

    def test_headerless_resume_keeps_the_old_whole_document_behaviour(self):
        resume = "Engineer at Acme 2022 - 2024. B.Tech Computer Science 2018 - 2022."
        assert svc.extract_experience_years(resume) == 6.0


class TestEndToEnd:
    def test_demonstrator_outranks_lister(self):
        """The acceptance test for the whole change.

        Order is pinned, not magnitudes, so this survives future weight tuning.
        """
        lister = svc.enhanced_score_candidate(LISTER, JD, PAIR_REQ, 3, "bachelor")
        demo = svc.enhanced_score_candidate(DEMONSTRATOR, JD, PAIR_REQ, 3, "bachelor")
        assert demo.ats_score > lister.ats_score
        assert demo.skills_match_score > lister.skills_match_score

    def test_lister_is_discounted_below_its_flat_match(self):
        lister = svc.enhanced_score_candidate(LISTER, JD, PAIR_REQ, 3, "bachelor")
        skills = lister.scoring_breakdown["sub_scores"]["skills"]
        assert skills["required_flat_pct"] == 100.0
        assert skills["demonstrated_count"] == 0
        assert lister.skills_match_score < 100.0

    def test_breakdown_carries_the_rubric_version(self):
        result = svc.enhanced_score_candidate(DEMONSTRATOR, JD, PAIR_REQ, 3, "bachelor")
        assert result.scoring_breakdown["rubric_version"] >= 2
        assert result.scoring_breakdown["weights_used"] == DEFAULT_ATS_WEIGHTS


class TestMandatoryCoverage:
    """The composite markdown for missing must-have skills.

    It lives on the composite, not inside the skills sub-score, because that
    sub-score is only 25% of the total — nothing inside it can express "this
    candidate does not have the required skills". Before this, a candidate
    matching zero requirements still reached 75, reported as "Very Good —
    Recommended, shortlist for interview".
    """

    JD = "Backend engineer. Python, FastAPI, PostgreSQL, Docker, AWS."
    REQ = ["python", "fastapi", "postgresql", "docker", "aws"]
    _EDU = "EDUCATION\nB.Tech Computer Science, IIT Kanpur, CGPA 8.5\n"
    QUALIFIED = (
        "SKILLS\nBackend\n\nEXPERIENCE\nEngineer at Acme, 2020 - 2024\n"
        "Built the billing API in Python with FastAPI on PostgreSQL.\n"
        "Containerised with Docker and deployed to AWS.\n" + _EDU
    )
    NO_SKILLS = (
        "SKILLS\nExcel, PowerPoint\n\nEXPERIENCE\nAnalyst at Beta, 2019 - 2024\n"
        "Prepared reports and managed stakeholder communication.\n" + _EDU
    )

    def score(self, resume, preferred):
        return svc.enhanced_score_candidate(
            resume, self.JD, self.REQ, 3, "bachelor", preferred_skills=preferred)

    def test_missing_all_mandatory_is_penalised_but_not_rejected(self):
        """Heavy, and deliberately not a knockout.

        A parser miss must cost a candidate their place in the ordering, never
        their existence in it — so they keep a score, a rating and a full
        breakdown.
        """
        r = self.score(self.NO_SKILLS, ["kubernetes"])
        assert r.ats_score > 0
        assert r.ats_score < 50          # below the auto-promote threshold
        assert r.rating                  # still rated
        assert r.scoring_breakdown["individual_scores"]
        assert r.scoring_breakdown["bonus"]["mandatory_factor"] < 1.0

    def test_full_coverage_is_untouched(self):
        """The invariant the whole design rests on: a candidate who meets every
        requirement is not moved at all."""
        r = self.score(self.QUALIFIED, ["kubernetes"])
        assert r.scoring_breakdown["bonus"]["mandatory_factor"] == 1.0

    def test_legacy_job_is_exempt(self):
        """preferred_skills=None means nobody has split this job's lists, so
        everything in `required` may be a nice-to-have HR had nowhere else to
        file. Marking candidates down against it would punish them for a data
        model they never saw."""
        for resume in (self.QUALIFIED, self.NO_SKILLS):
            r = self.score(resume, None)
            assert r.scoring_breakdown["bonus"]["mandatory_factor"] == 1.0

    def test_opting_in_only_moves_the_unqualified(self):
        qualified = (self.score(self.QUALIFIED, None).ats_score,
                     self.score(self.QUALIFIED, []).ats_score)
        unqualified = (self.score(self.NO_SKILLS, None).ats_score,
                       self.score(self.NO_SKILLS, []).ats_score)
        assert qualified[0] == qualified[1]      # unchanged
        assert unqualified[1] < unqualified[0]   # marked down

    def test_job_with_no_required_skills_is_exempt(self):
        r = svc.enhanced_score_candidate(
            self.QUALIFIED, self.JD, [], 3, "bachelor", preferred_skills=["aws"])
        assert r.scoring_breakdown["bonus"]["mandatory_factor"] == 1.0

    def test_breakdown_exposes_the_markdown(self):
        """A 20-point markdown must be auditable, not mysterious."""
        b = self.score(self.NO_SKILLS, ["kubernetes"]).scoring_breakdown["bonus"]
        assert "mandatory_coverage" in b and "mandatory_factor" in b


class TestMissingSkillsAgreesWithScoring:
    """`missing_skills` is shown next to the markdown it justifies, so the two
    must be decided by the same rule. A plain set difference disagreed with
    `_match_credit`: a candidate could earn full credit for "AWS Lambda"
    satisfying a required "lambda" and still be told they were missing it."""

    def test_phrase_match_is_not_reported_missing(self):
        r = svc.enhanced_score_candidate(
            "SKILLS\nAWS Lambda\n\nEXPERIENCE\nBuilt serverless handlers on AWS Lambda.\n",
            "Serverless engineer. Lambda required.", ["lambda"], 0, "bachelor",
            preferred_skills=[])
        assert "lambda" not in r.missing_skills
        assert r.skills_match_score > 0

    def test_genuinely_absent_skill_is_reported_missing(self):
        r = svc.enhanced_score_candidate(
            "SKILLS\nExcel\n\nEXPERIENCE\nReporting.\n",
            "Rust engineer.", ["rust"], 0, "bachelor", preferred_skills=[])
        assert "rust" in r.missing_skills


class TestUnreadableResumeIsNotUnqualified:
    """A resume we could not read is not a candidate who lacks the skills.

    The coverage multiplier computes coverage from `required_flat_pct`. When
    `extract_skills` returns [] — skills in an image, a scanned PDF, an OCR
    failure — that percentage is 0, and the candidate took the maximum penalty:
    harsher than someone who demonstrably matched one requirement in ten.

    The damage is not at the empty extreme (a blank resume scores ~0 anyway, so
    the multiplier is moot there). It lands on a resume that parses well EXCEPT
    for skills, which is exactly the common failure.
    """

    JD = "Backend engineer. Python, FastAPI, PostgreSQL, Docker, AWS."
    REQ = ["python", "fastapi", "postgresql", "docker", "aws"]

    # Strong on every channel the parser CAN read, but no skill token survives
    # extraction — the shape of a resume whose skills sat in an image. Kept free
    # of incidental technology words on purpose: an earlier draft of this fixture
    # ended "AWS Certified Solutions Architect", and extract_skills duly found
    # "aws", so the case under test never arose and the test failed against
    # correct code.
    SKILLS_UNREADABLE = """PROFESSIONAL SUMMARY
Experienced professional with eight years in the sector.

EXPERIENCE
Senior Associate at Acme Corp, 2016 - 2024
Led the regional team and owned the annual planning programme.
Mentored four colleagues and ran the weekly review meeting.

EDUCATION
B.Tech Computer Science, IIT Kanpur, CGPA 8.5
"""

    # Same body, but one requirement genuinely present.
    ONE_SKILL_MATCHED = SKILLS_UNREADABLE.replace(
        "PROFESSIONAL SUMMARY", "SKILLS\nPython\n\nPROFESSIONAL SUMMARY")

    def score(self, resume):
        return svc.enhanced_score_candidate(
            resume, self.JD, self.REQ, 3, "bachelor", preferred_skills=[])

    def test_no_skills_read_is_exempt_from_the_markdown(self):
        r = self.score(self.SKILLS_UNREADABLE)
        b = r.scoring_breakdown["bonus"]
        assert b["mandatory_factor"] == 1.0
        assert b["mandatory_exempt_reason"] == "no_skills_read"

    def test_unreadable_does_not_score_below_one_genuine_match(self):
        """The ordering inversion — the test that would have caught this.

        Before the fix the unreadable resume took factor 0.55 while the resume
        matching a single requirement took 0.595, so being unreadable was worse
        than being unqualified.
        """
        unreadable = self.score(self.SKILLS_UNREADABLE).ats_score
        one_match = self.score(self.ONE_SKILL_MATCHED).ats_score
        assert unreadable >= one_match

    def test_wrong_skills_are_still_penalised(self):
        """The guard against over-correcting.

        This resume's skills WERE read — they are simply the wrong ones. That is
        a finding about the candidate, so the markdown must still apply. Without
        this, the fix would become an exemption for anyone missing every
        requirement.
        """
        wrong = self.SKILLS_UNREADABLE.replace(
            "PROFESSIONAL SUMMARY", "SKILLS\nExcel, PowerPoint\n\nPROFESSIONAL SUMMARY")
        b = self.score(wrong).scoring_breakdown["bonus"]
        assert b["mandatory_factor"] < 1.0
        assert b["mandatory_exempt_reason"] is None

    def test_legacy_and_no_requirement_exemptions_are_named(self):
        legacy = svc.enhanced_score_candidate(
            self.SKILLS_UNREADABLE, self.JD, self.REQ, 3, "bachelor", preferred_skills=None)
        assert legacy.scoring_breakdown["bonus"]["mandatory_exempt_reason"] == "legacy_job"
        no_req = svc.enhanced_score_candidate(
            self.SKILLS_UNREADABLE, self.JD, [], 3, "bachelor", preferred_skills=[])
        assert no_req.scoring_breakdown["bonus"]["mandatory_exempt_reason"] == "no_required_skills"
