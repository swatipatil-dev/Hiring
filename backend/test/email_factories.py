"""Rows for email API tests: companies, connections, senders, routes, keys.

Builds rows directly (no network, no SMTP), so a test states exactly the
configuration it is about.
"""
import uuid

from src.email_api import constants as C
from src.email_api import crypto
from src.email_api.connections import account_key
from src.models.models import Company, EmailConnection, EmailSender, EmailRoute


def make_company(db, name="Acme"):
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c


def make_connection(db, company_id, *, status=C.CONN_ACTIVE, password="app-pw",
                    host="smtp.example.com", port=465, username=None, is_shared=False,
                    hourly_limit=None, name="SMTP"):
    username = username or f"hr-{uuid.uuid4().hex[:6]}@example.com"
    conn = EmailConnection(company_id=company_id, type=C.CONN_SMTP, provider="smtp", name=name,
                           status=status, host=host, port=port,
                           security=C.SECURITY_IMPLICIT_TLS if port == 465 else C.SECURITY_STARTTLS,
                           username=username, account_key=account_key(host, username),
                           is_shared=is_shared, hourly_limit=hourly_limit)
    if password:
        conn.credential_enc, conn.credential_key_version = crypto.encrypt_secret(password)
    db.add(conn)
    db.flush()
    return conn


def make_smtp_sender(db, company_id, label="HR", *, email=None, conn_status=C.CONN_ACTIVE,
                     enabled=True, password="app-pw", display_name=None, reply_to=None,
                     hourly_limit=None, is_shared=False, conn=None):
    conn = conn or make_connection(db, company_id, status=conn_status, password=password,
                                   username=email, is_shared=is_shared)
    sender = EmailSender(company_id=company_id, connection_id=conn.id, label=label,
                         email=(email or conn.username), display_name=display_name,
                         reply_to=reply_to, enabled=enabled, hourly_limit=hourly_limit,
                         verification_status=C.SENDER_PRIMARY)
    db.add(sender)
    db.flush()
    return sender, conn


def make_route(db, company_id, event_type, sender_id=None, *, enabled=True):
    route = EmailRoute(company_id=company_id, event_type=event_type, sender_id=sender_id,
                       enabled=enabled)
    db.add(route)
    db.flush()
    return route


def queue_message(db, company_id, event_type="offer_letter", *, to=("cand@example.net",),
                  subject="Your offer", html="<p>Welcome</p>", **kw):
    """Resolve the sender through the real policy and submit, without committing."""
    from src.email_api.messages import submit
    from src.email_api.policy import resolve_sender
    decision = resolve_sender(db, company_id, event_type,
                              requested_sender_id=kw.pop("requested_sender_id", None))
    return submit(db, tenant_id=company_id, decision=decision, event_type=event_type, to=to,
                  subject=subject, html=html, **kw)


class FakeAdapter:
    """Records every send; ``respond(snapshot)`` returns the SubmissionResult."""

    def __init__(self, respond=None):
        self.calls = []
        self.respond = respond or accepted

    def send(self, conn, snapshot, secret):
        self.calls.append({"conn": conn.id, "snapshot": snapshot, "secret": secret})
        return self.respond(snapshot)

    def validate_connection(self, conn, secret):
        from src.email_api.adapters.base import ValidationResult
        return ValidationResult(True, "done")


def accepted(snapshot):
    from src.email_api.adapters.base import SubmissionResult, RecipientResult
    return SubmissionResult(C.OUT_ACCEPTED, "done",
                            [RecipientResult(a, C.RCPT_ACCEPTED, 250, "OK")
                             for a in snapshot.envelope_recipients], smtp_code=250)


def failing(outcome, code, kind, *, connection_status=None, retry_after=None):
    from src.email_api.adapters.base import SubmissionResult, DeliveryError

    def respond(snapshot):
        return SubmissionResult(outcome, "auth", error=DeliveryError(
            code, kind, f"simulated {code}", connection_status=connection_status,
            retry_after=retry_after))
    return respond
