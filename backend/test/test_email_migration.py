"""Revision t2e3m4a5i6l7: legacy SMTP settings become connections, senders and routes.

Runs the real upgrade()/downgrade() through Alembic's Operations against the
test connection, then checks the result through the sender policy — the thing
that has to behave the same the morning after the upgrade.
"""
import importlib.util
import pathlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from src.email_api import constants as C, crypto
from src.email_api.policy import resolve_sender
from src.models.models import (CompanyMailbox, EmailConnection, EmailRoute, EmailSender,
                               PipelineStepConfig, TenantConfig)
from src.security import encrypt as legacy_encrypt
from test.email_factories import make_company

REV = (pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions"
       / "t2e3m4a5i6l7_migrate_smtp_to_connections.py")


def _rev():
    spec = importlib.util.spec_from_file_location("rev_t2e3", REV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(db, fn):
    db.flush()
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        fn()
    db.expire_all()


def _mailbox(db, co, name, *, port=465, password="pw", user=None, active=True):
    mb = CompanyMailbox(company_id=co.id, name=name, smtp_host="mail.acme.example", smtp_port=port,
                        smtp_user=user if user is not None else f"{name.lower()}@acme.example",
                        smtp_pass=legacy_encrypt(password) if password else None,
                        smtp_from_email=f"{name.lower()}@acme.example", hourly_limit=40,
                        is_active=active)
    db.add(mb)
    db.flush()
    return mb


@pytest.fixture()
def legacy(db):
    co = make_company(db, "Acme")
    db.add(TenantConfig(company_id=co.id, smtp_host="smtp.acme.example", smtp_port=587,
                        smtp_user="noreply@acme.example", smtp_pass=legacy_encrypt("default-pw"),
                        smtp_from_email="jobs@acme.example"))
    mbs = {
        "HR": _mailbox(db, co, "HR"),
        "Offers": _mailbox(db, co, "Offers", password=None),
        "Auth": _mailbox(db, co, "Auth"),
        "Relay": _mailbox(db, co, "Relay", port=25),
        "Old": _mailbox(db, co, "Old", active=False),
    }
    for step, mb, enabled in [("interview_scheduled", "HR", True), ("offer_letter", "Offers", True),
                              ("candidate_rejected", None, False), ("test_invitation", None, True),
                              ("panel_invite", "Old", True)]:
        db.add(PipelineStepConfig(company_id=co.id, step_key=step, enabled=enabled,
                                  mailbox_id=mbs[mb].id if mb else None))
    db.flush()
    return co, mbs


def _sender(db, sid):
    return db.query(EmailSender).filter_by(id=sid).one()


def _conn(db, sender):
    return db.query(EmailConnection).filter_by(id=sender.connection_id).one()


def test_mailboxes_become_senders_with_the_same_id(db, legacy):
    co, mbs = legacy
    _run(db, _rev().upgrade)
    hr = _sender(db, mbs["HR"].id)
    assert (hr.company_id, hr.label, hr.email) == (co.id, "HR", "hr@acme.example")
    assert hr.verification_status == C.SENDER_LEGACY and hr.hourly_limit == 40
    conn = _conn(db, hr)
    assert conn.status == C.CONN_ACTIVE and conn.security == C.SECURITY_IMPLICIT_TLS
    assert conn.credential_key_version == crypto.LEGACY
    assert crypto.decrypt_secret(conn.credential_enc, conn.credential_key_version) == "pw"
    assert _conn(db, _sender(db, mbs["Offers"].id)).error_code == "no_password"
    assert _conn(db, _sender(db, mbs["Relay"].id)).error_code == "port_not_allowed"
    assert _sender(db, mbs["Old"].id).enabled is False


def test_company_default_and_routes(db, legacy):
    co, mbs = legacy
    _run(db, _rev().upgrade)
    routes = {r.event_type: r for r in db.query(EmailRoute).filter_by(company_id=co.id)}
    default = _sender(db, routes[C.DEFAULT_ROUTE].sender_id)
    assert (default.label, default.email) == ("Company default", "jobs@acme.example")
    assert _conn(db, default).username == "noreply@acme.example"
    assert routes["interview_scheduled"].sender_id == mbs["HR"].id
    assert routes["candidate_rejected"].enabled is False
    assert routes[C.EVENT_AUTH_VERIFY].sender_id == mbs["Auth"].id
    assert routes[C.EVENT_AUTH_RESET].sender_id == mbs["Auth"].id
    # The old resolver skipped unusable mailboxes and used the default; so do the routes.
    assert "offer_letter" not in routes and "panel_invite" not in routes
    assert "test_invitation" not in routes


def test_policy_behaves_as_before(db, legacy):
    co, mbs = legacy
    _run(db, _rev().upgrade)
    assert resolve_sender(db, co.id, "interview_scheduled").sender_id == mbs["HR"].id
    assert resolve_sender(db, co.id, "offer_letter").from_email == "jobs@acme.example"
    assert resolve_sender(db, co.id, "panel_invite").from_email == "jobs@acme.example"
    assert resolve_sender(db, co.id, "candidate_rejected").status == "disabled"
    assert resolve_sender(db, co.id, C.EVENT_AUTH_VERIFY).sender_id == mbs["Auth"].id
    # An HR-picked mailbox that never worked now blocks instead of switching sender.
    picked = resolve_sender(db, co.id, "custom_mail", requested_sender_id=mbs["Offers"].id)
    assert picked.status == "blocked"


def test_idempotent(db, legacy):
    co, _ = legacy
    rev = _rev()
    _run(db, rev.upgrade)
    counts = lambda: (db.query(EmailConnection).filter_by(company_id=co.id).count(),  # noqa: E731
                      db.query(EmailSender).filter_by(company_id=co.id).count(),
                      db.query(EmailRoute).filter_by(company_id=co.id).count())
    first = counts()
    _run(db, rev.upgrade)
    assert counts() == first == (6, 6, 5)


def test_existing_routes_are_not_overwritten(db, legacy):
    co, mbs = legacy
    db.add(EmailRoute(company_id=co.id, event_type="interview_scheduled", enabled=False))
    _run(db, _rev().upgrade)
    route = db.query(EmailRoute).filter_by(company_id=co.id, event_type="interview_scheduled").one()
    assert route.enabled is False and route.sender_id is None


def test_downgrade_removes_only_what_it_added(db, legacy):
    co, mbs = legacy
    rev = _rev()
    _run(db, rev.upgrade)
    from src.email_api.messages import submit
    decision = resolve_sender(db, co.id, "interview_scheduled")
    submit(db, tenant_id=co.id, decision=decision, event_type="interview_scheduled",
           to=["c@example.net"], subject="s", html="b")
    _run(db, rev.downgrade)
    remaining = db.query(EmailSender).filter_by(company_id=co.id).all()
    # The sender a message references is kept; everything else migrated is gone.
    assert [s.id for s in remaining] == [mbs["HR"].id]
    assert db.query(EmailRoute).filter_by(company_id=co.id).count() == 0
    assert db.query(CompanyMailbox).filter_by(company_id=co.id).count() == 5


def test_reencrypt_script(db, legacy):
    import importlib
    script = importlib.import_module("scripts.reencrypt_email_credentials")
    co, mbs = legacy
    _run(db, _rev().upgrade)
    hr_conn = _conn(db, _sender(db, mbs["HR"].id))
    auth_conn = _conn(db, _sender(db, mbs["Auth"].id))
    auth_conn.credential_enc = "gAAAAA" + "x" * 120   # a token under some lost key
    relay = _conn(db, _sender(db, mbs["Relay"].id))
    relay.credential_enc = "plain-old-password"      # a pre-encryption row
    db.flush()
    stats = script.reencrypt(db)
    db.expire_all()
    assert stats["unreadable"] == 1 and stats["plaintext"] == 1
    assert hr_conn.credential_key_version == "k1"
    assert crypto.decrypt_secret(hr_conn.credential_enc, hr_conn.credential_key_version) == "pw"
    assert crypto.decrypt_secret(relay.credential_enc, relay.credential_key_version) == "plain-old-password"
    assert auth_conn.status == C.CONN_NEEDS_ATTENTION
    assert auth_conn.error_code == C.R_CREDENTIAL_UNREADABLE
    assert script.reencrypt(db)["rewritten"] == 0   # nothing left to do


def test_rollback_script_copies_new_settings_back(db, legacy):
    """After the upgrade, edits land only in the new tables; the rollback aid puts
    them where the previous release reads them."""
    import importlib
    from src.email_api import facade
    from src.security import decrypt as legacy_decrypt
    script = importlib.import_module("scripts.email_connections_to_legacy")
    co, mbs = legacy
    _run(db, _rev().upgrade)
    facade.update_mailbox(db, co.id, mbs["HR"].id, name="Hiring", host="mail.acme.example",
                          port=587, user="hr@acme.example", password="new-pw",
                          from_email="hr@acme.example")
    # The migration left offer_letter on the default (its mailbox could not send);
    # an admin now routes it to Hiring.
    db.add(EmailRoute(company_id=co.id, event_type="offer_letter", sender_id=mbs["HR"].id))
    db.flush()
    stats = script.copy_back(db)
    db.expire_all()
    mb = db.query(CompanyMailbox).filter_by(id=mbs["HR"].id).one()
    assert (mb.name, mb.smtp_port) == ("Hiring", 587)
    assert legacy_decrypt(mb.smtp_pass) == "new-pw"
    tc = db.query(TenantConfig).filter_by(company_id=co.id).one()
    assert legacy_decrypt(tc.smtp_pass) == "default-pw"
    step = db.query(PipelineStepConfig).filter_by(company_id=co.id, step_key="offer_letter").one()
    assert step.mailbox_id == mbs["HR"].id
    assert stats["tenant_configs"] == 1 and stats["mailboxes"] >= 5
