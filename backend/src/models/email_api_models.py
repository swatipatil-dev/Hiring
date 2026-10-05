"""
EMAIL API MODELS — connections, senders, routes, messages and their delivery trail.

The multi-tenant email API (docs/EMAIL_SYSTEM.md) separates three things the
legacy tables blurred together:

  * a **connection** is *how* mail leaves (an SMTP account today; OAuth mailboxes
    and verified domains later — ``type`` already allows them),
  * a **sender** is an authorised From address on a connection,
  * a **route** says which sender an event uses, or that the event is off.

A **message** freezes the sender, connection and rendered content chosen when it
was accepted, so later routing edits never change mail already queued.

Same-company ownership is enforced by the database, not only by the services:
each parent table carries ``UNIQUE(company_id, id)`` and every child references
``(company_id, <parent>_id)``. A plain FK to ``email_senders.id`` would accept
another company's sender; the composite one cannot. ``email_messages`` is the one
table whose sender may legitimately belong to a *different* company (the explicit
platform auth route), so it references the sender through ``sender_company_id``
and the service layer checks when that may differ from ``company_id``.

Enum-like columns are plain strings validated in ``src/email_api/constants.py``,
like the rest of the schema. Shares ``Base`` with ``models.py`` (imported at the
bottom of that module) so ``create_all`` and Alembic both see these tables.
"""
from datetime import datetime

from sqlalchemy import (Column, Integer, String, DateTime, Boolean, Text, JSON,
                        ForeignKey, ForeignKeyConstraint, UniqueConstraint, Index)
from sqlalchemy.dialects import mysql as mysql_types

from .models import Base, _uuid
from ..tenancy import TenantScoped


def _company_fk():
    return Column(String(36), ForeignKey("companies.id"), nullable=False)


class EmailConnection(Base, TenantScoped):
    """How mail leaves for a company: one upstream account and its credential."""
    __tablename__ = "email_connections"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_email_conn_company_id"),
        UniqueConstraint("legacy_source", name="uq_email_conn_legacy_source"),
        Index("ix_email_conn_company_status", "company_id", "status"),
    )

    id                     = Column(String(36), primary_key=True, default=_uuid)
    company_id             = _company_fk()
    # smtp | google_oauth | microsoft_oauth | verified_domain
    type                   = Column(String(30), nullable=False, default="smtp")
    provider               = Column(String(50), nullable=False, default="smtp")
    name                   = Column(String(100), nullable=False)
    # pending_verification | active | failed | needs_attention | disconnected
    status                 = Column(String(30), nullable=False, default="pending_verification")
    error_code             = Column(String(64), nullable=True)
    error_detail           = Column(String(500), nullable=True)
    host                   = Column(String(255), nullable=True)
    port                   = Column(Integer, nullable=True)
    security               = Column(String(30), nullable=True)   # implicit_tls | starttls_required
    username               = Column(String(255), nullable=True)
    # Ciphertext only; see src/email_api/crypto.py. NULL once disconnected.
    credential_enc         = Column(Text, nullable=True)
    credential_key_version = Column(String(20), nullable=True)   # k1, k2 ... | legacy
    config                 = Column(JSON, nullable=True)
    # Rate-limit bucket: two connections to the same upstream account share it.
    account_key            = Column(String(64), nullable=False)
    hourly_limit           = Column(Integer, nullable=True)
    # A platform connection used on behalf of several companies.
    is_shared              = Column(Boolean, nullable=False, default=False)
    verification_state     = Column(String(30), nullable=True)   # verified | legacy_unverified
    verified_at            = Column(DateTime, nullable=True)
    last_tested_at         = Column(DateTime, nullable=True)
    last_success_at        = Column(DateTime, nullable=True)
    last_error_at          = Column(DateTime, nullable=True)
    disconnected_at        = Column(DateTime, nullable=True)
    # Where a migrated/seeded row came from, e.g. "company_mailboxes:<id>" or
    # "env:SMTP". Makes the data migration and the platform seed idempotent.
    legacy_source          = Column(String(100), nullable=True)
    created_by_user_id     = Column(String(36), nullable=True)
    created_at             = Column(DateTime, default=datetime.utcnow)
    updated_at             = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailSender(Base, TenantScoped):
    """An authorised From identity on a connection.

    Senders migrated from ``company_mailboxes`` keep the mailbox's id, so every
    ``mailbox_id`` the frontend already holds is a valid ``sender_id``.
    """
    __tablename__ = "email_senders"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_email_sender_company_id"),
        UniqueConstraint("company_id", "label", name="uq_email_sender_company_label"),
        UniqueConstraint("legacy_source", name="uq_email_sender_legacy_source"),
        ForeignKeyConstraint(["company_id", "connection_id"],
                             ["email_connections.company_id", "email_connections.id"],
                             name="fk_email_sender_connection"),
    )

    id                  = Column(String(36), primary_key=True, default=_uuid)
    company_id          = _company_fk()
    connection_id       = Column(String(36), nullable=False)
    label               = Column(String(100), nullable=False)
    email               = Column(String(255), nullable=False)
    display_name        = Column(String(255), nullable=True)
    reply_to            = Column(String(255), nullable=True)
    # primary | approved_alias | legacy_unverified
    verification_status = Column(String(30), nullable=False, default="primary")
    enabled             = Column(Boolean, nullable=False, default=True)
    hourly_limit        = Column(Integer, nullable=True)
    deleted_at          = Column(DateTime, nullable=True)
    legacy_source       = Column(String(100), nullable=True)
    created_by_user_id  = Column(String(36), nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailRoute(Base, TenantScoped):
    """Which sender an event uses for a company, and whether the event is on.

    ``event_type='__default__'`` is the company default sender. A missing route
    means "use the default"; a route with ``enabled=False`` means "send nothing".
    """
    __tablename__ = "email_routes"
    __table_args__ = (
        UniqueConstraint("company_id", "event_type", name="uq_email_route_company_event"),
        ForeignKeyConstraint(["company_id", "sender_id"],
                             ["email_senders.company_id", "email_senders.id"],
                             name="fk_email_route_sender"),
    )

    id                 = Column(String(36), primary_key=True, default=_uuid)
    company_id         = _company_fk()
    event_type         = Column(String(64), nullable=False)
    sender_id          = Column(String(36), nullable=True)
    enabled            = Column(Boolean, nullable=False, default=True)
    policy_version     = Column(Integer, nullable=False, default=1)
    updated_by_user_id = Column(String(36), nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailMessage(Base, TenantScoped):
    """One accepted email: its tenant, frozen identity, content and current state."""
    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_email_msg_company_id"),
        UniqueConstraint("company_id", "is_test", "idempotency_key", name="uq_email_msg_idempotency"),
        Index("ix_email_msg_company_created", "company_id", "created_at"),
        Index("ix_email_msg_company_status", "company_id", "status"),
        Index("ix_email_msg_company_extref", "company_id", "external_reference"),
        Index("ix_email_msg_connection_status", "connection_id", "status"),
        ForeignKeyConstraint(["sender_company_id", "sender_id"],
                             ["email_senders.company_id", "email_senders.id"],
                             name="fk_email_msg_sender"),
        ForeignKeyConstraint(["sender_company_id", "connection_id"],
                             ["email_connections.company_id", "email_connections.id"],
                             name="fk_email_msg_connection"),
    )

    id                       = Column(String(36), primary_key=True, default=_uuid)
    company_id               = _company_fk()
    is_test                  = Column(Boolean, nullable=False, default=False)
    event_type               = Column(String(64), nullable=False)
    priority                 = Column(String(10), nullable=False, default="normal")   # auth | normal | bulk
    # queued | deferred | sending | accepted | delivered | bounced | complained |
    # failed | blocked | unknown | cancelled
    status                   = Column(String(20), nullable=False, default="queued")
    status_reason            = Column(String(64), nullable=True)
    status_detail            = Column(String(500), nullable=True)
    sender_id                = Column(String(36), nullable=True)
    sender_company_id        = Column(String(36), nullable=True)
    connection_id            = Column(String(36), nullable=True)
    # requested | route | tenant_default | platform_auth | platform_route
    route_source             = Column(String(30), nullable=True)
    policy_version           = Column(Integer, nullable=True)
    from_email               = Column(String(255), nullable=True)
    from_name                = Column(String(255), nullable=True)
    reply_to                 = Column(String(255), nullable=True)
    subject                  = Column(String(998), nullable=False, default="")
    # Encrypted JSON {"html", "text"} — the content exactly as rendered at submit.
    content_enc              = Column(Text().with_variant(mysql_types.MEDIUMTEXT(), "mysql"), nullable=True)
    content_key_version      = Column(String(20), nullable=True)
    content_sha256           = Column(String(64), nullable=True)
    content_purged_at        = Column(DateTime, nullable=True)
    is_sensitive             = Column(Boolean, nullable=False, default=False)
    template_id              = Column(String(36), nullable=True)
    message_id_header        = Column(String(255), nullable=True)
    actor_type               = Column(String(20), nullable=False, default="system")   # user | api_key | system
    actor_user_id            = Column(String(36), nullable=True)
    api_key_id               = Column(String(36), nullable=True)
    # Supplied by the customer's application; never an identity we verified.
    external_actor_reference = Column(String(255), nullable=True)
    external_reference       = Column(String(255), nullable=True)
    # ``metadata`` is reserved on declarative classes, hence the attribute name.
    metadata_json            = Column("metadata", JSON, nullable=True)
    reference_type           = Column(String(50), nullable=True)
    reference_id             = Column(String(36), nullable=True)
    step_key                 = Column(String(50), nullable=True)
    idempotency_key          = Column(String(255), nullable=True)
    request_hash             = Column(String(64), nullable=True)
    attempt_count            = Column(Integer, nullable=False, default=0)
    max_attempts             = Column(Integer, nullable=False, default=6)
    next_attempt_at          = Column(DateTime, nullable=True)
    lease_expires_at         = Column(DateTime, nullable=True)
    accepted_at              = Column(DateTime, nullable=True)
    completed_at             = Column(DateTime, nullable=True)
    cancelled_at             = Column(DateTime, nullable=True)
    created_at               = Column(DateTime, default=datetime.utcnow)
    updated_at               = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailRecipient(Base, TenantScoped):
    """One addressee of a message and what the upstream server said about it."""
    __tablename__ = "email_recipients"
    __table_args__ = (
        Index("ix_email_rcpt_company_email", "company_id", "email"),
        ForeignKeyConstraint(["company_id", "message_id"],
                             ["email_messages.company_id", "email_messages.id"],
                             name="fk_email_rcpt_message"),
    )

    id            = Column(String(36), primary_key=True, default=_uuid)
    company_id    = _company_fk()
    message_id    = Column(String(36), nullable=False)
    email         = Column(String(255), nullable=False)
    name          = Column(String(255), nullable=True)
    kind          = Column(String(5), nullable=False, default="to")   # to | cc | bcc
    # pending | accepted | failed | deferred | suppressed | unknown
    status        = Column(String(20), nullable=False, default="pending")
    smtp_code     = Column(Integer, nullable=True)
    smtp_response = Column(String(500), nullable=True)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailAttempt(Base, TenantScoped):
    """One try at handing a message to its connection."""
    __tablename__ = "email_attempts"
    __table_args__ = (
        UniqueConstraint("message_id", "attempt_no", name="uq_email_attempt_no"),
        Index("ix_email_attempt_account_started", "account_key", "started_at"),
        ForeignKeyConstraint(["company_id", "message_id"],
                             ["email_messages.company_id", "email_messages.id"],
                             name="fk_email_attempt_message"),
    )

    id                = Column(String(36), primary_key=True, default=_uuid)
    company_id        = _company_fk()
    message_id        = Column(String(36), nullable=False)
    attempt_no        = Column(Integer, nullable=False)
    connection_id     = Column(String(36), nullable=True)
    account_key       = Column(String(64), nullable=True)
    worker_id         = Column(String(100), nullable=True)
    # connect | tls | auth | mail | rcpt | data | done
    phase_reached     = Column(String(10), nullable=True)
    # accepted | temp_failure | perm_failure | blocked | unknown | deferred_ratelimit
    outcome           = Column(String(30), nullable=True)
    error_code        = Column(String(64), nullable=True)
    smtp_code         = Column(Integer, nullable=True)
    provider_response = Column(String(1000), nullable=True)
    started_at        = Column(DateTime, default=datetime.utcnow)
    finished_at       = Column(DateTime, nullable=True)
    duration_ms       = Column(Integer, nullable=True)


class EmailOutbox(Base, TenantScoped):
    """Committed work waiting to be handed to the queue.

    The database, not Redis, is the record of what still has to be sent: a row
    stays ``pending`` until published, and a ``published`` row whose lease
    expires is published again, so a Redis flush or a dead worker loses nothing.
    """
    __tablename__ = "email_outbox"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_email_outbox_message"),
        Index("ix_email_outbox_state_available", "state", "available_at"),
        Index("ix_email_outbox_state_lease", "state", "lease_expires_at"),
        ForeignKeyConstraint(["company_id", "message_id"],
                             ["email_messages.company_id", "email_messages.id"],
                             name="fk_email_outbox_message"),
    )

    id                 = Column(String(36), primary_key=True, default=_uuid)
    company_id         = _company_fk()
    message_id         = Column(String(36), nullable=False)
    state              = Column(String(10), nullable=False, default="pending")   # pending | published | done
    available_at       = Column(DateTime, nullable=False, default=datetime.utcnow)
    lease_owner        = Column(String(64), nullable=True)
    lease_expires_at   = Column(DateTime, nullable=True)
    publish_count      = Column(Integer, nullable=False, default=0)
    last_publish_error = Column(String(255), nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailSuppression(Base, TenantScoped):
    """An address this company must not mail again (hard bounce, complaint, manual)."""
    __tablename__ = "email_suppressions"
    __table_args__ = (
        UniqueConstraint("company_id", "email", name="uq_email_suppression_company_email"),
    )

    id                 = Column(String(36), primary_key=True, default=_uuid)
    company_id         = _company_fk()
    email              = Column(String(255), nullable=False)   # lower-cased
    reason             = Column(String(20), nullable=False)    # hard_bounce | complaint | manual
    source             = Column(String(20), nullable=False)    # smtp_5xx | admin | api
    created_by_user_id = Column(String(36), nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)


class TenantApiKey(Base, TenantScoped):
    """A company's credential for the external email API.

    Only a hash of the secret is stored; ``key_prefix`` is the non-secret part
    used to find the row and to name the key in the UI. Unrelated to the
    deprecated ``companies.api_key`` column, which authenticates nothing.
    """
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_prefix", name="uq_api_key_prefix"),
        Index("ix_api_key_company", "company_id"),
    )

    id                 = Column(String(36), primary_key=True, default=_uuid)
    company_id         = _company_fk()
    name               = Column(String(100), nullable=False)
    mode               = Column(String(4), nullable=False, default="live")   # live | test
    key_prefix         = Column(String(24), nullable=False)
    key_hash           = Column(String(64), nullable=False)
    scopes             = Column(JSON, nullable=False)
    allowed_ips        = Column(JSON, nullable=True)
    hourly_quota       = Column(Integer, nullable=True)
    rotated_from_id    = Column(String(36), nullable=True)
    created_by_user_id = Column(String(36), nullable=True)
    revoked_by_user_id = Column(String(36), nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)
    expires_at         = Column(DateTime, nullable=True)
    revoked_at         = Column(DateTime, nullable=True)
    last_used_at       = Column(DateTime, nullable=True)
    last_used_ip       = Column(String(45), nullable=True)
