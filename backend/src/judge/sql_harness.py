"""
In-CONTAINER SQL grading harness (stdlib only — NO app imports).

This file is copied into the sandbox workdir and run as ``python3 sql_harness.py``
inside the hardened ``hireflow-judge-python`` container, so candidate SQL is graded
with the same CPU / memory / wall-clock isolation as every other language. It reads a
JSON "job" on stdin and writes a JSON result on stdout.

The grading logic is a faithful port of the pure helpers in ``src/judge/sql_runner.py``
(in-memory SQLite, result-set comparison against a reference query, order-independent).
Keep the two in sync — ``test/test_judge_sql_sandbox.py`` asserts parity.

Job (stdin):
    {"candidate_sql": str,
     "reference_sql": str | null,
     "cases": [{"id","input"(setup SQL),"expected_output","weight","ordinal",
                "is_sample","is_hidden"}, ...]}

Result (stdout):
    {"verdict","score","max_score","passed_count","total_count","total_time_ms",
     "cases":[{"test_case_id","verdict","time_ms","stdout","stderr","ordinal","is_hidden"}]}
"""
import json
import sqlite3
import sys
import time

AC, WA, RE, PENDING = "AC", "WA", "RE", "PENDING"
# Mirror src/judge/verdict.py severity ordering (worst non-AC wins overall).
_SEVERITY = {AC: 0, WA: 1, RE: 2, "TLE": 3, "MLE": 4, "CE": 5}


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


def _run_script(conn, sql):
    """Run every statement; return (columns, rows) of the LAST result set."""
    cols, rows = None, []
    cur = conn.cursor()
    for stmt in _split_statements(sql):
        cur.execute(stmt)
        if cur.description is not None:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
    return cols, rows


def _query_result(setup_sql, query_sql):
    """Seed a fresh in-memory DB with setup_sql, then run query_sql."""
    conn = sqlite3.connect(":memory:")
    try:
        if (setup_sql or "").strip():
            conn.executescript(setup_sql)
        return _run_script(conn, query_sql)
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
    raw_cases = job.get("cases") or []
    cases_sorted = sorted(raw_cases, key=lambda c: c.get("ordinal", 0))

    max_score = float(sum(max(1, int(c.get("weight", 1) or 1)) for c in cases_sorted)) or 0.0
    out_cases, score, passed, total_time = [], 0.0, 0, 0

    for tc in cases_sorted:
        setup = tc.get("input", "") or ""
        ordinal = tc.get("ordinal", 0)
        is_hidden = bool(tc.get("is_hidden"))
        t0 = time.perf_counter()
        try:
            cand_cols, cand_rows = _query_result(setup, candidate_sql)
        except Exception as e:
            out_cases.append({
                "test_case_id": tc.get("id"), "verdict": RE,
                "time_ms": int((time.perf_counter() - t0) * 1000), "stdout": "",
                "stderr": str(e)[:10000], "ordinal": ordinal, "is_hidden": is_hidden,
            })
            continue

        ref_rows = None
        if (reference_sql or "").strip():
            try:
                _, ref_rows = _query_result(setup, reference_sql)
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
            "stdout": _format_rows(cand_cols, cand_rows)[:10000], "stderr": "",
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
