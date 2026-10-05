"""
Admin mail-routing view tests — GET /api/v1/admin/email/routing.

Covers:
- No password (plaintext OR stored ciphertext) ever reaches the response
- Each pipeline step reports its routed sender and its effective decision;
  a sender that cannot send is reported as a *blocked* step, with the reason
- Switched-off steps are reported as disabled (and are now enforced)
- The policy layers ("chain"), including the explicit platform route for
  verification codes; the environment is reported as never used
- delivery: queue mode, outbox backlog and recent outcomes
- Cross-tenant isolation and access control
"""
import json
import logging
import uuid
from unittest.mock import patch

import pytest

from src.email_api import constants as C
from src.models.models import EmailConnection
from src.tenancy.platform_company import ensure_platform_company
from test.email_factories import make_route, make_smtp_sender, queue_message


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _create_admin_directly(db, email: str, password: str = "Pass@1234",
                           company_id: str = None) -> str:
    from src.models.models import User
    from src.auth import get_password_hash
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=get_password_hash(password),
        role="admin",
        first_name="Admin",
        last_name="Tester",
        is_active=True,
        email_verified=True,
        company_id=company_id,
    )
    db.add(user)
    db.flush()
    return user.id


def _login(client, email: str, password: str = "Pass@1234") -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_company(db, name: str = "Routing Co") -> str:
    from src.models.models import Company
    company = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(company)
    db.flush()
    return company.id


def _sender(db, company_id, label="Offers", *, email="offers@acme.com", password="mailbox-secret",
            enabled=True, hourly_limit=None, conn_status=C.CONN_ACTIVE):
    sender, _ = make_smtp_sender(db, company_id, label, email=email, password=password,
                                 enabled=enabled, hourly_limit=hourly_limit,
                                 conn_status=conn_status)
    return sender


def _cc_row(db, company_id: str, step_key: str, extra_emails):
    from src.models.models import PipelineStepConfig
    row = PipelineStepConfig(
        id=str(uuid.uuid4()), company_id=company_id, step_key=step_key, enabled=True,
        extra_emails=json.dumps(extra_emails), notify_roles=json.dumps([]), as_cc=True,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr("src.mailq.queue.redis_available", lambda: False)


@pytest.fixture()
def admin_with_company(client, db):
    """Admin tied to a company. Returns (headers, company_id)."""
    cid = _make_company(db, name="Routing Admin Co")
    email = f"routing_admin_{uuid.uuid4().hex[:8]}@test.com"
    _create_admin_directly(db, email, company_id=cid)
    return _login(client, email), cid


def _routing(client, headers):
    resp = client.get("/api/v1/admin/email/routing", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _step(payload, step_key):
    return next(s for s in payload["steps"] if s["step_key"] == step_key)


def _chain(payload):
    return {c["layer"]: c for c in payload["chain"]}


# ─────────────────────────────────────────────────────────────────────────────
# The non-negotiable one: no credentials in the response
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.admin
def test_routing_never_leaks_passwords(client, db, admin_with_company):
    """Neither the plaintext password nor the stored ciphertext may appear."""
    headers, cid = admin_with_company

    tenant_plain  = "tenant-app-password-XYZ"
    mailbox_plain = "mailbox-app-password-ABC"
    default = _sender(db, cid, "Company default", email="hr@acme.com", password=tenant_plain)
    make_route(db, cid, C.DEFAULT_ROUTE, default.id)
    offers = _sender(db, cid, "Offers", password=mailbox_plain)
    make_route(db, cid, "offer_letter", offers.id)

    payload = _routing(client, headers)
    raw = json.dumps(payload)

    assert tenant_plain not in raw
    assert mailbox_plain not in raw
    for conn in db.query(EmailConnection).filter_by(company_id=cid):
        assert conn.credential_enc not in raw, "stored ciphertext leaked"

    # No credential-bearing key anywhere in the tree. Checked structurally rather
    # than by substring: labels legitimately contain the word "password".
    banned = {"password", "smtp_pass", "password_enc", "pass", "credential_enc", "secret"}

    def _walk(node, path="$"):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k.lower() not in banned, f"credential key {k!r} at {path}"
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
    _walk(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Per-step resolution
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_usable_sender_is_reported_for_step(client, db, admin_with_company):
    headers, cid = admin_with_company
    offers = _sender(db, cid, "Offers", email="offers@acme.com", hourly_limit=250)
    make_route(db, cid, "offer_letter", offers.id)

    step = _step(_routing(client, headers), "offer_letter")

    assert step["fallback_reason"] is None
    assert step["mailbox"]["name"] == "Offers"
    assert step["mailbox"]["usable"] is True
    assert step["mailbox"]["from_email"] == "offers@acme.com"
    assert step["mailbox"]["hourly_limit"] == 250
    assert step["effective"]["status"] == "ok"
    assert step["effective"]["route_source"] == C.SRC_ROUTE
    assert step["effective"]["from_email"] == "offers@acme.com"


@pytest.mark.admin
@pytest.mark.parametrize("kwargs,expected", [
    ({"password": None},                 "password"),
    ({"enabled": False},                 "disabled"),
    ({"conn_status": C.CONN_FAILED},     "failed"),
])
def test_unusable_step_sender_blocks_and_says_why(client, db, admin_with_company,
                                                  kwargs, expected):
    """A step whose sender cannot send is blocked — and the admin sees why.

    It used to fall back silently to the company default; even with a working
    default configured, nothing is now re-routed.
    """
    headers, cid = admin_with_company
    default = _sender(db, cid, "Default", email="hr@acme.com")
    make_route(db, cid, C.DEFAULT_ROUTE, default.id)
    broken = _sender(db, cid, "Interviews", email="interviews@acme.com", **kwargs)
    make_route(db, cid, "interview_scheduled", broken.id)

    step = _step(_routing(client, headers), "interview_scheduled")

    assert step["effective"]["status"] == "blocked"
    assert step["effective"]["from_email"] is None
    assert step["fallback_reason"] is not None
    assert expected in step["fallback_reason"].lower()
    # Named even when unusable — "Interviews (no password)" beats a bare reason.
    assert step["mailbox"]["name"] == "Interviews"
    assert step["mailbox"]["usable"] is False


@pytest.mark.admin
def test_unconfigured_company_reports_every_step_blocked(client, admin_with_company):
    """No sender at all: each step is listed, and each says mail is blocked."""
    from src.services.pipeline_notifications import STEP_KEYS
    headers, _ = admin_with_company

    payload = _routing(client, headers)

    assert [s["step_key"] for s in payload["steps"]] == list(STEP_KEYS)
    for s in payload["steps"]:
        assert s["enabled"] is True
        assert s["mailbox"] is None
        assert s["cc"] == []
        assert s["effective"]["status"] == "blocked"
        assert s["effective"]["reason_code"] == C.R_NO_SENDER
        assert s["fallback_reason"]


@pytest.mark.admin
def test_steps_use_the_company_default(client, db, admin_with_company):
    headers, cid = admin_with_company
    default = _sender(db, cid, "Default", email="hr@acme.com")
    make_route(db, cid, C.DEFAULT_ROUTE, default.id)

    step = _step(_routing(client, headers), "application_received")
    assert step["effective"]["status"] == "ok"
    assert step["effective"]["route_source"] == C.SRC_DEFAULT
    assert step["effective"]["from_email"] == "hr@acme.com"
    assert step["mailbox"] is None and step["fallback_reason"] is None


@pytest.mark.admin
def test_disabled_step_reports_enabled_false(client, db, admin_with_company):
    """Switched-off steps are now enforced: nothing is sent for them."""
    headers, cid = admin_with_company
    make_route(db, cid, "candidate_rejected", enabled=False)

    step = _step(_routing(client, headers), "candidate_rejected")
    assert step["enabled"] is False
    assert step["effective"]["status"] == "disabled"


@pytest.mark.admin
def test_step_cc_is_cleaned(client, db, admin_with_company):
    """CC must match what the real resolver applies: valid, de-duped, ordered."""
    headers, cid = admin_with_company
    _cc_row(db, cid, "offer_letter",
            ["ops@acme.com", "not-an-email", "OPS@acme.com", "vp@acme.com"])

    step = _step(_routing(client, headers), "offer_letter")
    assert step["cc"] == ["ops@acme.com", "vp@acme.com"]


# ─────────────────────────────────────────────────────────────────────────────
# Policy layers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_chain_marks_company_default_active_when_configured(client, db, admin_with_company):
    headers, cid = admin_with_company
    default = _sender(db, cid, "Default", email="hr@acme.com")
    make_route(db, cid, C.DEFAULT_ROUTE, default.id)

    chain = _chain(_routing(client, headers))

    assert chain["tenant_default"]["configured"] is True
    assert chain["tenant_default"]["active"] is True
    assert chain["tenant_default"]["sender"]["from_email"] == "hr@acme.com"
    assert chain["environment"]["active"] is False


@pytest.mark.admin
def test_chain_says_mail_is_blocked_when_unconfigured(client, admin_with_company):
    """There is no environment fallback to fall through to any more."""
    headers, _ = admin_with_company
    chain = _chain(_routing(client, headers))

    assert chain["tenant_default"]["configured"] is False
    assert chain["tenant_default"]["active"] is False
    assert "blocked" in chain["tenant_default"]["detail"].lower()
    assert chain["environment"]["configured"] is False
    assert chain["environment"]["active"] is False
    assert "never used" in chain["environment"]["detail"].lower()


@pytest.mark.admin
def test_chain_names_why_the_default_cannot_send(client, db, admin_with_company):
    headers, cid = admin_with_company
    default = _sender(db, cid, "Default", email="hr@acme.com", password=None)
    make_route(db, cid, C.DEFAULT_ROUTE, default.id)

    layer = _chain(_routing(client, headers))["tenant_default"]
    assert layer["configured"] is True and layer["active"] is False
    assert "password" in layer["detail"].lower()


@pytest.mark.admin
def test_platform_auth_route_is_shown_for_codes_only(client, db, admin_with_company):
    """A company with no sender gets verification codes from the platform's
    explicit auth route — and only those; other mail stays blocked."""
    headers, cid = admin_with_company
    platform = ensure_platform_company(db)
    p_sender = _sender(db, platform.id, "Platform Auth", email="auth@platform.example")
    make_route(db, platform.id, C.EVENT_AUTH_VERIFY, p_sender.id)

    payload = _routing(client, headers)
    layer = _chain(payload)["platform_auth_route"]
    assert layer["configured"] is True and layer["active"] is True
    assert layer["sender"]["from_email"] == "auth@platform.example"

    events = {e["event_type"]: e for e in payload["events"]}
    assert events[C.EVENT_AUTH_VERIFY]["route_source"] == C.SRC_PLATFORM_AUTH
    assert events["offer_letter"]["status"] == "blocked"
    # A client company never sees (or sends) the platform's own events.
    assert not any(k.startswith(("platform.", "support.")) for k in events)


@pytest.mark.admin
def test_company_auth_sender_wins_over_platform_route(client, db, admin_with_company):
    headers, cid = admin_with_company
    platform = ensure_platform_company(db)
    p_sender = _sender(db, platform.id, "Platform Auth", email="auth@platform.example")
    make_route(db, platform.id, C.EVENT_AUTH_VERIFY, p_sender.id)
    own = _sender(db, cid, "Default", email="hr@acme.com")
    make_route(db, cid, C.DEFAULT_ROUTE, own.id)

    layer = _chain(_routing(client, headers))["platform_auth_route"]
    assert layer["configured"] is False and layer["active"] is False


@pytest.mark.admin
def test_routing_view_logs_no_warnings(client, db, admin_with_company, caplog):
    """This panel gets reloaded while somebody debugs exactly this — it must stay quiet.

    The reason belongs in the response, not in the log, for a read-only view.
    """
    headers, cid = admin_with_company
    broken = _sender(db, cid, "Broken", password=None)
    make_route(db, cid, "offer_letter", broken.id)

    caplog.set_level(logging.WARNING)
    _routing(client, headers)

    assert not [r for r in caplog.records
                if r.name.startswith(("src.services", "src.email_api"))
                and r.levelno >= logging.WARNING]


# ─────────────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_delivery_waits_in_outbox_when_redis_absent(client, admin_with_company):
    headers, _ = admin_with_company
    with patch("src.mailq.queue._sync_forced", return_value=False):
        delivery = _routing(client, headers)["delivery"]

    assert delivery["mode"] == "outbox_waiting"
    assert delivery["redis_available"] is False
    assert delivery["queue_depth"] is None
    assert delivery["dispatcher_heartbeat_age_s"] is None


@pytest.mark.admin
def test_delivery_mode_queued_reports_depth(client, admin_with_company):
    headers, _ = admin_with_company

    class _FakeRedis:
        def llen(self, key):
            return 7

        def get(self, key):
            return None

    with patch("src.mailq.queue.redis_available", return_value=True), \
         patch("src.mailq.queue._sync_forced", return_value=False), \
         patch("src.mailq.queue.get_redis", return_value=_FakeRedis()):
        delivery = _routing(client, headers)["delivery"]

    assert delivery["mode"] == "queued"
    assert delivery["queue_depth"] == 7


@pytest.mark.admin
def test_delivery_reports_sync_forced(client, admin_with_company):
    headers, _ = admin_with_company
    with patch("src.mailq.queue.redis_available", return_value=True), \
         patch("src.mailq.queue._sync_forced", return_value=True), \
         patch("src.mailq.queue.get_redis", return_value=None):
        delivery = _routing(client, headers)["delivery"]

    assert delivery["mode"] == "inline"
    assert delivery["sync_forced"] is True


@pytest.mark.admin
def test_queue_probe_failure_degrades_gracefully(client, admin_with_company):
    """A broken Redis must not 500 the whole diagnostics page."""
    headers, _ = admin_with_company
    with patch("src.mailq.queue.redis_available", side_effect=RuntimeError("boom")):
        payload = _routing(client, headers)

    assert payload["delivery"]["mode"] == "outbox_waiting"
    assert payload["delivery"]["queue_depth"] is None
    assert len(payload["steps"]) > 0


@pytest.mark.admin
def test_delivery_counts_blocked_and_pending_mail(client, db, admin_with_company):
    headers, cid = admin_with_company
    queue_message(db, cid)                                   # blocked: no sender
    default = _sender(db, cid, "Default", email="hr@acme.com")
    make_route(db, cid, C.DEFAULT_ROUTE, default.id)
    queue_message(db, cid, subject="queued")                 # waits in the outbox
    db.flush()

    outbox = _routing(client, headers)["delivery"]["outbox"]
    assert outbox["blocked_24h"] == 1
    assert outbox["pending"] >= 1
    assert outbox["oldest_age_s"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Authorization / isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.admin
def test_cross_tenant_isolation(client, db):
    """Company B's admin must not see company A's senders."""
    cid_a = _make_company(db, "Company A")
    cid_b = _make_company(db, "Company B")

    default_a = _sender(db, cid_a, "A Default", email="secret-a@companyA.com")
    make_route(db, cid_a, C.DEFAULT_ROUTE, default_a.id)
    offers_a = _sender(db, cid_a, "A Offers", email="offers@companyA.com")
    make_route(db, cid_a, "offer_letter", offers_a.id)

    email_b = f"admin_b_{uuid.uuid4().hex[:6]}@test.com"
    _create_admin_directly(db, email_b, company_id=cid_b)
    b_headers = _login(client, email_b)

    raw = json.dumps(_routing(client, b_headers))

    assert "companyA.com" not in raw
    assert "A Offers" not in raw


@pytest.mark.security
@pytest.mark.admin
def test_non_admin_is_rejected(client, db):
    """HR may send a test email, but must not read the routing diagnostics."""
    from src.models.models import User
    from src.auth import get_password_hash
    cid = _make_company(db, "HR Co")
    email = f"hr_{uuid.uuid4().hex[:6]}@test.com"
    db.add(User(
        id=str(uuid.uuid4()), email=email,
        password_hash=get_password_hash("Pass@1234"),
        role="hr", first_name="HR", last_name="Tester",
        is_active=True, email_verified=True, company_id=cid,
    ))
    db.flush()

    resp = client.get("/api/v1/admin/email/routing", headers=_login(client, email))
    assert resp.status_code == 403


@pytest.mark.admin
def test_admin_without_company_gets_400(client, db):
    email = f"admin_noco_{uuid.uuid4().hex[:6]}@test.com"
    _create_admin_directly(db, email, company_id=None)

    resp = client.get("/api/v1/admin/email/routing", headers=_login(client, email))
    assert resp.status_code == 400
