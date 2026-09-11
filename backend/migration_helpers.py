"""Helpers shared by Alembic revisions.

Deliberately a top-level module rather than something under ``src/``: a revision is a
snapshot of one schema change and has to keep running years later, so it must not import
application code that moves under it. ``backend/`` is on ``sys.path`` for every Alembic
run — ``alembic.ini`` sets ``prepend_sys_path = .`` and ``alembic/env.py`` inserts it
explicitly — so ``from migration_helpers import ...`` resolves from a revision file.
"""
from typing import Optional, Tuple

import sqlalchemy as sa


def referenced_collation(bind, *targets: Tuple[str, str]) -> Optional[str]:
    """The collation shared by the columns a new table's foreign keys point at.

    MySQL requires both sides of a foreign key to share a collation. A ``CREATE TABLE``
    that names none takes the DATABASE default, and wherever that differs from the
    referenced column the constraint is rejected outright::

        (3780, "Referencing column 'company_id' and referenced column 'id' in
                foreign key constraint '...' are incompatible.")

    Which way the mismatch falls is environment-dependent, and no two of our
    environments agree: cPanel hands out ``utf8mb3`` databases holding ``utf8mb4`` tables
    (docs/DEPLOYMENT_NOTES.md), CI's ``mysql:8.0`` service defaults to
    ``utf8mb4_0900_ai_ci``, and a dev database built by ``init_db()`` is
    ``utf8mb4_unicode_ci``. So hardcoding a collation is not a fix — it works in whichever
    environment happens to agree and breaks in the rest, which is exactly how
    ``email_log`` reached CI red.

    Read the real collation instead and pass it as ``mysql_collate``. Pass *only* that:
    MySQL derives the character set from the collation, so naming one as well buys
    nothing and costs correctness, since the charset behind ``utf8mb3_general_ci`` is
    spelled ``utf8`` before MySQL 8.0.28 and ``utf8mb3`` after.

    Returns ``None`` when the answer cannot be trusted — a non-MySQL backend, a table
    that does not exist yet, or targets that disagree with one another — leaving the
    caller to fall back to the database default, the behaviour that predates this helper.
    """
    found = set()
    for table, column in targets:
        try:
            collation = bind.execute(sa.text(
                "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name "
                "AND COLUMN_NAME = :column_name"
            ), {"table_name": table, "column_name": column}).scalar()
        except Exception:
            return None
        if not collation:
            return None
        found.add(collation)

    return found.pop() if len(found) == 1 else None
