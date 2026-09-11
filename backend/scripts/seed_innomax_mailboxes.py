"""Seed the Innomax sender mailboxes and pre-map pipeline steps to them.

Creates the named sender mailboxes (Admin → Email Settings → Sender Mailboxes)
for the platform company and points each hiring-pipeline step at the right one,
so HR doesn't have to wire them by hand.

Passwords are intentionally left BLANK — a mailbox only sends once an admin fills
its password in the UI (until then that step falls back to the company default
sender). Idempotent: existing mailboxes are updated in place and their stored
password is preserved; existing step rows keep their extra_emails/roles.

``Auth`` is created but deliberately NOT in STEP_MAP: OTP and password-reset mail
find it by NAME (AUTH_MAILBOX_NAME, src/services/email_service.py), not through a
pipeline step. Renaming it silently drops auth mail to the company default.

The Admin UI is the supported way to manage these. Re-running this script after
manual edits is destructive in three ways: it force-resets is_active and enabled,
it overwrites mailbox_id on every step in STEP_MAP, and because it matches on
(company_id, name) a mailbox RENAMED in the UI gets recreated empty and the steps
repointed at it. Treat this as first-run convenience, not the source of truth.

Usage (from backend/):
    python scripts/seed_innomax_mailboxes.py                # platform company
    python scripts/seed_innomax_mailboxes.py <company_slug> # a specific company
"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.database_config_mysql import db_manager
from src.models.models import Company, CompanyMailbox, PipelineStepConfig
from src.tenancy.platform_company import get_platform_company

SMTP_HOST = "mail.innomaxskills.com"
SMTP_PORT = 465  # implicit SSL

# name -> address. name is what HR sees in the "Send from" dropdown.
MAILBOXES = [
    ("Auth",        "auth@innomaxskills.com"),
    ("Assessments", "assessments@innomaxskills.com"),
    ("Scheduling",  "scheduling@innomaxskills.com"),
    ("Offers",      "offers@innomaxskills.com"),
]

# pipeline step_key -> mailbox name (must exist in MAILBOXES).
# Steps NOT listed here keep the company default sender, which is correct - there
# is no mailbox for them yet. Add one only once it exists in MAILBOXES.
STEP_MAP = {
    "test_invitation":     "Assessments",
    "mcq_result":          "Assessments",
    "interview_scheduled": "Scheduling",
    "panel_invite":        "Scheduling",
    "offer_letter":        "Offers",
}


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else None
    db = db_manager.get_db_session()
    try:
        if slug:
            company = db.query(Company).filter(Company.slug == slug).first()
        else:
            company = get_platform_company(db)
        if not company:
            print(f"ERROR: company not found ({'slug=' + slug if slug else 'platform company'}).")
            sys.exit(1)
        print(f"Company: {company.name} (id={company.id})")

        # 1. Upsert mailboxes -------------------------------------------------
        name_to_id = {}
        created, updated = 0, 0
        for name, addr in MAILBOXES:
            mb = (
                db.query(CompanyMailbox)
                .filter(CompanyMailbox.company_id == company.id, CompanyMailbox.name == name)
                .first()
            )
            if mb:
                mb.smtp_host = SMTP_HOST
                mb.smtp_port = SMTP_PORT
                mb.smtp_user = addr
                mb.smtp_from_email = addr
                mb.is_active = True
                # password preserved on purpose
                updated += 1
            else:
                mb = CompanyMailbox(
                    company_id=company.id, name=name,
                    smtp_host=SMTP_HOST, smtp_port=SMTP_PORT,
                    smtp_user=addr, smtp_from_email=addr,
                    smtp_pass=None, is_active=True,
                )
                db.add(mb)
                created += 1
            db.flush()
            name_to_id[name] = mb.id

        # 2. Map steps -> mailbox --------------------------------------------
        mapped = 0
        for step_key, mb_name in STEP_MAP.items():
            row = (
                db.query(PipelineStepConfig)
                .filter(PipelineStepConfig.company_id == company.id,
                        PipelineStepConfig.step_key == step_key)
                .first()
            )
            if not row:
                row = PipelineStepConfig(
                    company_id=company.id, step_key=step_key,
                    enabled=True, extra_emails=json.dumps([]),
                    notify_roles=json.dumps([]), as_cc=True,
                )
                db.add(row)
            row.mailbox_id = name_to_id[mb_name]
            row.enabled = True
            mapped += 1

        db.commit()
        print(f"Mailboxes: {created} created, {updated} updated (total {len(MAILBOXES)}).")
        print(f"Pipeline steps mapped: {mapped}.")
        no_pw = [n for n, _ in MAILBOXES]
        print("\nNEXT STEP: set each mailbox's password in Admin -> Email Settings -> "
              "Sender Mailboxes, then press Test on each row to prove it works. Until a "
              "mailbox has a password it is inert and its step falls back to the "
              "company default sender.")
        print("Mailboxes needing a password:", ", ".join(no_pw))
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
