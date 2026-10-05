"""
HR "send from" mailbox picker — GET /hr/mail-preview and the mailbox_id override.

A "mailbox" is now an email API sender with its connection; a stage's mailbox
is the step's email route; the company default is the __default__ route.

Covers:
- mailbox_smtp_config refuses a mailbox belonging to another company
  (mailbox_id arrives from the client, so ownership must be enforced) — the
  decision is "blocked", never another tenant's sender
- resolve_step_sender precedence: HR override > stage mailbox > company default,
  and an unusable override BLOCKS instead of quietly switching sender
- /hr/mail-preview reports the stage default, resolved From (or the block
  reason), and the selectable list
- No credentials in the preview response
"""
import json
import uuid
import pytest

from src.email_api import constants as C
from test.email_factories import make_route, make_smtp_sender


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_company(db, name="Picker Co") -> str:
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c.id


def _create_user(db, email, role="hr", company_id=None, password="Pass@1234"):
    from src.models.models import User
    from src.auth import get_password_hash
    u = User(
        id=str(uuid.uuid4()), email=email,
        password_hash=get_password_hash(password), role=role,
        first_name=role.upper(), last_name="Tester",
        is_active=True, email_verified=True, company_id=company_id,
    )
    db.add(u)
    db.flush()
    return u.id


def _login(client, email, password="Pass@1234"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mailbox(db, company_id, name, *, user="box@acme.com", password="pw", is_active=True):
    sender, _ = make_smtp_sender(db, company_id, name, email=user, password=password,
                                 enabled=is_active)
    return sender


def _step(db, company_id, step_key, mailbox_id=None, extra_emails=None):
    """CC lives in the step config; the stage's mailbox is the step's route."""
    from src.models.models import PipelineStepConfig
    row = PipelineStepConfig(
        id=str(uuid.uuid4()), company_id=company_id, step_key=step_key,
        enabled=True, extra_emails=json.dumps(extra_emails or []),
        notify_roles=json.dumps([]), as_cc=True,
    )
    db.add(row)
    db.flush()
    if mailbox_id:
        make_route(db, company_id, step_key, mailbox_id)
    return row


def _tenant_smtp(db, company_id, user="default@acme.com", password="tenant-pw"):
    sender, conn = make_smtp_sender(db, company_id, "Company default", email=user,
                                    password=password)
    make_route(db, company_id, C.DEFAULT_ROUTE, sender.id)
    return conn


@pytest.fixture()
def hr_with_company(client, db):
    cid = _make_company(db, "HR Picker Co")
    email = f"hr_{uuid.uuid4().hex[:8]}@test.com"
    _create_user(db, email, role="hr", company_id=cid)
    return _login(client, email), cid


# ─────────────────────────────────────────────────────────────────────────────
# Ownership — the security-critical one
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
def test_override_rejects_other_company_mailbox(db):
    """An HR user must not be able to send using another tenant's mailbox."""
    from src.services.pipeline_notifications import mailbox_smtp_config
    cid_a = _make_company(db, "Company A")
    cid_b = _make_company(db, "Company B")
    mb_a = _mailbox(db, cid_a, "A Box", user="a@companyA.com")

    # B asking for A's mailbox id is blocked, and learns nothing about it.
    theirs = mailbox_smtp_config(db, cid_b, mb_a.id)
    assert theirs.decision.status == "blocked"
    assert theirs.decision.reason_code == C.R_SENDER_NOT_FOUND
    assert theirs["user"] is None and "a@companyA.com" not in repr(dict(theirs))
    # A asking for its own gets it.
    own = mailbox_smtp_config(db, cid_a, mb_a.id)
    assert own.decision.status == "ok" and own["user"] == "a@companyA.com"


@pytest.mark.security
def test_override_ignores_blank_and_blocks_unknown_mailbox(db):
    from src.services.pipeline_notifications import mailbox_smtp_config
    cid = _make_company(db, "Blank Co")
    assert mailbox_smtp_config(db, cid, None) is None
    assert mailbox_smtp_config(db, cid, "") is None
    assert mailbox_smtp_config(db, None, str(uuid.uuid4())) is None
    unknown = mailbox_smtp_config(db, cid, str(uuid.uuid4()))
    assert unknown.decision.reason_code == C.R_SENDER_NOT_FOUND


@pytest.mark.security
def test_override_blocks_unusable_mailbox(db):
    """A passwordless mailbox must not be used just because HR picked it."""
    from src.services.pipeline_notifications import mailbox_smtp_config
    cid = _make_company(db, "Nopass Co")
    mb = _mailbox(db, cid, "No Pass", password=None)
    ref = mailbox_smtp_config(db, cid, mb.id)
    assert ref.decision.status == "blocked" and ref["from_email"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Precedence
# ─────────────────────────────────────────────────────────────────────────────

def test_override_beats_stage_mailbox(db):
    from src.services.pipeline_notifications import resolve_step_sender
    cid = _make_company(db, "Precedence Co")
    stage_box = _mailbox(db, cid, "Stage", user="stage@acme.com")
    chosen    = _mailbox(db, cid, "Chosen", user="chosen@acme.com")
    _step(db, cid, "offer_letter", mailbox_id=stage_box.id)

    smtp, _ = resolve_step_sender(db, cid, "offer_letter", chosen.id)
    assert smtp["user"] == "chosen@acme.com"
    assert smtp.decision.route_source == C.SRC_REQUESTED


def test_stage_mailbox_used_when_no_override(db):
    from src.services.pipeline_notifications import resolve_step_sender
    cid = _make_company(db, "Stage Default Co")
    stage_box = _mailbox(db, cid, "Stage", user="stage@acme.com")
    _step(db, cid, "offer_letter", mailbox_id=stage_box.id)

    smtp, _ = resolve_step_sender(db, cid, "offer_letter", None)
    assert smtp["user"] == "stage@acme.com"


def test_company_default_when_no_stage_mailbox(db):
    from src.services.pipeline_notifications import resolve_step_sender
    cid = _make_company(db, "Company Default Co")
    _tenant_smtp(db, cid, user="default@acme.com")

    smtp, _ = resolve_step_sender(db, cid, "offer_letter", None)
    assert smtp["user"] == "default@acme.com"


def test_unusable_override_blocks_instead_of_switching_sender(db):
    """Picking a passwordless mailbox blocks the email. It used to fall back to
    the stage mailbox — a different From than HR chose, without telling them."""
    from src.services.pipeline_notifications import resolve_step_sender
    cid = _make_company(db, "Fallback Co")
    stage_box = _mailbox(db, cid, "Stage", user="stage@acme.com")
    broken    = _mailbox(db, cid, "Broken", password=None)
    _step(db, cid, "offer_letter", mailbox_id=stage_box.id)

    smtp, _ = resolve_step_sender(db, cid, "offer_letter", broken.id)
    assert smtp.decision.status == "blocked"
    assert smtp["user"] is None


def test_cc_still_returned_alongside_sender(db):
    from src.services.pipeline_notifications import resolve_step_sender
    cid = _make_company(db, "CC Co")
    _step(db, cid, "offer_letter", extra_emails=["ops@acme.com"])
    _, disp = resolve_step_sender(db, cid, "offer_letter", None)
    assert disp["cc"] == ["ops@acme.com"]


# ─────────────────────────────────────────────────────────────────────────────
# /hr/mail-preview
# ─────────────────────────────────────────────────────────────────────────────

def _preview(client, headers, step_key="offer_letter"):
    r = client.get("/api/v1/hr/mail-preview", headers=headers,
                   params={"step_key": step_key})
    assert r.status_code == 200, r.text
    return r.json()


def test_preview_reports_stage_mailbox_as_default(client, db, hr_with_company):
    headers, cid = hr_with_company
    mb = _mailbox(db, cid, "Offers", user="offers@acme.com")
    _step(db, cid, "offer_letter", mailbox_id=mb.id, extra_emails=["ops@acme.com"])

    p = _preview(client, headers)

    assert p["default_mailbox_id"] == mb.id
    assert p["from_email"] == "offers@acme.com"
    assert p["source"] == "step_mailbox"
    assert p["source_label"] == "Offers"
    assert p["cc"] == ["ops@acme.com"]
    assert p["fallback_reason"] is None


def test_preview_falls_back_to_company_default(client, db, hr_with_company):
    headers, cid = hr_with_company
    _tenant_smtp(db, cid, user="default@acme.com")

    p = _preview(client, headers)

    assert p["default_mailbox_id"] is None
    assert p["source"] == "company_default"
    assert p["from_email"] == "default@acme.com"


def test_preview_explains_unusable_stage_mailbox(client, db, hr_with_company):
    """A stage mailbox that cannot send blocks the stage's mail — the preview
    names the mailbox and says why, instead of showing the default as From."""
    headers, cid = hr_with_company
    _tenant_smtp(db, cid, user="default@acme.com")
    mb = _mailbox(db, cid, "Offers", password=None)
    _step(db, cid, "offer_letter", mailbox_id=mb.id)

    p = _preview(client, headers)

    assert "no password" in p["fallback_reason"]
    assert p["source"] == "blocked"
    assert p["default_mailbox_id"] == mb.id
    assert p["from_email"] == ""


def test_preview_reports_unconfigured_company_as_blocked(client, db, hr_with_company):
    headers, _ = hr_with_company
    p = _preview(client, headers)
    assert p["source"] == "blocked" and p["from_email"] == ""
    assert p["fallback_reason"]


def test_preview_reports_disabled_step(client, db, hr_with_company):
    headers, cid = hr_with_company
    _tenant_smtp(db, cid, user="default@acme.com")
    make_route(db, cid, "offer_letter", enabled=False)

    p = _preview(client, headers)

    assert p["source"] == "disabled" and p["enabled"] is False


def test_preview_lists_selectable_mailboxes_only_for_this_company(client, db, hr_with_company):
    headers, cid = hr_with_company
    _mailbox(db, cid, "Mine", user="mine@acme.com")
    _mailbox(db, cid, "Inactive", user="old@acme.com", is_active=False)
    other = _make_company(db, "Other Co")
    _mailbox(db, other, "Theirs", user="theirs@other.com")

    p = _preview(client, headers)
    names = {m["name"] for m in p["mailboxes"]}

    assert names == {"Mine"}                      # enabled + own company only
    assert "theirs@other.com" not in json.dumps(p)


def test_preview_flags_missing_password_in_list(client, db, hr_with_company):
    headers, cid = hr_with_company
    _mailbox(db, cid, "Broken", password=None)
    p = _preview(client, headers)
    assert p["mailboxes"][0]["has_password"] is False
    assert p["mailboxes"][0]["usable"] is False


@pytest.mark.security
def test_preview_never_returns_credentials(client, db, hr_with_company):
    headers, cid = hr_with_company
    tc = _tenant_smtp(db, cid, user="default@acme.com")
    mb, mb_conn = make_smtp_sender(db, cid, "Offers", email="offers@acme.com",
                                   password="mailbox-secret-123")
    _step(db, cid, "offer_letter", mailbox_id=mb.id)

    raw = json.dumps(_preview(client, headers))

    assert "mailbox-secret-123" not in raw
    assert "tenant-pw" not in raw
    assert mb_conn.credential_enc not in raw
    assert tc.credential_enc not in raw


def test_preview_rejects_unknown_step(client, hr_with_company):
    headers, _ = hr_with_company
    r = client.get("/api/v1/hr/mail-preview", headers=headers,
                   params={"step_key": "not_a_step"})
    assert r.status_code == 400


@pytest.mark.security
def test_preview_requires_hr_or_admin(client, db):
    cid = _make_company(db, "Cand Co")
    email = f"cand_{uuid.uuid4().hex[:6]}@test.com"
    _create_user(db, email, role="candidate", company_id=cid)
    r = client.get("/api/v1/hr/mail-preview", headers=_login(client, email),
                   params={"step_key": "offer_letter"})
    assert r.status_code == 403
