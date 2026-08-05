"""Fix the candidate code-template on SQL coding questions.

A bug in question generation forced a **Python** starter template
(``import sys / def main(): / sys.stdin.read()``) onto *every* coding question,
including SQL ones — so candidates opening a SQL question saw Python boilerplate
and were nudged toward writing the wrong (often MySQL-flavored) code. The generator
is now language-aware; this script repairs questions already saved with the Python
template.

For every ``interview_questions`` row that is a coding + SQL question (detected via
``sql_testgen.looks_like_sql`` on its reference query / category) whose
``code_template`` is the Python boilerplate (or empty), set it to the SQL starter
``-- Write your SQL query below``. Idempotent — safe to re-run.

Usage::

    python scripts/repair_sql_code_templates.py            # repair
    python scripts/repair_sql_code_templates.py --dry-run   # report only, write nothing
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.config.database_config_mysql import db_manager
from src.judge import sql_testgen as g

SQL_TEMPLATE = "-- Write your SQL query below\n"


def _is_python_or_empty(tpl) -> bool:
    t = (tpl or "").strip()
    return (not t) or t.startswith("import sys") or t.startswith("def ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args()

    db = db_manager.get_db_session()  # no tenant bound -> sees every company's rows
    repaired = skipped = 0
    try:
        rows = db.execute(text(
            "SELECT id, sample_answer, correct_answer, category, code_template "
            "FROM interview_questions WHERE question_type='coding'"
        )).fetchall()

        for qid, sample_answer, correct_answer, category, tpl in rows:
            ref = sample_answer or correct_answer
            if not g.looks_like_sql(ref, category):
                continue
            if (tpl or "").strip() == SQL_TEMPLATE.strip():
                skipped += 1
                continue
            if not _is_python_or_empty(tpl):
                # A non-Python, non-SQL-starter custom template — leave it alone.
                skipped += 1
                continue

            print(f"[OK]   {qid[:8]} — Python/empty template -> SQL starter")
            if not args.dry_run:
                db.execute(
                    text("UPDATE interview_questions SET code_template=:t WHERE id=:id"),
                    {"t": SQL_TEMPLATE, "id": qid},
                )
            repaired += 1

        if not args.dry_run:
            db.commit()
        print(f"\nDone. repaired={repaired} skipped={skipped}"
              f"{' (dry-run, nothing written)' if args.dry_run else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
