"""
Unit tests for llm_resume_extractor.

The LLM is untrusted input on a path that writes straight to VARCHAR columns, so the
validator — not the prompt — is what actually enforces the two guarantees this feature
rests on:

  1. "Fill blanks only": a field the regex parser already found can never be
     overwritten, however confidently the model volunteers it.
  2. Nothing reaches the database that MySQL would reject or that a human would have
     to undo — placeholders, over-length strings, impossible years, junk emails.

Gemini is mocked throughout; these tests make no network calls.

Run with:
    pytest test/test_llm_resume_extractor.py -v
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from src.services import llm_resume_extractor as ex


RESUME = "Priya Raman\nB.Tech, Anna University, 2019\nBackend Engineer\n"

# A profile where the regex parser found the email but nothing else.
PARTIAL = {
    "full_name": None, "email": "priya@example.com", "phone": None,
    "college": None, "year_of_passing": None, "job_role": None, "highest_degree": None,
}
FULL = {f: "found" for f in ex.PROFILE_FIELDS}


@pytest.fixture
def gemini(monkeypatch):
    """Force the feature on and hand back a canned model response."""
    monkeypatch.setattr(ex, "LLM_RESUME_EXTRACTION", True)
    monkeypatch.setattr(ex, "_enabled", lambda: True)

    def _run(raw):
        with patch("src.services.gemini_service._generate_content", return_value=raw) as m:
            return ex.extract_gaps_sync(RESUME, PARTIAL), m
    return _run


# ── missing_fields ────────────────────────────────────────────────────────────

def test_missing_fields_lists_only_blanks():
    assert ex.missing_fields(PARTIAL) == [
        "full_name", "phone", "college", "year_of_passing", "job_role", "highest_degree"
    ]


def test_missing_fields_empty_when_profile_complete():
    assert ex.missing_fields(FULL) == []


def test_missing_fields_handles_none():
    assert ex.missing_fields(None) == list(ex.PROFILE_FIELDS)


# ── Guarantee 1: fill blanks only ─────────────────────────────────────────────

def test_field_already_filled_is_discarded(gemini):
    """The model volunteers a new email; the regex already found one. Ignore it."""
    fills, _ = gemini('{"email": "wrong@evil.com", "college": "Anna University"}')
    assert "email" not in fills
    assert fills["college"] == "Anna University"


def test_no_call_when_nothing_is_missing(monkeypatch):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content") as m:
        assert ex.extract_gaps_sync(RESUME, FULL) == {}
        m.assert_not_called()


def test_unknown_key_is_discarded(gemini):
    fills, _ = gemini('{"salary": "20 LPA", "college": "Anna University"}')
    assert fills == {"college": "Anna University"}


# ── Guarantee 2: validation ───────────────────────────────────────────────────

@pytest.mark.parametrize("junk", [
    "N/A", "n/a", "none", "NULL", "unknown", "Not Found", "not specified", "", "  ", "-",
])
def test_placeholder_values_are_rejected(gemini, junk):
    fills, _ = gemini('{"college": "%s"}' % junk)
    assert "college" not in fills


def test_year_string_is_coerced_to_int(gemini):
    fills, _ = gemini('{"year_of_passing": "2019"}')
    assert fills["year_of_passing"] == 2019
    assert isinstance(fills["year_of_passing"], int)


@pytest.mark.parametrize("year", [1899, 1949, 3050, "twenty nineteen", "2 years"])
def test_out_of_range_or_unparseable_year_rejected(gemini, year):
    fills, _ = gemini('{"year_of_passing": %s}' % repr(year).replace("'", '"'))
    assert "year_of_passing" not in fills


def test_year_at_upper_bound_accepted(gemini):
    """A fresher's expected graduation year is legitimately in the near future."""
    soon = datetime.utcnow().year + 2
    fills, _ = gemini('{"year_of_passing": %d}' % soon)
    assert fills["year_of_passing"] == soon


@pytest.mark.parametrize("bad", ["not-an-email", "priya@", "@example.com", "priya example.com"])
def test_invalid_email_rejected(monkeypatch, bad):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    blank = dict(PARTIAL, email=None)
    with patch("src.services.gemini_service._generate_content",
               return_value='{"email": "%s"}' % bad):
        assert "email" not in ex.extract_gaps_sync(RESUME, blank)


def test_valid_email_accepted_when_blank(monkeypatch):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    blank = dict(PARTIAL, email=None)
    with patch("src.services.gemini_service._generate_content",
               return_value='{"email": "priya.raman@example.co.in"}'):
        assert ex.extract_gaps_sync(RESUME, blank)["email"] == "priya.raman@example.co.in"


def test_overlong_value_rejected(gemini):
    """VARCHAR(255) — must be caught here, not as a MySQL truncation at commit()."""
    fills, _ = gemini('{"college": "%s"}' % ("x" * 400))
    assert "college" not in fills


def test_overlong_degree_rejected(gemini):
    """highest_degree is only VARCHAR(100), narrower than the rest."""
    fills, _ = gemini('{"highest_degree": "%s"}' % ("y" * 150))
    assert "highest_degree" not in fills


@pytest.mark.parametrize("bad_name", [
    "call me at 9876543210",           # digits
    "priya@example.com",               # an address, not a name
    "Priya Raman is a backend engineer with eight years of experience",  # a sentence
    "12345",
])
def test_implausible_full_name_rejected(gemini, bad_name):
    fills, _ = gemini('{"full_name": "%s"}' % bad_name)
    assert "full_name" not in fills


def test_plausible_full_name_accepted(gemini):
    fills, _ = gemini('{"full_name": "Priya Raman"}')
    assert fills["full_name"] == "Priya Raman"


def test_whitespace_is_normalised(gemini):
    fills, _ = gemini('{"college": "  Anna   University  "}')
    assert fills["college"] == "Anna University"


# ── Response parsing ──────────────────────────────────────────────────────────

def test_markdown_fenced_json_is_parsed(gemini):
    fills, _ = gemini('```json\n{"college": "Anna University"}\n```')
    assert fills["college"] == "Anna University"


def test_json_surrounded_by_prose_is_parsed(gemini):
    fills, _ = gemini('Sure! Here you go:\n{"college": "Anna University"}\nHope that helps.')
    assert fills["college"] == "Anna University"


def test_explicit_nulls_are_dropped(gemini):
    fills, _ = gemini('{"college": null, "job_role": "Backend Engineer"}')
    assert fills == {"job_role": "Backend Engineer"}


@pytest.mark.parametrize("garbage", ["", "no json here", "[1,2,3]", "{broken", "null"])
def test_unparseable_response_yields_empty(gemini, garbage):
    fills, _ = gemini(garbage)
    assert fills == {}


# ── Degradation ───────────────────────────────────────────────────────────────

def test_model_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content",
               side_effect=RuntimeError("429 quota exhausted")):
        assert ex.extract_gaps_sync(RESUME, PARTIAL) == {}


def test_flag_off_makes_no_call(monkeypatch):
    monkeypatch.setattr(ex, "LLM_RESUME_EXTRACTION", False)
    with patch("src.services.gemini_service._generate_content") as m:
        assert ex.extract_gaps_sync(RESUME, PARTIAL) == {}
        m.assert_not_called()


def test_missing_credentials_makes_no_call(monkeypatch):
    monkeypatch.setattr(ex, "LLM_RESUME_EXTRACTION", True)
    monkeypatch.setattr("src.services.gemini_service.credentials_configured", lambda: False)
    with patch("src.services.gemini_service._generate_content") as m:
        assert ex.extract_gaps_sync(RESUME, PARTIAL) == {}
        m.assert_not_called()


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_resume_text_makes_no_call(monkeypatch, text):
    monkeypatch.setattr(ex, "_enabled", lambda: True)
    with patch("src.services.gemini_service._generate_content") as m:
        assert ex.extract_gaps_sync(text, PARTIAL) == {}
        m.assert_not_called()


# ── Prompt construction ───────────────────────────────────────────────────────

def test_prompt_asks_only_for_missing_fields():
    prompt = ex._build_prompt(RESUME, ["college", "job_role"])
    assert '"college"' in prompt and '"job_role"' in prompt
    assert '"email"' not in prompt and '"full_name"' not in prompt


def test_prompt_truncates_long_resumes(monkeypatch):
    monkeypatch.setattr(ex, "LLM_RESUME_TEXT_CHARS", 100)
    prompt = ex._build_prompt("z" * 5000, ["college"])
    assert "z" * 100 in prompt
    assert "z" * 101 not in prompt


# ── profile_source_map ────────────────────────────────────────────────────────

class _Entry:
    def __init__(self, profile_source):
        self.profile_source = profile_source


@pytest.mark.parametrize("raw", [None, "", "not json", "[1,2]", '"a string"', "{}"])
def test_profile_source_map_degrades_to_empty(raw):
    assert ex.profile_source_map(_Entry(raw)) == {}


def test_profile_source_map_reads_json():
    got = ex.profile_source_map(_Entry('{"college": "llm", "email": "regex"}'))
    assert got == {"college": "llm", "email": "regex"}


def test_profile_source_map_drops_empty_values():
    assert ex.profile_source_map(_Entry('{"college": "llm", "phone": null}')) == {"college": "llm"}
