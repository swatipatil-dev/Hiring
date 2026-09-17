"""
Parser tests for the MCQ CSV importer.

These exist because of a real incident: a file whose first column was headed
"Question No." imported 30 questions whose text was "1".."30". The real question
text was silently discarded, and because the duplicate check is scoped to the
interview rather than the skill, every later upload to that test then collided
with the junk and imported nothing.

Pure unit tests — no app, no DB.
"""

import csv
import io

import pytest

from generator_router import _parse_mcq_csv, _sniff_delimiter


HEADER = ["Question No.", "Question", "Option A", "Option B", "Option C", "Option D", "Correct Answer"]
ROW = [
    "1",
    "What is the primary purpose of an employee onboarding process?",
    "To calculate employee salary",
    "To help a new employee understand the organization, role, policies, and processes",
    "To terminate employees",
    "To prepare GST returns",
    "B",
]
QUESTION = ROW[1]
OPTIONS = ROW[2:6]


def _csv(rows, delimiter=","):
    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter).writerows(rows)
    return buf.getvalue()


# ── the regression ────────────────────────────────────────────────────────────

def test_question_no_column_does_not_become_the_question():
    """The incident, pinned.

    "Question No." normalises to "questionno", which CONTAINS "question", so a
    substring match picks the serial number sitting in column 0 and throws away
    the real question in column 1.
    """
    items, errors = _parse_mcq_csv(_csv([HEADER, ROW]))

    assert len(items) == 1, errors
    assert items[0].question_text == QUESTION
    assert items[0].question_text != "1"
    assert items[0].options == OPTIONS
    assert items[0].correct_answer == "B"


@pytest.mark.parametrize("serial_header", [
    "Question No.", "Question Number", "S.No", "Sr. No", "Sl No", "#", "No", "Index", "ID",
])
def test_serial_columns_never_win_the_question_column(serial_header):
    rows = [[serial_header] + HEADER[1:], ROW]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert items[0].question_text == QUESTION


def test_serial_column_does_not_become_an_option():
    """With no recognisable Option headers the parser falls back to 'everything
    else is an option' — the serial column must still be excluded."""
    rows = [
        ["S.No", "Question", "A", "B", "C", "D", "Correct Answer"],
        ["7", QUESTION, *OPTIONS, "B"],
    ]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert "7" not in items[0].options
    assert items[0].options == OPTIONS


def test_numeric_question_text_is_refused_not_imported():
    """Belt and braces: even if some future header shape slips the serial column
    through, a bare number must never be stored as a question."""
    rows = [["Question", "Option A", "Option B", "Correct Answer"], ["12", "yes", "no", "A"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert items == []
    assert errors and "12" in errors[0]


# ── delimiters ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("delim,label", [
    (",", "comma"), (";", "semicolon"), ("\t", "tab"), ("|", "pipe"),
])
def test_all_delimiters_parse_identically(delim, label):
    """Copying cells out of Excel gives tabs; several European locales give
    semicolons. A comma-only reader turns the whole row into one cell."""
    items, errors = _parse_mcq_csv(_csv([HEADER, ROW], delimiter=delim))
    assert len(items) == 1, f"{label}: {errors}"
    assert items[0].question_text == QUESTION
    assert items[0].options == OPTIONS
    assert items[0].correct_answer == "B"


def test_comma_still_wins_for_prose_containing_commas():
    """The question text here has commas in it; the delimiter must stay a comma
    rather than being out-voted by punctuation."""
    assert _sniff_delimiter(_csv([HEADER, ROW])) == ","


def test_sniffer_falls_back_to_comma_on_a_single_column():
    assert _sniff_delimiter("question\nWhat is 2+2?\n") == ","


# ── header spellings ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("opt_headers", [
    ["Option A", "Option B", "Option C", "Option D"],
    ["option_a", "option_b", "option_c", "option_d"],
    ["optionA", "optionB", "optionC", "optionD"],
    ["OPTION-A", "OPTION-B", "OPTION-C", "OPTION-D"],
    ["A", "B", "C", "D"],
])
def test_option_header_spellings(opt_headers):
    rows = [["Question", *opt_headers, "Correct Answer"], [QUESTION, *OPTIONS, "B"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert items[0].options == OPTIONS


@pytest.mark.parametrize("answer_header", [
    "Correct Answer", "correct_option", "Answer", "Correct", "Answer Key", "Ans",
])
def test_answer_header_spellings(answer_header):
    rows = [["Question", "Option A", "Option B", "Option C", "Option D", answer_header],
            [QUESTION, *OPTIONS, "B"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert items[0].correct_answer == "B"


def test_the_shipped_template_header_still_parses():
    """frontend CreateTestWizard generates exactly this; a pinned frontend test
    asserts the header, so it must keep working here."""
    rows = [["question", "option_a", "option_b", "option_c", "option_d", "correct_option"],
            ["What is 2 + 2?", "3", "4", "5", "6", "B"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert items[0].question_text == "What is 2 + 2?"
    assert items[0].correct_answer == "B"


# ── shape handling ────────────────────────────────────────────────────────────

def test_headerless_file_falls_back_to_positions():
    items, errors = _parse_mcq_csv("What is 2+2?,3,4,5,6,B\n")
    assert len(items) == 1, errors
    assert items[0].question_text == "What is 2+2?"
    assert items[0].options == ["3", "4", "5", "6"]
    assert items[0].correct_answer == "B"


def test_blank_rows_are_skipped():
    rows = [HEADER, ROW, ["", "", "", "", "", "", ""], ROW[:1] + ["Another?"] + OPTIONS + ["C"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 2, errors


def test_empty_file_yields_nothing_without_raising():
    assert _parse_mcq_csv("") == ([], [])
    assert _parse_mcq_csv("\n\n") == ([], [])


def test_utf8_bom_and_quoted_commas_survive():
    text = "﻿" + _csv([
        ["Question", "Option A", "Option B", "Correct Answer"],
        ["Which list is right, and why?", "a, b", "c, d", "A"],
    ])
    items, errors = _parse_mcq_csv(text.lstrip("﻿"))
    assert len(items) == 1, errors
    assert items[0].options == ["a, b", "c, d"]


def test_multiple_rows_keep_their_own_text():
    """The bug's signature was every row collapsing onto a serial number; make
    sure distinct rows stay distinct."""
    rows = [HEADER]
    for i in range(1, 6):
        rows.append([str(i), f"Question number {i}?", *OPTIONS, "B"])
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 5, errors
    assert [it.question_text for it in items] == [f"Question number {i}?" for i in range(1, 6)]
