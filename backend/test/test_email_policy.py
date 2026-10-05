"""The sender policy: every branch, and the rule that nothing falls back."""
import pytest

from src.email_api import constants as C
from src.email_api.policy import resolve_sender, describe_routing
from src.models.models import TenantConfig
from src.tenancy import set_current_company, reset_current_company
from src.tenancy.platform_company import ensure_platform_company
from test.email_factories import make_company, make_smtp_sender, make_route


@pytest.fixture()
def co(db):
    return make_company(db, "Client")


def test_env_smtp_never_used(db, co, monkeypatch):
    """Core rule: an unconfigured client company is blocked, never sent from env."""
    import src.services.email_service as es
    monkeypatch.setattr(es, "SMTP_USER", "platform@gmail.com")
    monkeypatch.setattr(es, "SMTP_PASS", "env-secret")
    monkeypatch.setenv("SMTP_USER", "platform@gmail.com")
    d = resolve_sender(db, co.id, "interview_scheduled")
    assert d.status == "blocked" and d.reason_code == C.R_NO_SENDER
    assert d.sender_id is None and d.from_email is None


def test_tenant_default(db, co):
    sender, conn = make_smtp_sender(db, co.id, display_name="ABC HR", reply_to="jobs@example.com")
    make_route(db, co.id, C.DEFAULT_ROUTE, sender.id)
    d = resolve_sender(db, co.id, "offer_letter")
    assert d.ok and d.route_source == C.SRC_DEFAULT
    assert (d.sender_id, d.connection_id, d.sender_company_id) == (sender.id, conn.id, co.id)
    assert (d.from_email, d.from_name, d.reply_to) == (sender.email, "ABC HR", "jobs@example.com")


def test_event_route_beats_default(db, co):
    default, _ = make_smtp_sender(db, co.id, "Default")
    offers, _ = make_smtp_sender(db, co.id, "Offers")
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    make_route(db, co.id, "offer_letter", offers.id)
    assert resolve_sender(db, co.id, "offer_letter").sender_id == offers.id
    assert resolve_sender(db, co.id, "interview_scheduled").sender_id == default.id


def test_disabled_route_sends_nothing(db, co):
    default, _ = make_smtp_sender(db, co.id)
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    make_route(db, co.id, "candidate_rejected", enabled=False)
    d = resolve_sender(db, co.id, "candidate_rejected")
    assert d.status == "disabled" and d.sender_id is None


def test_broken_route_sender_blocks_without_fallback(db, co):
    default, _ = make_smtp_sender(db, co.id, "Default")
    broken, _ = make_smtp_sender(db, co.id, "Broken", conn_status=C.CONN_FAILED)
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    make_route(db, co.id, "offer_letter", broken.id)
    d = resolve_sender(db, co.id, "offer_letter")
    assert d.status == "blocked" and d.reason_code == C.R_ROUTE_SENDER_UNAVAILABLE
    assert d.from_email is None


def test_broken_default_blocks(db, co):
    default, _ = make_smtp_sender(db, co.id, password=None)  # no credential stored
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    d = resolve_sender(db, co.id, "offer_letter")
    assert d.status == "blocked" and d.reason_code == C.R_ROUTE_SENDER_UNAVAILABLE
    assert "password" in d.reason


def test_requested_sender(db, co):
    default, _ = make_smtp_sender(db, co.id, "Default")
    picked, _ = make_smtp_sender(db, co.id, "Picked")
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    d = resolve_sender(db, co.id, "custom_mail", requested_sender_id=picked.id)
    assert d.ok and d.sender_id == picked.id and d.route_source == C.SRC_REQUESTED


@pytest.mark.parametrize("problem,code", [
    ("foreign", C.R_SENDER_NOT_FOUND),
    ("disabled", C.R_SENDER_DISABLED),
    ("failed", C.R_CONNECTION_UNAVAILABLE),
    ("missing", C.R_SENDER_NOT_FOUND),
])
def test_bad_requested_sender_blocks_even_with_good_default(db, co, problem, code):
    default, _ = make_smtp_sender(db, co.id, "Default")
    make_route(db, co.id, C.DEFAULT_ROUTE, default.id)
    if problem == "foreign":
        other = make_company(db, "Other")
        sid = make_smtp_sender(db, other.id)[0].id
    elif problem == "disabled":
        sid = make_smtp_sender(db, co.id, "Off", enabled=False)[0].id
    elif problem == "failed":
        sid = make_smtp_sender(db, co.id, "Failed", conn_status=C.CONN_FAILED)[0].id
    else:
        sid = "00000000-0000-0000-0000-000000000000"
    d = resolve_sender(db, co.id, "custom_mail", requested_sender_id=sid)
    assert d.status == "blocked" and d.reason_code == code
    assert d.from_email is None


def test_auth_mail_uses_explicit_platform_route(db, co):
    platform = ensure_platform_company(db)
    p_sender, p_conn = make_smtp_sender(db, platform.id, "Platform Auth", is_shared=True)
    make_route(db, platform.id, C.EVENT_AUTH_VERIFY, p_sender.id)
    # Bound to the client, as in a real request: the platform rows are filtered
    # out unless the policy deliberately bypasses for this one lookup.
    token = set_current_company(co.id)
    try:
        d = resolve_sender(db, co.id, C.EVENT_AUTH_VERIFY)
        workflow = resolve_sender(db, co.id, "interview_scheduled")
    finally:
        reset_current_company(token)
    assert d.ok and d.route_source == C.SRC_PLATFORM_AUTH
    assert d.sender_company_id == platform.id and d.company_id == co.id
    assert d.connection_id == p_conn.id
    # The platform route is for auth mail only.
    assert workflow.status == "blocked" and workflow.reason_code == C.R_NO_SENDER


def test_company_sender_wins_for_auth_mail(db, co):
    platform = ensure_platform_company(db)
    p_sender, _ = make_smtp_sender(db, platform.id, "Platform Auth")
    make_route(db, platform.id, C.EVENT_AUTH_RESET, p_sender.id)
    own, _ = make_smtp_sender(db, co.id)
    make_route(db, co.id, C.DEFAULT_ROUTE, own.id)
    d = resolve_sender(db, co.id, C.EVENT_AUTH_RESET)
    assert d.sender_id == own.id and d.route_source == C.SRC_DEFAULT


def test_auth_mail_blocks_without_platform_route(db, co):
    ensure_platform_company(db)
    d = resolve_sender(db, co.id, C.EVENT_AUTH_VERIFY)
    assert d.status == "blocked" and d.reason_code == C.R_NO_SENDER


def test_platform_events_only_for_platform(db, co):
    d = resolve_sender(db, co.id, "platform.tenant_welcome")
    assert d.status == "blocked" and d.reason_code == C.R_EVENT_NOT_ALLOWED
    platform = ensure_platform_company(db)
    s, _ = make_smtp_sender(db, platform.id, "Official")
    make_route(db, platform.id, "platform.tenant_welcome", s.id)
    assert resolve_sender(db, platform.id, "platform.tenant_welcome").ok


def test_unknown_event(db, co):
    assert resolve_sender(db, co.id, "made.up").reason_code == C.R_EVENT_NOT_ALLOWED


def test_unmigrated_legacy_settings_are_reported(db, co):
    db.add(TenantConfig(company_id=co.id, smtp_host="smtp.example.com", smtp_port=587,
                        smtp_user="hr@example.com", smtp_pass="x"))
    db.flush()
    d = resolve_sender(db, co.id, "offer_letter")
    assert d.status == "blocked" and d.reason_code == C.R_MIGRATION_PENDING


def test_describe_routing_has_no_secrets(db, co):
    s, _ = make_smtp_sender(db, co.id)
    make_route(db, co.id, C.DEFAULT_ROUTE, s.id)
    make_route(db, co.id, "candidate_rejected", enabled=False)
    rows = {r["event_type"]: r for r in describe_routing(db, co.id)}
    assert rows["offer_letter"]["status"] == "ok"
    assert rows["candidate_rejected"]["status"] == "disabled"
    assert "platform.tenant_welcome" not in rows
    assert "app-pw" not in repr(rows)
