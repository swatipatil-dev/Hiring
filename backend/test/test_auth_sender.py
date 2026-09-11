"""
The sender chain for OTP / verification / password-reset mail — resolve_auth_sender.

Auth mail is the only category not routed by a pipeline step: it finds its mailbox
by NAME. That makes it the one path with no UI of its own showing which sender won,
so every way it can fall back is covered here.

Covers:
- named Auth mailbox wins when usable
- each unusable branch falls through to the company override, and says why in the log
- an absent mailbox falls through in silence — not having one is a choice, not a fault
- the chain is identical for all three call sites (registration, resend, reset)
"""
import logging
import uuid

import pytest

from src.security import encrypt


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_company(db, name: str = "Auth Co") -> str:
    from src.models.models import Company
    company = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(company)
    db.flush()
    return company.id


def _make_mailbox(db, company_id: str, name: str, *,
                  user="auth@acme.com", password="mailbox-secret", is_active=True):
    """`password=None` leaves smtp_pass unset — what the seed script produces."""
    from src.models.models import CompanyMailbox
    mb = CompanyMailbox(
        id=str(uuid.uuid4()),
        company_id=company_id,
        name=name,
        smtp_host="mail.acme.com",
        smtp_port=465,
        smtp_user=user,
        smtp_pass=encrypt(password) if password else None,
        smtp_from_email=user,
        is_active=is_active,
    )
    db.add(mb)
    db.flush()
    return mb


def _make_company_override(db, company_id: str, user="company@acme.com"):
    from src.models.models import TenantConfig
    tc = TenantConfig(
        id=str(uuid.uuid4()), company_id=company_id,
        smtp_host="smtp.acme.com", smtp_port=587,
        smtp_user=user, smtp_pass=encrypt("company-pw"), smtp_from_email=user,
    )
    db.add(tc)
    db.flush()
    return tc


@pytest.fixture()
def auth_name():
    from src.services.email_service import AUTH_MAILBOX_NAME
    return AUTH_MAILBOX_NAME


_EMAIL_LOGGER = "src.services.email_service"


def _warnings(caplog) -> str:
    """Only this module's warnings.

    ``caplog.text`` collects every logger that propagates to root, and importing the
    app emits unrelated warnings (proctoring's optional deps), so asserting on the
    whole buffer would be both flaky and falsely reassuring.
    """
    return "\n".join(r.getMessage() for r in caplog.records
                     if r.name == _EMAIL_LOGGER and r.levelno >= logging.WARNING)


# ─────────────────────────────────────────────────────────────────────────────
# The chain
# ─────────────────────────────────────────────────────────────────────────────

def test_named_mailbox_wins(db, auth_name):
    from src.services.email_service import resolve_auth_sender
    cid = _make_company(db)
    _make_mailbox(db, cid, auth_name, user="auth@acme.com")
    _make_company_override(db, cid)

    cfg = resolve_auth_sender(db, cid)

    assert cfg["from_email"] == "auth@acme.com"
    assert cfg["password"] == "mailbox-secret"   # decrypted, not the ciphertext


def test_falls_through_to_the_company_override(db, auth_name):
    """The layer below the mailbox. Before this was unified, two of the three call
    sites skipped it entirely for the platform company and went straight to env."""
    from src.services.email_service import resolve_auth_sender
    cid = _make_company(db)
    _make_company_override(db, cid, user="company@acme.com")

    cfg = resolve_auth_sender(db, cid)

    assert cfg["from_email"] == "company@acme.com"


def test_falls_through_to_env_when_nothing_is_configured(db):
    """None means "use the env SMTP_* default" — the address being complained about."""
    from src.services.email_service import resolve_auth_sender
    cid = _make_company(db)

    assert resolve_auth_sender(db, cid) is None


def test_a_differently_named_mailbox_is_not_used(db):
    """The lookup is on the exact name. A mailbox renamed in the UI stops being found,
    which is how auth mail went out from a personal address on this deployment."""
    from src.services.email_service import resolve_auth_sender
    cid = _make_company(db)
    _make_mailbox(db, cid, "Auth Mailbox", user="auth@acme.com")
    _make_company_override(db, cid, user="company@acme.com")

    cfg = resolve_auth_sender(db, cid)

    assert cfg["from_email"] == "company@acme.com"


def test_another_companys_mailbox_is_not_used(db, auth_name):
    from src.services.email_service import resolve_auth_sender
    mine = _make_company(db, "Mine")
    theirs = _make_company(db, "Theirs")
    _make_mailbox(db, theirs, auth_name, user="auth@theirs.com")

    assert resolve_auth_sender(db, mine) is None


def test_no_company_bound_resolves_to_env(db):
    from src.services.email_service import resolve_auth_sender
    assert resolve_auth_sender(db, None) is None


# ─────────────────────────────────────────────────────────────────────────────
# Saying why — the fallback used to be completely silent
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kwargs,expected", [
    ({"password": None},   "no password set"),
    ({"is_active": False}, "mailbox is inactive"),
    ({"user": None},       "no SMTP user"),
])
def test_unusable_mailbox_falls_back_and_logs_why(db, caplog, auth_name,
                                                  kwargs, expected):
    """A mailbox that exists but cannot send is a misconfiguration; say so.

    The password branch is the one that bit us: seed_innomax_mailboxes.py inserts
    smtp_pass=None deliberately, and nothing anywhere said the mailbox was inert.
    """
    from src.services.email_service import resolve_auth_sender
    cid = _make_company(db)
    _make_mailbox(db, cid, auth_name, **kwargs)

    caplog.set_level(logging.WARNING, logger=_EMAIL_LOGGER)
    assert resolve_auth_sender(db, cid) is None

    logged = _warnings(caplog)
    assert expected in logged
    assert "Falling back to the default sender" in logged


def test_inactive_is_not_reported_as_missing(db, caplog, auth_name):
    """is_active used to be in the WHERE clause, which turned a disabled mailbox into
    "no such mailbox" and hid the one switch the admin needed to flip."""
    from src.services.email_service import resolve_auth_sender
    cid = _make_company(db)
    _make_mailbox(db, cid, auth_name, is_active=False)

    caplog.set_level(logging.WARNING, logger=_EMAIL_LOGGER)
    resolve_auth_sender(db, cid)

    logged = _warnings(caplog)
    assert "mailbox is inactive" in logged
    assert "no such mailbox" not in logged


def test_absent_mailbox_logs_nothing(db, caplog, auth_name):
    """A company that never created one is not misconfigured. Warning on every send
    would train everyone to ignore the line that matters."""
    from src.services.email_service import resolve_auth_sender
    cid = _make_company(db)

    caplog.set_level(logging.WARNING, logger=_EMAIL_LOGGER)
    resolve_auth_sender(db, cid)

    assert _warnings(caplog) == ""


def test_usable_mailbox_logs_nothing(db, caplog, auth_name):
    from src.services.email_service import resolve_auth_sender
    cid = _make_company(db)
    _make_mailbox(db, cid, auth_name)

    caplog.set_level(logging.WARNING, logger=_EMAIL_LOGGER)
    resolve_auth_sender(db, cid)

    assert _warnings(caplog) == ""


# ─────────────────────────────────────────────────────────────────────────────
# All three call sites share the chain
# ─────────────────────────────────────────────────────────────────────────────

def test_every_auth_call_site_uses_the_shared_resolver():
    """They each inlined this and drifted: src/auth.py fell back to the company
    override while auth_routes.py used resolve_sender(), whose default
    category="system" returns None for the platform company without consulting
    tenant_configs — so one user could get two codes from two addresses."""
    import pathlib
    import re

    backend = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("src/auth.py", "routes/auth_routes.py"):
        source = (backend / rel).read_text(encoding="utf-8")
        assert "resolve_auth_sender" in source, f"{rel} must use the shared resolver"
        assert not re.search(r"resolve_company_mailbox\(.*AUTH_MAILBOX_NAME", source), (
            f"{rel} re-inlines the auth chain — call resolve_auth_sender instead"
        )
