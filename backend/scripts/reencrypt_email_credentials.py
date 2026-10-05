#!/usr/bin/env python
"""
Re-encrypt email connection credentials (and stored message content) under the
primary key in EMAIL_CREDENTIAL_KEYS.

Two jobs, one pass:

* **After the upgrade** that introduced email connections, migrated passwords
  are still in their old form (key version ``legacy``): a Fernet token under
  ``src/security.py``'s key, or — from very old rows — the plain password. This
  rewrites each under the new keyring. A token that will not decrypt with the
  legacy key is NOT guessed at: the connection is set to ``needs_attention``
  (``credential_unreadable``) so its mail blocks visibly until an admin
  re-enters the password.
* **Key rotation.** Prepend a new key to EMAIL_CREDENTIAL_KEYS
  (``k2:<new>,k1:<old>``), restart, run this, then drop the old key. Every value
  not under the primary key is rewritten.

Usage (from backend/):

    python scripts/reencrypt_email_credentials.py --dry-run
    python scripts/reencrypt_email_credentials.py
    python scripts/reencrypt_email_credentials.py --content     # message bodies too

Needs EMAIL_CREDENTIAL_KEYS, and EMAIL_ENCRYPTION_KEY (or SECRET_KEY) as the
legacy key while legacy rows remain. Prints no secret. Safe to re-run.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def reencrypt(db, *, dry_run: bool = False, content: bool = False) -> dict:
    from src.email_api import constants as C, crypto
    from src.models.models import EmailConnection, EmailMessage
    primary = crypto.primary_version()
    if primary is None:
        raise SystemExit("EMAIL_CREDENTIAL_KEYS is not set; nothing can be re-encrypted.")
    stats = {"rewritten": 0, "plaintext": 0, "unreadable": 0, "current": 0, "content": 0}
    rows = (db.query(EmailConnection)
            .filter(EmailConnection.credential_enc.isnot(None))
            .all())
    for conn in rows:
        if conn.credential_key_version == primary:
            stats["current"] += 1
            continue
        try:
            if conn.credential_key_version == crypto.LEGACY:
                if crypto.looks_like_fernet_token(conn.credential_enc):
                    secret = crypto.decrypt_legacy(conn.credential_enc)
                else:
                    secret = conn.credential_enc          # a pre-encryption plain row
                    stats["plaintext"] += 1
            else:
                secret = crypto.decrypt_secret(conn.credential_enc, conn.credential_key_version)
        except crypto.CredentialUnavailable:
            stats["unreadable"] += 1
            print(f"  unreadable: connection {conn.id} ({conn.name}, company {conn.company_id})")
            if not dry_run:
                conn.status = C.CONN_NEEDS_ATTENTION
                conn.error_code = C.R_CREDENTIAL_UNREADABLE
                conn.error_detail = "Stored password could not be decrypted; re-enter it."
            continue
        stats["rewritten"] += 1
        if not dry_run:
            conn.credential_enc, conn.credential_key_version = crypto.encrypt_secret(secret)
        secret = None  # noqa: F841
    if content:
        msgs = (db.query(EmailMessage)
                .filter(EmailMessage.content_enc.isnot(None),
                        EmailMessage.content_key_version != primary)
                .yield_per(500))
        for msg in msgs:
            try:
                body = crypto.decrypt_content(msg.content_enc)
            except crypto.CredentialUnavailable:
                print(f"  unreadable content: message {msg.id}")
                continue
            stats["content"] += 1
            if not dry_run:
                msg.content_enc, msg.content_key_version = crypto.encrypt_content(body)
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    parser.add_argument("--content", action="store_true", help="also re-encrypt stored message content")
    args = parser.parse_args(argv)
    from src.config.database_config_mysql import db_manager
    import src.models.models  # noqa: F401
    db = db_manager.get_db_session()
    try:
        stats = reencrypt(db, dry_run=args.dry_run, content=args.content)
    finally:
        db.close()
    verb = "would rewrite" if args.dry_run else "rewrote"
    print(f"{verb} {stats['rewritten']} credential(s) "
          f"({stats['plaintext']} were plain text); {stats['current']} already current; "
          f"{stats['unreadable']} unreadable (set to needs_attention)"
          + (f"; {verb} {stats['content']} message bodies" if args.content else ""))
    return 1 if stats["unreadable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
