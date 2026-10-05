"""The email API tables enforce company ownership in the database itself.

A plain foreign key to ``email_senders.id`` would accept another company's
sender; the composite ``(company_id, id)`` keys must not. These tests write rows
directly, bypassing every service check, so they prove the last line of defence
holds on its own.
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from src.models.models import (Company, EmailConnection, EmailSender, EmailRoute,
                               EmailMessage, EmailRecipient, EmailOutbox)


def _company(db, name):
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c


def _connection(db, company):
    conn = EmailConnection(company_id=company.id, name="SMTP", account_key=uuid.uuid4().hex,
                           host="smtp.example.com", port=465, security="implicit_tls")
    db.add(conn)
    db.flush()
    return conn


def _sender(db, company, conn, label="HR"):
    s = EmailSender(company_id=company.id, connection_id=conn.id, label=label,
                    email=f"{label.lower()}@example.com")
    db.add(s)
    db.flush()
    return s


def _expect_rejected(db, row):
    sp = db.begin_nested()
    db.add(row)
    with pytest.raises(IntegrityError):
        db.flush()
    sp.rollback()


@pytest.fixture()
def two_companies(db):
    a, b = _company(db, "Alpha"), _company(db, "Beta")
    conn_b = _connection(db, b)
    return a, b, conn_b, _sender(db, b, conn_b)


def test_sender_cannot_use_another_companys_connection(db, two_companies):
    a, _b, conn_b, _ = two_companies
    _expect_rejected(db, EmailSender(company_id=a.id, connection_id=conn_b.id,
                                     label="Stolen", email="x@example.com"))


def test_route_cannot_point_at_another_companys_sender(db, two_companies):
    a, _b, _conn_b, sender_b = two_companies
    _expect_rejected(db, EmailRoute(company_id=a.id, event_type="__default__",
                                    sender_id=sender_b.id))


def test_message_sender_and_connection_must_belong_together(db, two_companies):
    a, b, _conn_b, sender_b = two_companies
    conn_a = _connection(db, a)
    # sender from B, connection from A, both claimed under B
    _expect_rejected(db, EmailMessage(company_id=a.id, event_type="api.message",
                                      sender_company_id=b.id, sender_id=sender_b.id,
                                      connection_id=conn_a.id))


def test_message_children_must_share_its_company(db, two_companies):
    a, b, conn_b, sender_b = two_companies
    msg = EmailMessage(company_id=b.id, event_type="api.message", sender_company_id=b.id,
                       sender_id=sender_b.id, connection_id=conn_b.id)
    db.add(msg)
    db.flush()
    _expect_rejected(db, EmailRecipient(company_id=a.id, message_id=msg.id, email="c@example.net"))
    _expect_rejected(db, EmailOutbox(company_id=a.id, message_id=msg.id))


def test_blocked_message_may_have_no_sender(db, two_companies):
    a, *_ = two_companies
    db.add(EmailMessage(company_id=a.id, event_type="interview_scheduled",
                        status="blocked", status_reason="no_sender_configured"))
    db.flush()


def test_idempotency_key_unique_per_company_and_mode(db, two_companies):
    a, b, *_ = two_companies
    db.add(EmailMessage(company_id=a.id, event_type="api.message", idempotency_key="k1"))
    db.flush()
    _expect_rejected(db, EmailMessage(company_id=a.id, event_type="api.message", idempotency_key="k1"))
    # Same key in test mode, or in another company, is a different namespace.
    db.add(EmailMessage(company_id=a.id, event_type="api.message", idempotency_key="k1", is_test=True))
    db.add(EmailMessage(company_id=b.id, event_type="api.message", idempotency_key="k1"))
    # Internal sends carry no key at all, and NULLs never collide.
    db.add(EmailMessage(company_id=a.id, event_type="api.message"))
    db.add(EmailMessage(company_id=a.id, event_type="api.message"))
    db.flush()
