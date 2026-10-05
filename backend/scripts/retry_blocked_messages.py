#!/usr/bin/env python
"""
Retry email messages that were blocked for a given reason, after fixing the cause.

Typical use: mail accepted before the settings migration ran is blocked with
``email_migration_pending``; once ``alembic upgrade head`` has run:

    python scripts/retry_blocked_messages.py --reason email_migration_pending --dry-run
    python scripts/retry_blocked_messages.py --reason email_migration_pending

Each message is resolved again under today's routing (src/email_api/messages.retry);
one that is still blocked is reported and left as it is. --since limits it to
recent mail (default 7 days) so a stale offer letter is not sent weeks late.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Retry blocked email messages")
    parser.add_argument("--reason", required=True, help="status_reason to retry, e.g. email_migration_pending")
    parser.add_argument("--company", help="only this company id")
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    from src.config.database_config_mysql import db_manager
    import src.models.models  # noqa: F401
    from src.email_api import constants as C, messages as M
    from src.models.models import EmailMessage
    db = db_manager.get_db_session()
    retried = still = 0
    try:
        q = (db.query(EmailMessage)
             .filter(EmailMessage.status == C.BLOCKED, EmailMessage.status_reason == args.reason,
                     EmailMessage.created_at >= datetime.utcnow() - timedelta(days=args.since_days)))
        if args.company:
            q = q.filter(EmailMessage.company_id == args.company)
        for msg in q.all():
            sp = db.begin_nested()
            try:
                M.retry(db, msg)
                retried += 1
                sp.commit() if not args.dry_run else sp.rollback()
            except M.SubmitError as e:
                sp.rollback()
                still += 1
                print(f"  still blocked: {msg.id} ({msg.event_type}, company {msg.company_id}): {e.code}")
        if args.dry_run:
            db.rollback()
        else:
            db.commit()   # publishes the retried messages (outbox)
    finally:
        db.close()
    print(f"{'would retry' if args.dry_run else 'retried'} {retried}; {still} still blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
