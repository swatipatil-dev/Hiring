"""Company API keys: shown once, hashed, scoped, revocable, rotatable, audited."""
from datetime import datetime, timedelta

from src.email_api import api_keys, constants as C
from src.models.models import AuditLog, TenantApiKey
from test.email_api_world import world, send_body  # noqa: F401  (fixture)

KEYS = "/api/v1/admin/api-keys"


def _create(client, headers, **kw):
    body = {"name": "ATS integration", "mode": "live", "scopes": ["email:send", "email:read"]}
    body.update(kw)
    return client.post(KEYS, json=body, headers=headers)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_created_key_is_shown_once_and_stored_hashed(client, world, db):
    r = _create(client, world.a.admin)
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    assert token.startswith("hf_live_") and r.json()["key_prefix"] == token[:16]
    row = db.query(TenantApiKey).filter_by(id=r.json()["id"]).one()
    assert row.key_hash != token and token not in row.key_hash and row.company_id == world.a.id
    listing = client.get(KEYS, headers=world.a.admin).json()
    assert token not in str(listing) and "key_hash" not in str(listing)
    assert db.query(AuditLog).filter_by(action="api_key.create", company_id=world.a.id).count() == 1


def test_key_sends_within_its_scopes(client, world):
    token = _create(client, world.a.admin).json()["token"]
    r = client.post("/api/v1/email/messages", json=send_body(), headers=_auth(token))
    assert r.status_code == 202
    read_only = _create(client, world.a.admin, name="ro", scopes=["email:read"]).json()["token"]
    r = client.post("/api/v1/email/messages", json=send_body(), headers=_auth(read_only))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "insufficient_scope"


def test_only_admin_sessions_manage_keys(client, world):
    assert _create(client, world.a.hr).status_code == 403
    # A key can never mint keys.
    assert client.get(KEYS, headers=world.a.live).status_code == 401


def test_unknown_scope_rejected(client, world):
    r = _create(client, world.a.admin, scopes=["email:send", "billing:write"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "scope_invalid"


def test_revoked_and_expired_keys_401(client, world, db):
    r = client.post(f"{KEYS}/{world.a.live_key.id}/revoke", headers=world.a.admin)
    assert r.status_code == 200 and r.json()["state"] == "revoked"
    r = client.get("/api/v1/email/messages", headers=world.a.live)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "api_key_revoked"
    world.a.test_key.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    r = client.get("/api/v1/email/messages", headers=world.a.test)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "api_key_expired"


def test_right_prefix_wrong_secret(client, world):
    token = world.a.live["Authorization"].split()[1]
    forged = token[:16] + "A" * (len(token) - 16)
    r = client.get("/api/v1/email/messages", headers=_auth(forged))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "api_key_invalid"


def test_ip_restriction(client, world, db):
    world.a.live_key.allowed_ips = ["203.0.113.0/24"]
    db.commit()
    r = client.get("/api/v1/email/messages", headers=world.a.live)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "ip_not_allowed"


def test_rotation_overlaps_then_old_key_expires(client, world, db):
    r = client.post(f"{KEYS}/{world.a.live_key.id}/rotate", json={"overlap_hours": 2}, headers=world.a.admin)
    assert r.status_code == 201, r.text
    new = r.json()["token"]
    assert r.json()["rotated_from_id"] == world.a.live_key.id
    assert client.get("/api/v1/email/messages", headers=world.a.live).status_code == 200
    assert client.get("/api/v1/email/messages", headers=_auth(new)).status_code == 200
    db.refresh(world.a.live_key)
    assert world.a.live_key.expires_at <= datetime.utcnow() + timedelta(hours=2, seconds=5)
    world.a.live_key.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/v1/email/messages", headers=world.a.live).status_code == 401
    assert client.get("/api/v1/email/messages", headers=_auth(new)).status_code == 200


def test_suspended_company_keys_stop_working(client, world, db):
    world.a.company.is_active = False
    db.commit()
    r = client.get("/api/v1/email/messages", headers=world.a.live)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "company_inactive"


def test_last_used_is_recorded(client, world, db):
    client.get("/api/v1/email/messages", headers=world.a.live)
    db.refresh(world.a.live_key)
    assert world.a.live_key.last_used_at is not None


def test_hash_uses_pepper_when_configured(monkeypatch):
    plain = api_keys.hash_token("hf_live_x")
    monkeypatch.setenv("EMAIL_API_KEY_PEPPER", "pepper")
    assert api_keys.hash_token("hf_live_x") != plain and len(api_keys.hash_token("hf_live_x")) == 64
