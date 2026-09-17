"""
Delete pool questions whose text is just a row number.

A CSV whose first column was headed "Question No." used to import the serial
number as the question text, because the column matcher took a substring match
on "question" and "questionno" sorts first. The parser is fixed
(generator_router._parse_mcq_csv), but rows already written look like:

    question_text = "1", "2", "3" ...

They are unusable, they show up in "Use Previous", and because the importer's
duplicate check is scoped to the interview rather than the skill, they collide
with every later upload to the same test and block it entirely.

Read-only by default. It prints what it found and changes nothing unless you
pass --yes.

    python scripts/purge_numeric_questions.py                  # list only
    python scripts/purge_numeric_questions.py --interview 42   # narrow it
    python scripts/purge_numeric_questions.py --yes            # actually delete

Run from the backend/ directory.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.database_config_mysql import db_manager  # noqa: E402
from src.models.question_models import Question  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


def is_junk(text) -> bool:
    """A question whose entire text is a number. Never legitimate."""
    return bool(text) and str(text).strip().isdigit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interview", type=int, default=None, help="restrict to one interview id")
    ap.add_argument("--skill", default=None, help="restrict to one skill name (exact)")
    ap.add_argument("--yes", action="store_true", help="actually delete; without it nothing changes")
    args = ap.parse_args()

    Session = sessionmaker(bind=db_manager.engine)
    db = Session()
    try:
        # Only the four columns this script needs, never whole ORM objects: the
        # model carries columns that a database one migration behind does not
        # have yet, and SELECTing them fails the whole query with
        # "Unknown column ... in 'field list'". This has to run anywhere.
        q = db.query(
            Question.id, Question.interview_id,
            Question.question_text, Question.skill_name,
        )
        if args.interview is not None:
            q = q.filter(Question.interview_id == args.interview)
        if args.skill:
            q = q.filter(Question.skill_name == args.skill)

        # Filtering in Python, not SQL: "is it entirely digits" has no portable
        # SQL spelling, and the table is small enough that correctness beats
        # cleverness here.
        junk = [row for row in q.all() if is_junk(row.question_text)]

        if not junk:
            print("No questions with a purely numeric text found. Nothing to do.")
            return 0

        print(f"Found {len(junk)} question(s) whose text is just a number:\n")
        by_group = {}
        for row in junk:
            by_group.setdefault((row.interview_id, row.skill_name), []).append(row)

        for (interview_id, skill), rows in sorted(by_group.items(), key=lambda kv: str(kv[0])):
            texts = ", ".join(sorted((str(r.question_text) for r in rows), key=lambda t: int(t))[:12])
            more = "" if len(rows) <= 12 else f" … (+{len(rows) - 12} more)"
            print(f"  interview={interview_id}  skill={skill!r}  count={len(rows)}")
            print(f"      texts: {texts}{more}")

        if not args.yes:
            print("\nDry run — nothing deleted. Re-run with --yes to delete these.")
            return 0

        # Delete by primary key for the same reason the SELECT is narrow — no
        # ORM instances to hydrate, so a column the model knows about and the
        # database does not cannot break the delete.
        ids = [row.id for row in junk]
        deleted = (
            db.query(Question)
            .filter(Question.id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()
        print(f"\nDeleted {deleted} question(s).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
