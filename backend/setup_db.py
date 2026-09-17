"""
Create all database tables from SQLAlchemy ORM models.

    python setup_db.py                 # create missing tables. Destroys nothing.
    python setup_db.py --drop          # ALSO drop the tables below first (dev/test only)
    python setup_db.py --drop --force  # allow --drop against a non dev/test database

This script used to drop ``interviews``, ``interview_feedback`` and ``candidates`` at
import time, against a hardcoded ``root@localhost/recruitment_db``, with no confirmation.
Because CLAUDE.md documents it as the normal way to set the database up, following the
project's own instructions on a machine pointed at real data destroyed every completed
interview, its feedback, and every interviewer name recorded on them.

Two things changed. Dropping is now opt-in behind ``--drop``, so the documented no-flag
invocation only creates. And the target now comes from DATABASE_URL, so this script cannot
quietly hit a different database than the application does.
"""
import sys

sys.path.insert(0, '.')

from sqlalchemy import text

from src.config.database_config_mysql import DATABASE_URL, db_manager
from src.models.models import Base
# Imported for their side effect: registering these tables on Base.metadata.
from src.models.question_models import Question, QuestionType, DifficultyLevel  # noqa: F401

# Dropped only with --drop. `interviews`, `interview_feedback` and `candidates` carry
# interviewer names, panel rosters and scorecards — losing them is unrecoverable.
DROPPABLE_TABLES = [
    'interview_questions', 'interview_feedback', 'interviews',
    'ats_results', 'offers', 'candidate_stage_history',
    'hr_tasks', 'assessment_rounds', 'candidates', 'job_postings',
    'system_config', 'ats_interviews', 'ats_system',
    'followup_questions', 'question_evaluations',
]

# A database is only safe to drop if its name says it is disposable.
SAFE_SUFFIXES = ('_test', '_dev', '_local')


def _database_name() -> str:
    tail = DATABASE_URL.rsplit('/', 1)[-1]
    return tail.split('?', 1)[0]


def _confirm_drop(db_name: str, force: bool) -> bool:
    safe = db_name.endswith(SAFE_SUFFIXES)
    if not safe and not force:
        print(f"\n  REFUSING to drop tables in '{db_name}'.")
        print(f"  Its name does not end in {' / '.join(SAFE_SUFFIXES)}, so it may hold real data.")
        print("  Re-run with --drop --force if you are certain.\n")
        return False

    print(f"\n  About to DROP {len(DROPPABLE_TABLES)} tables in '{db_name}':")
    print("    " + ", ".join(DROPPABLE_TABLES))
    print("\n  This destroys every interview, its feedback, and the interviewer names on them.")
    print("  It cannot be undone.\n")
    try:
        typed = input(f"  Type the database name ({db_name}) to confirm: ")
    except EOFError:
        # No tty (CI, a hook, a piped shell). Refusing is the only safe reading of silence.
        print("  No interactive input available — refusing to drop.")
        return False
    return typed.strip() == db_name


def drop_tables(db_name: str) -> None:
    with db_manager.engine.begin() as conn:
        conn.execute(text('SET FOREIGN_KEY_CHECKS=0'))
        for t in DROPPABLE_TABLES:
            # Table names are from the constant above, never user input.
            conn.execute(text(f'DROP TABLE IF EXISTS {t}'))
            print(f'  Dropped: {t}')
        conn.execute(text('SET FOREIGN_KEY_CHECKS=1'))


def main() -> int:
    args = set(sys.argv[1:])
    unknown = args - {'--drop', '--force'}
    if unknown:
        print(f"Unknown option(s): {', '.join(sorted(unknown))}")
        print(__doc__)
        return 2

    db_name = _database_name()
    print(f"Target database: {db_name}")

    if '--drop' in args:
        if not _confirm_drop(db_name, force='--force' in args):
            print("  Aborted. Nothing was dropped.")
            return 1
        drop_tables(db_name)
    else:
        print("No --drop given; existing tables are left untouched.")

    print('\nCreating tables from ORM models...')
    Base.metadata.create_all(db_manager.engine)

    with db_manager.engine.connect() as conn:
        tables = [r[0] for r in conn.execute(text('SHOW TABLES')).fetchall()]
    print(f'Done. {len(tables)} tables present:')
    print(tables)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
