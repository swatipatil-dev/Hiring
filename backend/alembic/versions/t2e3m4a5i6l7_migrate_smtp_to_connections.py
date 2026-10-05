"""Copy legacy SMTP settings into email connections, senders and routes

Data only. Reads the pre-API tables and writes the email API's, leaving the old
rows untouched so the previous release keeps working if this is rolled back:

  tenant_configs (smtp_*)        → connection "Company SMTP" + sender "Company default"
                                   + the company's __default__ route
  company_mailboxes              → one connection + one sender each. The sender
                                   KEEPS THE MAILBOX'S ID, so every mailbox_id the
                                   frontend and pipeline_step_configs hold is a
                                   valid sender_id.
  pipeline_step_configs          → email_routes (enabled flag, chosen mailbox)
  mailbox named AUTH_MAILBOX_NAME → the auth.verify_email / auth.password_reset
                                   routes, replacing the old match-by-name

Passwords are copied as stored, marked key version ``legacy``. A revision must
not import the application's keyring, so re-encryption under the new keys is a
separate step: scripts/reencrypt_email_credentials.py (docs/DEPLOY_UPDATE.md).

Connections are ``active`` / ``legacy_unverified`` unless they plainly cannot
work under the new rules — no password, or a port other than 465/587 — in which
case they are ``needs_attention`` with a reason, and mail routed to them blocks
visibly instead of silently going out from the platform's account.

Idempotent: every row it writes carries ``legacy_source`` (routes carry a
marker in ``updated_by_user_id``), and existing rows are never duplicated or
overwritten. Downgrade removes exactly those rows, except any a message still
references.

Revision ID: t2e3m4a5i6l7
Revises: t1e2m3a4i5l6
Create Date: 2026-10-05 00:00:00.000000
"""
import hashlib
import os
import uuid
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 't2e3m4a5i6l7'
down_revision: Union[str, None] = 't1e2m3a4i5l6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARKER = "alembic:t2e3m4a5i6l7"
ALLOWED_PORTS = {465: "implicit_tls", 587: "starttls_required"}
AUTH_EVENTS = ("auth.verify_email", "auth.password_reset")


def _account_key(host, user):
    raw = f"{(host or '').strip().lower()}|{(user or '').strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _status(user, password, port):
    """(status, error_code, security) for a legacy configuration."""
    security = ALLOWED_PORTS.get(port)
    if not user:
        return "needs_attention", "no_username", security
    if not password:
        return "needs_attention", "no_password", security
    if security is None:
        return "needs_attention", "port_not_allowed", None
    return "active", None, security


def _usable(mb) -> bool:
    """What the legacy resolver accepted: active, with a login and a password."""
    active = mb.is_active is None or bool(mb.is_active)
    return bool(active and mb.smtp_user and mb.smtp_pass
                and ALLOWED_PORTS.get(int(mb.smtp_port or 587)))


def _exists(bind, table, legacy_source):
    return bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE legacy_source = :s"),
                        {"s": legacy_source}).first() is not None


def _free_label(bind, company_id, wanted):
    label, n = wanted, 2
    while bind.execute(sa.text("SELECT 1 FROM email_senders WHERE company_id = :c AND label = :l"),
                       {"c": company_id, "l": label}).first():
        label, n = f"{wanted} ({n})", n + 1
    return label


def _insert_pair(bind, *, company_id, source, name, label, host, port, user, password,
                 from_email, enabled=True, hourly_limit=None, sender_id=None):
    now = datetime.utcnow()
    host = (host or "smtp.gmail.com").strip()   # the legacy sender's own default
    port = int(port or 587)
    status, error, security = _status(user, password, port)
    conn_id = str(uuid.uuid4())
    bind.execute(sa.text(
        "INSERT INTO email_connections (id, company_id, type, provider, name, status, error_code, "
        "host, port, security, username, credential_enc, credential_key_version, account_key, "
        "hourly_limit, is_shared, verification_state, legacy_source, created_at, updated_at) "
        "VALUES (:id, :company_id, 'smtp', 'smtp', :name, :status, :error, :host, :port, :security, "
        ":user, :cred, :cred_v, :akey, :limit, 0, 'legacy_unverified', :src, :now, :now)"),
        {"id": conn_id, "company_id": company_id, "name": name[:100], "status": status,
         "error": error, "host": host, "port": port, "security": security, "user": user,
         "cred": password or None, "cred_v": "legacy" if password else None,
         "akey": _account_key(host, user), "limit": hourly_limit, "src": source, "now": now})
    sid = sender_id or str(uuid.uuid4())
    bind.execute(sa.text(
        "INSERT INTO email_senders (id, company_id, connection_id, label, email, "
        "verification_status, enabled, hourly_limit, legacy_source, created_at, updated_at) "
        "VALUES (:id, :company_id, :conn, :label, :email, 'legacy_unverified', :enabled, :limit, "
        ":src, :now, :now)"),
        {"id": sid, "company_id": company_id, "conn": conn_id,
         "label": _free_label(bind, company_id, label[:100]),
         "email": (from_email or user or "unknown@invalid").strip().lower(),
         "enabled": bool(enabled), "limit": hourly_limit, "src": source, "now": now})
    return sid


def _route(bind, company_id, event_type, sender_id, enabled=True):
    exists = bind.execute(sa.text(
        "SELECT 1 FROM email_routes WHERE company_id = :c AND event_type = :e"),
        {"c": company_id, "e": event_type}).first()
    if exists:
        return
    now = datetime.utcnow()
    bind.execute(sa.text(
        "INSERT INTO email_routes (id, company_id, event_type, sender_id, enabled, policy_version, "
        "updated_by_user_id, created_at, updated_at) "
        "VALUES (:id, :c, :e, :s, :en, 1, :m, :now, :now)"),
        {"id": str(uuid.uuid4()), "c": company_id, "e": event_type, "s": sender_id,
         "en": bool(enabled), "m": MARKER, "now": now})


def upgrade() -> None:
    bind = op.get_bind()

    # Mailboxes first: they keep their ids, and their names take precedence over
    # the "Company default" label if the two ever collide.
    mailboxes = bind.execute(sa.text(
        "SELECT id, company_id, name, smtp_host, smtp_port, smtp_user, smtp_pass, "
        "smtp_from_email, hourly_limit, is_active FROM company_mailboxes")).fetchall()
    for mb in mailboxes:
        source = f"company_mailboxes:{mb.id}"
        if _exists(bind, "email_connections", source):
            continue
        _insert_pair(bind, company_id=mb.company_id, source=source, name=mb.name, label=mb.name,
                     host=mb.smtp_host, port=mb.smtp_port, user=mb.smtp_user,
                     password=mb.smtp_pass, from_email=mb.smtp_from_email,
                     enabled=mb.is_active is None or bool(mb.is_active),
                     hourly_limit=mb.hourly_limit, sender_id=mb.id)

    configs = bind.execute(sa.text(
        "SELECT id, company_id, smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from_email "
        "FROM tenant_configs WHERE smtp_user IS NOT NULL AND smtp_user <> ''")).fetchall()
    for tc in configs:
        source = f"tenant_configs:{tc.id}"
        if _exists(bind, "email_connections", source):
            continue
        sid = _insert_pair(bind, company_id=tc.company_id, source=source, name="Company SMTP",
                           label="Company default", host=tc.smtp_host, port=tc.smtp_port,
                           user=tc.smtp_user, password=tc.smtp_pass, from_email=tc.smtp_from_email)
        _route(bind, tc.company_id, "__default__", sid)

    # Routes reproduce what the old resolver actually did. It silently skipped a
    # mailbox that was inactive or had no credentials and used the company
    # default instead, so a route only names a mailbox that could send. Pointing
    # one at a mailbox that never worked would turn today's working mail into
    # blocked mail on the day of the upgrade. From here on the strict rule
    # applies: a sender an admin selects and that later breaks, blocks.
    usable = {mb.id: mb.company_id for mb in mailboxes if _usable(mb)}
    steps = bind.execute(sa.text(
        "SELECT company_id, step_key, enabled, mailbox_id FROM pipeline_step_configs")).fetchall()
    for st in steps:
        enabled = st.enabled is None or bool(st.enabled)
        sender = st.mailbox_id if usable.get(st.mailbox_id) == st.company_id else None
        # A row that is on and names no usable mailbox changes nothing; leave it out.
        if enabled and sender is None:
            continue
        _route(bind, st.company_id, st.step_key, sender, enabled)

    auth_name = os.getenv("AUTH_MAILBOX_NAME", "Auth")
    for mb in mailboxes:
        if mb.name == auth_name and usable.get(mb.id) == mb.company_id:
            for event in AUTH_EVENTS:
                _route(bind, mb.company_id, event, mb.id)


def downgrade() -> None:
    bind = op.get_bind()
    legacy = "(legacy_source LIKE 'company_mailboxes:%' OR legacy_source LIKE 'tenant_configs:%')"
    bind.execute(sa.text("DELETE FROM email_routes WHERE updated_by_user_id = :m"), {"m": MARKER})
    # Rows a message still points at stay: the message's history is worth more
    # than a clean downgrade, and the old code ignores these tables anyway.
    bind.execute(sa.text(
        f"UPDATE email_routes SET sender_id = NULL WHERE sender_id IN "
        f"(SELECT id FROM (SELECT id FROM email_senders WHERE {legacy}) s)"))
    bind.execute(sa.text(
        f"DELETE FROM email_senders WHERE {legacy} AND id NOT IN "
        f"(SELECT sender_id FROM email_messages WHERE sender_id IS NOT NULL)"))
    bind.execute(sa.text(
        f"DELETE FROM email_connections WHERE {legacy} "
        f"AND id NOT IN (SELECT connection_id FROM email_senders) "
        f"AND id NOT IN (SELECT connection_id FROM email_messages WHERE connection_id IS NOT NULL)"))
