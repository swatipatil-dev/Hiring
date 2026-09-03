"""
Unit tests for llm_profile_extractor — the Gemini gap-filler behind candidate
resume auto-fill.

Two properties carry the whole feature, and neither can rest on the prompt:

  1. **Empty sections only.** A section the regex extractors DID read is never sent
     to the model and never replaced. Topping up a real list would leave the
     candidate unable to tell which rows came off their own resume.
  2. **Nothing malformed reaches the review form.** Items are validated against the
     real column widths that /import/apply writes into, so a value MySQL would
     truncate is dropped here instead.

Gemini is mocked throughout; these tests make no network calls.

Run with:
    pytest test/test_llm_profile_extractor.py -v
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from src.services import llm_profile_extractor as ex


RESUME = "Priya Raman\nBackend engineer at Acme since 2021. B.Tech, Anna University."

# The parser read work + education, but found no certifications and no projects.
PARSED_PARTIAL = {
    "work_experience": [{"title": "Backend Engineer", "company": "Acme", "confidence": 0.6}],
    "education": [{"degree": "B.Tech", "institution": "Anna University", "confidence": 0.6}],
    "certifications": [],
    "projects": [],
}
PARSED_FULL = {
    "work_experience": [{"title": "Backend Engineer", "company": "Acme"}],
    "education": [{"degree": "B.Tech", "institution": "Anna University"}],
    "certifications": ["AWS Certified Developer"],
    "projects": [{"name": "Rate limiter"}],
}
PARSED_EMPTY = {"work_experience": [], "education": [], "certifications": [], "projects": []}


@pytest.fixture
def gemini(monkeypatch):
    """Force the feature on and hand back a canned model response."""
    monkeypatch.setattr(ex, "LLM_RESUME_EXTRACTION", True, raising=False)
    monkeypatch.setattr(ex, "_enabled", lambda: True)

    def _run(raw, parsed=PARSED_PARTIAL):
        with patch("src.services.gemini_service._generate_content", return_value=raw) as m:
            return ex.extract_sections_sync(RESUME, parsed), m
    return _run


# ── missing_sections ──────────────────────────────────────────────────────────

def test_missing_sections_lists_only_empty_ones():
    assert ex.missing_sections(PARSED_PARTIAL) == ["certifications", "projects"]


def test_missing_sections_empty_when_everything_parsed():
    assert ex.missing_sections(PARSED_FULL) == []


def test_missing_sections_handles_none():
    assert ex.missing_sections(None) == list(ex.SECTIONS)


# ── broken_sections: junk counts as unread ────────────────────────────────────
# The fixtures below are the ACTUAL output observed from a live /import/parse on a
# resume written as prose with no section headings. Before this check existed those
# sections were "non-empty", so the model was never asked and the junk was locked in.

# education: qualification fragments scraped out of sentences, no institution at all
LIVE_JUNK_EDUCATION = [
    {"degree": "b.tech", "institution": "", "confidence": 0.5},
    {"degree": "be", "institution": "", "confidence": 0.5},
    {"degree": "associate", "institution": "", "confidence": 0.5},
]
# certifications: unrelated lines glued together
LIVE_JUNK_CERTS = [
    "Certified\nOutside of work I keep a couple of things\nwritten in Python on top of Redis",
    "Certified Solutions Architect - Associate\nKubernetes Application Developer one",
]


def test_live_junk_education_is_treated_as_unread():
    assert "education" in ex.broken_sections({"education": LIVE_JUNK_EDUCATION})


def test_live_junk_certifications_are_treated_as_unread():
    assert "certifications" in ex.broken_sections({"certifications": LIVE_JUNK_CERTS})


def test_good_education_is_left_alone():
    good = [{"degree": "B.Tech Computer Science", "institution": "Anna University"}]
    assert ex.broken_sections({"education": good}) == []


def test_degree_without_institution_survives_when_it_is_informative():
    """The ATS fallback never supplies an institution; a real degree string is still
    worth keeping, so absence of institution alone must not condemn the section."""
    assert ex.broken_sections({"education": [{"degree": "B.Tech Computer Science",
                                              "institution": ""}]}) == []


def test_one_bad_row_among_good_ones_does_not_condemn_the_section():
    """A single junk row is the candidate's to delete, not ours to discard."""
    mixed = [
        {"degree": "B.Tech Computer Science", "institution": "Anna University"},
        {"degree": "M.Sc Data Science", "institution": "IIT Madras"},
        {"degree": "be", "institution": ""},
    ]
    assert ex.broken_sections({"education": mixed}) == []


def test_good_certifications_are_left_alone():
    assert ex.broken_sections({"certifications": ["AWS Certified Solutions Architect"]}) == []


def test_work_without_any_company_is_treated_as_unread():
    assert "work_experience" in ex.broken_sections({
        "work_experience": [{"title": "Engineer", "company": ""}]})


def test_work_with_companies_is_left_alone():
    assert ex.broken_sections({
        "work_experience": [{"title": "Engineer", "company": "Acme"}]}) == []


def test_sentence_fragment_projects_are_treated_as_unread():
    assert "projects" in ex.broken_sections({
        "projects": [{"name": "ranks candidates by skill overlap"}]})


def test_real_project_titles_are_left_alone():
    assert ex.broken_sections({"projects": [{"name": "Distributed Rate Limiter"}]}) == []


def test_empty_sections_are_not_reported_as_broken():
    """Emptiness is missing_sections' job; reporting it twice would double-count."""
    assert ex.broken_sections(PARSED_EMPTY) == []


# ── fillable_sections: the union the model is actually offered ────────────────

def test_fillable_covers_both_empty_and_broken():
    parsed = {
        "work_experience": [],                       # empty
        "education": LIVE_JUNK_EDUCATION,            # broken
        "certifications": ["AWS Certified Developer"],  # fine
        "projects": [],                              # empty
    }
    assert ex.fillable_sections(parsed) == ["work_experience", "education", "projects"]


def test_fillable_is_empty_when_everything_parsed_well():
    assert ex.fillable_sections(PARSED_FULL) == []


def test_broken_section_is_replaced_not_merged(gemini):
    """All-or-nothing: the junk rows go, the model's rows stand alone."""
    parsed = dict(PARSED_FULL, education=LIVE_JUNK_EDUCATION)
    filled, _ = gemini(json.dumps({
        "education": [{"degree": "B.Tech Computer Science", "institution": "Anna University"}],
    }), parsed=parsed)
    assert len(filled["education"]) == 1
    assert filled["education"][0]["institution"] == "Anna University"


def test_a_well_parsed_section_is_still_never_replaced(gemini):
    """The original guarantee has to survive the new rule."""
    filled, _ = gemini(json.dumps({
        "certifications": ["Something The Model Made Up"],
    }), parsed=dict(PARSED_FULL, education=LIVE_JUNK_EDUCATION))
    assert "certifications" not in filled


# ── Property 1: empty sections only ───────────────────────────────────────────

def test_section_that_parsed_is_never_replaced(gemini):
    """The model volunteers work history the parser already read. Ignore it."""
    filled, _ = gemini(json.dumps({
        "work_experience": [{"title": "Fabricated Role", "company": "Nowhere Inc"}],
        "projects": [{"name": "Rate limiter"}],
    }))
    assert "work_experience" not in filled
    assert [p["name"] for p in filled["projects"]] == ["Rate limiter"]


def test_no_call_when_every_section_parsed(monkeypatch):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content") as m:
        assert ex.extract_sections_sync(RESUME, PARSED_FULL) == {}
        m.assert_not_called()


def test_prompt_asks_only_for_empty_sections():
    prompt = ex._build_prompt(RESUME, ["projects", "certifications"])
    assert '"projects"' in prompt and '"certifications"' in prompt
    assert '"work_experience"' not in prompt and '"education"' not in prompt


def test_prompt_truncates_long_resumes(monkeypatch):
    monkeypatch.setattr(ex, "LLM_RESUME_TEXT_CHARS", 100)
    prompt = ex._build_prompt("z" * 5000, ["projects"])
    assert "z" * 100 in prompt
    assert "z" * 101 not in prompt


def test_unknown_section_is_discarded(gemini):
    filled, _ = gemini(json.dumps({"salary": ["20 LPA"], "projects": [{"name": "Parser"}]}))
    assert set(filled) == {"projects"}


# ── Property 2: validation ────────────────────────────────────────────────────

def test_work_item_needs_at_least_one_side(gemini):
    filled, _ = gemini(json.dumps({
        "work_experience": [{"title": "", "company": ""},
                            {"title": "Engineer", "company": ""}],
    }), parsed=PARSED_EMPTY)
    assert len(filled["work_experience"]) == 1
    assert filled["work_experience"][0]["title"] == "Engineer"
    assert filled["work_experience"][0]["company"] == ""


def test_overlong_institution_is_dropped(gemini):
    """institution_name is VARCHAR(255) — must be caught before /import/apply."""
    filled, _ = gemini(json.dumps({
        "education": [{"degree": "B.Tech", "institution": "x" * 400}],
    }), parsed=PARSED_EMPTY)
    # The degree survives; only the oversized side is discarded.
    assert filled["education"][0]["degree"] == "B.Tech"
    assert filled["education"][0]["institution"] == ""


def test_overlong_degree_is_dropped(gemini):
    """degree is only VARCHAR(100), narrower than institution."""
    filled, _ = gemini(json.dumps({
        "education": [{"degree": "y" * 200, "institution": "Anna University"}],
    }), parsed=PARSED_EMPTY)
    assert filled["education"][0]["degree"] == ""
    assert filled["education"][0]["institution"] == "Anna University"


def test_item_dropped_when_both_sides_overlong(gemini):
    filled, _ = gemini(json.dumps({
        "education": [{"degree": "y" * 200, "institution": "x" * 400}],
    }), parsed=PARSED_EMPTY)
    assert "education" not in filled


def test_overlong_certification_is_dropped(gemini):
    filled, _ = gemini(json.dumps({
        "certifications": ["c" * 300, "AWS Certified Developer"],
    }), parsed=PARSED_EMPTY)
    assert filled["certifications"] == ["AWS Certified Developer"]


@pytest.mark.parametrize("junk", ["N/A", "n/a", "none", "NULL", "unknown", "not found", "", "  "])
def test_placeholder_values_are_dropped(gemini, junk):
    filled, _ = gemini(json.dumps({"certifications": [junk]}), parsed=PARSED_EMPTY)
    assert "certifications" not in filled


def test_lists_are_capped(gemini):
    """A runaway response must not flood the review form."""
    filled, _ = gemini(json.dumps({
        "projects": [{"name": f"Project {i}"} for i in range(60)],
    }), parsed=PARSED_EMPTY)
    assert len(filled["projects"]) == ex._CAPS["projects"]


def test_accepted_items_carry_confidence_below_the_review_threshold(gemini):
    """ResumeImportReview reads `confidence`; AI rows must land unticked."""
    filled, _ = gemini(json.dumps({
        "work_experience": [{"title": "Engineer", "company": "Acme"}],
        "projects": [{"name": "Parser"}],
    }), parsed=PARSED_EMPTY)
    assert filled["work_experience"][0]["confidence"] < 0.7
    assert filled["projects"][0]["confidence"] < 0.7


def test_whitespace_is_normalised(gemini):
    filled, _ = gemini(json.dumps({"certifications": ["  AWS   Certified  Developer "]}),
                       parsed=PARSED_EMPTY)
    assert filled["certifications"] == ["AWS Certified Developer"]


# ── Shape tolerance ───────────────────────────────────────────────────────────

def test_projects_accepts_a_list_of_plain_strings(gemini):
    """Models answer with bare names about as often as objects."""
    filled, _ = gemini(json.dumps({"projects": ["Rate limiter", "Chat app"]}),
                       parsed=PARSED_EMPTY)
    assert [p["name"] for p in filled["projects"]] == ["Rate limiter", "Chat app"]


def test_certifications_accepts_objects(gemini):
    filled, _ = gemini(json.dumps({"certifications": [{"name": "AWS Certified Developer"}]}),
                       parsed=PARSED_EMPTY)
    assert filled["certifications"] == ["AWS Certified Developer"]


def test_strings_where_work_objects_belong_are_dropped(gemini):
    """A job needs a title/company pair; a bare string is not enough to offer."""
    filled, _ = gemini(json.dumps({"work_experience": ["Backend Engineer at Acme"]}),
                       parsed=PARSED_EMPTY)
    assert "work_experience" not in filled


def test_object_where_a_list_belongs_is_ignored(gemini):
    filled, _ = gemini(json.dumps({"projects": {"name": "Rate limiter"}}), parsed=PARSED_EMPTY)
    assert filled == {}


def test_markdown_fenced_json_is_parsed(gemini):
    filled, _ = gemini('```json\n{"projects": [{"name": "Rate limiter"}]}\n```')
    assert filled["projects"][0]["name"] == "Rate limiter"


def test_json_surrounded_by_prose_is_parsed(gemini):
    filled, _ = gemini('Sure!\n{"projects": [{"name": "Rate limiter"}]}\nHope that helps.')
    assert filled["projects"][0]["name"] == "Rate limiter"


@pytest.mark.parametrize("garbage", ["", "no json here", "[1,2,3]", "{broken", "null"])
def test_unparseable_response_yields_empty(gemini, garbage):
    filled, _ = gemini(garbage)
    assert filled == {}


# ── Degradation ───────────────────────────────────────────────────────────────

def test_model_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content",
               side_effect=RuntimeError("429 quota exhausted")):
        assert ex.extract_sections_sync(RESUME, PARSED_PARTIAL) == {}


def test_flag_off_makes_no_call(monkeypatch):
    monkeypatch.setattr("src.services.llm_resume_extractor.LLM_RESUME_EXTRACTION", False)
    with patch("src.services.gemini_service._generate_content") as m:
        assert ex.extract_sections_sync(RESUME, PARSED_PARTIAL) == {}
        m.assert_not_called()


def test_missing_credentials_makes_no_call(monkeypatch):
    monkeypatch.setattr("src.services.llm_resume_extractor.LLM_RESUME_EXTRACTION", True)
    monkeypatch.setattr("src.services.gemini_service.credentials_configured", lambda: False)
    with patch("src.services.gemini_service._generate_content") as m:
        assert ex.extract_sections_sync(RESUME, PARSED_PARTIAL) == {}
        m.assert_not_called()


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_resume_text_makes_no_call(monkeypatch, text):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content") as m:
        assert ex.extract_sections_sync(text, PARSED_PARTIAL) == {}
        m.assert_not_called()


# ── Async wrapper ─────────────────────────────────────────────────────────────
# Driven with asyncio.run rather than pytest.mark.asyncio: the pinned
# pytest-asyncio (0.21.1) does not work on this Python, which is what leaves
# test_hybrid_ats_pytest.py red. These assertions do not need the plugin.

def test_async_wrapper_returns_the_same_result(monkeypatch):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content",
               return_value='{"projects": [{"name": "Rate limiter"}]}'):
        filled = asyncio.run(ex.extract_sections(RESUME, PARSED_PARTIAL))
    assert filled["projects"][0]["name"] == "Rate limiter"


def test_async_wrapper_skips_the_thread_hop_when_nothing_missing(monkeypatch):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content") as m:
        assert asyncio.run(ex.extract_sections(RESUME, PARSED_FULL)) == {}
        m.assert_not_called()


def test_async_wrapper_swallows_model_failure(monkeypatch):
    """The route awaits this inline; a raise here would cost the candidate their parse."""
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content",
               side_effect=RuntimeError("boom")):
        assert asyncio.run(ex.extract_sections(RESUME, PARSED_PARTIAL)) == {}
