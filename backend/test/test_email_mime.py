"""The deliverability headers survive the move from _deliver to the email API.

build_mime makes the message; dispatch decides the display name, the
List-Unsubscribe target and whether the mail is an automated OTP.
"""
import email

import pytest

from src.email_api import constants as C
from src.email_api.adapters import base as A
from src.email_api.mime import build_mime
from src.email_api.worker import deliver_message
from src.services import email_service as es
from test.email_factories import FakeAdapter, make_company, make_route, make_smtp_sender


def _parse(**kw):
    args = dict(from_email="hr@abc.example", from_name="ABC via HireFlow", to=["cand@example.net"],
                subject="Subject", html="<p>Hi<br>there</p>")
    args.update(kw)
    return email.message_from_string(build_mime(**args).as_string())


def test_branded_mail_headers():
    msg = _parse(cc=["hm@abc.example"], unsubscribe_to="help@abc.example")
    assert msg["From"] == "ABC via HireFlow <hr@abc.example>"
    assert msg["Cc"] == "hm@abc.example"
    assert msg["List-Unsubscribe"] == "<mailto:help@abc.example?subject=unsubscribe>"
    assert msg["Auto-Submitted"] is None
    assert msg["Message-ID"] and msg["Date"] and msg["X-Mailer"] == "HireFlow Notifications"
    parts = [p.get_content_type() for p in msg.walk() if not p.is_multipart()]
    assert parts == ["text/plain", "text/html"]
    assert "Hi\nthere" in msg.get_payload()[0].get_payload(decode=True).decode()


def test_otp_mail_headers():
    msg = _parse(auto_submitted=True, reply_to="help@abc.example", unsubscribe_to="help@abc.example")
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Precedence"] == "bulk"
    assert msg["List-Unsubscribe"] is None
    assert msg["Reply-To"] == "help@abc.example"


@pytest.fixture()
def sent(db, monkeypatch):
    """Dispatch through a company sender and capture what the adapter is handed."""
    co = make_company(db, "Acme")
    sender, _ = make_smtp_sender(db, co.id, "HR", email="hr@acme.example")
    make_route(db, co.id, C.DEFAULT_ROUTE, sender.id)
    fake = FakeAdapter()
    monkeypatch.setattr(A, "get_adapter", lambda conn, is_test=False: fake)
    monkeypatch.setattr("src.mailq.queue.redis_available", lambda: False)

    def run(display_name=None, **kw):
        sender.display_name = display_name
        db.flush()
        ref = es.resolve_sender(db, co.id, event_type=kw.pop("event", "offer_letter"))
        res = es.dispatch("cand@example.net", "Subject", "<p>x</p>", smtp_config=ref, db=db, **kw)
        deliver_message(res["message_id"], co.id)
        return fake.calls[-1]["snapshot"]
    return run


def test_display_name_is_the_senders_or_the_branded_default(sent):
    branding = {"company_name": "Acme", "support_email": "help@acme.example"}
    assert sent(branding=branding).from_name == "Acme via HireFlow"
    assert sent(display_name="Acme Careers", branding=branding).from_name == "Acme Careers"
    assert sent().from_name == "HireFlow"


def test_unsubscribe_and_otp_flags_reach_the_adapter(sent):
    snap = sent(branding={"company_name": "Acme", "support_email": "help@acme.example"})
    assert snap.unsubscribe_to == "help@acme.example" and not snap.auto_submitted
    otp = sent(branding={"company_name": "Acme", "support_email": "help@acme.example"},
               is_otp=True, event=C.EVENT_AUTH_VERIFY)
    assert otp.auto_submitted and otp.unsubscribe_to is None
