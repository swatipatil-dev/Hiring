"""Spec acceptance: Tenant A cannot access or send through Tenant B's resources."""
import pytest

from src.models.models import EmailMessage
from test.email_api_world import world, send_body  # noqa: F401  (fixture)


@pytest.fixture()
def b_message(client, world):
    r = client.post("/api/v1/email/messages", json=send_body(), headers=world.b.live)
    assert r.status_code == 202
    return r.json()["message_id"]


@pytest.mark.parametrize("cred", ["admin", "live"])
def test_cannot_read_or_act_on_another_companys_message(client, world, b_message, cred):
    h = getattr(world.a, cred)
    assert client.get(f"/api/v1/email/messages/{b_message}", headers=h).status_code == 404
    assert client.post(f"/api/v1/email/messages/{b_message}/cancel", headers=h).status_code == 404
    assert client.post(f"/api/v1/email/messages/{b_message}/retry", headers=h).status_code == 404
    listed = client.get("/api/v1/email/messages", headers=h).json()["messages"]
    assert b_message not in [m["message_id"] for m in listed]


@pytest.mark.parametrize("cred", ["admin", "live"])
def test_cannot_use_another_companys_sender(client, world, db, cred):
    h = getattr(world.a, cred)
    r = client.post("/api/v1/email/messages", json=send_body(sender_id=world.b.sender.id), headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sender_not_found"
    assert db.query(EmailMessage).filter_by(company_id=world.a.id).count() == 0
    assert db.query(EmailMessage).filter_by(company_id=world.b.id).count() == 0


def test_cannot_touch_another_companys_connection_or_sender(client, world):
    h = world.a.live
    assert client.post(f"/api/v1/email/connections/{world.b.conn.id}/test", headers=h).status_code == 404
    assert client.post(f"/api/v1/email/connections/{world.b.conn.id}/disconnect", headers=h).status_code == 404
    r = client.patch(f"/api/v1/email/senders/{world.b.sender.id}", json={"enabled": False}, headers=h)
    assert r.status_code == 404
    r = client.post("/api/v1/email/senders", json={"connection_id": world.b.conn.id,
                                                   "email": "x@beta.example", "label": "x"}, headers=h)
    assert r.status_code == 404
    assert world.b.conn.id not in client.get("/api/v1/email/connections", headers=h).text
    assert world.b.sender.id not in client.get("/api/v1/email/senders", headers=h).text


def test_body_company_id_is_never_authority(client, world, db):
    r = client.post("/api/v1/email/messages", json=send_body(company_id=world.b.id), headers=world.a.live)
    assert r.status_code == 422
    assert db.query(EmailMessage).filter_by(company_id=world.b.id).count() == 0


def test_cannot_manage_another_companys_keys(client, world):
    r = client.post(f"/api/v1/admin/api-keys/{world.b.live_key.id}/revoke", headers=world.a.admin)
    assert r.status_code == 404
    assert client.get("/api/v1/email/messages", headers=world.b.live).status_code == 200
    keys = client.get("/api/v1/admin/api-keys", headers=world.a.admin).json()["api_keys"]
    assert world.b.live_key.id not in [k["id"] for k in keys]
