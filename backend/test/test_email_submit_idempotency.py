"""Accepting an email: validation, persistence, blocked messages, idempotency."""
import json

import pytest

from src.email_api import constants as C
from src.email_api import crypto
from src.email_api import messages as M
from src.email_api.outbox import PENDING_KEY
from src.models.models import (EmailMessage, EmailRecipient, EmailOutbox, EmailRoute,
                               MailTemplate)
from test.email_factories import make_company, make_smtp_sender, make_route, queue_message


@pytest.fixture()
def co(db):
    c = make_company(db, "Client")
    sender, _ = make_smtp_sender(db, c.id, display_name="Client HR")
    make_route(db, c.id, C.DEFAULT_ROUTE, sender.id)
    c.sender = sender
    return c


def _count(db, model, company_id):
    return db.query(model).filter(model.company_id == company_id).count()


def test_submit_persists_message_recipients_and_outbox(db, co):
    res = queue_message(db, co.id, to=[{"email": "Cand@Example.net", "name": "Cand"}],
                        cc=["hm@client.example", "cand@example.net"], bcc=["audit@client.example"])
    msg = res.message
    assert res.status == C.QUEUED and not res.replayed
    assert (msg.sender_id, msg.sender_company_id) == (co.sender.id, co.id)
    assert msg.from_email == co.sender.email and msg.from_name == "Client HR"
    assert msg.message_id_header == f"<{msg.id}@hireflow.io>"
    rcpts = {(r.kind, r.email) for r in db.query(EmailRecipient).filter_by(message_id=msg.id)}
    # The CC duplicate of the To address is dropped, case-insensitively.
    assert rcpts == {("to", "Cand@Example.net"), ("cc", "hm@client.example"),
                     ("bcc", "audit@client.example")}
    box = db.query(EmailOutbox).filter_by(message_id=msg.id).one()
    assert box.state == "pending" and box.company_id == co.id
    assert "Welcome" not in msg.content_enc
    assert json.loads(crypto.decrypt_content(msg.content_enc))["html"] == "<p>Welcome</p>"
    assert (msg.id, co.id, C.PRIORITY_NORMAL, False) in db.info[PENDING_KEY]


def test_blocked_message_is_recorded_without_outbox(db):
    c = make_company(db, "Unconfigured")
    res = queue_message(db, c.id)
    assert res.status == C.BLOCKED
    assert res.message.status_reason == C.R_NO_SENDER and res.message.sender_id is None
    assert _count(db, EmailOutbox, c.id) == 0
    assert PENDING_KEY not in db.info or not any(i[0] == res.message.id for i in db.info[PENDING_KEY])


def test_disabled_route_creates_nothing(db, co):
    make_route(db, co.id, "candidate_rejected", enabled=False)
    res = queue_message(db, co.id, "candidate_rejected")
    assert res.status == "disabled" and res.message is None
    assert _count(db, EmailMessage, co.id) == 0


def test_same_key_same_request_returns_original(db, co):
    first = queue_message(db, co.id, idempotency_key="interview-982-v1")
    again = queue_message(db, co.id, idempotency_key="interview-982-v1")
    assert again.replayed and again.message.id == first.message.id
    assert _count(db, EmailMessage, co.id) == 1


def test_same_key_changed_request_conflicts(db, co):
    queue_message(db, co.id, idempotency_key="k")
    with pytest.raises(M.IdempotencyConflict) as e:
        queue_message(db, co.id, idempotency_key="k", subject="Changed")
    assert e.value.status_code == 409


def test_routing_change_between_retries_is_not_a_conflict(db, co):
    first = queue_message(db, co.id, idempotency_key="k")
    other, _ = make_smtp_sender(db, co.id, "Other")
    route = db.query(EmailRoute).filter_by(company_id=co.id, event_type=C.DEFAULT_ROUTE).one()
    route.sender_id = other.id
    db.flush()
    assert queue_message(db, co.id, idempotency_key="k").message.id == first.message.id


def test_concurrent_duplicate_resolves_to_one_record(db, co, monkeypatch):
    first = queue_message(db, co.id, idempotency_key="race")
    real = M._existing
    calls = {"n": 0}

    def miss_once(*args):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*args)
    monkeypatch.setattr(M, "_existing", miss_once)
    # The pre-check misses (as if the other request had not committed yet); the
    # unique key then catches it and the original is returned.
    again = queue_message(db, co.id, idempotency_key="race")
    assert again.replayed and again.message.id == first.message.id
    assert _count(db, EmailMessage, co.id) == 1


@pytest.mark.parametrize("kw,code", [
    ({"subject": "Hi\r\nBcc: x@evil.example"}, "header_invalid"),
    ({"subject": ""}, "subject_required"),
    ({"html": "", "text": None}, "content_required"),
    ({"to": []}, "recipient_required"),
    ({"to": ["not-an-address"]}, "recipient_invalid"),
    ({"to": [f"c{i}@example.net" for i in range(51)]}, "too_many_recipients"),
    ({"to": [{"email": "c@example.net", "name": "A\nB"}]}, "header_invalid"),
    ({"html": "x" * (1024 * 1024 + 1)}, "body_too_large"),
    ({"metadata": {"k": "v" * 9000}}, "metadata_too_large"),
])
def test_validation(db, co, kw, code):
    with pytest.raises(M.SubmitError) as e:
        queue_message(db, co.id, **kw)
    assert e.value.code == code
    assert _count(db, EmailMessage, co.id) == 0


def test_template_is_rendered_escaped_and_frozen(db, co):
    tpl = MailTemplate(company_id=co.id, name="Invite", subject="Interview for {{job_title}}",
                       body="Dear {{candidate_name}},\nSee you at {{time}}.")
    db.add(tpl)
    db.flush()
    res = queue_message(db, co.id, subject=None, html=None, template_id=tpl.id,
                        variables={"job_title": "AI Developer",
                                   "candidate_name": "<script>alert(1)</script>"})
    msg = res.message
    assert msg.subject == "Interview for AI Developer"
    body = json.loads(crypto.decrypt_content(msg.content_enc))["html"]
    assert "&lt;script&gt;" in body and "<script>" not in body
    assert "<br>" in body and "{{time}}" in body
    tpl.body = "edited later"
    db.flush()
    assert "Dear" in json.loads(crypto.decrypt_content(msg.content_enc))["html"]


def test_template_and_content_are_exclusive(db, co):
    with pytest.raises(M.SubmitError) as e:
        queue_message(db, co.id, template_id="x", variables={})
    assert e.value.code == "content_conflict"


def test_other_companys_template_is_not_found(db, co):
    other = make_company(db, "Other")
    tpl = MailTemplate(company_id=other.id, name="T", subject="s", body="b")
    db.add(tpl)
    db.flush()
    with pytest.raises(M.SubmitError) as e:
        queue_message(db, co.id, subject=None, html=None, template_id=tpl.id)
    assert e.value.code == "template_not_found"


def test_decision_for_another_company_is_refused(db, co):
    from src.email_api.policy import resolve_sender
    other = make_company(db, "Other")
    decision = resolve_sender(db, co.id, "offer_letter")
    with pytest.raises(M.SubmitError):
        M.submit(db, tenant_id=other.id, decision=decision, event_type="offer_letter",
                 to=["c@example.net"], subject="s", html="b")
