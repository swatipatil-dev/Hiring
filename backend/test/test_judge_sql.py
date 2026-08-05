"""
SQL judge tests (SQLite, in-process).

Unlike the Docker-backed languages, the SQL judge runs in-process against an
ephemeral in-memory SQLite DB (``src/judge/sql_runner.py``), so these tests need
NO Docker and are never skipped.

For a SQL test case, ``input`` is the setup SQL (CREATE/INSERT seeding the schema),
``source_code`` is the candidate's query, and grading compares the candidate's
result set to ``expected_output`` — or to a reference query's result set when one
is supplied via ``judge_sql(..., reference_sql=...)``.
"""
from src.judge import verdict as V
from src.judge.runner import judge_submission, TestCaseInput
from src.judge.sql_runner import judge_sql

SEED = "CREATE TABLE nums(x INTEGER);\nINSERT INTO nums(x) VALUES (1),(2),(3);\n"

# Sum of the seeded rows is 6.
SUM_CASES = [
    TestCaseInput(id="1", input=SEED, expected_output="6", ordinal=0, is_hidden=False),
]


def test_sql_accepted():
    r = judge_submission("sql", "SELECT SUM(x) FROM nums;", SUM_CASES)
    assert r.verdict == V.AC
    assert r.passed_count == 1 and r.total_count == 1


def test_sql_wrong_answer():
    # COUNT(x) is 3, not the expected 6.
    r = judge_submission("sql", "SELECT COUNT(x) FROM nums;", SUM_CASES)
    assert r.verdict == V.WA


def test_sql_runtime_error():
    r = judge_submission("sql", "SELECT * FROM does_not_exist;", SUM_CASES)
    assert r.verdict == V.RE


def test_sql_reference_match():
    # Reference path: candidate query is graded against the reference query's
    # result set (order-independent), not the stored expected_output string.
    r = judge_sql(
        "SELECT 1 + 2 + 3;",
        SUM_CASES,
        reference_sql="SELECT SUM(x) FROM nums;",
    )
    assert r.verdict == V.AC
    assert r.passed_count == 1
