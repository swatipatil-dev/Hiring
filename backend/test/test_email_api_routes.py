"""The external email API end to end, through HTTP."""
import pytest

from src.email_api import constants as C
from src.models.models import EmailConnection, EmailMessage
from test.email_api_world import world, send_body  # noqa: F401  (fixture)


def _send(client, headers, body=None, key=None):
    h = dict(headers)
    if key:
        h["Idempotency-Key"] = key
    return client.post("/api/v1/email/messages", json=body or send_body(), headers=h)


def test_send_with_admin_session(client, world):
    r = _send(client, world.a.admin)
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["status"] == C.QUEUED
    assert out["sender"] == {"id": world.a.sender.id, "email": "hr@alpha.example",
                             "name": "Alpha Recruitment"}
    detail = client.get(f"/api/v1/email/messages/{out['message_id']}", headers=world.a.admin).json()
    assert detail["actor"]["type"] == "user" and detail["actor"]["user_id"]
    assert detail["route_source"] == C.SRC_DEFAULT
    assert detail["connection"]["id"] == world.a.conn.id
    assert detail["recipients"] == [{"email": "candidate@example.net", "name": "Candidate",
                                     "kind": "to", "status": "pending", "smtp_code": None}]
    assert detail["delivery_events_supported"] is False
    assert "html" not in detail and "Your interview is scheduled" not in str(detail)


def test_send_with_api_key_records_customer_reference(client, world):
    r = _send(client, world.a.live, send_body(external_actor_reference="recruiter-17",
                                              external_reference="application_982",
                                              metadata={"interview_id": "int_456"}))
    assert r.status_code == 202, r.text
    detail = client.get(f"/api/v1/email/messages/{r.json()['message_id']}", headers=world.a.live).json()
    assert detail["actor"]["type"] == "api_key" and detail["actor"]["api_key_id"] == world.a.live_key.id
    assert detail["actor"]["external_actor_reference"] == "recruiter-17"
    assert detail["actor"]["external_actor_reference_verified"] is False
    assert detail["external_reference"] == "application_982"
    assert detail["metadata"] == {"interview_id": "int_456"}


def test_idempotency(client, world, db):
    first = _send(client, world.a.live, key="interview-app-982-round-2-v1")
    again = _send(client, world.a.live, key="interview-app-982-round-2-v1")
    assert first.status_code == 202 and again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["message_id"] == first.json()["message_id"]
    changed = _send(client, world.a.live, send_body(subject="Changed"), key="interview-app-982-round-2-v1")
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "idempotency_conflict"
    assert db.query(EmailMessage).filter_by(company_id=world.a.id).count() == 1


@pytest.mark.parametrize("body,code", [
    (send_body(company_id="anything"), None),
    (send_body(template_id="t1", template_version=3, subject=None, html=None), "template_version_unsupported"),
    (send_body(event_type="auth.verify_email"), C.R_EVENT_NOT_ALLOWED),
    (send_body(event_type="platform.tenant_welcome"), C.R_EVENT_NOT_ALLOWED),
    (send_body(template_id="t1"), None),
    ({"to": [{"email": "c@example.net"}], "subject": "s"}, None),
    (send_body(subject="a\r\nBcc: x@evil.example"), "header_invalid"),
])
def test_rejected_requests(client, world, db, body, code):
    r = _send(client, world.a.live, body)
    assert r.status_code == 422, r.text
    if code:
        assert r.json()["detail"]["code"] == code
    assert db.query(EmailMessage).filter_by(company_id=world.a.id).count() == 0


def test_unconfigured_company_is_blocked_not_sent_from_platform(client, world, db):
    from src.models.models import EmailRoute
    db.query(EmailRoute).filter_by(company_id=world.a.id).delete()
    db.commit()
    r = _send(client, world.a.live)
    assert r.status_code == 202
    assert r.json()["status"] == C.BLOCKED and r.json()["status_reason"] == C.R_NO_SENDER
    assert r.json()["sender"]["email"] is None


def test_explicit_sender_must_be_usable(client, world, db):
    world.a.sender.enabled = False
    db.commit()
    r = _send(client, world.a.live, send_body(sender_id=world.a.sender.id))
    assert r.status_code == 202 and r.json()["status"] == C.BLOCKED
    assert r.json()["status_reason"] == C.R_SENDER_DISABLED


def test_test_and_live_data_are_separate(client, world):
    live_id = _send(client, world.a.live).json()["message_id"]
    test_id = _send(client, world.a.test).json()["message_id"]
    assert client.get(f"/api/v1/email/messages/{test_id}", headers=world.a.live).status_code == 404
    assert client.get(f"/api/v1/email/messages/{live_id}", headers=world.a.test).status_code == 404
    live_list = [m["message_id"] for m in client.get("/api/v1/email/messages", headers=world.a.live).json()["messages"]]
    test_list = client.get("/api/v1/email/messages", headers=world.a.test).json()["messages"]
    assert live_list == [live_id] and [m["message_id"] for m in test_list] == [test_id]
    assert test_list[0]["test"] is True


def test_test_key_never_reaches_a_server(client, world, monkeypatch):
    calls = []
    monkeypatch.setattr("src.email_api.adapters.smtp.SMTPAdapter.send", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("EMAIL_SYNC", "1")
    ok = _send(client, world.a.test).json()["message_id"]
    bad = _send(client, world.a.test, send_body(to=[{"email": "fail+x@example.net"}])).json()["message_id"]
    assert client.get(f"/api/v1/email/messages/{ok}", headers=world.a.test).json()["status"] == C.ACCEPTED
    assert client.get(f"/api/v1/email/messages/{bad}", headers=world.a.test).json()["status"] == C.FAILED
    assert calls == []


def test_cancel_and_retry(client, world):
    mid = _send(client, world.a.live).json()["message_id"]
    r = client.post(f"/api/v1/email/messages/{mid}/cancel", headers=world.a.live)
    assert r.status_code == 200 and r.json()["status"] == C.CANCELLED
    again = client.post(f"/api/v1/email/messages/{mid}/cancel", headers=world.a.live)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "not_cancellable"
    retry = client.post(f"/api/v1/email/messages/{mid}/retry", headers=world.a.live)
    assert retry.status_code == 409 and retry.json()["detail"]["code"] == "not_retryable"


def test_quota(client, world, db):
    world.a.live_key.hourly_quota = 2
    db.commit()
    assert _send(client, world.a.live).status_code == 202
    assert _send(client, world.a.live, send_body(subject="2")).status_code == 202
    r = _send(client, world.a.live, send_body(subject="3"))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # The quota is for API keys; the company's own signed-in admins are not metered.
    assert _send(client, world.a.admin, send_body(subject="session")).status_code == 202


def test_list_paginates(client, world):
    ids = [_send(client, world.a.live, send_body(subject=f"m{i}")).json()["message_id"] for i in range(5)]
    seen, cursor = [], None
    while True:
        url = "/api/v1/email/messages?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=world.a.live).json()
        seen += [m["message_id"] for m in page["messages"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == 5


def test_auth_required(client, world):
    assert client.post("/api/v1/email/messages", json=send_body()).status_code == 401
    bad = {"Authorization": "Bearer hf_live_" + "x" * 40}
    r = client.post("/api/v1/email/messages", json=send_body(), headers=bad)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "api_key_invalid"


def test_hr_can_send_but_not_manage_connections(client, world):
    assert _send(client, world.a.hr).status_code == 202
    assert client.get("/api/v1/email/connections", headers=world.a.hr).status_code == 200
    r = client.post(f"/api/v1/email/connections/{world.a.conn.id}/disconnect", headers=world.a.hr)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "insufficient_scope"


def test_candidate_cannot_use_email_api(client, world, candidate_headers):
    assert client.get("/api/v1/email/messages", headers=candidate_headers).status_code == 403


# ── connections and senders ─────────────────────────────────────────────────

SMTP = {"name": "ABC Recruitment Mailbox", "host": "mail.abc.example", "port": 465,
        "security": "implicit_tls", "username": "hr@abc.example", "password": "app-pw",
        "sender_email": "hr@abc.example", "sender_name": "ABC Recruitment"}


def test_create_smtp_connection(client, world, monkeypatch):
    monkeypatch.setenv("EMAIL_DELIVERY_MODE", "null")
    r = client.post("/api/v1/email/connections/smtp", json=SMTP, headers=world.a.live)
    assert r.status_code == 201, r.text
    conn, sender = r.json()["connection"], r.json()["sender"]
    assert conn["status"] == C.CONN_ACTIVE and conn["has_credential"]
    assert sender["email"] == "hr@abc.example" and sender["verification_status"] == C.SENDER_PRIMARY
    listing = client.get("/api/v1/email/connections", headers=world.a.live).text
    assert "app-pw" not in listing and "credential_enc" not in listing


@pytest.mark.parametrize("change,code", [
    ({"port": 25}, "port_not_allowed"),
    ({"host": "10.0.0.5"}, "host_ip_literal"),
    ({"sender_email": "ceo@other.example"}, "sender_not_authorised"),
])
def test_create_smtp_connection_rejected(client, world, db, change, code):
    r = client.post("/api/v1/email/connections/smtp", json={**SMTP, **change}, headers=world.a.live)
    assert r.status_code == 422 and r.json()["detail"]["code"] == code
    assert db.query(EmailConnection).filter_by(company_id=world.a.id).count() == 1  # the fixture's


def test_private_destination_rejected(client, world, monkeypatch):
    from src.email_api import ssrf
    monkeypatch.setattr(ssrf, "_system_resolve", lambda host, port: ["169.254.169.254"])
    r = client.post("/api/v1/email/connections/smtp", json=SMTP, headers=world.a.live)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "destination_not_allowed"


def test_test_keys_cannot_touch_mailboxes(client, world):
    r = client.post("/api/v1/email/connections/smtp", json=SMTP, headers=world.a.test)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "live_key_required"


def test_connection_test_send(client, world, monkeypatch):
    monkeypatch.setenv("EMAIL_DELIVERY_MODE", "null")
    url = f"/api/v1/email/connections/{world.a.conn.id}/test"
    r = client.post(url, json={"to": "stranger@example.net"}, headers=world.a.live)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "test_recipient_not_allowed"
    r = client.post(url, headers=world.a.live)
    assert r.status_code == 200 and r.json()["sent"] is True, r.text
    assert r.json()["connection"]["verification_state"] == "verified"


def test_disconnect_blocks_future_mail(client, world):
    r = client.post(f"/api/v1/email/connections/{world.a.conn.id}/disconnect", headers=world.a.live)
    assert r.status_code == 200 and r.json()["connection"]["status"] == C.CONN_DISCONNECTED
    sent = _send(client, world.a.live).json()
    assert sent["status"] == C.BLOCKED and sent["status_reason"] == C.R_ROUTE_SENDER_UNAVAILABLE


def test_senders(client, world):
    r = client.post("/api/v1/email/senders", json={"connection_id": world.a.conn.id,
                                                   "email": "careers@alpha.example", "label": "Careers",
                                                   "display_name": "Alpha Careers"}, headers=world.a.live)
    assert r.status_code == 201 and r.json()["verification_status"] == C.SENDER_ALIAS
    sid = r.json()["id"]
    r = client.post("/api/v1/email/senders", json={"connection_id": world.a.conn.id,
                                                   "email": "ceo@elsewhere.example", "label": "Spoof"},
                    headers=world.a.live)
    assert r.status_code == 422
    r = client.patch(f"/api/v1/email/senders/{sid}", json={"display_name": "Careers Team", "enabled": False},
                     headers=world.a.live)
    assert r.status_code == 200 and r.json()["display_name"] == "Careers Team" and not r.json()["enabled"]
    r = client.patch(f"/api/v1/email/senders/{sid}", json={"email": "x@alpha.example"}, headers=world.a.live)
    assert r.status_code == 422   # the address is immutable
    labels = [s["label"] for s in client.get("/api/v1/email/senders", headers=world.a.live).json()["senders"]]
    assert labels == ["Careers", "HR"]
