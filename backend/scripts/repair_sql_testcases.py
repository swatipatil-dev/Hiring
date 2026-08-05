"""Backfill validated test cases for SQL coding questions.

Many SQL coding questions were saved with a correct reference query
(``sample_answer``) but no usable ``test_cases`` — so the candidate editor shows
an empty "Expected Output" and the answer can't be auto-graded. This script finds
those questions and, using the reference query as ground truth:

  * recomputes ``expected_output`` for any existing cases (dropping broken ones), or
  * synthesizes seed datasets via the LLM and computes their expected output,

then writes back at least one gradeable sample case. Expected outputs are computed
by actually running the reference query against the seed in SQLite, so they are
correct by construction. Idempotent — safe to re-run.

Usage::

    python scripts/repair_sql_testcases.py            # repair only questions with no usable cases
    python scripts/repair_sql_testcases.py --all       # also recompute questions that already have cases
    python scripts/repair_sql_testcases.py --dry-run    # report what would change, write nothing
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.config.database_config_mysql import db_manager
from src.judge import sql_testgen as g


def _has_usable_cases(raw) -> bool:
    try:
        cases = json.loads(raw) if raw else []
    except Exception:
        return False
    return isinstance(cases, list) and any(
        isinstance(c, dict) and (c.get("expected_output", "") or "").strip() for c in cases
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="also recompute questions that already have usable cases")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args()

    db = db_manager.get_db_session()  # no tenant bound -> sees every company's rows
    repaired = skipped = failed = 0
    try:
        rows = db.execute(text(
            "SELECT id, question_text, sample_answer, test_cases, category "
            "FROM interview_questions WHERE question_type='coding' "
            "AND sample_answer IS NOT NULL AND sample_answer <> ''"
        )).fetchall()

        for qid, qtext, sample_answer, raw_tc, category in rows:
            if not g.looks_like_sql(sample_answer, category):
                continue
            if _has_usable_cases(raw_tc) and not args.all:
                skipped += 1
                continue

            try:
                existing = json.loads(raw_tc) if raw_tc else []
            except Exception:
                existing = []

            cases = g.ensure_sql_testcases(qtext, sample_answer, existing)
            if not cases:
                print(f"[FAIL] {qid[:8]} — could not build any valid case")
                failed += 1
                continue

            samples = sum(1 for c in cases if c.get("is_sample"))
            print(f"[OK]   {qid[:8]} — {len(cases)} case(s), {samples} sample; "
                  f"sample expected = {cases[0]['expected_output'][:60]!r}")
            if not args.dry_run:
                db.execute(
                    text("UPDATE interview_questions SET test_cases=:tc WHERE id=:id"),
                    {"tc": json.dumps(cases), "id": qid},
                )
            repaired += 1

        if not args.dry_run:
            db.commit()
        print(f"\nDone. repaired={repaired} skipped={skipped} failed={failed}"
              f"{' (dry-run, nothing written)' if args.dry_run else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
