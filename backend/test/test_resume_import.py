"""
Resume auto-fill (Smart Profile Import) tests.

Two layers:
  1. Pure unit tests for the section-anchored extraction helpers in
     candidate_profile_routes — no DB, no Ollama, fast. These guard the bug
     fixes documented for the import flow (fake projects from wrapped bullets,
     company coming out as the section heading, degree="be"/missing institution).
  2. Contract tests for POST /import/apply — append-only, skill dedupe + 30-cap,
     and scalar fill-only-if-empty. Runs against the DB in CI.

The /import/parse endpoint itself is exercised indirectly: it depends on real
PDF extraction (pdfplumber), so its parsing logic lives in the helpers tested
here directly rather than through a binary fixture.
"""
import pytest

from routes.candidate_profile_routes import (
    CONF_SECTION,
    CONF_SCAN,
    CONF_INFERRED,
    _projects_from_section,
    _work_experience_from_section,
    _education_from_section,
    _certifications_from_section,
)


# ── helpers: projects ────────────────────────────────────────────────────────

def test_projects_primary_uses_tech_anchor():
    """Title is the non-bullet line directly above a 'Tech:' line."""
    section = "Smart Resume Parser\nTech: Python, spaCy\n- Built an NER pipeline\n"
    assert _projects_from_section(section) == [
        {"name": "Smart Resume Parser", "confidence": CONF_INFERRED}
    ]


def test_projects_ignores_wrapped_bullet_fragments():
    """Regression: lowercase mid-sentence wrapped bullets are NOT fake projects."""
    section = (
        "Hiring Platform\nTech: FastAPI, React\n"
        "- developed an ATS scoring service that\n"
        "ranks candidates by skill overlap\n"
    )
    names = [p["name"] for p in _projects_from_section(section)]
    assert names == ["Hiring Platform"]
    assert not any(n.startswith("ranks") for n in names)


def test_projects_empty_section_returns_empty():
    assert _projects_from_section("") == []
    assert _projects_from_section("   \n  ") == []


# ── helpers: work experience ─────────────────────────────────────────────────

def test_work_experience_company_before_date():
    """Regression: company is the text before the date token, not the heading."""
    section = "Innomax IT Solutions  Jan 2020 - Dec 2021\nSoftware Engineer\n- shipped features\n"
    out = _work_experience_from_section(section)
    assert out and out[0]["company"] == "Innomax IT Solutions"
    assert out[0]["title"] == "Software Engineer"


def test_work_experience_skips_bullets_and_empty():
    assert _work_experience_from_section("") == []
    # A pure bullet line with no date yields nothing.
    assert _work_experience_from_section("- did some things\n- and more") == []


# ── helpers: education ───────────────────────────────────────────────────────

def test_education_degree_and_institution():
    """Regression: degree keeps its full name (not 'be'); institution captured."""
    section = "B-Tech Computer Science and Engineering 2018 - 2022\nMalla Reddy University Hyderabad\n"
    out = _education_from_section(section)
    assert out and "Tech" in out[0]["degree"]
    assert out[0]["institution"] == "Malla Reddy University"  # location dropped


def test_education_keeps_university_of_name():
    """'University of X' must survive intact rather than being cut at the keyword."""
    section = "Master of Science 2015\nUniversity of Madrid Spain\n"
    out = _education_from_section(section)
    assert out and out[0]["institution"].startswith("University of Madrid")


# ── helpers: certifications ──────────────────────────────────────────────────

def test_certifications_keeps_full_name():
    """Regression: full cert name preserved, not truncated to a vendor token."""
    section = "- AWS Certified Solutions Architect – Associate (2022)\n- Google Cloud Professional\n"
    out = _certifications_from_section(section)
    assert "AWS Certified Solutions Architect" in out[0]
    assert any("Google Cloud" in c for c in out)


def test_certifications_empty_returns_empty():
    assert _certifications_from_section("") == []


# ── confidence calibration ───────────────────────────────────────────────────
# The number decides whether a row arrives pre-ticked in the review screen. It was
# miscalibrated: section-anchored rows carried 0.6 (below the 0.7 threshold) while
# whole-document scan rows inherited up to 0.85 from extract_education_info, so the
# BETTER extraction arrived unticked and the worse one pre-ticked. In practice
# nothing was ever pre-ticked. These pin the ordering, not the literals.

# Mirrors HIGH_CONF in frontend/src/pages/hr/../candidate/ResumeImportReview.jsx.
REVIEW_THRESHOLD = 0.7


def test_section_extraction_outranks_whole_document_scan():
    """The ordering that was inverted. This is the whole point of the calibration."""
    assert CONF_SECTION > CONF_SCAN
    assert CONF_SECTION > CONF_INFERRED


def test_section_rows_are_pre_ticked():
    assert CONF_SECTION >= REVIEW_THRESHOLD


def test_scan_and_inferred_rows_are_opt_in():
    """An unanchored guess must never arrive already accepted."""
    assert CONF_SCAN < REVIEW_THRESHOLD
    assert CONF_INFERRED < REVIEW_THRESHOLD


def test_work_from_section_is_confident_enough_to_pre_tick():
    section = "Acme Corp  Jan 2021 - Present\nSenior Backend Engineer\n- Built APIs\n"
    rows = _work_experience_from_section(section)
    assert rows, "the fixture should yield a row"
    assert all(r["confidence"] >= REVIEW_THRESHOLD for r in rows)


def test_education_from_section_is_confident_enough_to_pre_tick():
    section = "B.Tech Computer Science 2019\nAnna University, Chennai\n"
    rows = _education_from_section(section)
    assert rows, "the fixture should yield a row"
    assert all(r["confidence"] >= REVIEW_THRESHOLD for r in rows)


def test_projects_stay_opt_in():
    """A title-shape guess, not a heading-anchored read — it must not pre-tick."""
    section = "Smart Resume Parser\nTech: Python, spaCy\n- Built an NER pipeline\n"
    rows = _projects_from_section(section)
    assert rows
    assert all(r["confidence"] < REVIEW_THRESHOLD for r in rows)


def test_llm_filled_rows_stay_opt_in():
    """AI suggestions are offered, never pre-accepted."""
    from src.services.llm_profile_extractor import _LLM_CONFIDENCE
    assert _LLM_CONFIDENCE < REVIEW_THRESHOLD


# ── contract: POST /import/apply ─────────────────────────────────────────────

@pytest.mark.candidate
def test_import_apply_adds_sections(client, candidate_headers):
    resp = client.post("/api/v1/candidate/profile/import/apply", headers=candidate_headers, json={
        "skills": ["Python", "FastAPI"],
        "work_experience": [{"title": "Engineer", "company": "Acme"}],
        "education": [{"degree": "B-Tech", "institution": "MRU"}],
        "certifications": ["AWS Certified Solutions Architect"],
        "projects": [{"name": "Resume Parser"}],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skills_added"] == 2
    assert body["work_experience_added"] == 1
    assert body["education_added"] == 1
    assert body["certifications_added"] == 1
    assert body["projects_added"] == 1


@pytest.mark.candidate
def test_import_apply_dedupes_skills(client, candidate_headers):
    """Re-applying an existing skill (case-insensitive) does not duplicate it."""
    client.post("/api/v1/candidate/profile/import/apply", headers=candidate_headers,
                json={"skills": ["Python"]})
    resp = client.post("/api/v1/candidate/profile/import/apply", headers=candidate_headers,
                       json={"skills": ["python", "Django"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skills_added"] == 1  # only Django; "python" deduped


@pytest.mark.candidate
def test_import_apply_fills_headline_only_if_empty(client, candidate_headers):
    """Scalar fields are filled only when empty, never overwritten."""
    r1 = client.post("/api/v1/candidate/profile/import/apply", headers=candidate_headers,
                     json={"headline": "First Headline"})
    assert r1.status_code == 200
    client.post("/api/v1/candidate/profile/import/apply", headers=candidate_headers,
                json={"headline": "Second Headline"})

    profile = client.get("/api/v1/candidate/profile", headers=candidate_headers).json()
    assert profile.get("headline") == "First Headline"


@pytest.mark.candidate
def test_import_apply_requires_auth(client):
    assert client.post("/api/v1/candidate/profile/import/apply", json={}).status_code in (401, 403)
