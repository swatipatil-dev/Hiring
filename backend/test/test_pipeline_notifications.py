"""The per-step pipeline notification resolver.

CC comes from ``pipeline_step_configs``; the sender and on/off switch come from
the email API's routes and sender policy. ``smtp_config`` is always a
``SenderRef`` — a decision (ok / blocked / disabled), never a credential — so
no call site can fall through to another sender.
"""
import json
import types
import uuid

from src.email_api import constants as C
from src.email_api.compat import SenderRef
from src.services import pipeline_notifications as pn
from test.email_factories import make_company, make_route, make_smtp_sender


def _row(**kw):
    """Build a fake PipelineStepConfig-like row (for the pure serialisers)."""
    defaults = dict(
        enabled=True, extra_emails=None, notify_roles=None, as_cc=True,
        mailbox_id=None, step_key="interview_scheduled",
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _cc_row(db, company_id, step_key, emails, notify_roles=None):
    from src.models.models import PipelineStepConfig
    db.add(PipelineStepConfig(id=str(uuid.uuid4()), company_id=company_id, step_key=step_key,
                              enabled=True, extra_emails=json.dumps(emails),
                              notify_roles=json.dumps(notify_roles or []), as_cc=True))
    db.flush()


def test_json_list_parsing():
    assert pn._json_list(None) == []
    assert pn._json_list("not json") == []
    assert pn._json_list('["a@x.com", " b@x.com "]') == ["a@x.com", "b@x.com"]
    assert pn._json_list('{"x":1}') == []  # not a list


def test_is_valid_step():
    assert pn.is_valid_step("interview_scheduled")
    assert not pn.is_valid_step("nope")


def test_disabled_step_returns_no_cc_and_a_disabled_decision(db):
    co = make_company(db, "Off Co")
    _cc_row(db, co.id, "interview_scheduled", ["x@x.com"])
    make_route(db, co.id, "interview_scheduled", enabled=False)
    out = pn.resolve_step_dispatch(db, co.id, "interview_scheduled")
    assert out["enabled"] is False and out["cc"] == []
    assert isinstance(out["smtp_config"], SenderRef)
    assert out["smtp_config"].decision.status == "disabled"


def test_unconfigured_company_is_blocked_not_defaulted(db):
    """No route and no default sender: blocked — never the environment's account."""
    co = make_company(db, "Bare Co")
    out = pn.resolve_step_dispatch(db, co.id, "interview_scheduled")
    ref = out["smtp_config"]
    assert out["enabled"] is True and out["cc"] == []
    assert ref.decision.status == "blocked" and ref.decision.reason_code == C.R_NO_SENDER
    assert ref["from_email"] is None and ref["host"] is None
    # Truthy on purpose: `disp["smtp_config"] or resolve_sender(...)` stops here.
    assert bool(ref) is True


def test_invalid_step_is_refused(db):
    co = make_company(db, "Invalid Co")
    out = pn.resolve_step_dispatch(db, co.id, "bogus_step")
    assert out["cc"] == []
    assert out["smtp_config"].decision.reason_code == C.R_EVENT_NOT_ALLOWED


def test_cc_is_only_typed_emails_deduped_and_validated(db):
    co = make_company(db, "CC Co")
    # notify_roles is present in the row but MUST be ignored (no role expansion).
    _cc_row(db, co.id, "interview_scheduled",
            ["typed@x.com", "DUP@x.com", "dup@x.com", "not-an-email", ""],
            notify_roles=["hr", "admin"])
    out = pn.resolve_step_dispatch(db, co.id, "interview_scheduled")
    assert out["enabled"] is True
    # "not-an-email"/blank dropped, DUP/dup collapsed, order preserved, NO role emails.
    assert out["cc"] == ["typed@x.com", "DUP@x.com"]


def test_role_helper_is_gone():
    # The role-expansion helper/constant must no longer exist.
    assert not hasattr(pn, "_role_emails")
    assert not hasattr(pn, "NOTIFY_ROLES")


def test_step_sender_is_used_and_carries_no_password(db):
    co = make_company(db, "Step Co")
    default, _ = make_smtp_sender(db, co.id, "Default", email="default@acme.com")
    offers, conn = make_smtp_sender(db, co.id, "Offers", email="offers@acme.com",
                                    password="step-secret-pw")
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    make_route(db, co.id, "interview_scheduled", offers.id)
    ref = pn.resolve_step_dispatch(db, co.id, "interview_scheduled")["smtp_config"]
    assert ref.decision.status == "ok" and ref.decision.route_source == C.SRC_ROUTE
    assert ref["from_email"] == "offers@acme.com" and ref["user"] == "offers@acme.com"
    assert (ref["host"], ref["port"]) == (conn.host, conn.port)
    assert ref["sender_id"] == offers.id
    assert "password" not in ref and "step-secret-pw" not in repr(dict(ref))


def test_no_step_sender_means_company_default(db):
    co = make_company(db, "Default Co")
    default, _ = make_smtp_sender(db, co.id, "Default", email="default@acme.com")
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    ref = pn.resolve_step_dispatch(db, co.id, "interview_scheduled")["smtp_config"]
    assert ref["from_email"] == "default@acme.com"
    assert ref.decision.route_source == C.SRC_DEFAULT


def test_broken_step_sender_blocks_instead_of_falling_back(db):
    co = make_company(db, "Broken Co")
    default, _ = make_smtp_sender(db, co.id, "Default", email="default@acme.com")
    broken, _ = make_smtp_sender(db, co.id, "Broken", email="broken@acme.com", password=None)
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    make_route(db, co.id, "interview_scheduled", broken.id)
    ref = pn.resolve_step_dispatch(db, co.id, "interview_scheduled")["smtp_config"]
    assert ref.decision.status == "blocked"
    assert ref.decision.reason_code == C.R_ROUTE_SENDER_UNAVAILABLE
    assert ref["from_email"] is None


def test_mailbox_config_helper_guards(db):
    co = make_company(db, "Guard Co")
    other = make_company(db, "Other Co")
    theirs, _ = make_smtp_sender(db, other.id, "Theirs", email="theirs@other.com")
    off, _ = make_smtp_sender(db, co.id, "Off", email="off@acme.com", enabled=False)
    nopw, _ = make_smtp_sender(db, co.id, "NoPw", email="nopw@acme.com", password=None)
    # No id → nothing chosen.
    assert pn.mailbox_smtp_config(db, co.id, None) is None
    assert pn.mailbox_smtp_config(db, None, theirs.id) is None
    # Foreign, disabled or credential-less → blocked, never used.
    for sid, code in [(theirs.id, C.R_SENDER_NOT_FOUND), (off.id, C.R_SENDER_DISABLED),
                      (nopw.id, C.R_CONNECTION_UNAVAILABLE)]:
        ref = pn.mailbox_smtp_config(db, co.id, sid)
        assert ref.decision.status == "blocked" and ref.decision.reason_code == code
        assert ref["from_email"] is None


def test_describe_step_dispatch_reports_effective_decision(db):
    co = make_company(db, "Describe Co")
    broken, _ = make_smtp_sender(db, co.id, "Broken", email="broken@acme.com", password=None)
    make_route(db, co.id, "offer_letter", broken.id)
    d = pn.describe_step_dispatch(db, co.id, "offer_letter")
    assert d["mailbox"]["id"] == broken.id and d["mailbox"]["usable"] is False
    assert d["effective"]["status"] == "blocked" and d["fallback_reason"]


def test_serialize_row_shape():
    row = _row(extra_emails='["a@x.com"]', notify_roles='["hr"]', mailbox_id="mb1")
    data = pn.serialize_row(row)
    assert "smtp_pass" not in data
    assert "notify_roles" not in data          # role-based CC removed from the API
    assert data["mailbox_id"] == "mb1"
    assert data["extra_emails"] == ["a@x.com"]


def test_default_step_payload_shape():
    data = pn.default_step_payload("offer_letter")
    assert "notify_roles" not in data
    assert data["mailbox_id"] is None
    assert data["label"] == pn.STEP_LABELS["offer_letter"]
    assert data["enabled"] is True
