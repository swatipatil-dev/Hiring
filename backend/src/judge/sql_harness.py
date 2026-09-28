"""
In-CONTAINER SQL grading harness (stdlib only — NO app imports).

This file is copied into the sandbox workdir and run as ``python3 sql_harness.py``
inside the hardened ``hireflow-judge-python`` container, so candidate SQL is graded
with the same CPU / memory / wall-clock isolation as every other language. It reads a
JSON "job" on stdin and writes a JSON result on stdout.

It is also imported in-process by ``src/judge/sql_runner.py`` (test-case generation
runs LLM-written seed SQL through the same restricted path), so there is exactly one
implementation of the rules below.

Every case gets a fresh in-memory SQLite database that is thrown away afterwards —
nothing reaches disk or outlives the case. On top of that, the database is
restricted:

  * Seeding (the question's setup script): anything except ATTACH/DETACH and
    extension loading, under a time limit.
  * The query being graded (candidate's or reference): read-only. The connection is
    switched to ``PRAGMA query_only`` and an authorizer allows only reading —
    SELECT / WITH / VALUES, reading tables, calling ordinary functions. So a query
    cannot write, attach another database file, change pragmas or load extensions.
  * Each query has its own time limit (a runaway recursive CTE is a TLE for that
    case only, not for every case) and a row limit.

Job (stdin):
    {"candidate_sql": str,
     "reference_sql": str | null,
     "query_timeout_ms": int (optional),
     "cases": [{"id","input"(setup SQL),"expected_output","weight","ordinal",
                "is_sample","is_hidden"}, ...]}

Result (stdout):
    {"verdict","score","max_score","passed_count","total_count","total_time_ms",
     "cases":[{"test_case_id","verdict","time_ms","stdout","stderr","ordinal","is_hidden"}]}
"""
import json
import re
import sqlite3
import sys
import time

AC, WA, RE, TLE, PENDING = "AC", "WA", "RE", "TLE", "PENDING"
# Mirror src/judge/verdict.py severity ordering (worst non-AC wins overall).
_SEVERITY = {AC: 0, WA: 1, RE: 2, TLE: 3, "MLE": 4, "CE": 5}

QUERY_TIMEOUT_MS = 2000          # per query, per case
SEED_TIMEOUT_MS = 2000           # per setup script
MAX_ROWS = 10000                 # rows a query may return
_RESULT_BUDGET = 200 * 1024      # total stdout text across cases; the sandbox caps at 256 KB

# Functions a query may never call, even if the build provides them.
_BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
# What a read-only query is allowed to do (sqlite3 authorizer action codes).
_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                 getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_READ_ONLY_START = re.compile(r"^\s*(?:SELECT|WITH|VALUES)\b", re.I)


class QueryRejected(Exception):
    """The query tried something other than reading."""


class QueryTimeout(Exception):
    """The query ran past its time limit."""


class TooManyRows(Exception):
    """The query returned more than MAX_ROWS rows."""


def _split_statements(sql):
    """Split a SQL script on top-level semicolons (quote-aware)."""
    statements, buf, quote = [], [], None
    for ch in sql or "":
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _strip_comments(stmt):
    stmt = re.sub(r"/\*.*?\*/", " ", stmt, flags=re.S)
    return re.sub(r"--[^\n]*", " ", stmt)


def _seed_authorizer(action, arg1, arg2, db_name, source):
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _BLOCKED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _read_only_authorizer(action, arg1, arg2, db_name, source):
    if action not in _READ_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _BLOCKED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _set_deadline(conn, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000.0
    # Called every N SQLite VM steps; a non-zero return aborts the statement.
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
    return deadline


def _timed_out(err, deadline):
    return "interrupted" in str(err).lower() and time.monotonic() > deadline


def _run_read_only(conn, sql, timeout_ms):
    """Run a read-only script; return (columns, rows) of the LAST result set."""
    statements = _split_statements(sql)
    for stmt in statements:
        if not _READ_ONLY_START.match(_strip_comments(stmt)):
            raise QueryRejected("Only SELECT queries are allowed in this question "
                                "(statements must start with SELECT, WITH or VALUES).")
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_read_only_authorizer)
    deadline = _set_deadline(conn, timeout_ms)
    cols, rows = None, []
    cur = conn.cursor()
    try:
        for stmt in statements:
            cur.execute(stmt)
            if cur.description is not None:
                cols = [d[0] for d in cur.description]
                rows = []
                while True:
                    chunk = cur.fetchmany(1000)
                    if not chunk:
                        break
                    rows.extend(chunk)
                    if len(rows) > MAX_ROWS:
                        raise TooManyRows(f"The result has more than {MAX_ROWS} rows.")
    except sqlite3.DatabaseError as e:
        if _timed_out(e, deadline):
            raise QueryTimeout(f"The query ran longer than {timeout_ms} ms.")
        if "not authorized" in str(e).lower() or "readonly" in str(e).lower() \
                or "read-only" in str(e).lower():
            raise QueryRejected("Only reading is allowed in this question: the query tried "
                                "to change data, attach a database, change a setting or "
                                "load an extension.")
        raise
    return cols, rows


def _seed(conn, setup_sql, timeout_ms=SEED_TIMEOUT_MS):
    if not (setup_sql or "").strip():
        return
    conn.set_authorizer(_seed_authorizer)
    deadline = _set_deadline(conn, timeout_ms)
    try:
        conn.executescript(setup_sql)
    except sqlite3.DatabaseError as e:
        if _timed_out(e, deadline):
            raise QueryTimeout(f"The setup script ran longer than {timeout_ms} ms.")
        raise
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)


def _query_result(setup_sql, query_sql, timeout_ms=QUERY_TIMEOUT_MS):
    """Seed a fresh in-memory DB with setup_sql, then run query_sql read-only."""
    conn = sqlite3.connect(":memory:")
    try:
        _seed(conn, setup_sql)
        return _run_read_only(conn, query_sql, timeout_ms)
    finally:
        conn.close()


def _format_rows(cols, rows):
    if not rows:
        return ""
    return "\n".join(
        ",".join("" if v is None else str(v) for v in row) for row in rows
    )


def _rows_equal(a, b):
    def norm(rows):
        return sorted(tuple("" if v is None else str(v) for v in row) for row in rows)
    return norm(a) == norm(b)


def _normalize_output(text):
    """Mirror verdict.normalize_output: normalize line endings, rstrip each line,
    drop trailing blank lines."""
    if text is None:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _outputs_match(actual, expected):
    return _normalize_output(actual) == _normalize_output(expected)


def judge(job):
    candidate_sql = job.get("candidate_sql") or ""
    reference_sql = job.get("reference_sql")
    timeout_ms = int(job.get("query_timeout_ms") or QUERY_TIMEOUT_MS)
    raw_cases = job.get("cases") or []
    cases_sorted = sorted(raw_cases, key=lambda c: c.get("ordinal", 0))
    # Share the output budget between cases, so one huge result can't push the whole
    # JSON past the sandbox's output cap (which would turn every case into RE).
    per_case_text = max(1000, min(10000, _RESULT_BUDGET // max(1, len(cases_sorted))))

    max_score = float(sum(max(1, int(c.get("weight", 1) or 1)) for c in cases_sorted)) or 0.0
    out_cases, score, passed, total_time = [], 0.0, 0, 0

    for tc in cases_sorted:
        setup = tc.get("input", "") or ""
        ordinal = tc.get("ordinal", 0)
        is_hidden = bool(tc.get("is_hidden"))
        t0 = time.perf_counter()

        def fail(verdict, message):
            out_cases.append({
                "test_case_id": tc.get("id"), "verdict": verdict,
                "time_ms": int((time.perf_counter() - t0) * 1000), "stdout": "",
                "stderr": str(message)[:per_case_text], "ordinal": ordinal, "is_hidden": is_hidden,
            })

        try:
            cand_cols, cand_rows = _query_result(setup, candidate_sql, timeout_ms)
        except QueryTimeout as e:
            fail(TLE, e)
            continue
        except TooManyRows as e:
            fail(WA, e)
            continue
        except Exception as e:          # QueryRejected, SQL errors
            fail(RE, e)
            continue

        ref_rows = None
        if (reference_sql or "").strip():
            try:
                _, ref_rows = _query_result(setup, reference_sql, timeout_ms)
            except Exception:
                ref_rows = None

        if ref_rows is not None:
            ok = _rows_equal(cand_rows, ref_rows)
        else:
            ok = _outputs_match(_format_rows(cand_cols, cand_rows), tc.get("expected_output", "") or "")

        elapsed = int((time.perf_counter() - t0) * 1000)
        total_time = max(total_time, elapsed)
        verdict = AC if ok else WA
        if verdict == AC:
            passed += 1
            score += max(1, int(tc.get("weight", 1) or 1))
        out_cases.append({
            "test_case_id": tc.get("id"), "verdict": verdict, "time_ms": elapsed,
            "stdout": _format_rows(cand_cols, cand_rows)[:per_case_text], "stderr": "",
            "ordinal": ordinal, "is_hidden": is_hidden,
        })

    verdicts = [c["verdict"] for c in out_cases]
    if not verdicts:
        overall = PENDING
    else:
        overall = AC
        for v in verdicts:
            if _SEVERITY.get(v, 1) > _SEVERITY.get(overall, 0):
                overall = v
    return {
        "verdict": overall, "score": score, "max_score": max_score,
        "passed_count": passed, "total_count": len(cases_sorted),
        "total_time_ms": total_time, "cases": out_cases,
    }


def main():
    job = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps(judge(job)))


if __name__ == "__main__":
    main()
