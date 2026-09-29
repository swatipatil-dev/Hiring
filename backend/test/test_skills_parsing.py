"""Tests for the shared skill-column reader.

These columns hold four formats written by different code paths over the years.
The contract is that every one of them reads back as the same list, and that a
malformed value returns [] rather than taking out the page reading it.
"""

import pytest

from src.services.skills_parsing import dump_skills, parse_skills


class TestFormats:
    def test_json_array(self):
        assert parse_skills('["Python", "React"]') == ["Python", "React"]

    def test_python_repr(self):
        """Regression: a raw list assigned to a Text column.

        This used to fail json.loads on the single quotes, fall through to the
        comma split, and yield ["['Python'", "'React']"] — tokens matching no
        skill, silently costing the candidate every requirement on the job.
        """
        assert parse_skills("['Python', 'React']") == ["Python", "React"]

    def test_comma_separated(self):
        assert parse_skills("Python, React") == ["Python", "React"]

    def test_already_a_list(self):
        assert parse_skills(["Python", "React"]) == ["Python", "React"]

    def test_single_skill_no_delimiter(self):
        assert parse_skills("Python") == ["Python"]


class TestEmptyAndMalformed:
    @pytest.mark.parametrize("value", [None, "", "   ", "\n\t", [], "[]", "null"])
    def test_empty_inputs_give_an_empty_list(self, value):
        assert parse_skills(value) == []

    @pytest.mark.parametrize("value", [
        '["Python", ',        # truncated JSON
        "['Python',",         # truncated repr
        "{'a': 1}",           # a dict, not a list
        '{"skills": ["Python"]}',
        123,
        object(),
    ])
    def test_malformed_never_raises(self, value):
        assert isinstance(parse_skills(value), list)

    def test_a_dict_does_not_become_skills(self):
        assert parse_skills('{"skills": ["Python"]}') == []


class TestCleaning:
    def test_whitespace_is_stripped(self):
        assert parse_skills('["  Python  ", "React "]') == ["Python", "React"]

    def test_blank_entries_are_dropped(self):
        assert parse_skills("Python, , React,") == ["Python", "React"]

    def test_non_strings_in_a_list_are_coerced(self):
        assert parse_skills([1, "React"]) == ["1", "React"]


class TestDump:
    def test_none_stays_none(self):
        """NULL is the "not set" sentinel and must survive a round trip."""
        assert dump_skills(None) is None

    def test_empty_list_is_an_explicit_empty(self):
        assert dump_skills([]) == "[]"

    def test_round_trips(self):
        assert parse_skills(dump_skills(["Python", "React"])) == ["Python", "React"]

    def test_none_and_empty_are_distinguishable_after_a_round_trip(self):
        assert dump_skills(None) != dump_skills([])
        assert parse_skills(dump_skills(None)) == parse_skills(dump_skills([])) == []
