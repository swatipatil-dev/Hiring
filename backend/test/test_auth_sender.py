"""
The sender for OTP / verification / password-reset mail — resolve_auth_sender.

Auth mail has its own chain because a company that has connected no mailbox still
needs its users to be able to register and reset passwords:

    the company's auth route → the company's default sender
      → the platform's explicit auth route (visible in Admin → Mail Routing)

and never the environment. Every way the chain can end is covered here, plus the
three call sites (registration, resend, reset) end to end.
"""
import pytest

from src.email_api import constants as C
from src.models.models import EmailMessage
from src.tenancy.platform_company import ensure_platform_company
from test.email_factories import make_company, make_route, make_smtp_sender


@pytest.fixture()
def platform_auth(db):
    """The platform's auth route, as seeded from SMTP_* (src/email_api/bootstrap.py)."""
    platform = ensure_platform_company(db)
    sender, _ = make_smtp_sender(db, platform.id, "Platform System",
                                 email="system@platform.example", is_shared=True)
    make_route(db, platform.id, C.EVENT_AUTH_VERIFY, sender.id)
    make_route(db, platform.id, C.EVENT_AUTH_RESET, sender.id)
    return sender


def _resolve(db, cid, purpose=C.EVENT_AUTH_VERIFY):
    from src.services.email_service import resolve_auth_sender
    return resolve_auth_sender(db, cid, purpose)


# ── the chain ───────────────────────────────────────────────────────────────

def test_company_auth_route_wins(db, platform_auth):
    co = make_company(db)
    auth, _ = make_smtp_sender(db, co.id, "Auth", email="auth@acme.example")
    default, _ = make_smtp_sender(db, co.id, "Default", email="hello@acme.example")
    make_route(db, co.id, C.EVENT_AUTH_VERIFY, auth.id)
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    ref = _resolve(db, co.id)
    assert ref.ok and ref["from_email"] == "auth@acme.example"
    assert "password" not in ref and "app-pw" not in repr(dict(ref))


def test_each_purpose_has_its_own_route(db, platform_auth):
    co = make_company(db)
    verify, _ = make_smtp_sender(db, co.id, "Verify", email="verify@acme.example")
    make_route(db, co.id, C.EVENT_AUTH_VERIFY, verify.id)
    assert _resolve(db, co.id, C.EVENT_AUTH_VERIFY)["from_email"] == "verify@acme.example"
    # No reset route and no default: the platform route answers for reset codes.
    assert _resolve(db, co.id, C.EVENT_AUTH_RESET)["from_email"] == "system@platform.example"


def test_falls_through_to_the_company_default(db, platform_auth):
    co = make_company(db)
    default, _ = make_smtp_sender(db, co.id, "Default", email="hello@acme.example")
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    ref = _resolve(db, co.id)
    assert ref["from_email"] == "hello@acme.example"
    assert ref["route_source"] == C.SRC_DEFAULT


def test_unconfigured_company_uses_the_explicit_platform_route(db, platform_auth):
    co = make_company(db)
    ref = _resolve(db, co.id)
    assert ref.ok and ref["route_source"] == C.SRC_PLATFORM_AUTH
    assert ref["from_email"] == "system@platform.example"
    assert ref["sender_company_id"] != co.id and ref["company_id"] == co.id


def test_without_a_platform_route_auth_mail_is_blocked_not_sent_from_env(db, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "platform@gmail.com")
    monkeypatch.setenv("SMTP_PASS", "env-secret")
    ensure_platform_company(db)
    co = make_company(db)
    ref = _resolve(db, co.id)
    assert ref["status"] == "blocked" and ref["reason_code"] == C.R_NO_SENDER
    assert ref["from_email"] is None


def test_another_companys_auth_route_is_not_used(db, platform_auth):
    mine, theirs = make_company(db, "Mine"), make_company(db, "Theirs")
    s, _ = make_smtp_sender(db, theirs.id, "Auth", email="auth@theirs.example")
    make_route(db, theirs.id, C.EVENT_AUTH_VERIFY, s.id)
    assert _resolve(db, mine.id)["from_email"] == "system@platform.example"


def test_no_company_is_blocked(db):
    ref = _resolve(db, None)
    assert ref["status"] == "blocked" and ref["from_email"] is None


@pytest.mark.parametrize("kwargs,words", [
    ({"password": None}, "no password"),
    ({"enabled": False}, "disabled"),
    ({"conn_status": C.CONN_FAILED}, "failed"),
])
def test_broken_company_auth_sender_blocks_and_says_why(db, platform_auth, kwargs, words):
    """A sender the company configured but that cannot send is a fault to fix,
    not a cue to send from somewhere else — the old chain fell through silently."""
    co = make_company(db)
    s, _ = make_smtp_sender(db, co.id, "Auth", email="auth@acme.example", **kwargs)
    make_route(db, co.id, C.EVENT_AUTH_VERIFY, s.id)
    ref = _resolve(db, co.id)
    assert ref["status"] == "blocked" and ref["reason_code"] == C.R_ROUTE_SENDER_UNAVAILABLE
    assert words in ref["reason"].lower()


# ── the three call sites ───────────────────────────────────────────────────

def test_every_auth_call_site_uses_the_shared_resolver():
    """They each inlined the chain once and drifted, so one user could get two
    codes from two addresses. Each must name the purpose it sends for."""
    import pathlib
    backend = pathlib.Path(__file__).resolve().parent.parent
    auth = (backend / "src/auth.py").read_text(encoding="utf-8")
    routes = (backend / "routes/auth_routes.py").read_text(encoding="utf-8")
    assert 'resolve_auth_sender(db, db_user.company_id, "auth.verify_email")' in auth
    assert "resolve_auth_sender(session, company_id, purpose)" in routes
    assert '"auth.verify_email"' in routes and '"auth.password_reset"' in routes


def _messages(db, company_id):
    db.expire_all()
    return (db.query(EmailMessage)
            .filter(EmailMessage.company_id == company_id)
            .order_by(EmailMessage.created_at)
            .all())


def test_registration_resend_and_reset_send_auth_mail(client, db, platform_auth, monkeypatch):
    """End to end through the API: each produces an auth-priority message from
    the platform route, recorded like any other email, OTP content never kept."""
    monkeypatch.setattr("src.mailq.queue.redis_available", lambda: False)
    db.commit()
    reg = client.post("/api/v1/auth/register", json={
        "email": "newcandidate@example.net", "password": "Str0ng!Passw0rd#",
        "role": "candidate", "first_name": "New", "last_name": "Person"})
    assert reg.status_code in (200, 201), reg.text
    platform = platform_auth.company_id
    client.post("/api/v1/auth/resend-otp", json={"email": "newcandidate@example.net"})
    client.post("/api/v1/auth/forgot-password", json={"email": "newcandidate@example.net"})
    msgs = _messages(db, platform)
    # Same-second rows have no order (DATETIME has no fraction), so compare as a multiset.
    assert sorted(m.event_type for m in msgs) == sorted(
        [C.EVENT_AUTH_VERIFY, C.EVENT_AUTH_VERIFY, C.EVENT_AUTH_RESET])
    for m in msgs:
        assert m.priority == C.PRIORITY_AUTH and m.is_sensitive
        assert m.from_email == "system@platform.example"
        # Redis is down: auth mail is delivered in-process, then its code is purged.
        assert m.status == C.ACCEPTED and m.content_enc is None
