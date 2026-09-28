"""Evidence behind resume scores (src/services/ats_evidence.py and the scorer).

- A skill shown in an experience/project entry earns more than one only listed in a
  skills section; a related skill earns partial credit; each carries an excerpt.
- Skills are matched as whole terms on the full text ("java" is not in "javascript").
- Unknown (not stated) differs from not met: unknown parts are excluded, not scored 0.
- No credit for bare words: "github" in a skills list, or "international" for internship.
"""
import json

import pytest

from src.services import ats_evidence as ev
from src.services.enhanced_ats_service import EnhancedATSScoringService, detect_sections


def units(text):
    return ev.section_units(detect_sections(text))


RESUME_DEMONSTRATED = """Priya Sharma
priya@example.com | +91 98765 43210

Experience
Backend Engineer, Acme Corp (2021 - 2025)
- Built payment APIs in Python and FastAPI serving 2M requests a day.
- Containerised services with Docker and deployed to AWS.

Projects
- Job matcher: Python, scikit-learn, PostgreSQL.

Education
B.Tech Computer Science, IIT Bombay, CGPA 8.4

Skills
Python, FastAPI, Docker, AWS, PostgreSQL, Git
"""

RESUME_LISTED = """Arjun Rao

Experience
Support Associate, Beta Ltd (2021 - 2025)
- Handled customer tickets and wrote status reports.

Education
B.Tech Computer Science, Some College, CGPA 8.4

Skills
Python, FastAPI, Docker, AWS, PostgreSQL, Git
"""


# ── skill evidence ───────────────────────────────────────────────────────────

def test_demonstrated_beats_listed_and_carries_an_excerpt():
    u = units(RESUME_DEMONSTRATED)
    c = ev.classify_skill("FastAPI", u, set())
    assert c["level"] == "demonstrated"
    assert "payment APIs" in c["excerpts"][0]["excerpt"]
    c2 = ev.classify_skill("FastAPI", units(RESUME_LISTED), set())
    assert c2["level"] == "listed"
    assert ev.EVIDENCE_CREDIT["demonstrated"] > ev.EVIDENCE_CREDIT["listed"]


def test_java_is_not_found_inside_javascript():
    u = units("Skills\nJavaScript, TypeScript, React")
    assert ev.classify_skill("Java", u, set())["level"] == "not_found"
    assert ev.classify_skill("JavaScript", u, set())["level"] == "listed"


@pytest.mark.parametrize("skill", ["C++", "C#", ".NET", "Node.js"])
def test_symbol_skills_are_found(skill):
    u = units(f"Experience\n- Built services in {skill} for a trading desk.")
    assert ev.classify_skill(skill, u, set())["level"] == "demonstrated"


def test_contacts_are_redacted_from_excerpts():
    u = units("Experience\n- Python developer, contact me at dev@mail.com or +1 555 123 4567 for Python work.")
    ex = ev.classify_skill("Python", u, set())["excerpts"][0]["excerpt"]
    assert "@" not in ex and "555" not in ex


def test_a_skill_late_in_a_long_resume_is_found():
    svc = EnhancedATSScoringService()
    filler = "Experience\n" + "\n".join(f"- Maintained internal tool number {i} for the finance team." for i in range(400))
    resume = filler + "\n- Built the data pipeline in Rust for the risk team.\n"
    r = svc.enhanced_score_candidate(resume_text=resume, job_description="Rust engineer", required_skills=["Rust"],
                                     required_experience=0, preferred_skills=[])
    assert r.missing_mandatory == []
    item = r.explanation["mandatory"]["items"][0]
    assert item["level"] == "demonstrated" and "Rust" in item["excerpts"][0]["excerpt"]


# ── unknown vs not met ───────────────────────────────────────────────────────

def test_experience_years_unknown_is_not_zero():
    u = units("Skills\nPython")
    years, method, _ = ev.experience_years_evidence(u, extracted_years=0.0, known_years=None)
    assert years is None and method == "not_found"
    score, detail = ev.experience_score(years, required_years=3, work_experiences=[], links_text="", units=u)
    assert detail["relevant_status"] == "unknown" and detail["relevant_experience"] is None


def test_experience_below_is_distinct_from_unknown():
    score, detail = ev.experience_score(1.0, required_years=4, work_experiences=[], links_text="", units=[])
    assert detail["relevant_status"] == "below" and detail["relevant_experience"] < 50


def test_profile_years_win_and_zero_is_known():
    years, method, _ = ev.experience_years_evidence([], extracted_years=5.0, known_years=0)
    assert years == 0.0 and method == "profile"


def test_all_unknown_education_is_excluded_not_zero():
    score, detail = ev.education_score(degree_sub=0.0, cand_degree=0, tier=None, has_institution=False,
                                       cgpa_sub=0.0, cgpa_found=False, units=[])
    assert score is None
    assert detail["degree_status"] == detail["tier_status"] == detail["cgpa_status"] == "unknown"


def test_unknown_factor_is_reweighted_and_lowers_coverage():
    w = {"education": 0.35, "skills_match": 0.25, "experience": 0.15, "certification": 0.10,
         "semantic": 0.05, "project": 0.05, "keyword": 0.05}
    scores = {k: 80.0 for k in w}
    scores["education"] = None
    total, coverage, rows = ev.combine(scores, w)
    assert total == pytest.approx(80.0)          # not dragged down by a zero
    assert coverage == pytest.approx(0.65)
    assert next(r for r in rows if r["key"] == "education")["status"] == "unknown_excluded"


# ── no credit for bare words ─────────────────────────────────────────────────

def test_github_in_a_skills_list_is_not_a_profile():
    _, d = ev.experience_score(None, 0, [], "", units("Skills\nGit, GitHub, Docker"))
    assert not d["has_github"]
    _, d = ev.experience_score(None, 0, [], "https://github.com/priya-dev", [])
    assert d["has_github"]


def test_international_is_not_an_internship():
    _, d = ev.experience_score(None, 0, [{"job_title": "International Sales Lead"}], "", [])
    assert d["internship_count"] == 0
    _, d = ev.experience_score(None, 0, [{"employment_type": "Internship", "job_title": "SDE"}], "", [])
    assert d["internship_count"] == 1


def test_hackathon_only_counts_in_an_evidence_section():
    _, d = ev.experience_score(None, 0, [], "", units("Skills\nHackathons, Python"))
    assert not d["has_hackathon"]
    _, d = ev.experience_score(None, 0, [], "", units("Achievements\n- Won the Smart India Hackathon 2022"))
    assert d["has_hackathon"]


# ── end to end ───────────────────────────────────────────────────────────────

def test_demonstrated_candidate_outranks_listed_one():
    svc = EnhancedATSScoringService()
    kw = dict(job_description="Backend engineer: Python, FastAPI, Docker, AWS.",
              required_skills=["Python", "FastAPI", "Docker", "AWS"], required_experience=2, preferred_skills=[])
    shown = svc.enhanced_score_candidate(resume_text=RESUME_DEMONSTRATED, **kw)
    listed = svc.enhanced_score_candidate(resume_text=RESUME_LISTED, **kw)
    assert shown.skills_match_score > listed.skills_match_score
    assert shown.mandatory_met == listed.mandatory_met == 4       # both "meet" them…
    levels = {c["level"] for c in listed.explanation["mandatory"]["items"]}
    assert levels == {"listed"}                                     # …but only one shows them


def test_explanation_is_bounded():
    items = [{"skill": f"s{i}", "level": "demonstrated",
              "excerpts": [{"section": "experience", "excerpt": "x" * 180}] * 2} for i in range(200)]
    expl = ev.cap_explanation({"mandatory": {"items": items}, "preferred": {"items": []}})
    assert len(json.dumps(expl).encode()) <= ev.EXPLANATION_MAX_BYTES


def test_headings_on_the_same_line_as_their_content_are_recognised():
    """'EXPERIENCE 2018-2024 Manager ...' used to leave every line under "other":
    the dates were never read as experience and scored as zero."""
    import src.services.ats_evidence as ev
    from src.services.enhanced_ats_service import detect_sections
    resume = ("SKILLS SEO, Google Ads\n"
              "EXPERIENCE 2018-2024 Digital Marketing Manager at GrowthBrand\n"
              "Experience with HubSpot campaigns\n"
              "CERTIFICATIONS Google Ads Certified\n")
    units = ev.section_units(detect_sections(resume))
    assert ("experience", "2018-2024 Digital Marketing Manager at GrowthBrand") in units
    # Prose starting with a heading word is not a heading.
    assert ("experience", "Experience with HubSpot campaigns") in units
    assert ("certifications", "Google Ads Certified") in units
    years, method, _ = ev.experience_years_evidence(units, 6.0, None)
    assert (years, method) == (6.0, "resume")
