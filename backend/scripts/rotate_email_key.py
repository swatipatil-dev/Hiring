#!/usr/bin/env python
"""
Re-encrypt every stored SMTP password from one EMAIL_ENCRYPTION_KEY to another.

Why this exists
---------------
A deployment that never set EMAIL_ENCRYPTION_KEY still encrypted its mailbox
passwords — with a key derived from SECRET_KEY, or, if that was unset too, from
the literal "hireflow-dev-key-change-me" in src/security.py. That constant is
committed to this repository, so anyone with the source and a copy of the
database can read those passwords. docs/GCP_DATA_MIGRATION.md records exactly
that happening.

Moving to a real key is not a config edit: change EMAIL_ENCRYPTION_KEY on its
own and every existing row becomes undecryptable, and mail stops *silently* —
decrypt() returns "" and the callers fall back to the raw ciphertext as if it
were the password (src/services/email_service.py:132, :224).

This script performs the missing step: decrypt with the old key, re-encrypt with
the new, in one pass. It needs no knowledge of the passwords themselves.

What it touches
---------------
Every column in the codebase that holds a Fernet token, found by auditing each
encrypt() call site:

    company_mailboxes.smtp_pass   (routes/admin_routes.py:1515, :1564)
    tenant_configs.smtp_pass      (routes/admin_routes.py:1399)

email_service.dispatch() also encrypts, but into the RQ job payload in Redis
rather than the database — see the warning printed at the end of a real run.

Usage
-----
    # always look first
    python scripts/rotate_email_key.py --old-key <current> --new-key <new> --dry-run

    # then apply
    python scripts/rotate_email_key.py --old-key <current> --new-key <new>

Generate the new key with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Back up first — this rewrites credentials in place:

    mysqldump -u <user> -p <db> company_mailboxes tenant_configs > email-creds.sql

Safe to re-run: a row already encrypted under the new key is recognised and
skipped. No password is ever printed.
"""
import argparse
import os
import sys

# Importable when run as `python scripts/rotate_email_key.py` from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    sys.exit("cryptography is not installed — run this inside the venv:\n"
             "  /opt/hireflow/venv/bin/python scripts/rotate_email_key.py ...")

from sqlalchemy import text  # noqa: E402

from src.config.database_config_mysql import db_manager  # noqa: E402

# (table, primary key column, ciphertext column)
TARGETS = [
    ("company_mailboxes", "id", "smtp_pass"),
    ("tenant_configs", "id", "smtp_pass"),
]

ROTATE, ALREADY, UNREADABLE = "rotate", "already", "unreadable"


def _classify(value: str, old: Fernet, new: Fernet):
    """Decide what to do with one stored value, without mutating anything.

    Three outcomes, and the third is the one that matters:

      rotate      decrypts with the old key — the normal case
      already     decrypts with the new key — a re-run, skip it
      unreadable  neither. Could be legacy plaintext (the `decrypt(x) or x`
                  fallback in email_service means plaintext rows genuinely
                  exist), or a third key from some earlier deployment. Either
                  way, encrypting it blindly would be indistinguishable from
                  corrupting it, so leave it alone and make the operator look.
    """
    raw = value.encode()
    try:
        return ROTATE, old.decrypt(raw).decode()
    except (InvalidToken, ValueError, TypeError):
        pass
    try:
        new.decrypt(raw)
        return ALREADY, None
    except (InvalidToken, ValueError, TypeError):
        return UNREADABLE, None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-encrypt stored SMTP passwords under a new EMAIL_ENCRYPTION_KEY.")
    ap.add_argument("--old-key", required=True,
                    help="the key the rows are encrypted with today")
    ap.add_argument("--new-key", required=True,
                    help="the key to re-encrypt them under")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    if args.old_key == args.new_key:
        return _fail("--old-key and --new-key are identical; nothing to do")
    try:
        old, new = Fernet(args.old_key.encode()), Fernet(args.new_key.encode())
    except Exception as e:
        return _fail(f"invalid Fernet key ({e}). Keys are 44-character "
                     f"urlsafe-base64, e.g. from Fernet.generate_key()")

    mode = "DRY RUN — nothing will be written" if args.dry_run else "APPLYING CHANGES"
    print(f"{mode}\n")

    totals = {ROTATE: 0, ALREADY: 0, UNREADABLE: 0}
    unreadable_rows: list[str] = []

    for table, pk, col in TARGETS:
        with db_manager.engine.begin() as conn:
            if not conn.dialect.has_table(conn, table):
                print(f"{table}: table does not exist — skipped")
                continue

            rows = conn.execute(text(
                f"SELECT {pk}, {col} FROM {table} "
                f"WHERE {col} IS NOT NULL AND {col} != ''"
            )).fetchall()

            counts = {ROTATE: 0, ALREADY: 0, UNREADABLE: 0}
            for row_id, value in rows:
                verdict, plaintext = _classify(value, old, new)
                counts[verdict] += 1
                totals[verdict] += 1

                if verdict == UNREADABLE:
                    unreadable_rows.append(f"{table}.{pk}={row_id}")
                    continue
                if verdict == ALREADY or args.dry_run:
                    continue

                # Re-encrypt in the same transaction as the read.
                conn.execute(
                    text(f"UPDATE {table} SET {col} = :v WHERE {pk} = :id"),
                    {"v": new.encrypt(plaintext.encode()).decode(), "id": row_id},
                )

            print(f"{table}: {len(rows)} row(s) with a stored password")
            print(f"    re-encrypt : {counts[ROTATE]}")
            print(f"    already new: {counts[ALREADY]}")
            print(f"    unreadable : {counts[UNREADABLE]}")

    print()
    if unreadable_rows:
        print("These rows decrypt with NEITHER key and were left untouched:")
        for r in unreadable_rows:
            print(f"    {r}")
        print("\nThey are legacy plaintext, or encrypted under a third key. Use\n"
              "  python scripts/find_email_key.py --ciphertext '<value>'\n"
              "to identify the key, or simply re-enter those mailbox passwords in\n"
              "Admin → Email Settings, which re-encrypts them under the current key.")
        print()

    if args.dry_run:
        print(f"Dry run complete — {totals[ROTATE]} row(s) would be re-encrypted.")
        print("Re-run without --dry-run to apply.")
    else:
        print(f"Done — {totals[ROTATE]} row(s) re-encrypted.")
        if totals[ROTATE] or totals[ALREADY]:
            print("\nNow, in this order:")
            print(f"  1. Set EMAIL_ENCRYPTION_KEY={args.new_key}")
            print("     in /opt/hireflow/backend/.env, then: chmod 600 that file.")
            print("  2. Drain the email queue first if it is not empty — jobs")
            print("     already in Redis carry a password encrypted with the OLD")
            print("     key, and after the restart they fail SMTP auth, which is")
            print("     deliberately not retried (src/mailq/worker.py). Check")
            print("     delivery.queue_depth on GET /api/v1/admin/email/routing.")
            print("  3. systemctl restart hireflow-api hireflow-email-worker")
            print("  4. Verify with POST /api/v1/admin/email/test")
            print("\nThe old passwords were exposed by the old key — re-encrypting")
            print("hides them again but does not un-leak them. Change them at each")
            print("mail provider and re-enter them in Admin → Email Settings.")

    # Non-zero when something needs a human: it is not a failure of the rotation,
    # but it must not scroll past unnoticed in a deploy log.
    return 1 if unreadable_rows else 0


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
