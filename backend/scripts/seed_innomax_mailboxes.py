"""Seed the Innomax sender mailboxes and pre-map pipeline steps to them.

Creates the 10 named sender mailboxes (Admin → Email Settings → Sender Mailboxes)
for the platform (Innomax) company and points each hiring-pipeline step at the
right one, so HR doesn't have to wire them by hand.

Passwords are intentionally left BLANK — a mailbox only sends once an admin fills
its App Password in the UI (until then that step falls back to the company
default sender). Idempotent: existing mailboxes are updated in place and their
stored password is preserved; existing step rows keep their extra_emails/roles.

Scope: only the candidate/HR pipeline-step senders. auth@ (OTPs), support@
tickets, admin@ and no-reply@ are still created as selectable mailboxes but are
NOT auto-wired — those flows keep using their existing routing.

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

SMTP_HOST = "mail.innomaxsol.com"
SMTP_PORT = 465  # implicit SSL

# name -> address. name is what HR sees in the "Send from" dropdown.
MAILBOXES = [
    ("Careers",       "careers@innomaxsol.com"),
    ("Support",       "support@innomaxsol.com"),
    ("Auth",          "auth@innomaxsol.com"),
    ("Scheduling",    "scheduling@innomaxsol.com"),
    ("Assessments",   "assessments@innomaxsol.com"),
    ("Notifications", "notifications@innomaxsol.com"),
    ("Offers",        "offers@innomaxsol.com"),
    ("HR",            "hr@innomaxsol.com"),
    ("Admin",         "admin@innomaxsol.com"),
    ("No-Reply",      "no-reply@innomaxsol.com"),
]

# pipeline step_key -> mailbox name (must exist in MAILBOXES).
STEP_MAP = {
    "application_received": "Careers",
    "shortlisted":         "Notifications",
    "test_invitation":     "Assessments",
    "mcq_result":          "Assessments",
    "interview_scheduled": "Scheduling",
    "panel_invite":        "Scheduling",
    "offer_letter":        "Offers",
    "offer_accepted":      "HR",
    "offer_rejected":      "HR",
    "candidate_rejected":  "Notifications",
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
        print("\nNEXT STEP: set each mailbox's App Password in Admin -> Email Settings -> "
              "Sender Mailboxes. Until then, that step falls back to the company default sender.")
        print("Mailboxes needing a password:", ", ".join(no_pw))
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
