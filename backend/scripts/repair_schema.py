"""Inspect -- and optionally repair -- a database that was built by create_all instead
of by migrations.

The situation this exists for: `Base.metadata.create_all()` runs at startup and creates
missing TABLES, but never alters existing ones. A deployment that has only ever
bootstrapped that way therefore has no `alembic_version` row at all, and any release
that added a COLUMN to a table that already existed shipped code selecting a column the
database does not have. Every request touching it returns 500.

`alembic upgrade head` alone does NOT fix that. With no `alembic_version`, Alembic
considers the database to be at *base* and replays all 29 migrations from the root --
against a schema that already exists. The root revision is unguarded
(`add_column('users', 'company_id')`), so it fails on the first statement.

The fix is to STAMP the database at the revision its schema already matches, then
upgrade the remainder. This script works out whether that is needed, shows you exactly
what it would do, and only acts when you pass --apply.

Usage::

    # 1. open a tunnel to Cloud SQL
    cloud-sql-proxy.exe <PROJECT:REGION:INSTANCE> --port 3307

    # 2. point at it
    set DATABASE_URL=mysql+pymysql://USER:PASS@127.0.0.1:3307/DBNAME

    # 3. look, without changing anything
    python scripts/repair_schema.py

    # 4. if the report looks right
    python scripts/repair_schema.py --apply

Read-only by default. --apply performs DDL; take a backup first.
"""
import argparse
import sys
from pathlib import Path

# Appended, never inserted: the backend contains a directory named `alembic`, and
# putting the root first makes `import alembic` resolve to those migration scripts as an
# empty namespace package instead of the library.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR))

# The revision that created `resume_pool`. A database with that table has, in effect,
# everything up to and including this revision, because create_all built those tables
# from the same models the migrations describe.
DEFAULT_BASELINE = "u1k2l3m4n5o6"

# Columns the Talent Pool selects that only migrations add.
POOL_COLUMNS = [
    "source", "source_user_id", "source_candidate_id", "imported_at",
    "stage", "notes", "last_contact_at", "source_channel",
]


def _alembic():
    import alembic
    if getattr(alembic, "__file__", None) is None:
        raise RuntimeError(
            "`import alembic` found the migrations directory, not the library. "
            "Run this as `python scripts/repair_schema.py` from the backend root."
        )
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return command, cfg


def inspect(engine):
    from sqlalchemy import inspect as sa_inspect, text

    insp = sa_inspect(engine)
    state = {
        "has_alembic_version": insp.has_table("alembic_version"),
        "revision": None,
        "has_resume_pool": insp.has_table("resume_pool"),
        "has_attachments": insp.has_table("resume_pool_attachments"),
        "missing_columns": [],
    }

    if state["has_alembic_version"]:
        with engine.connect() as c:
            state["revision"] = c.execute(
                text("SELECT version_num FROM alembic_version")).scalar()

    if state["has_resume_pool"]:
        present = {c["name"] for c in insp.get_columns("resume_pool")}
        state["missing_columns"] = [c for c in POOL_COLUMNS if c not in present]

    return state


def report(state, baseline):
    print("\n--- current state ------------------------------------------")
    print(f"  alembic_version table : {'yes' if state['has_alembic_version'] else 'NO'}")
    print(f"  stamped revision      : {state['revision'] or '(none)'}")
    print(f"  resume_pool table     : {'yes' if state['has_resume_pool'] else 'NO'}")
    print(f"  attachments table     : {'yes' if state['has_attachments'] else 'NO'}")
    print(f"  missing pool columns  : {', '.join(state['missing_columns']) or 'none'}")

    print("\n--- diagnosis ----------------------------------------------")
    if not state["has_resume_pool"]:
        print("  resume_pool does not exist. This database is older than the talent")
        print("  pool, or is not the one the app uses. Stop and check DATABASE_URL.")
        return False

    # Branch on the REVISION, not on the table. A failed `alembic upgrade` leaves an
    # empty alembic_version table behind, so "table exists" does not mean "stamped" —
    # and treating it as stamped would recommend an upgrade that fails all over again.
    # "Complete" means every column AND the attachments table. Checking only columns
    # reported success on a database whose CREATE TABLE had failed half a migration
    # earlier, which is precisely the state worth catching.
    incomplete = state["missing_columns"] or not state["has_attachments"]

    if state["revision"]:
        if incomplete:
            print(f"  Stamped at {state['revision']}, but the schema is incomplete.")
            print("  `alembic upgrade head` should finish it.")
            return True
        print("  Stamped and complete -- nothing to repair.")
        return False

    if state["has_alembic_version"]:
        print("  alembic_version exists but is EMPTY -- an earlier upgrade attempt")
        print("  failed part-way. Alembic still reads this as base, so a retry would")
        print("  fail identically.")
    else:
        print("  No alembic_version: this schema was built by create_all, never migrated.")
    if state["missing_columns"]:
        print(f"  Upgrading from base would replay all migrations against existing")
        print(f"  tables and fail on the first one. Stamp at {baseline} first, then")
        print("  upgrade -- that runs only the guarded, idempotent migrations.")
        return True
    print("  No columns missing, but nothing is stamped. Stamping records reality so")
    print("  future migrations work; no DDL will run.")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="perform the stamp/upgrade (default is report only)")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE,
                    help=f"revision to stamp when unstamped (default {DEFAULT_BASELINE})")
    args = ap.parse_args()

    from src.config.database_config_mysql import DATABASE_URL, db_manager

    shown = DATABASE_URL
    if "@" in shown:                       # never print the password
        head, tail = shown.split("@", 1)
        shown = head.split("//")[0] + "//***@" + tail
    print(f"database: {shown}")

    engine = db_manager.engine
    state = inspect(engine)
    actionable = report(state, args.baseline)

    if not actionable:
        return 0

    if not args.apply:
        print("\n  Report only. Re-run with --apply to make these changes.")
        print("  Take a backup first -- this performs DDL on a live database.\n")
        return 0

    command, cfg = _alembic()
    print("\n--- applying -----------------------------------------------")

    if not state["revision"]:
        print(f"  stamping at {args.baseline} ...")
        command.stamp(cfg, args.baseline)

    print("  upgrading to head ...")
    command.upgrade(cfg, "head")

    after = inspect(engine)
    print("\n--- result -------------------------------------------------")
    print(f"  stamped revision      : {after['revision']}")
    print(f"  missing pool columns  : {', '.join(after['missing_columns']) or 'none'}")
    print(f"  attachments table     : {'yes' if after['has_attachments'] else 'NO'}")

    if after["missing_columns"]:
        print("\n  Columns are STILL missing -- do not treat this as fixed.\n")
        return 1
    print("\n  Schema is complete. Reload /hr/talent-pool to confirm.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
