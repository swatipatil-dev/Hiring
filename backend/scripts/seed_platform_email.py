#!/usr/bin/env python
"""
Create the platform company's email connections from SMTP_* / INNOMAX_* env.

The API does this at startup (src/email_api/bootstrap.py); run it by hand to see
what it would do first, or to push changed env credentials into existing rows:

    python scripts/seed_platform_email.py --dry-run
    python scripts/seed_platform_email.py
    python scripts/seed_platform_email.py --update     # overwrite seeded rows from env

Needs EMAIL_CREDENTIAL_KEYS. Prints no password.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed platform email connections from env")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update", action="store_true",
                        help="refresh rows seeded earlier with the current env values")
    args = parser.parse_args(argv)
    from src.config.database_config_mysql import db_manager
    import src.models.models  # noqa: F401
    from src.email_api.bootstrap import seed_platform_email_from_env
    db = db_manager.get_db_session()
    try:
        actions = seed_platform_email_from_env(db, update=args.update, dry_run=args.dry_run)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()
    for a in actions or ["nothing to do"]:
        print(("would " if args.dry_run else "") + a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
