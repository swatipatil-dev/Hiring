"""Unit tests for the judge's verdict logic and language registry (no Docker needed)."""
import pytest

from src.judge import verdict as V
from src.judge import languages as L


# ── output normalization / comparison ───────────────────────────────────────

def test_normalize_strips_trailing_ws_and_blank_lines():
    assert V.normalize_output("1 2 \n3\n\n") == "1 2\n3"
    assert V.normalize_output("hello\r\nworld\r\n") == "hello\nworld"
    assert V.normalize_output(None) == ""


def test_match_tolerates_trailing_newline_and_spaces():
    assert V.outputs_match("5\n", "5")
    assert V.outputs_match("5  ", "5")
    assert V.outputs_match("a\nb\n", "a\nb")


def test_match_detects_real_differences():
    assert not V.outputs_match("5", "6")
    assert not V.outputs_match("1 2", "1  2")  # internal spacing matters
    assert not V.outputs_match("a\nb", "b\na")


# ── verdict aggregation (worst case wins) ────────────────────────────────────

def test_aggregate_all_ac_is_ac():
    assert V.aggregate_verdict(["AC", "AC", "AC"]) == V.AC


def test_aggregate_empty_is_pending():
    assert V.aggregate_verdict([]) == V.PENDING


@pytest.mark.parametrize("cases,expected", [
    (["AC", "WA"], V.WA),
    (["AC", "WA", "RE"], V.RE),
    (["AC", "WA", "TLE"], V.TLE),
    (["AC", "TLE", "MLE"], V.MLE),
    (["WA", "CE"], V.CE),
])
def test_aggregate_picks_highest_severity(cases, expected):
    assert V.aggregate_verdict(cases) == expected


# ── language registry ────────────────────────────────────────────────────────

def test_supported_languages_present():
    langs = L.supported_languages()
    assert {"python", "cpp", "javascript"}.issubset(set(langs))


def test_get_language_unknown_raises():
    with pytest.raises(ValueError):
        L.get_language("brainfuck")


def test_language_catalog_shape():
    cat = L.language_catalog()
    assert all({"key", "label", "monaco"} <= set(item) for item in cat)


def test_python_is_interpreted_cpp_is_compiled():
    assert L.get_language("python").compile_cmd is None
    assert L.get_language("cpp").compile_cmd is not None
