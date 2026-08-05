"""
Sandboxed SQL grading tests — SQL is now graded INSIDE the hireflow-judge-python
container via sql_harness.py. These exercise the real container plumbing, so they
need Docker + the python image (auto-skipped otherwise).

They also assert PARITY: the sandboxed result matches the in-process reference
implementation (sql_runner.judge_sql) for the same job, proving the move into Docker
didn't change grading behavior.
"""
import pytest

from src.judge import verdict as V
from src.judge.runner import judge_submission, TestCaseInput
from src.judge.sql_sandbox import judge_sql_sandboxed, run_sql_once_sandboxed
from src.judge.sql_runner import judge_sql
from src.judge.languages import get_language
from src.judge.sandbox import docker_available

pytestmark = pytest.mark.skipif(
    not docker_available(),
    reason="Docker daemon not reachable; start Docker Desktop and build judge images.",
)

IMG = get_language("sql").image
SEED = "CREATE TABLE nums(x INTEGER);\nINSERT INTO nums(x) VALUES (1),(2),(3);\n"
CASES = [TestCaseInput(id="1", input=SEED, expected_output="6", ordinal=0, is_hidden=False, is_sample=True)]


def test_sandboxed_accepted():
    r = judge_sql_sandboxed("SELECT SUM(x) FROM nums;", CASES, image=IMG)
    assert r.verdict == V.AC
    assert r.passed_count == 1 and r.total_count == 1
    assert r.cases[0].stdout.strip() == "6"


def test_sandboxed_wrong_answer():
    r = judge_sql_sandboxed("SELECT COUNT(x) FROM nums;", CASES, image=IMG)
    assert r.verdict == V.WA


def test_sandboxed_runtime_error():
    r = judge_sql_sandboxed("SELECT * FROM does_not_exist;", CASES, image=IMG)
    assert r.verdict == V.RE
    assert r.cases[0].stderr


def test_sandboxed_reference_match():
    # Graded against the reference query's result set, not the stored expected_output.
    r = judge_sql_sandboxed("SELECT 1 + 2 + 3;", CASES, image=IMG,
                            reference_sql="SELECT SUM(x) FROM nums;")
    assert r.verdict == V.AC


def test_sandboxed_order_independent():
    # Multi-row result: reference rows come back in a different ORDER but must still AC,
    # proving the result-set (multiset) comparison survived the move into Docker.
    seed = "CREATE TABLE t(n INTEGER);\nINSERT INTO t(n) VALUES (3),(1),(2);\n"
    cases = [TestCaseInput(id="1", input=seed, expected_output="", ordinal=0, is_hidden=False, is_sample=True)]
    r = judge_sql_sandboxed("SELECT n FROM t ORDER BY n ASC;", cases, image=IMG,
                            reference_sql="SELECT n FROM t ORDER BY n DESC;")
    assert r.verdict == V.AC


def test_run_once_sandboxed():
    r = run_sql_once_sandboxed("SELECT SUM(x) AS total FROM nums;", SEED, image=IMG)
    assert r.status == "ok"
    assert r.stdout.strip() == "6"


def test_judge_submission_routes_sql_to_sandbox():
    # The generic entrypoint must route 'sql' through the sandbox and still grade it.
    r = judge_submission("sql", "SELECT SUM(x) FROM nums;", CASES)
    assert r.verdict == V.AC


def test_parity_with_in_process_reference():
    # Same job through the sandbox vs the in-process reference implementation → same verdict/score.
    for sql in ("SELECT SUM(x) FROM nums;", "SELECT COUNT(x) FROM nums;", "SELECT * FROM nope;"):
        sandboxed = judge_sql_sandboxed(sql, CASES, image=IMG, reference_sql="SELECT SUM(x) FROM nums;")
        inproc = judge_sql(sql, CASES, reference_sql="SELECT SUM(x) FROM nums;")
        assert sandboxed.verdict == inproc.verdict, sql
        assert sandboxed.passed_count == inproc.passed_count, sql
