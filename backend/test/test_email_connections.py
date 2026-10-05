"""Creating, verifying, editing and disconnecting SMTP connections, and sender rules."""
import pytest

from src.email_api import constants as C
from src.email_api import connections as conns
from src.email_api import senders as S
from src.email_api.policy import resolve_sender
from src.models.models import EmailConnection
from test.email_factories import make_company, make_route

PUBLIC = lambda host, port: ["93.184.216.34"]   # noqa: E731
PRIVATE = lambda host, port: ["10.1.2.3"]       # noqa: E731


@pytest.fixture()
def co(db):
    return make_company(db, "Client")


@pytest.fixture(autouse=True)
def null_delivery(monkeypatch):
    # NullAdapter verifies any login not starting with "fail".
    monkeypatch.setenv("EMAIL_DELIVERY_MODE", "null")


def _create(db, co, **kw):
    args = dict(company_id=co.id, name="HR", host="mail.abc.example", port=465,
                username="hr@abc.example", password="app-pw", resolver=PUBLIC)
    args.update(kw)
    return conns.create_smtp(db, **args)


def test_create_verifies_and_encrypts(db, co):
    conn = _create(db, co)
    assert conn.status == C.CONN_ACTIVE and conn.verification_state == "verified"
    assert conn.security == C.SECURITY_IMPLICIT_TLS
    assert "app-pw" not in conn.credential_enc and conn.credential_key_version == "k1"
    assert conn.account_key == conns.account_key("mail.abc.example", "hr@abc.example")
    pub = conns.public(conn)
    assert pub["has_credential"] and "credential_enc" not in pub
    assert "app-pw" not in repr(pub)


@pytest.mark.parametrize("kw,code", [
    ({"resolver": PRIVATE}, "destination_not_allowed"),
    ({"port": 25}, "port_not_allowed"),
    ({"host": "127.0.0.1"}, "host_ip_literal"),
    ({"port": 465, "security": "starttls_required"}, "security_mismatch"),
    ({"password": ""}, "password_required"),
])
def test_rejected_endpoints_store_nothing(db, co, kw, code):
    with pytest.raises(conns.ConnectionError_) as e:
        _create(db, co, **kw)
    assert e.value.code == code
    assert db.query(EmailConnection).filter(EmailConnection.company_id == co.id).count() == 0


def test_failed_login_is_stored_as_failed(db, co):
    conn = _create(db, co, username="fail@abc.example")
    assert conn.status == C.CONN_FAILED and conn.error_code == C.R_AUTH_FAILED


def test_update_keeps_password_unless_given(db, co):
    conn = _create(db, co)
    before = conn.credential_enc
    conns.update_smtp(db, conn, name="Renamed")
    assert conn.credential_enc == before and conn.status == C.CONN_ACTIVE
    conns.update_smtp(db, conn, port=587, resolver=PUBLIC)
    assert conn.security == C.SECURITY_STARTTLS and conn.status == C.CONN_ACTIVE
    conns.update_smtp(db, conn, password="new-pw")
    assert conn.credential_enc != before


def test_update_rechecks_destination(db, co):
    conn = _create(db, co)
    with pytest.raises(conns.ConnectionError_):
        conns.update_smtp(db, conn, host="evil.example", resolver=PRIVATE)


def test_disconnect_destroys_credential_and_blocks(db, co):
    conn = _create(db, co)
    sender = S.create(db, company_id=co.id, conn=conn, email="hr@abc.example", label="HR")
    make_route(db, co.id, C.DEFAULT_ROUTE, sender.id)
    assert resolve_sender(db, co.id, "offer_letter").ok
    conns.disconnect(db, conn)
    assert conn.credential_enc is None and conn.status == C.CONN_DISCONNECTED
    d = resolve_sender(db, co.id, "offer_letter")
    assert d.status == "blocked" and d.reason_code == C.R_ROUTE_SENDER_UNAVAILABLE
    with pytest.raises(conns.ConnectionError_):
        conns.update_smtp(db, conn, name="x")


def test_unreadable_credential_needs_attention(db, co):
    conn = _create(db, co)
    conn.credential_enc = "k1$garbage"
    result = conns.verify(db, conn)
    assert not result.ok and conn.status == C.CONN_NEEDS_ATTENTION
    assert conn.error_code == C.R_CREDENTIAL_UNREADABLE


def test_sender_address_rules(db, co):
    conn = _create(db, co)
    assert S.create(db, company_id=co.id, conn=conn, email="hr@abc.example",
                    label="HR").verification_status == C.SENDER_PRIMARY
    assert S.create(db, company_id=co.id, conn=conn, email="careers@abc.example",
                    label="Careers").verification_status == C.SENDER_ALIAS
    with pytest.raises(S.SenderError) as e:
        S.create(db, company_id=co.id, conn=conn, email="ceo@other.example", label="Spoof")
    assert e.value.code == "sender_not_authorised"
    with pytest.raises(S.SenderError):
        S.create(db, company_id=co.id, conn=conn, email="hr@abc.example", label="HR")  # label taken
    with pytest.raises(S.SenderError):
        S.create(db, company_id=co.id, conn=conn, email="x@abc.example", label="Bad",
                 display_name="A\r\nBcc: e@x")


def test_sender_cannot_use_other_companys_connection(db, co):
    other = make_company(db, "Other")
    conn = _create(db, other)
    with pytest.raises(S.SenderError):
        S.create(db, company_id=co.id, conn=conn, email="hr@abc.example", label="HR")


def test_soft_delete_clears_routes(db, co):
    conn = _create(db, co)
    sender = S.create(db, company_id=co.id, conn=conn, email="hr@abc.example", label="HR")
    route = make_route(db, co.id, "offer_letter", sender.id)
    S.soft_delete(db, sender)
    assert route.sender_id is None and route.policy_version == 2
    assert S.usable_reason(sender, conn) == C.R_SENDER_NOT_FOUND
    # The label is free again.
    S.create(db, company_id=co.id, conn=conn, email="hr@abc.example", label="HR")
