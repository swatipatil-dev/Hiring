"""
End-to-end smoke test of the candidate code-evaluation + scoring engine.

Drives the SAME function the candidate's in-test Submit calls
(`grade_interview_question`) for every supported language, with a CORRECT
solution (expect Accepted + full points) and a WRONG/partial solution
(expect a failing verdict + reduced points). Proves the scorer discriminates
and awards partial credit. Requires Docker + the hireflow-judge-* images.
"""
import json
import sys
from types import SimpleNamespace

from src.judge.interview_grading import grade_interview_question
from src.config.database_config_mysql import db_manager
from src.models.question_models import Question

# ── stdin/stdout problem: "read two ints, print their sum" (points=3, 3 cases) ──
SUM_CASES = [
    {"input": "2 3",     "expected_output": "5",   "is_sample": True,  "weight": 1},
    {"input": "10 20",   "expected_output": "30",  "is_sample": False, "weight": 1},
    {"input": "100 250", "expected_output": "350", "is_sample": False, "weight": 1},
]
sum_q = SimpleNamespace(points=3, test_cases=json.dumps(SUM_CASES), sample_answer=None)

# correct solutions (full marks) and a "hardcoded 5" wrong solution (passes only
# the first case -> partial credit) per language.
SOLUTIONS = {
    "python": (
        "a,b=map(int,input().split())\nprint(a+b)",
        "print(5)",
    ),
    "cpp": (
        "#include <iostream>\nint main(){long a,b;std::cin>>a>>b;std::cout<<a+b;}",
        "#include <iostream>\nint main(){std::cout<<5;}",
    ),
    "javascript": (
        "const d=require('fs').readFileSync(0,'utf8').trim().split(/\\s+/).map(Number);console.log(d[0]+d[1]);",
        "console.log(5);",
    ),
    "java": (
        "import java.util.*;public class Main{public static void main(String[] x){"
        "Scanner s=new Scanner(System.in);System.out.println(s.nextLong()+s.nextLong());}}",
        "public class Main{public static void main(String[] x){System.out.println(5);}}",
    ),
}

# ── SQL: use the REAL interview question already in the DB ──
SQL_QID = "3a1f500f-b084-4874-b7ac-6629fd3e0742"
SQL_CORRECT = "SELECT name FROM employees WHERE salary > (SELECT AVG(salary) FROM employees);"
SQL_WRONG = "SELECT name FROM employees;"  # returns everyone -> WA


def show(lang, label, r):
    pts = f"{r['points_awarded']}/{r['max_points']}"
    print(f"  [{lang:10}] {label:8} verdict={str(r['verdict']):6} "
          f"passed={r['passed']}/{r['total']} points={pts:5} "
          f"score={r['score']}/{r['max_score']}"
          + (f"  MSG={r['message']}" if r.get('message') else ""))
    return r


def main():
    print("=" * 78)
    print("CODE-EVALUATION SMOKE TEST  (correct -> AC/full ; wrong -> fail/partial)")
    print("=" * 78)
    results = []

    for lang, (good, bad) in SOLUTIONS.items():
        print(f"\n>>> {lang}")
        rg = show(lang, "correct", grade_interview_question(sum_q, lang, good, reveal_hidden=True))
        rb = show(lang, "wrong",   grade_interview_question(sum_q, lang, bad, reveal_hidden=True))
        results.append((lang, rg, rb))

    # SQL against the real DB question
    print("\n>>> sql")
    db = db_manager.get_db_session()
    sql_q = db.query(Question).filter(Question.id == SQL_QID).first()
    db.close()
    rg = show("sql", "correct", grade_interview_question(sql_q, "sql", SQL_CORRECT, reveal_hidden=True))
    rb = show("sql", "wrong",   grade_interview_question(sql_q, "sql", SQL_WRONG, reveal_hidden=True))
    results.append(("sql", rg, rb))

    # ── assertions ──
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    ok = True
    for lang, rg, rb in results:
        good_ok = rg["verdict"] == "AC" and rg["points_awarded"] == rg["max_points"] and rg["max_points"] > 0
        bad_ok = rb["verdict"] != "AC" and rb["points_awarded"] < rg["max_points"]
        line = f"  {lang:10} correct={'PASS' if good_ok else 'FAIL'}  wrong-discriminated={'PASS' if bad_ok else 'FAIL'}"
        print(line)
        ok = ok and good_ok and bad_ok
    print("\nRESULT:", "ALL GREEN - PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
