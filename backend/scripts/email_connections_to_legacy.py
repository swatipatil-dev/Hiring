#!/usr/bin/env python
"""
Copy email API senders back into the legacy SMTP tables — for a code rollback only.

After the email API release, mailbox and Email Settings changes are written to
email_connections / email_senders / email_routes, and tenant_configs,
company_mailboxes and pipeline_step_configs.mailbox_id/enabled are no longer
touched. If that release is rolled back, the previous code reads the legacy
tables and would not see changes made since. Run this BEFORE downgrading the
code (and before `alembic downgrade`), while the new tables still exist:

    python scripts/email_connections_to_legacy.py --dry-run
    python scripts/email_connections_to_legacy.py

What it writes, per company:
  * the default sender  → tenant_configs smtp_* (password re-encrypted with the
                          legacy key, src/security.py)
  * every other sender  → company_mailboxes, keeping the sender's id (which is the
                          mailbox id it was migrated from, where there was one)
  * pipeline routes     → pipeline_step_configs.enabled / mailbox_id

Platform senders seeded from the environment are skipped; the old code reads the
environment directly. A sender whose password cannot be decrypted is copied
without one. Prints no secret.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def copy_back(db, *, dry_run: bool = False) -> dict:
    from src.email_api import constants as C, crypto
    from src.models.models import (CompanyMailbox, EmailConnection, EmailRoute, EmailSender,
                                   PipelineStepConfig, TenantConfig)
    from src.security import encrypt as legacy_encrypt
    from src.services.pipeline_notifications import STEP_KEYS

    stats = {"tenant_configs": 0, "mailboxes": 0, "steps": 0, "no_password": 0}
    conns = {c.id: c for c in db.query(EmailConnection).all()}

    def legacy_password(conn):
        if conn is None or not conn.credential_enc:
            return None
        try:
            return legacy_encrypt(crypto.decrypt_secret(conn.credential_enc,
                                                        conn.credential_key_version))
        except crypto.CredentialUnavailable:
            stats["no_password"] += 1
            return None

    defaults = {r.company_id: r.sender_id for r in
                db.query(EmailRoute).filter(EmailRoute.event_type == C.DEFAULT_ROUTE)}
    for sender in db.query(EmailSender).filter(EmailSender.deleted_at.is_(None)).all():
        if (sender.legacy_source or "").startswith("env:"):
            continue
        conn = conns.get(sender.connection_id)
        if defaults.get(sender.company_id) == sender.id:
            tc = db.query(TenantConfig).filter(TenantConfig.company_id == sender.company_id).first()
            if tc is None:
                tc = TenantConfig(company_id=sender.company_id)
                db.add(tc)
            tc.smtp_host, tc.smtp_port = conn.host if conn else None, conn.port if conn else None
            tc.smtp_user, tc.smtp_from_email = conn.username if conn else None, sender.email
            tc.smtp_pass = legacy_password(conn)
            tc.updated_at = datetime.utcnow()
            stats["tenant_configs"] += 1
            continue
        mb = db.query(CompanyMailbox).filter(CompanyMailbox.id == sender.id).first()
        if mb is None:
            mb = CompanyMailbox(id=sender.id, company_id=sender.company_id)
            db.add(mb)
        mb.name = sender.label[:100]
        mb.smtp_host, mb.smtp_port = conn.host if conn else None, conn.port if conn else None
        mb.smtp_user, mb.smtp_from_email = conn.username if conn else None, sender.email
        mb.smtp_pass = legacy_password(conn)
        mb.hourly_limit = sender.hourly_limit or (conn.hourly_limit if conn else None)
        mb.is_active = bool(sender.enabled)
        mb.updated_at = datetime.utcnow()
        stats["mailboxes"] += 1
    db.flush()

    mailbox_ids = {m.id for m in db.query(CompanyMailbox.id)}
    for route in db.query(EmailRoute).filter(EmailRoute.event_type.in_(STEP_KEYS)).all():
        row = (db.query(PipelineStepConfig)
               .filter(PipelineStepConfig.company_id == route.company_id,
                       PipelineStepConfig.step_key == route.event_type)
               .first())
        if row is None:
            row = PipelineStepConfig(company_id=route.company_id, step_key=route.event_type)
            db.add(row)
        row.enabled = bool(route.enabled)
        row.mailbox_id = route.sender_id if route.sender_id in mailbox_ids else None
        row.updated_at = datetime.utcnow()
        stats["steps"] += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Copy email API senders back to legacy tables")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    from src.config.database_config_mysql import db_manager
    import src.models.models  # noqa: F401
    db = db_manager.get_db_session()
    try:
        stats = copy_back(db, dry_run=args.dry_run)
    finally:
        db.close()
    verb = "would copy" if args.dry_run else "copied"
    print(f"{verb} {stats['tenant_configs']} company default(s), {stats['mailboxes']} mailbox(es), "
          f"{stats['steps']} step setting(s); {stats['no_password']} without a readable password")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
