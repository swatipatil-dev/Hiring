"""HR mail templates — /api/v1/hr/mail-templates CRUD.

These used to live in each browser's localStorage, so the things worth pinning
down are the ones that were impossible before: another HR user in the same
company sees the same list, another company sees none of it, and the starter
set is seeded exactly once rather than re-appearing on every read.
"""
import uuid
import pytest


def _make_company(db, name="Tpl Co") -> str:
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c.id


def _create_user(db, email, role="hr", company_id=None, password="Pass@1234"):
    from src.models.models import User
    from src.auth import get_password_hash
    u = User(
        id=str(uuid.uuid4()), email=email,
        password_hash=get_password_hash(password), role=role,
        first_name=role.upper(), last_name="Tester",
        is_active=True, email_verified=True, company_id=company_id,
    )
    db.add(u)
    db.flush()
    return u.id


def _login(client, email, password="Pass@1234"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


NEW_TPL = {"stage": "custom", "name": "My Template",
           "subject": "Hi {{candidate_name}}", "body": "Body text"}


def test_seeds_starter_set_on_first_read(client, db):
    cid = _make_company(db)
    _create_user(db, "hr_seed@test.com", "hr", cid)
    headers = _login(client, "hr_seed@test.com")

    r = client.get("/api/v1/hr/mail-templates", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 9
    assert all(t["is_builtin"] for t in rows)
    assert {t["stage"] for t in rows} == {"offer", "rejection", "interview_invite", "welcome"}


def test_seed_is_idempotent(client, db):
    """A second read must not duplicate the starter set."""
    cid = _make_company(db)
    _create_user(db, "hr_seed2@test.com", "hr", cid)
    headers = _login(client, "hr_seed2@test.com")

    first = client.get("/api/v1/hr/mail-templates", headers=headers).json()
    second = client.get("/api/v1/hr/mail-templates", headers=headers).json()
    assert len(first) == len(second) == 9
    assert [t["id"] for t in first] == [t["id"] for t in second]


def test_crud_round_trip(client, db):
    cid = _make_company(db)
    _create_user(db, "hr_crud@test.com", "hr", cid)
    headers = _login(client, "hr_crud@test.com")
    client.get("/api/v1/hr/mail-templates", headers=headers)   # seed

    created = client.post("/api/v1/hr/mail-templates", json=NEW_TPL, headers=headers)
    assert created.status_code == 200, created.text
    tid = created.json()["id"]
    assert created.json()["name"] == "My Template"
    assert created.json()["is_builtin"] is False

    listed = client.get("/api/v1/hr/mail-templates", headers=headers).json()
    assert any(t["id"] == tid for t in listed)
    assert len(listed) == 10

    updated = client.put(f"/api/v1/hr/mail-templates/{tid}",
                         json={**NEW_TPL, "name": "Renamed", "body": "New body"},
                         headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["body"] == "New body"

    deleted = client.delete(f"/api/v1/hr/mail-templates/{tid}", headers=headers)
    assert deleted.status_code == 200
    assert all(t["id"] != tid for t in
               client.get("/api/v1/hr/mail-templates", headers=headers).json())


def test_second_hr_in_same_company_sees_the_same_templates(client, db):
    """The whole point of moving off localStorage."""
    cid = _make_company(db, "Shared Co")
    _create_user(db, "hr_a@test.com", "hr", cid)
    _create_user(db, "hr_b@test.com", "hr", cid)

    ha = _login(client, "hr_a@test.com")
    client.get("/api/v1/hr/mail-templates", headers=ha)
    made = client.post("/api/v1/hr/mail-templates", json=NEW_TPL, headers=ha).json()

    hb = _login(client, "hr_b@test.com")
    seen = client.get("/api/v1/hr/mail-templates", headers=hb).json()
    assert any(t["id"] == made["id"] for t in seen)


def test_other_company_cannot_read_or_mutate(client, db):
    a = _make_company(db, "Alpha Co")
    b = _make_company(db, "Beta Co")
    _create_user(db, "hr_alpha@test.com", "hr", a)
    _create_user(db, "hr_beta@test.com", "hr", b)

    ha = _login(client, "hr_alpha@test.com")
    client.get("/api/v1/hr/mail-templates", headers=ha)
    mine = client.post("/api/v1/hr/mail-templates", json=NEW_TPL, headers=ha).json()

    hb = _login(client, "hr_beta@test.com")
    theirs = client.get("/api/v1/hr/mail-templates", headers=hb).json()
    assert all(t["id"] != mine["id"] for t in theirs)

    # 404 rather than 403 — a 403 would confirm the id exists.
    assert client.put(f"/api/v1/hr/mail-templates/{mine['id']}",
                      json=NEW_TPL, headers=hb).status_code == 404
    assert client.delete(f"/api/v1/hr/mail-templates/{mine['id']}",
                         headers=hb).status_code == 404


def test_duplicate_name_rejected(client, db):
    cid = _make_company(db)
    _create_user(db, "hr_dup@test.com", "hr", cid)
    headers = _login(client, "hr_dup@test.com")
    client.get("/api/v1/hr/mail-templates", headers=headers)

    assert client.post("/api/v1/hr/mail-templates", json=NEW_TPL, headers=headers).status_code == 200
    assert client.post("/api/v1/hr/mail-templates", json=NEW_TPL, headers=headers).status_code == 400


def test_unknown_stage_falls_back_to_custom(client, db):
    cid = _make_company(db)
    _create_user(db, "hr_stage@test.com", "hr", cid)
    headers = _login(client, "hr_stage@test.com")

    r = client.post("/api/v1/hr/mail-templates",
                    json={**NEW_TPL, "stage": "not_a_stage"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["stage"] == "custom"


def test_response_carries_every_field_the_ui_reads(client, db):
    cid = _make_company(db)
    _create_user(db, "hr_fields@test.com", "hr", cid)
    headers = _login(client, "hr_fields@test.com")

    row = client.get("/api/v1/hr/mail-templates", headers=headers).json()[0]
    for key in ("id", "stage", "name", "subject", "body", "is_builtin", "is_active"):
        assert key in row, f"missing {key}"


def test_candidate_cannot_access(client, db):
    cid = _make_company(db)
    _create_user(db, "cand_tpl@test.com", "candidate", cid)
    headers = _login(client, "cand_tpl@test.com")
    assert client.get("/api/v1/hr/mail-templates", headers=headers).status_code == 403


def test_company_less_hr_rejected(client, db):
    _create_user(db, "hr_notenant@test.com", "hr", None)
    headers = _login(client, "hr_notenant@test.com")
    assert client.get("/api/v1/hr/mail-templates", headers=headers).status_code == 400
