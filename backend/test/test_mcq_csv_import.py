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


# ── code-review regressions ───────────────────────────────────────────────────
#
# One test per finding from the review of the original fix. Each of these
# passed the first version of the parser and should not again.

@pytest.mark.parametrize("serial_header", [
    "Question #",      # _norm strips "#", leaving exactly "question"
    "Question ID",     # not in the blocklist as first written
    "Question Sr No",
    "Question Number",
    "#",
    "S.No",
])
def test_serial_headers_never_win_even_against_an_exact_match(serial_header):
    """The exact-match pass used to run before the blocklist was consulted, so
    "Question #" — which normalises to plain "question" — took column 0."""
    rows = [[serial_header, "Question", "Option A", "Option B", "Option C", "Option D", "Answer"],
            ["1", QUESTION, *OPTIONS, "B"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert items[0].question_text == QUESTION
    assert items[0].correct_answer == "B"


def test_header_row_is_not_eaten_when_only_serial_mentions_question():
    """With "Question ID, Description, ..." no non-serial header contains
    "question", which made has_header False — so the header row was parsed as
    data and the literal string "Question ID" became a question."""
    rows = [["Question ID", "Description", "Option A", "Option B", "Correct Answer"],
            ["1", QUESTION, "yes", "no", "A"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert items[0].question_text == QUESTION
    assert items[0].question_text != "Question ID"


def test_hash_column_never_becomes_an_option():
    """_norm("#") is "", which was not in the blocklist, so the option fallback
    accepted it: options came back as ['7', 'yes', 'no']."""
    rows = [["#", "Question", "Choice 1", "Choice 2", "Correct Answer"],
            ["7", QUESTION, "yes", "no", "A"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert "7" not in items[0].options
    assert items[0].options == ["yes", "no"]


def test_blank_trailing_column_never_becomes_an_option():
    """Excel leaves unnamed trailing columns; they normalise to "" too."""
    rows = [["Question", "Choice 1", "Choice 2", "Correct Answer", ""],
            [QUESTION, "yes", "no", "A", ""]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert items[0].options == ["yes", "no"]


@pytest.mark.parametrize("serial_text", ["1", "1.", "01.", "1.0", "Q1", "#3", " 2 "])
def test_serial_like_question_text_is_refused(serial_text):
    """The guard tested str.isdigit() only, so "1." / "1.0" (a float-typed serial
    column) and "Q1" were imported as question text and went on to poison the
    interview-scoped duplicate check exactly as "1" did."""
    rows = [["Question", "Option A", "Option B", "Correct Answer"], [serial_text, "yes", "no", "A"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert items == [], f"{serial_text!r} was imported"
    assert errors


@pytest.mark.parametrize("real_question", [
    "What is 2 + 2?",
    "Which is 1.5 times 2?",     # contains a float
    "Q. What is onboarding?",    # starts with Q
    "2026 hiring targets: which is right?",
])
def test_real_questions_are_not_mistaken_for_serials(real_question):
    """The broadened guard must not start rejecting genuine questions."""
    rows = [["Question", "Option A", "Option B", "Correct Answer"], [real_question, "yes", "no", "A"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert len(items) == 1, errors
    assert items[0].question_text == real_question


def test_parse_errors_are_returned_when_every_row_fails():
    """The endpoint reports parse_errors only if it receives them. With every row
    holding a serial number, items is empty — the case the diagnostic exists for."""
    rows = [["Question No.", "Question", "Option A", "Option B", "Correct Answer"]]
    for i in range(1, 4):
        rows.append([str(i), str(i), "yes", "no", "A"])
    items, errors = _parse_mcq_csv(_csv(rows))
    assert items == []
    assert errors, "no diagnostic produced for an all-serial file"
    assert "row number" in errors[0].lower()


def test_file_with_no_question_column_says_so():
    """Serial + options + answer and nothing else: the fallback used to promote
    option A to be the question rather than admitting it could not tell."""
    rows = [["Question No.", "Option A", "Option B", "Correct Answer"], ["1", "yes", "no", "A"]]
    items, errors = _parse_mcq_csv(_csv(rows))
    assert items == []
    assert errors and "question column" in errors[0].lower()


def test_tab_file_wins_over_prose_commas():
    """Counting raw characters picked the comma here — the question carries more
    commas than the row has tabs — and shredded the row into one cell."""
    line = ("Which are HR functions: hiring, onboarding, payroll, training, appraisal?"
            "\ta\tb\tc\td\tA")
    assert _sniff_delimiter(line) == "\t"
    items, errors = _parse_mcq_csv(line)
    assert len(items) == 1, errors
    assert len(items[0].options) == 4
    assert items[0].correct_answer == "A"


def test_quoted_commas_do_not_flip_the_delimiter():
    text = _csv([
        ["Question", "Option A", "Option B", "Correct Answer"],
        ["Which list is right, and why, exactly?", "a, b, c", "d, e, f", "A"],
    ])
    assert _sniff_delimiter(text) == ","
    items, errors = _parse_mcq_csv(text)
    assert len(items) == 1, errors
    assert items[0].options == ["a, b, c", "d, e, f"]
