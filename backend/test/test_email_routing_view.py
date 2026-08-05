"""
Admin mail-routing view tests — GET /api/v1/admin/email/routing.

Covers:
- No SMTP password (plaintext OR stored ciphertext) ever reaches the response
- Every mailbox_unusable_reason branch surfaces as a step fallback_reason
- A usable mailbox is reported as the step's sender
- Cross-tenant isolation
- delivery.mode reflects queued vs inline
- Disabled steps are reported but NOT as "off" (the HR toggle is not enforced)
"""
import json
import uuid
import pytest
from unittest.mock import patch

from src.security import encrypt


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


def _make_mailbox(db, company_id: str, name: str = "Offers", *,
                  user="offers@acme.com", password="mailbox-secret",
                  is_active=True, hourly_limit=None):
    """Create a CompanyMailbox. `password=None` leaves smtp_pass unset."""
    from src.models.models import CompanyMailbox
    mb = CompanyMailbox(
        id=str(uuid.uuid4()),
        company_id=company_id,
        name=name,
        smtp_host="smtp.acme.com",
        smtp_port=587,
        smtp_user=user,
        smtp_pass=encrypt(password) if password else None,
        smtp_from_email=user,
        hourly_limit=hourly_limit,
        is_active=is_active,
    )
    db.add(mb)
    db.flush()
    return mb


def _map_step(db, company_id: str, step_key: str, mailbox_id=None, *,
              enabled=True, extra_emails=None):
    from src.models.models import PipelineStepConfig
    row = PipelineStepConfig(
        id=str(uuid.uuid4()),
        company_id=company_id,
        step_key=step_key,
        enabled=enabled,
        extra_emails=json.dumps(extra_emails or []),
        notify_roles=json.dumps([]),
        as_cc=True,
        mailbox_id=mailbox_id,
    )
    db.add(row)
    db.flush()
    return row


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


# ─────────────────────────────────────────────────────────────────────────────
# The non-negotiable one: no credentials in the response
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.admin
def test_routing_never_leaks_passwords(client, db, admin_with_company):
    """Neither the plaintext password nor the stored ciphertext may appear.

    Both the tenant_configs override and a CompanyMailbox hold encrypted
    credentials, and this endpoint reads both.
    """
    from src.models.models import TenantConfig
    headers, cid = admin_with_company

    tenant_plain  = "tenant-app-password-XYZ"
    mailbox_plain = "mailbox-app-password-ABC"

    tc = TenantConfig(
        id=str(uuid.uuid4()), company_id=cid,
        smtp_host="smtp.gmail.com", smtp_port=587,
        smtp_user="hr@acme.com", smtp_pass=encrypt(tenant_plain),
        smtp_from_email="hr@acme.com",
    )
    db.add(tc)
    mb = _make_mailbox(db, cid, "Offers", password=mailbox_plain)
    _map_step(db, cid, "offer_letter", mailbox_id=mb.id)
    db.flush()

    payload = _routing(client, headers)
    raw = json.dumps(payload)

    assert tenant_plain not in raw
    assert mailbox_plain not in raw
    assert tc.smtp_pass not in raw, "stored tenant ciphertext leaked"
    assert mb.smtp_pass not in raw, "stored mailbox ciphertext leaked"

    # No credential-bearing key anywhere in the tree. Checked structurally rather
    # than by substring: the "OTP & password reset" label legitimately contains
    # the word, and _public_sender allow-lists keys so a new smtp_config field
    # must not start leaking silently.
    banned = {"password", "smtp_pass", "password_enc", "pass"}
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
def test_usable_mailbox_is_reported_for_step(client, db, admin_with_company):
    headers, cid = admin_with_company
    mb = _make_mailbox(db, cid, "Offers", user="offers@acme.com", hourly_limit=250)
    _map_step(db, cid, "offer_letter", mailbox_id=mb.id)

    step = _step(_routing(client, headers), "offer_letter")

    assert step["fallback_reason"] is None
    assert step["mailbox"]["name"] == "Offers"
    assert step["mailbox"]["usable"] is True
    assert step["mailbox"]["from_email"] == "offers@acme.com"
    assert step["mailbox"]["hourly_limit"] == 250


@pytest.mark.admin
@pytest.mark.parametrize("kwargs,expected", [
    ({"password": None},              "no password set"),
    ({"is_active": False},            "mailbox is inactive"),
    ({"user": None},                  "no SMTP user"),
])
def test_fallback_reason_branches(client, db, admin_with_company, kwargs, expected):
    """Each unusable-mailbox branch must surface to the admin, not just the log."""
    headers, cid = admin_with_company
    mb = _make_mailbox(db, cid, "Interviews", **kwargs)
    _map_step(db, cid, "interview_scheduled", mailbox_id=mb.id)

    step = _step(_routing(client, headers), "interview_scheduled")

    assert step["fallback_reason"] is not None
    assert expected in step["fallback_reason"]
    # Named even when unusable — "Interviews (no password)" beats a bare reason.
    assert step["mailbox"]["name"] == "Interviews"
    assert step["mailbox"]["usable"] is False


@pytest.mark.admin
def test_missing_mailbox_reason_is_reported():
    """A dangling mailbox_id can't be inserted (FK), so cover the branch directly.

    It is still reachable at runtime: the row is read in a separate query from the
    step row, so a concurrent delete lands here.
    """
    from src.services.pipeline_notifications import mailbox_unusable_reason
    assert mailbox_unusable_reason(None) == "no such mailbox"


@pytest.mark.admin
def test_unconfigured_steps_default_cleanly(client, admin_with_company):
    """A company with no step rows still gets one entry per step, all defaulted."""
    from src.services.pipeline_notifications import STEP_KEYS
    headers, _ = admin_with_company

    payload = _routing(client, headers)

    assert [s["step_key"] for s in payload["steps"]] == list(STEP_KEYS)
    for s in payload["steps"]:
        assert s["enabled"] is True
        assert s["mailbox"] is None
        assert s["fallback_reason"] is None
        assert s["cc"] == []


@pytest.mark.admin
def test_disabled_step_reports_enabled_false(client, db, admin_with_company):
    """The UI needs this flag to say 'not yet enforced' rather than 'off'."""
    headers, cid = admin_with_company
    _map_step(db, cid, "candidate_rejected", enabled=False)

    step = _step(_routing(client, headers), "candidate_rejected")
    assert step["enabled"] is False


@pytest.mark.admin
def test_step_cc_is_cleaned(client, db, admin_with_company):
    """CC must match what the real resolver applies: valid, de-duped, ordered."""
    headers, cid = admin_with_company
    _map_step(db, cid, "offer_letter",
              extra_emails=["ops@acme.com", "not-an-email", "OPS@acme.com", "vp@acme.com"])

    step = _step(_routing(client, headers), "offer_letter")
    assert step["cc"] == ["ops@acme.com", "vp@acme.com"]


# ─────────────────────────────────────────────────────────────────────────────
# Sender chain
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_chain_marks_company_override_active_when_configured(client, db, admin_with_company):
    from src.models.models import TenantConfig
    headers, cid = admin_with_company
    db.add(TenantConfig(
        id=str(uuid.uuid4()), company_id=cid,
        smtp_host="smtp.gmail.com", smtp_port=587,
        smtp_user="hr@acme.com", smtp_pass=encrypt("pw"),
        smtp_from_email="hr@acme.com",
    ))
    db.flush()

    chain = {c["layer"]: c for c in _routing(client, headers)["chain"]}

    assert chain["company_override"]["configured"] is True
    assert chain["company_override"]["active"] is True
    assert chain["company_override"]["sender"]["from_email"] == "hr@acme.com"
    assert chain["env_default"]["active"] is False


@pytest.mark.admin
def test_chain_falls_through_to_env_when_unconfigured(client, admin_with_company):
    headers, _ = admin_with_company
    chain = {c["layer"]: c for c in _routing(client, headers)["chain"]}

    assert chain["company_override"]["configured"] is False
    assert chain["env_default"]["active"] is True
    # A client company is never routed through the platform's Innomax mailboxes.
    assert chain["platform_category"]["configured"] is False


@pytest.mark.admin
def test_chain_counts_usable_step_mailboxes(client, db, admin_with_company):
    headers, cid = admin_with_company
    good = _make_mailbox(db, cid, "Good")
    bad  = _make_mailbox(db, cid, "Bad", password=None)
    _map_step(db, cid, "offer_letter", mailbox_id=good.id)
    _map_step(db, cid, "interview_scheduled", mailbox_id=bad.id)

    chain = {c["layer"]: c for c in _routing(client, headers)["chain"]}

    assert chain["step_mailbox"]["configured"] is True
    assert "1 of 2" in chain["step_mailbox"]["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# Delivery mode
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_delivery_mode_inline_when_redis_absent(client, admin_with_company):
    headers, _ = admin_with_company
    with patch("src.mailq.queue.redis_available", return_value=False), \
         patch("src.mailq.queue._sync_forced", return_value=False):
        delivery = _routing(client, headers)["delivery"]

    assert delivery["mode"] == "inline"
    assert delivery["redis_available"] is False
    assert delivery["queue_depth"] is None


@pytest.mark.admin
def test_delivery_mode_queued_reports_depth(client, admin_with_company):
    headers, _ = admin_with_company

    class _FakeRedis:
        def llen(self, key):
            return 7

    with patch("src.mailq.queue.redis_available", return_value=True), \
         patch("src.mailq.queue._sync_forced", return_value=False), \
         patch("src.mailq.queue.get_redis", return_value=_FakeRedis()):
        delivery = _routing(client, headers)["delivery"]

    assert delivery["mode"] == "queued"
    assert delivery["queue_depth"] == 7


@pytest.mark.admin
def test_delivery_mode_inline_when_sync_forced(client, admin_with_company):
    """EMAIL_SYNC beats a live Redis — mail sends in-request either way."""
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

    assert payload["delivery"]["mode"] == "inline"
    assert payload["delivery"]["queue_depth"] is None
    assert len(payload["steps"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Authorization / isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.admin
def test_cross_tenant_isolation(client, db):
    """Company B's admin must not see company A's mailboxes or sender."""
    from src.models.models import TenantConfig
    cid_a = _make_company(db, "Company A")
    cid_b = _make_company(db, "Company B")

    db.add(TenantConfig(
        id=str(uuid.uuid4()), company_id=cid_a,
        smtp_host="smtp.gmail.com", smtp_port=587,
        smtp_user="secret-a@companyA.com", smtp_pass=encrypt("pw-a"),
        smtp_from_email="secret-a@companyA.com",
    ))
    mb_a = _make_mailbox(db, cid_a, "A Offers", user="offers@companyA.com")
    _map_step(db, cid_a, "offer_letter", mailbox_id=mb_a.id)

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
