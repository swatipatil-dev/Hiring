"""SMTPAdapter classifies every outcome by how far the conversation got.

A scripted fake server stands in for the network. The cases that matter most:
nothing after the payload is ever retried blindly, and no failure ever leads to
a different sender.
"""
import email
import smtplib
import ssl
from types import SimpleNamespace

import pytest

from src.email_api import constants as C
from src.email_api import ssrf
from src.email_api.adapters import base
from src.email_api.adapters.base import Snapshot, get_adapter, UnsupportedConnection
from src.email_api.adapters.null import NullAdapter
from src.email_api.adapters.smtp import SMTPAdapter
from src.email_api.mime import build_mime


class FakeServer:
    """Records the conversation; ``script`` decides the replies."""
    instances = []

    def __init__(self, host, port, *, pinned_ip, timeout, context=None, script=None):
        self.host, self.port, self.pinned_ip = host, port, pinned_ip
        self.script = script or {}
        self.log, self.payload, self._replies = [], None, []
        if self.script.get("connect_error"):
            raise self.script["connect_error"]
        FakeServer.instances.append(self)

    def ehlo(self):
        self.log.append("ehlo")

    def has_extn(self, name):
        return self.script.get("starttls", True)

    def starttls(self, context):
        self.log.append("starttls")

    def login(self, user, password):
        self.log.append(("login", user, password))
        if self.script.get("auth_fail"):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials")

    def mail(self, addr):
        self.log.append(("mail", addr))
        return self.script.get("mail", (250, b"OK"))

    def rcpt(self, addr):
        self.log.append(("rcpt", addr))
        return self.script.get("rcpt", {}).get(addr, (250, b"OK"))

    def putcmd(self, cmd):
        self.log.append(cmd)
        self._replies = [self.script.get("data_intro", (354, b"go ahead")),
                         self.script.get("data_final", (250, b"queued as ABC"))]

    def getreply(self):
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def send(self, data):
        if self.script.get("send_error"):
            raise self.script["send_error"]
        self.payload = data

    def quit(self):
        self.log.append("quit")

    def close(self):
        pass


@pytest.fixture()
def smtp(monkeypatch):
    FakeServer.instances = []
    state = {"script": {}, "ips": ["93.184.216.34"]}

    def factory(*args, **kwargs):
        return FakeServer(*args, script=state["script"], **kwargs)
    monkeypatch.setattr(ssrf, "PinnedSMTP", factory)
    monkeypatch.setattr(ssrf, "PinnedSMTP_SSL", factory)
    monkeypatch.setattr(ssrf, "resolve_public_ips",
                        lambda host, port, resolver=None: _resolve(state["ips"]))
    return state


def _resolve(ips):
    bad = [ip for ip in ips if not ssrf.ip_allowed(ip)]
    if bad:
        raise ssrf.DestinationNotAllowed("destination_not_allowed", "private")
    return ips


def _conn(port=587, security="starttls_required"):
    return SimpleNamespace(type="smtp", host="smtp.abc.example", port=port, security=security,
                           username="hr@abc.example")


def _snap(**kw):
    base_kw = dict(from_email="hr@abc.example", from_name="ABC Recruitment",
                   subject="Interview", html="<p>Hello</p>", to=("cand@example.net",),
                   reply_to="hr@abc.example", message_id_header="<msg-1@hireflow.io>")
    base_kw.update(kw)
    return Snapshot(**base_kw)


def test_accepted_uses_sender_identity_and_hides_bcc(smtp):
    res = SMTPAdapter().send(_conn(), _snap(cc=("hm@abc.example",), bcc=("audit@abc.example",)), "pw")
    assert res.outcome == C.OUT_ACCEPTED and res.phase_reached == "done"
    server = FakeServer.instances[0]
    assert server.pinned_ip == "93.184.216.34" and server.host == "smtp.abc.example"
    assert ("login", "hr@abc.example", "pw") in server.log
    assert ("mail", "hr@abc.example") in server.log
    assert {a for k, a in [e for e in server.log if isinstance(e, tuple) and e[0] == "rcpt"]} == {
        "cand@example.net", "hm@abc.example", "audit@abc.example"}
    msg = email.message_from_bytes(server.payload)
    assert msg["From"] == "ABC Recruitment <hr@abc.example>"
    assert msg["Reply-To"] == "hr@abc.example"
    assert msg["Message-ID"] == "<msg-1@hireflow.io>"
    assert "audit@abc.example" not in server.payload.decode()
    assert [r.status for r in res.recipients] == [C.RCPT_ACCEPTED] * 3


def test_starttls_runs_before_login(smtp):
    SMTPAdapter().send(_conn(), _snap(), "pw")
    log = FakeServer.instances[0].log
    assert log.index("starttls") < next(i for i, e in enumerate(log) if isinstance(e, tuple))


def test_no_starttls_blocks_before_credentials(smtp):
    smtp["script"]["starttls"] = False
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_BLOCKED and res.error.code == C.R_STARTTLS_UNAVAILABLE
    assert not any(isinstance(e, tuple) and e[0] == "login" for e in FakeServer.instances[0].log)


def test_auth_failure_blocks_and_fails_connection(smtp):
    smtp["script"]["auth_fail"] = True
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_BLOCKED
    assert res.error.code == C.R_AUTH_FAILED and res.error.connection_status == C.CONN_FAILED


def test_disconnect_after_data_is_unknown(smtp):
    smtp["script"]["data_final"] = smtplib.SMTPServerDisconnected("gone")
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_UNKNOWN and res.error.kind == base.UNKNOWN
    assert res.recipients[0].status == C.RCPT_UNKNOWN


def test_timeout_while_sending_payload_is_unknown(smtp):
    smtp["script"]["send_error"] = TimeoutError("timed out")
    assert SMTPAdapter().send(_conn(), _snap(), "pw").outcome == C.OUT_UNKNOWN


def test_data_refused_before_payload_is_not_unknown(smtp):
    smtp["script"]["data_intro"] = (554, b"no")
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_PERM and res.error.kind == base.PERMANENT


def test_final_data_4xx_is_temporary(smtp):
    smtp["script"]["data_final"] = (451, b"try later")
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_TEMP and res.error.kind == base.TEMPORARY


def test_rate_limit_at_mail_from_is_throttled(smtp):
    smtp["script"]["mail"] = (421, b"4.7.0 Too many messages, slow down")
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_TEMP and res.error.kind == base.THROTTLED
    assert res.error.retry_after >= 900


def test_sender_refused_blocks(smtp):
    smtp["script"]["mail"] = (553, b"5.7.1 Sender address rejected: not owned by user")
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_BLOCKED and res.error.code == C.R_SENDER_REJECTED


def test_per_recipient_outcomes(smtp):
    smtp["script"]["rcpt"] = {"gone@example.net": (550, b"5.1.1 User unknown")}
    res = SMTPAdapter().send(_conn(), _snap(to=("cand@example.net", "gone@example.net")), "pw")
    assert res.outcome == C.OUT_ACCEPTED
    by = {r.email: r for r in res.recipients}
    assert by["cand@example.net"].status == C.RCPT_ACCEPTED
    assert by["gone@example.net"].status == C.RCPT_FAILED and by["gone@example.net"].suppress


def test_all_recipients_deferred_sends_nothing(smtp):
    smtp["script"]["rcpt"] = {"cand@example.net": (451, b"greylisted")}
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_TEMP and "data" not in FakeServer.instances[0].log


def test_cert_failure_blocks(smtp):
    smtp["script"]["connect_error"] = ssl.SSLCertVerificationError("certificate verify failed")
    res = SMTPAdapter().send(_conn(465, "implicit_tls"), _snap(), "pw")
    assert res.outcome == C.OUT_BLOCKED and res.error.code == C.R_TLS_CERT_INVALID


def test_network_error_before_data_is_temporary(smtp):
    smtp["script"]["connect_error"] = ConnectionRefusedError("refused")
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_TEMP and res.error.kind == base.TEMPORARY


def test_destination_rechecked_on_every_send(smtp):
    smtp["ips"] = ["10.0.0.9"]  # DNS now points inside
    res = SMTPAdapter().send(_conn(), _snap(), "pw")
    assert res.outcome == C.OUT_BLOCKED and res.error.code == C.R_DESTINATION_NOT_ALLOWED
    assert FakeServer.instances == []


def test_port_25_never_connects(smtp):
    res = SMTPAdapter().send(_conn(25, None), _snap(), "pw")
    assert res.outcome == C.OUT_BLOCKED and FakeServer.instances == []


def test_validate_connection(smtp):
    assert SMTPAdapter().validate_connection(_conn(), "pw").ok
    smtp["script"]["auth_fail"] = True
    v = SMTPAdapter().validate_connection(_conn(), "pw")
    assert not v.ok and v.error.code == C.R_AUTH_FAILED


def test_header_injection_is_neutralised():
    msg = build_mime(from_email="hr@abc.example", from_name="ABC\r\nBcc: evil@x.example",
                     to=["c@example.net"], subject="Hi\r\nBcc: evil@x.example", html="<p>x</p>")
    raw = msg.as_string()
    assert "\nBcc:" not in raw and "\r\nBcc:" not in raw


def test_null_adapter_simulations():
    a = NullAdapter()
    assert a.send(None, _snap(), "").outcome == C.OUT_ACCEPTED
    assert a.send(None, _snap(to=("unknown+x@example.net",)), "").outcome == C.OUT_UNKNOWN
    assert a.send(None, _snap(to=("throttle+x@example.net",)), "").error.kind == base.THROTTLED
    bounced = a.send(None, _snap(to=("bounce+x@example.net",)), "")
    assert bounced.outcome == C.OUT_PERM and bounced.recipients[0].suppress


def test_get_adapter(monkeypatch):
    smtp_conn = SimpleNamespace(type="smtp")
    assert isinstance(get_adapter(smtp_conn, is_test=True), NullAdapter)
    assert isinstance(get_adapter(smtp_conn), SMTPAdapter)
    monkeypatch.setenv("EMAIL_DELIVERY_MODE", "null")
    assert isinstance(get_adapter(smtp_conn), NullAdapter)
    monkeypatch.delenv("EMAIL_DELIVERY_MODE")
    with pytest.raises(UnsupportedConnection):
        get_adapter(SimpleNamespace(type="google_oauth"))
