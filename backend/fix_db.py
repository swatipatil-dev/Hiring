"""
Drop the recreatable tables so the next `python setup_db.py` rebuilds them from scratch.

    python fix_db.py --drop            # dev/test databases only
    python fix_db.py --drop --force    # allow against any database

This file used to drop `interviews`, `interview_feedback` and `candidates` the moment it
was imported, against a hardcoded `root@localhost/recruitment_db`, with no confirmation —
so `python fix_db.py` on the wrong machine destroyed every completed interview and the
interviewer names recorded on them.

It now shares setup_db's guarded drop path: the target comes from DATABASE_URL, dropping
is opt-in, and a non dev/test database is refused unless --force is passed and the name is
typed back.
"""
import sys

sys.path.insert(0, '.')

from setup_db import DROPPABLE_TABLES, _confirm_drop, _database_name, drop_tables  # noqa: E402


def main() -> int:
    args = set(sys.argv[1:])
    unknown = args - {'--drop', '--force'}
    if unknown:
        print(f"Unknown option(s): {', '.join(sorted(unknown))}")
        print(__doc__)
        return 2

    if '--drop' not in args:
        print(__doc__)
        print(f"Nothing done. Pass --drop to actually drop the {len(DROPPABLE_TABLES)} tables.")
        return 1

    db_name = _database_name()
    print(f"Target database: {db_name}")
    if not _confirm_drop(db_name, force='--force' in args):
        print("  Aborted. Nothing was dropped.")
        return 1

    drop_tables(db_name)
    print("\nDone. Run `python setup_db.py` to recreate them.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
