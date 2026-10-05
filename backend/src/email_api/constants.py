"""Vocabularies for the email API — the values the string columns may hold.

Kept as plain strings, like the rest of the schema; this module is where they
are spelled once and validated.
"""
from __future__ import annotations

from typing import Dict

from src.services.pipeline_notifications import STEP_LABELS

# ── connections ──────────────────────────────────────────────────────────────
CONN_SMTP = "smtp"
CONNECTION_TYPES = ("smtp", "google_oauth", "microsoft_oauth", "verified_domain")

CONN_PENDING = "pending_verification"
CONN_ACTIVE = "active"
CONN_FAILED = "failed"
CONN_NEEDS_ATTENTION = "needs_attention"
CONN_DISCONNECTED = "disconnected"

SECURITY_IMPLICIT_TLS = "implicit_tls"
SECURITY_STARTTLS = "starttls_required"

# ── senders ──────────────────────────────────────────────────────────────────
SENDER_PRIMARY = "primary"
SENDER_ALIAS = "approved_alias"
SENDER_LEGACY = "legacy_unverified"

# ── routes ───────────────────────────────────────────────────────────────────
DEFAULT_ROUTE = "__default__"

# ── message states (spec §12) ────────────────────────────────────────────────
QUEUED = "queued"
DEFERRED = "deferred"
SENDING = "sending"
ACCEPTED = "accepted"
DELIVERED = "delivered"
BOUNCED = "bounced"
COMPLAINED = "complained"
FAILED = "failed"
BLOCKED = "blocked"
UNKNOWN = "unknown"
CANCELLED = "cancelled"

MESSAGE_STATES = (QUEUED, DEFERRED, SENDING, ACCEPTED, DELIVERED, BOUNCED,
                  COMPLAINED, FAILED, BLOCKED, UNKNOWN, CANCELLED)
# A worker never picks these up again on its own. ``unknown`` is terminal on
# purpose: the server may already have accepted the mail, so only a person who
# accepts the duplicate risk can send it again.
TERMINAL_STATES = frozenset({ACCEPTED, DELIVERED, BOUNCED, COMPLAINED, FAILED,
                             BLOCKED, UNKNOWN, CANCELLED})

# ── recipient states ─────────────────────────────────────────────────────────
RCPT_PENDING = "pending"
RCPT_ACCEPTED = "accepted"
RCPT_FAILED = "failed"
RCPT_DEFERRED = "deferred"
RCPT_SUPPRESSED = "suppressed"
RCPT_UNKNOWN = "unknown"

# ── attempt outcomes ─────────────────────────────────────────────────────────
OUT_ACCEPTED = "accepted"
OUT_TEMP = "temp_failure"
OUT_PERM = "perm_failure"
OUT_BLOCKED = "blocked"
OUT_UNKNOWN = "unknown"
OUT_RATELIMIT = "deferred_ratelimit"

# ── priorities ───────────────────────────────────────────────────────────────
PRIORITY_AUTH = "auth"
PRIORITY_NORMAL = "normal"
PRIORITY_BULK = "bulk"

# ── route sources ────────────────────────────────────────────────────────────
SRC_REQUESTED = "requested"
SRC_ROUTE = "route"
SRC_DEFAULT = "tenant_default"
SRC_PLATFORM_AUTH = "platform_auth"
SRC_PLATFORM_ROUTE = "platform_route"

# ── reason codes (status_reason / SenderDecision.reason_code) ───────────────
R_NO_SENDER = "no_sender_configured"
R_ROUTE_SENDER_UNAVAILABLE = "route_sender_unavailable"
R_SENDER_NOT_FOUND = "sender_not_found"
R_SENDER_DISABLED = "sender_disabled"
R_SENDER_CHANGED = "sender_changed"
R_CONNECTION_UNAVAILABLE = "connection_unavailable"
R_ROUTE_DISABLED = "route_disabled"
R_EVENT_NOT_ALLOWED = "event_not_allowed"
R_MIGRATION_PENDING = "email_migration_pending"
R_AUTH_FAILED = "auth_failed"
R_TLS_CERT_INVALID = "tls_cert_invalid"
R_STARTTLS_UNAVAILABLE = "starttls_unavailable"
R_SENDER_REJECTED = "sender_rejected"
R_DESTINATION_NOT_ALLOWED = "destination_not_allowed"
R_CREDENTIAL_UNREADABLE = "credential_unreadable"
R_CONNECTION_TYPE_UNSUPPORTED = "connection_type_unsupported"
R_ALL_SUPPRESSED = "all_recipients_suppressed"
R_WORKER_LOST = "worker_lost_during_send"
R_RETRIES_EXHAUSTED = "retries_exhausted"
R_DELIVERY_PAUSED = "delivery_paused"
R_RATE_LIMITED = "rate_limited"

# ── API scopes (spec §3) ─────────────────────────────────────────────────────
# Only scopes something actually enforces are accepted on a key; the spec's
# domains:write, templates:write and webhooks:write arrive with their phases.
SCOPES = ("email:send", "email:read", "connections:read", "connections:write",
          "senders:read", "senders:write")
ADMIN_SCOPES = frozenset(SCOPES)
HR_SCOPES = frozenset({"email:send", "email:read", "connections:read", "senders:read"})

# ── events ───────────────────────────────────────────────────────────────────
CAT_TENANT = "tenant"      # the company's own mail; never sent from another company's sender
CAT_AUTH = "auth"          # OTP / verification / reset; may use the explicit platform auth route
CAT_PLATFORM = "platform"  # the platform company's own operational mail

EVENT_AUTH_VERIFY = "auth.verify_email"
EVENT_AUTH_RESET = "auth.password_reset"
EVENT_API = "api.message"
EVENT_CONNECTION_TEST = "connection.test"

EVENT_CATALOG: Dict[str, Dict[str, str]] = {
    **{key: {"category": CAT_TENANT, "label": label} for key, label in STEP_LABELS.items()},
    "workflow.reminder_digest": {"category": CAT_TENANT, "label": "Stage reminder digest"},
    EVENT_API: {"category": CAT_TENANT, "label": "API message"},
    EVENT_CONNECTION_TEST: {"category": CAT_TENANT, "label": "Connection test"},
    EVENT_AUTH_VERIFY: {"category": CAT_AUTH, "label": "Email verification code"},
    EVENT_AUTH_RESET: {"category": CAT_AUTH, "label": "Password reset code"},
    "platform.tenant_welcome": {"category": CAT_PLATFORM, "label": "New company welcome"},
    "support.ticket_to_platform": {"category": CAT_PLATFORM, "label": "Support ticket (to platform)"},
    "support.reply_to_platform": {"category": CAT_PLATFORM, "label": "Support reply (to platform)"},
    "support.reply_to_admin": {"category": CAT_PLATFORM, "label": "Support reply (to company admin)"},
}


def event_category(event_type: str):
    entry = EVENT_CATALOG.get(event_type)
    return entry["category"] if entry else None
