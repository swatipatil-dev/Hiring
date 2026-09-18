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

A row is NEVER deleted if a candidate has interacted with it — referenced by
``interview_code_submissions``, ``communication_answers``,
``candidate_question_history``, or present by id inside an interview's
``answers_json``. Those are reported as skipped and left for a human. The same
rule as scripts/dedupe_questions.py, using the same helper.

``question_set_assignments`` rows ARE removed first: they are pure join rows
carrying no candidate data, and four tables hold foreign keys to
``interview_questions.id`` with no ON DELETE clause, so MySQL refuses the parent
delete while any child row survives. The ORM cascade does not help — a bulk
Query.delete() bypasses it.

Read-only by default. Nothing changes unless you pass --yes.

    python scripts/purge_numeric_questions.py                  # list only
    python scripts/purge_numeric_questions.py --interview 42   # narrow it
    python scripts/purge_numeric_questions.py --yes            # actually delete

Run from the backend/ directory.
"""

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # backend/ — for src.*, generator_router
# scripts/ itself, so sibling helpers import as `dedupe_questions` rather than
# `scripts.dedupe_questions`: site-packages ships its own regular `scripts`
# package, and a regular package anywhere on the path beats this directory
# (which has no __init__.py), so the dotted form resolves to the wrong module.
sys.path.insert(0, str(_HERE))

from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.config.database_config_mysql import db_manager  # noqa: E402
from src.models.question_models import (  # noqa: E402
    Question,
    QuestionSetAssignment,
)

# Reuse the "a candidate touched this" definition rather than restating it —
# it also covers Interview.answers_json, which is easy to forget.
from dedupe_questions import _build_inuse_ids  # noqa: E402

try:
    # Shared with the importer so the parser and the cleanup agree on what junk
    # is. If they drift, the parser starts rejecting rows this cannot find.
    from generator_router import is_serial_like
except ImportError:  # pragma: no cover - importer not on the path
    def is_serial_like(text) -> bool:
        return bool(text) and str(text).strip().isdigit()


def classify(db, interview_id=None, skill=None):
    """Split the junk questions into (safe to delete, leave for a human).

    Separate from main() so the FK and candidate-safety behaviour can be tested
    without driving argparse.
    """
    # Four columns, never whole ORM objects: the model carries columns that a
    # database one migration behind does not have yet, and SELECTing them
    # fails the whole query with "Unknown column ... in 'field list'".
    q = db.query(
        Question.id, Question.interview_id,
        Question.question_text, Question.skill_name,
    )
    if interview_id is not None:
        q = q.filter(Question.interview_id == interview_id)
    if skill:
        q = q.filter(Question.skill_name == skill)

    # Filtering in Python: "is this text a bare row number" has no portable
    # SQL spelling, and the table is small enough that clarity wins.
    junk = [row for row in q.all() if is_serial_like(row.question_text)]
    if not junk:
        return [], []

    inuse = _build_inuse_ids(db)
    return [r for r in junk if r.id not in inuse], [r for r in junk if r.id in inuse]


def delete_questions(db, ids):
    """Delete questions by id, clearing their test-set assignments first.

    Returns (questions_deleted, assignments_deleted).

    The assignments have to go first: four tables hold foreign keys to
    interview_questions.id with no ON DELETE clause, so MySQL raises 1451 on the
    parent while any child row survives, and a bulk Query.delete() skips the ORM
    cascade that would otherwise clear them. The other three child tables cannot
    have rows for these ids — that is exactly what classify() screens out.
    """
    if not ids:
        return 0, 0
    assignments = (
        db.query(QuestionSetAssignment)
        .filter(QuestionSetAssignment.question_id.in_(ids))
        .delete(synchronize_session=False)
    )
    deleted = (
        db.query(Question)
        .filter(Question.id.in_(ids))
        .delete(synchronize_session=False)
    )
    return deleted, assignments


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--interview", type=int, default=None, help="restrict to one interview id")
    ap.add_argument("--skill", default=None, help="restrict to one skill name (exact)")
    ap.add_argument("--yes", action="store_true", help="actually delete; without it nothing changes")
    args = ap.parse_args()

    Session = sessionmaker(bind=db_manager.engine)
    db = Session()
    try:
        deletable, protected = classify(db, interview_id=args.interview, skill=args.skill)

        if not deletable and not protected:
            print("No questions with a row-number question text found. Nothing to do.")
            return 0

        print(f"Found {len(deletable) + len(protected)} question(s) whose text is just a row number.\n")

        def _summarise(rows, heading):
            if not rows:
                return
            print(heading)
            groups = {}
            for row in rows:
                groups.setdefault((row.interview_id, row.skill_name), []).append(row)
            for (interview_id, skill), rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
                texts = ", ".join(sorted(str(r.question_text) for r in rs)[:12])
                more = "" if len(rs) <= 12 else f" … (+{len(rs) - 12} more)"
                print(f"  interview={interview_id}  skill={skill!r}  count={len(rs)}")
                print(f"      {texts}{more}")
            print()

        _summarise(deletable, "Safe to delete:")
        _summarise(
            protected,
            "SKIPPED — a candidate has already answered or been assigned these.\n"
            "Review them by hand; this script will not touch them:",
        )

        if not deletable:
            print("Nothing is safe to delete automatically.")
            return 0

        if not args.yes:
            print("Dry run — nothing deleted. Re-run with --yes to delete the safe ones.")
            return 0

        try:
            deleted, assignments = delete_questions(db, [r.id for r in deletable])
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            print(f"\nDelete failed, nothing was changed: {type(exc).__name__}: {exc}")
            print("If this is a foreign-key error, a table references these questions "
                  "that this script does not know about. Report it rather than forcing it.")
            return 1

        print(f"\nDeleted {deleted} question(s) and {assignments} test-set assignment(s).")
        if protected:
            print(f"Left {len(protected)} in place because a candidate had interacted with them.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
