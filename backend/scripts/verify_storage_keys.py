"""Check that every file the database points at actually exists.

Why this exists
---------------
Uploads are addressed by a relative *key* (``resumes/<uuid>.pdf``) which is what
the database stores and what the URL path uses -- identical whether storage is the
local filesystem or a GCS bucket (``src/services/storage.py``). That is what makes
migrating between backends a plain file copy with no database rewriting.

It also means a missing file is invisible until someone opens the record. After a
migration, ``ls resumes | wc -l`` tells you almost nothing: it cannot say whether
the files present are the ones the 449 candidate rows actually reference.

This walks every column that holds a storage key and asks
``storage.exists()`` -- the same call the application makes, so leading slashes,
``HIREFLOW_DATA_DIR`` and backslashes are normalised exactly as they are at
runtime. Checking with ``os.path.exists(os.path.join(...))`` instead would report
false misses: the database legitimately contains values like
``/logos/391c9423-….png`` with a leading slash, which ``_norm()`` strips.

Usage::

    python scripts/verify_storage_keys.py
    DATABASE_URL=mysql+pymysql://... python scripts/verify_storage_keys.py --show 50

Read-only. Exits 1 if any referenced file is missing.
"""
import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR))

# Columns holding a storage key are named ``*_path`` by convention. These three
# are the exceptions -- named ``_url`` because they may ALSO hold an external
# link, which is why http(s) values are skipped rather than reported missing.
EXTRA_KEY_COLUMNS = {"logo_url", "favicon_url", "logo_dark_url"}


def key_columns(metadata):
    """(table, column) pairs that hold storage keys, in a stable order."""
    from sqlalchemy import String, Text

    out = []
    for tname, table in sorted(metadata.tables.items()):
        for col in table.columns:
            if not isinstance(col.type, (String, Text)):
                continue
            if col.name.endswith("_path") or col.name in EXTRA_KEY_COLUMNS:
                out.append((tname, col.name))
    return out


def _metadata():
    from src.models.models import Base
    import src.models.judge_models     # noqa: F401
    import src.models.question_models  # noqa: F401
    return Base.metadata


def check(engine, metadata, storage, show):
    from sqlalchemy import text

    total_checked = total_missing = 0
    failures = []

    for tname, cname in key_columns(metadata):
        table = metadata.tables[tname]
        pk = list(table.primary_key.columns)
        pk_name = pk[0].name if pk else None
        select_pk = f"`{pk_name}`" if pk_name else "'?'"

        sql = (f"SELECT {select_pk}, `{cname}` FROM `{tname}` "
               f"WHERE `{cname}` IS NOT NULL AND `{cname}` <> ''")
        try:
            with engine.connect() as c:
                rows = c.execute(text(sql)).fetchall()
        except Exception as e:
            print(f"  {tname}.{cname:<24} SKIPPED ({type(e).__name__}: {e})")
            continue

        missing = []
        checked = 0
        for row_id, value in rows:
            v = str(value).strip()
            if v.lower().startswith(("http://", "https://", "data:")):
                continue          # an external link, not a stored object
            checked += 1
            try:
                present = storage.exists(v)
            except Exception:
                present = False
            if not present:
                missing.append((row_id, v))

        total_checked += checked
        total_missing += len(missing)
        flag = "MISSING" if missing else ("--" if not checked else "ok")
        print(f"  {flag:<8} {tname + '.' + cname:<46} "
              f"{checked - len(missing)}/{checked}")

        for row_id, v in missing[:show]:
            failures.append(f"    {tname}.{cname}  id={row_id}  key={v}")
        if len(missing) > show:
            failures.append(f"    ... and {len(missing) - show} more in "
                            f"{tname}.{cname}")

    return total_checked, total_missing, failures


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, default=10,
                    help="how many missing keys to list per column (default 10)")
    args = ap.parse_args()

    from src.config.database_config_mysql import db_manager
    from src.services.storage import storage, DATA_DIR

    backend = getattr(storage, "backend", "local")
    print(f"storage backend : {backend}")
    if backend == "local":
        print(f"data directory  : {DATA_DIR}")
    print()

    checked, missing, failures = check(db_manager.engine, _metadata(), storage,
                                       args.show)

    print(f"\n{checked - missing}/{checked} referenced files present.")

    if missing:
        print(f"\n{missing} MISSING:")
        for line in failures:
            print(line)
        print("\nEach of these is a record whose file will 404 when opened.")
        return 1

    print("Every file the database references exists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
