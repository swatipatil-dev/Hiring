"""The platform's env mailboxes become explicit connections, senders and routes."""
import logging

import pytest

from src.email_api import constants as C, crypto
from src.email_api.bootstrap import check_email_migration_applied, seed_platform_email_from_env
from src.email_api.policy import resolve_sender
from src.models.models import (CompanyMailbox, EmailConnection, EmailRoute, EmailSender)
from src.tenancy.platform_company import ensure_platform_company
from test.email_factories import make_company, make_smtp_sender, make_route


@pytest.fixture()
def env(monkeypatch):
    values = {"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_USER": "platform@gmail.com",
              "SMTP_PASS": "gmail-app-pw", "FROM_EMAIL": "",
              "INNOMAX_SMTP_HOST": "mail.innomaxsol.com", "INNOMAX_SMTP_PORT": "465",
              "INNOMAX_HR_USER": "hr@innomaxsol.com", "INNOMAX_HR_PASS": "hr-pw",
              "INNOMAX_OFFICIAL_USER": "official@innomaxsol.com", "INNOMAX_OFFICIAL_PASS": "off-pw"}
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return values


@pytest.fixture()
def platform(db):
    return ensure_platform_company(db)


def _sources(db):
    return {c.legacy_source: c for c in db.query(EmailConnection)
            .filter(EmailConnection.legacy_source.like("env:%"))}


def test_seeds_three_shared_connections_with_encrypted_passwords(db, env, platform):
    seed_platform_email_from_env(db)
    conns = _sources(db)
    assert set(conns) == {"env:SMTP", "env:INNOMAX_HR", "env:INNOMAX_OFFICIAL"}
    for c in conns.values():
        assert c.company_id == platform.id and c.is_shared and c.status == C.CONN_ACTIVE
        for password in ("gmail-app-pw", "hr-pw", "off-pw"):
            assert password not in c.credential_enc
    assert crypto.decrypt_secret(conns["env:INNOMAX_HR"].credential_enc,
                                 conns["env:INNOMAX_HR"].credential_key_version) == "hr-pw"
    assert conns["env:SMTP"].security == C.SECURITY_STARTTLS


def test_routes_reproduce_the_platform_companys_old_senders(db, env, platform):
    seed_platform_email_from_env(db)
    by_event = lambda e: resolve_sender(db, platform.id, e).from_email  # noqa: E731
    assert by_event("interview_scheduled") == "hr@innomaxsol.com"          # candidate -> HR
    assert by_event("application_received") == "platform@gmail.com"       # "system" category
    assert by_event("platform.tenant_welcome") == "official@innomaxsol.com"
    assert by_event("support.reply_to_admin") == "platform@gmail.com"
    assert by_event(C.EVENT_AUTH_VERIFY) == "platform@gmail.com"


def test_client_without_sender_gets_platform_auth_only(db, env, platform):
    seed_platform_email_from_env(db)
    client = make_company(db, "Client")
    auth = resolve_sender(db, client.id, C.EVENT_AUTH_RESET)
    assert auth.ok and auth.route_source == C.SRC_PLATFORM_AUTH
    assert auth.from_email == "platform@gmail.com"
    offer = resolve_sender(db, client.id, "offer_letter")
    assert offer.status == "blocked" and offer.reason_code == C.R_NO_SENDER


def test_idempotent_and_never_overwrites(db, env, platform, monkeypatch):
    assert seed_platform_email_from_env(db)
    before = _sources(db)["env:INNOMAX_HR"].credential_enc
    monkeypatch.setenv("INNOMAX_HR_PASS", "rotated-pw")
    assert seed_platform_email_from_env(db) == []
    assert _sources(db)["env:INNOMAX_HR"].credential_enc == before
    seed_platform_email_from_env(db, update=True)
    conn = _sources(db)["env:INNOMAX_HR"]
    assert crypto.decrypt_secret(conn.credential_enc, conn.credential_key_version) == "rotated-pw"
    assert db.query(EmailSender).filter_by(company_id=platform.id).count() == 3


def test_admin_routes_win(db, env, platform):
    own, _ = make_smtp_sender(db, platform.id, "Careers")
    make_route(db, platform.id, C.DEFAULT_ROUTE, own.id)
    make_route(db, platform.id, C.EVENT_AUTH_VERIFY, own.id)
    seed_platform_email_from_env(db)
    assert resolve_sender(db, platform.id, "offer_letter").sender_id == own.id
    assert resolve_sender(db, platform.id, C.EVENT_AUTH_VERIFY).sender_id == own.id


def test_innomax_hr_replaces_a_migrated_tenant_config_default_once(db, env, platform):
    tc_sender, _ = make_smtp_sender(db, platform.id, "Company default")
    tc_sender.legacy_source = "tenant_configs:x"
    make_route(db, platform.id, C.DEFAULT_ROUTE, tc_sender.id)
    seed_platform_email_from_env(db)
    assert resolve_sender(db, platform.id, "offer_letter").from_email == "hr@innomaxsol.com"


def test_dry_run_writes_nothing(db, env, platform):
    actions = seed_platform_email_from_env(db, dry_run=True)
    assert any("Innomax HR" in a for a in actions)
    assert _sources(db) == {} and db.query(EmailRoute).filter_by(company_id=platform.id).count() == 0


def test_missing_env_and_missing_keys(db, env, platform, monkeypatch):
    monkeypatch.setenv("INNOMAX_OFFICIAL_PASS", "")
    monkeypatch.setenv("EMAIL_CREDENTIAL_KEYS", "")
    assert "nothing seeded" in seed_platform_email_from_env(db)[0]
    monkeypatch.setenv("EMAIL_CREDENTIAL_KEYS", "k1:1Cwutj66M7kQjcOhEKI8L4e1dk9lra3tx0O05CThawI=")
    seed_platform_email_from_env(db)
    assert "env:INNOMAX_OFFICIAL" not in _sources(db)


def test_unmigrated_legacy_settings_logged_critical(db, caplog):
    co = make_company(db, "Legacy")
    db.add(CompanyMailbox(company_id=co.id, name="HR", smtp_user="hr@x.example", smtp_pass="x"))
    db.flush()
    with caplog.at_level(logging.CRITICAL):
        assert check_email_migration_applied(db) is False
    assert "have not been migrated" in caplog.text
