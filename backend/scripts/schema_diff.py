"""Diff the ORM models against a live database.

Why this exists
---------------
``alembic current`` is not evidence that a schema is correct. A database can be
STAMPED at head while its schema is not, and that combination is silent until a
request touches the missing column and returns 500.

It happens whenever a database is bootstrapped by ``Base.metadata.create_all()``
and then stamped forward -- which is exactly how both of our databases were built.
``create_all`` creates missing TABLES but never ALTERs an existing one, so any
migration that adds a COLUMN to a table create_all already made is skipped, while
the version table happily claims it ran.

Found in the wild: the GCP export was stamped at ``x4n5o6p7q8r9`` but was missing
``company_mailboxes.hourly_limit``, added by ``s9i0j1k2l3m4`` -- an *ancestor* of
that revision, so ``alembic upgrade head`` would skip it forever. SQLAlchemy emits
every mapped column in its SELECTs, so one missing column takes out the whole
model: the admin mailbox screen and every outbound email that resolves a sender.

``repair_schema.py`` answers a narrower question (stamp-or-upgrade, against a
hardcoded list of talent-pool columns) and would not have caught it. This compares
everything the models declare.

Usage::

    python scripts/schema_diff.py                     # the configured database
    DATABASE_URL=mysql+pymysql://... python scripts/schema_diff.py

Read-only. Exits 1 when anything the code needs is absent, so it works as a gate
in a deployment or migration script.

Types are deliberately NOT compared. Presence is what causes 500s; type
comparison across MySQL reflection and SQLAlchemy declarations produces mostly
false positives (``VARCHAR(500)`` vs ``String(500)``, ``TEXT`` vs ``Text``) and a
noisy check gets ignored, which is worse than no check.
"""
import argparse
import sys
from pathlib import Path

# Appended, never inserted: the backend contains a directory named `alembic`, and
# putting the root first makes `import alembic` resolve to those migration scripts
# as an empty namespace package instead of the library.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR))


def _metadata():
    """Every table the application declares.

    All three model modules must be imported. Importing only ``src.models.models``
    silently omits the judge and question tables -- the same trap that makes
    ``create_all`` under-build a fresh database.
    """
    from src.models.models import Base
    import src.models.judge_models    # noqa: F401  registers the judge tables
    import src.models.question_models  # noqa: F401  registers Question et al.
    return Base.metadata


def _masked(url: str) -> str:
    if "@" not in url:
        return url
    head, tail = url.split("@", 1)
    return head.split("//")[0] + "//***@" + tail


def _revisions(engine):
    """(stamped revision, script head) -- or (None, None) if unavailable."""
    from sqlalchemy import inspect as sa_inspect, text

    stamped = None
    if sa_inspect(engine).has_table("alembic_version"):
        with engine.connect() as c:
            stamped = c.execute(text("SELECT version_num FROM alembic_version")).scalar()

    head = None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        pass
    return stamped, head


def diff(engine, metadata):
    """Everything the models declare that the database lacks."""
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(engine)
    live_tables = set(insp.get_table_names())

    missing_tables, missing_columns, missing_indexes = [], [], []

    for name, table in sorted(metadata.tables.items()):
        if name not in live_tables:
            missing_tables.append(name)
            continue  # its columns are implied; do not list them twice

        have_cols = {c["name"] for c in insp.get_columns(name)}
        missing_columns += [f"{name}.{c.name}" for c in table.columns
                            if c.name not in have_cols]

        # Reflected index names, plus unique constraints -- MySQL implements a
        # UNIQUE constraint as an index, but SQLAlchemy reports them separately
        # depending on how it was declared, so collect both sides.
        have_keys = {i["name"] for i in insp.get_indexes(name) if i.get("name")}
        try:
            have_keys |= {u["name"] for u in insp.get_unique_constraints(name)
                          if u.get("name")}
        except NotImplementedError:
            pass

        for idx in table.indexes:
            if idx.name and idx.name not in have_keys:
                missing_indexes.append(
                    f"{name}.{idx.name}" + (" (UNIQUE)" if idx.unique else ""))
        for con in table.constraints:
            n = getattr(con, "name", None)
            if type(con).__name__ == "UniqueConstraint" and n and n not in have_keys:
                missing_indexes.append(f"{name}.{n} (UNIQUE)")

    return {
        "tables": missing_tables,
        "columns": missing_columns,
        "indexes": missing_indexes,
        "extra_tables": sorted(live_tables - set(metadata.tables) - {"alembic_version"}),
    }


def _section(title, items, empty="none"):
    print(f"\n{title} ({len(items)}):")
    for i in items:
        print(f"    {i}")
    if not items:
        print(f"    {empty}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quiet", action="store_true",
                    help="print only differences and the verdict")
    args = ap.parse_args()

    from src.config.database_config_mysql import DATABASE_URL, db_manager

    metadata = _metadata()
    engine = db_manager.engine

    if not args.quiet:
        print(f"database : {_masked(DATABASE_URL)}")
        print(f"models   : {len(metadata.tables)} tables")

    stamped, head = _revisions(engine)
    if not args.quiet:
        print(f"revision : {stamped or '(unstamped)'}"
              + (f"   head: {head}" if head else ""))
        if stamped and head and stamped != head:
            print("           -> behind head; `alembic upgrade head` has work to do")

    d = diff(engine, metadata)

    _section("TABLES the database is missing", d["tables"])
    _section("COLUMNS the database is missing", d["columns"])
    _section("INDEXES / UNIQUE constraints the database is missing", d["indexes"])

    if d["extra_tables"] and not args.quiet:
        _section("tables present but not declared by the models (informational)",
                 d["extra_tables"])

    broken = d["tables"] or d["columns"]
    if broken:
        print("\nFAIL: the code selects things this database does not have.")
        if stamped and head and stamped == head:
            print("      Note it IS stamped at head -- that is not evidence of "
                  "anything.\n      A migration that predates the stamp was never "
                  "applied, and\n      `alembic upgrade head` will skip it. Apply "
                  "the DDL directly.")
        else:
            print("      Try `alembic upgrade head` first, then re-run this.")
        return 1

    if d["indexes"]:
        print("\nWARN: indexes are missing. Nothing will 500, but a missing UNIQUE "
              "\n      constraint means duplicate rows are already possible.")
        return 1

    print("\nOK: every table, column and index the models declare is present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
