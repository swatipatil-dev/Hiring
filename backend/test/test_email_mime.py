"""The message the legacy sender builds through email_api.mime.build_mime.

Pins the deliverability headers so moving the builder changed nothing.
"""
import email
from unittest.mock import patch, MagicMock

import src.services.email_service as es


def _capture(**kwargs):
    server = MagicMock()
    server.__enter__.return_value = server
    cfg = {"host": "smtp.example.com", "port": 587, "user": "hr@abc.example",
           "password": "pw", "from_email": "hr@abc.example"}
    with patch.object(es, "EMAIL_ENABLED", True), \
         patch("src.services.email_service.smtplib.SMTP", return_value=server):
        result = es._deliver("cand@example.net", "Subject", "<p>Hi<br>there</p>",
                             kwargs.get("reply_to"), cfg, kwargs.get("branding"),
                             kwargs.get("is_otp", False), kwargs.get("cc"))
    assert result["sent"], result
    from_addr, rcpts, raw = server.sendmail.call_args.args
    return from_addr, rcpts, email.message_from_string(raw)


def test_branded_mail_headers():
    from_addr, rcpts, msg = _capture(branding={"company_name": "ABC", "support_email": "help@abc.example"},
                                     cc=["hm@abc.example", "CAND@example.net"])
    assert from_addr == "hr@abc.example"
    assert rcpts == ["cand@example.net", "hm@abc.example"]
    assert msg["From"] == "ABC via HireFlow <hr@abc.example>"
    assert msg["Cc"] == "hm@abc.example"
    assert msg["List-Unsubscribe"] == "<mailto:help@abc.example?subject=unsubscribe>"
    assert msg["Auto-Submitted"] is None
    assert msg["Message-ID"] and msg["Date"] and msg["X-Mailer"] == "HireFlow Notifications"
    parts = [p.get_content_type() for p in msg.walk() if not p.is_multipart()]
    assert parts == ["text/plain", "text/html"]
    assert "Hi\nthere" in msg.get_payload()[0].get_payload(decode=True).decode()


def test_otp_mail_headers():
    _, _, msg = _capture(is_otp=True, reply_to="help@abc.example")
    assert msg["From"] == "HireFlow <hr@abc.example>"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Precedence"] == "bulk"
    assert msg["List-Unsubscribe"] is None
    assert msg["Reply-To"] == "help@abc.example"
