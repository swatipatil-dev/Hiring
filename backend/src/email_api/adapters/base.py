"""The contract every delivery mechanism implements.

An adapter knows how to talk to one kind of connection (SMTP today; Gmail API,
Microsoft Graph and a transactional provider later). It does not touch the
database: the worker hands it a connection, a sender, a frozen message snapshot
and the decrypted secret, and records whatever comes back.

``Capabilities`` says what an adapter can honestly report. Plain SMTP cannot see
delivery, bounces or complaints, so its flags are all False and the API never
claims ``delivered`` for SMTP mail — only ``accepted``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class Capabilities:
    supports_delivery_events: bool = False
    supports_complaint_events: bool = False
    supports_sent_folder: bool = False
    supports_reply_sync: bool = False
    supports_shared_mailbox: bool = False


# DeliveryError.kind
BLOCKED = "blocked"        # configuration/authorisation problem: stop, a person must act
TEMPORARY = "temporary"    # retry with backoff
THROTTLED = "throttled"    # retry later, respecting retry_after
PERMANENT = "permanent"    # the message itself was refused; do not retry
UNKNOWN = "unknown"        # the server may have accepted it; never retry automatically


@dataclass
class DeliveryError:
    code: str
    kind: str
    detail: str = ""
    smtp_code: Optional[int] = None
    retry_after: Optional[int] = None
    # Set when the failure says the connection itself is broken, e.g. "failed"
    # after a rejected password, so the next message blocks without trying.
    connection_status: Optional[str] = None


@dataclass
class RecipientResult:
    email: str
    status: str                       # accepted | failed | deferred
    smtp_code: Optional[int] = None
    smtp_response: Optional[str] = None
    suppress: bool = False            # a hard "no such user" — add to suppressions


@dataclass
class SubmissionResult:
    outcome: str                      # constants.OUT_*
    phase_reached: str
    recipients: List[RecipientResult] = field(default_factory=list)
    error: Optional[DeliveryError] = None
    smtp_code: Optional[int] = None
    provider_response: Optional[str] = None


@dataclass
class ValidationResult:
    ok: bool
    phase_reached: str
    error: Optional[DeliveryError] = None


@dataclass(frozen=True)
class Snapshot:
    """Everything needed to build the message, frozen when it was accepted."""
    from_email: str
    from_name: Optional[str]
    subject: str
    html: str
    text: Optional[str] = None
    to: tuple = ()
    cc: tuple = ()
    bcc: tuple = ()
    reply_to: Optional[str] = None
    message_id_header: Optional[str] = None
    auto_submitted: bool = False
    unsubscribe_to: Optional[str] = None

    @property
    def envelope_recipients(self) -> List[str]:
        return [*self.to, *self.cc, *self.bcc]


class Adapter(Protocol):
    capabilities: Capabilities

    def validate_connection(self, conn, secret: str) -> ValidationResult: ...

    def send(self, conn, snapshot: Snapshot, secret: str) -> SubmissionResult: ...


class UnsupportedConnection(Exception):
    """No adapter exists yet for this connection type."""


def delivery_mode() -> str:
    """``null`` routes every send to the NullAdapter (local development)."""
    return os.getenv("EMAIL_DELIVERY_MODE", "").strip().lower()


def get_adapter(conn, is_test: bool = False) -> Adapter:
    """The adapter for a connection.

    Test-mode messages (``hf_test_`` keys) never leave the building, whatever
    connection they name. Types other than SMTP are the seam for the OAuth and
    verified-domain phases; until then they raise and the message is blocked.
    """
    from .null import NullAdapter
    if is_test or delivery_mode() == "null":
        return NullAdapter()
    if conn.type == "smtp":
        from .smtp import SMTPAdapter
        return SMTPAdapter()
    raise UnsupportedConnection(conn.type)
