"""Turn the platform's environment mailboxes into explicit, visible database records.

The legacy sender read ``SMTP_*`` and ``INNOMAX_*`` from the environment at send
time, and fell back to them whenever a company had nothing configured. The email
API never reads them at send time. Instead this seeds them, once, as the
platform company's own connections and senders, with routes that reproduce what
the platform company's mail did before:

  SMTP_*            → "Platform System":  application_received (that call site
                      used the "system" category), workflow reminder digests,
                      support mail, and verification/reset codes when no
                      platform "Auth" mailbox was migrated.
  INNOMAX_HR_*      → "Innomax HR":       the platform company's default sender.
  INNOMAX_OFFICIAL_*→ "Innomax Official": the welcome email to new client companies.

The platform's auth routes are what the sender policy uses — explicitly, and
only for verification/reset mail — for client companies that have no sender of
their own. Everything is shown in Admin → Mail Routing.

Idempotent on ``legacy_source``; an existing row is never changed unless
``update=True`` (``scripts/seed_platform_email.py --update``). Runs at API
startup and is safe to run from several workers at once.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Optional

from sqlalchemy.exc import IntegrityError

from . import constants as C
from . import crypto, ssrf
from .connections import account_key

logger = logging.getLogger(__name__)

SYSTEM_EVENTS = ("application_received", "workflow.reminder_digest", "support.ticket_to_platform",
                 "support.reply_to_platform", "support.reply_to_admin")
SEED_MARKER = "seed:platform-env"


def _specs():
    smtp_user = os.getenv("SMTP_USER", "")
    innomax_host = os.getenv("INNOMAX_SMTP_HOST", "mail.innomaxsol.com")
    innomax_port = os.getenv("INNOMAX_SMTP_PORT", "465")
    return [
        {"source": "env:SMTP", "name": "Platform System",
         "host": os.getenv("SMTP_HOST", "smtp.gmail.com"), "port": os.getenv("SMTP_PORT", "587"),
         "user": smtp_user, "password": os.getenv("SMTP_PASS", ""),
         "from": os.getenv("FROM_EMAIL", "") or smtp_user,
         "routes": SYSTEM_EVENTS, "auth_routes": True, "default_route": False},
        {"source": "env:INNOMAX_HR", "name": "Innomax HR", "host": innomax_host,
         "port": innomax_port, "user": os.getenv("INNOMAX_HR_USER", ""),
         "password": os.getenv("INNOMAX_HR_PASS", ""), "from": os.getenv("INNOMAX_HR_USER", ""),
         "routes": (), "auth_routes": False, "default_route": True},
        {"source": "env:INNOMAX_OFFICIAL", "name": "Innomax Official", "host": innomax_host,
         "port": innomax_port, "user": os.getenv("INNOMAX_OFFICIAL_USER", ""),
         "password": os.getenv("INNOMAX_OFFICIAL_PASS", ""),
         "from": os.getenv("INNOMAX_OFFICIAL_USER", ""),
         "routes": ("platform.tenant_welcome",), "auth_routes": False, "default_route": False},
    ]


def _route(db, company_id, event_type, sender_id, *, replace=False, actions=None, dry_run=False):
    from src.models.models import EmailRoute
    row = (db.query(EmailRoute)
           .filter(EmailRoute.company_id == company_id, EmailRoute.event_type == event_type)
           .first())
    if row is not None and not replace:
        return
    actions.append(f"route {event_type} -> {sender_id}")
    if dry_run:
        return
    if row is None:
        db.add(EmailRoute(company_id=company_id, event_type=event_type, sender_id=sender_id,
                          enabled=True, updated_by_user_id=SEED_MARKER))
    else:
        row.sender_id = sender_id
        row.policy_version = (row.policy_version or 1) + 1
        row.updated_by_user_id = SEED_MARKER
    db.flush()


def seed_platform_email_from_env(db, *, update: bool = False, dry_run: bool = False) -> List[str]:
    """Create (or with ``update``, refresh) the platform's env-defined mailboxes.

    Returns a list of human-readable actions. The caller commits.
    """
    from src.models.models import EmailConnection, EmailSender, EmailRoute
    from src.tenancy import tenant_bypass
    from src.tenancy.platform_company import get_platform_company_id
    actions: List[str] = []
    with tenant_bypass():
        platform_id = get_platform_company_id(db)
        if not platform_id:
            return ["no platform company yet; nothing seeded"]
        if not crypto.is_configured():
            logger.critical("EMAIL_CREDENTIAL_KEYS is not set: platform mailboxes from the "
                            "environment cannot be stored, so platform mail will be blocked.")
            return ["EMAIL_CREDENTIAL_KEYS missing; nothing seeded"]
        for spec in _specs():
            if not spec["user"] or not spec["password"]:
                continue
            conn = (db.query(EmailConnection)
                    .filter(EmailConnection.legacy_source == spec["source"]).first())
            created = conn is None
            if not created and not update:
                continue
            try:
                port = int(spec["port"])
            except ValueError:
                port = 0
            security = ssrf.allowed_ports().get(port)
            status = C.CONN_ACTIVE if security else C.CONN_NEEDS_ATTENTION
            actions.append(f"{'create' if created else 'update'} {spec['name']} "
                           f"({spec['user']} via {spec['host']}:{port}, {status})")
            if dry_run:
                continue
            if created:
                conn = EmailConnection(company_id=platform_id, type=C.CONN_SMTP, provider="smtp",
                                       name=spec["name"], legacy_source=spec["source"],
                                       is_shared=True, created_at=datetime.utcnow())
                db.add(conn)
            conn.host, conn.port, conn.security = spec["host"].strip().lower(), port, security
            conn.username = spec["user"]
            conn.account_key = account_key(conn.host, conn.username)
            conn.credential_enc, conn.credential_key_version = crypto.encrypt_secret(spec["password"])
            conn.status = status
            conn.error_code = None if security else "port_not_allowed"
            conn.verification_state = "legacy_unverified"
            db.flush()
            sender = (db.query(EmailSender)
                      .filter(EmailSender.legacy_source == spec["source"]).first())
            if sender is None:
                label, n = spec["name"], 2
                while (db.query(EmailSender.id)
                       .filter(EmailSender.company_id == platform_id, EmailSender.label == label)
                       .first()):
                    label, n = f"{spec['name']} ({n})", n + 1
                sender = EmailSender(company_id=platform_id, connection_id=conn.id, label=label,
                                     email=spec["from"].strip().lower(),
                                     verification_status=C.SENDER_LEGACY,
                                     legacy_source=spec["source"], enabled=True)
                db.add(sender)
            else:
                sender.connection_id = conn.id
                sender.email = spec["from"].strip().lower()
            db.flush()

            for event in spec["routes"]:
                _route(db, platform_id, event, sender.id, actions=actions)
            if spec["auth_routes"]:
                for event in (C.EVENT_AUTH_VERIFY, C.EVENT_AUTH_RESET):
                    _route(db, platform_id, event, sender.id, actions=actions)
            if spec["default_route"]:
                # Today the Innomax HR mailbox wins over the platform's own
                # tenant_configs row, so on first seeding it replaces a default
                # the data migration copied from there.
                current = (db.query(EmailRoute)
                           .filter(EmailRoute.company_id == platform_id,
                                   EmailRoute.event_type == C.DEFAULT_ROUTE).first())
                replace = created and current is not None and _from_tenant_config(db, current.sender_id)
                _route(db, platform_id, C.DEFAULT_ROUTE, sender.id, replace=replace, actions=actions)
    return actions


def _from_tenant_config(db, sender_id: Optional[str]) -> bool:
    from src.models.models import EmailSender
    if not sender_id:
        return True
    s = db.query(EmailSender).filter(EmailSender.id == sender_id).first()
    return bool(s and (s.legacy_source or "").startswith("tenant_configs:"))


def seed_at_startup() -> None:
    """Startup hook: seed, then commit. Never stops the API from starting."""
    from src.config.database_config_mysql import db_manager
    db = db_manager.get_db_session()
    try:
        actions = seed_platform_email_from_env(db)
        db.commit()
        for a in actions:
            logger.info("[EMAIL] platform seed: %s", a)
        check_email_migration_applied(db)
    except IntegrityError:
        # Another worker seeded the same rows first.
        db.rollback()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.error("[EMAIL] platform mailbox seeding failed: %s", e)
    finally:
        db.close()


def check_email_migration_applied(db) -> bool:
    """Log CRITICAL if legacy SMTP settings exist but were never migrated.

    Until revision t2e3m4a5i6l7 runs, every company with legacy settings has its
    mail blocked (reason ``email_migration_pending``) rather than sent.
    """
    from sqlalchemy import text
    try:
        legacy = db.execute(text(
            "SELECT (SELECT COUNT(*) FROM tenant_configs WHERE smtp_user IS NOT NULL AND smtp_user <> '') "
            "+ (SELECT COUNT(*) FROM company_mailboxes)")).scalar() or 0
        migrated = db.execute(text(
            "SELECT COUNT(*) FROM email_connections WHERE legacy_source LIKE 'tenant_configs:%' "
            "OR legacy_source LIKE 'company_mailboxes:%'")).scalar() or 0
    except Exception as e:  # noqa: BLE001
        logger.error("[EMAIL] could not check the email settings migration: %s", e)
        return False
    if legacy and not migrated:
        logger.critical("[EMAIL] %d legacy SMTP setting(s) have not been migrated to email "
                        "connections; that mail is blocked until `alembic upgrade head` runs "
                        "(revision t2e3m4a5i6l7).", legacy)
        return False
    return True
