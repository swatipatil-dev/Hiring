"""Candidate SQL runs in a restricted, disposable database.

The harness is the same file the sandbox container runs (and sql_runner imports),
so these run it in-process — no Docker needed.

- Every case starts from a fresh in-memory database; nothing carries over.
- The graded query is read-only: writes, ATTACH, PRAGMA and extension loading fail
  as RE with a plain explanation.
- A runaway query is a TLE for its own case; the other cases are still graded.
- Results are capped in rows and in total output size.
- Test-case generation (in the API process) goes through the same restrictions.
"""
import json

import pytest

from src.judge import sql_harness as H
from src.judge import sql_runner

SEED = "CREATE TABLE emp(id INTEGER, name TEXT, dept TEXT, salary INTEGER);\n" \
       "INSERT INTO emp VALUES (1,'a','x',10),(2,'b','x',20),(3,'c','y',30);"
REF = "SELECT dept, SUM(salary) FROM emp GROUP BY dept"


def _job(sql, n_cases=1, **extra):
    return {"candidate_sql": sql, "reference_sql": REF,
            "cases": [{"id": str(i), "input": SEED, "expected_output": "", "weight": 1,
                       "ordinal": i, "is_hidden": i > 0} for i in range(n_cases)], **extra}


def _verdicts(sql, **kw):
    r = H.judge(_job(sql, **kw))
    return [c["verdict"] for c in r["cases"]], r


@pytest.mark.parametrize("sql", [
    REF,
    "WITH t AS (SELECT dept, salary FROM emp) SELECT dept, SUM(salary) FROM t GROUP BY dept",
    "SELECT dept, total FROM (SELECT dept, SUM(salary) OVER (PARTITION BY dept) AS total, "
    "ROW_NUMBER() OVER (PARTITION BY dept) AS rn FROM emp) WHERE rn = 1",
    "-- totals per department\nSELECT dept, SUM(salary) FROM emp GROUP BY dept;",
])
def test_ordinary_queries_still_pass(sql):
    assert _verdicts(sql)[0] == ["AC"]


@pytest.mark.parametrize("sql", [
    "DROP TABLE emp; SELECT 1",
    "UPDATE emp SET salary = 0; SELECT dept, SUM(salary) FROM emp GROUP BY dept",
    "DELETE FROM emp",
    "INSERT INTO emp VALUES (9,'z','z',1)",
    "CREATE TABLE t(a); SELECT 1",
    "ATTACH DATABASE '/tmp/carry.db' AS c; SELECT 1",
    "PRAGMA table_info(emp)",
    "SELECT load_extension('/tmp/evil.so')",
    "VACUUM INTO '/tmp/copy.db'",
    "WITH x AS (SELECT 1) DELETE FROM emp",
])
def test_writes_and_escapes_are_refused(sql):
    verdicts, r = _verdicts(sql)
    assert verdicts == ["RE"], (sql, r)


def test_refusal_explains_itself():
    _, r = _verdicts("DELETE FROM emp")
    assert "Only SELECT queries are allowed" in r["cases"][0]["stderr"]


def test_nothing_carries_over_between_cases():
    # Even if a statement could write, each case gets a brand-new database: the second
    # case must see the original three rows.
    _, r = _verdicts("SELECT COUNT(*) FROM emp", n_cases=2)
    assert [c["stdout"] for c in r["cases"]] == ["3", "3"]


def test_runaway_query_is_tle_for_that_case_only():
    runaway = ("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) "
               "SELECT dept, SUM(salary) FROM emp WHERE (SELECT MAX(n) FROM r) > 0 GROUP BY dept")
    job = _job(runaway, n_cases=2, query_timeout_ms=200)
    r = H.judge(job)
    assert [c["verdict"] for c in r["cases"]] == ["TLE", "TLE"]
    assert all(c["time_ms"] < 2000 for c in r["cases"])
    assert r["verdict"] == "TLE"


def test_row_limit(monkeypatch):
    monkeypatch.setattr(H, "MAX_ROWS", 50)
    big = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 100) SELECT n FROM r"
    verdicts, r = _verdicts(big)
    assert verdicts == ["WA"]
    assert "more than 50 rows" in r["cases"][0]["stderr"]


def test_output_stays_under_the_sandbox_cap():
    # Many cases each returning a wide result: the JSON must still fit, or every case
    # would turn into RE when the sandbox truncates stdout.
    wide = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5000) " \
           "SELECT n, 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' FROM r"
    r = H.judge(_job(wide, n_cases=20))
    assert len(json.dumps(r)) < 256 * 1024


def test_setup_script_cannot_attach_files():
    job = _job(REF)
    job["cases"][0]["input"] = "ATTACH DATABASE '/tmp/x.db' AS x; " + SEED
    assert [c["verdict"] for c in H.judge(job)["cases"]] == ["RE"]


def test_generation_path_uses_the_same_rules():
    # sql_testgen calls sql_runner._query_result in the API process.
    assert sql_runner._query_result is H._query_result
    with pytest.raises(H.QueryRejected):
        sql_runner._query_result(SEED, "DELETE FROM emp")
    cols, rows = sql_runner._query_result(SEED, REF)
    assert sorted(rows) == [("x", 30), ("y", 30)]
