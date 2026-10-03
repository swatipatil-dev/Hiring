"""
A candidate whose profile row predates tenancy (company_id NULL) could not load
their own profile: the tenant filter hid the row, _get_or_create_profile tried to
insert a second one for the same user_id, and the unique constraint turned
GET /candidate/profile into a 500.
"""

import uuid

import pytest

PASSWORD = "Pass@1234"


def _company(db):
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name="Orphan Co", slug=f"op-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c


def _candidate(db, company):
    from src.auth import get_password_hash
    from src.models.models import User
    u = User(id=str(uuid.uuid4()), email=f"cand_{uuid.uuid4().hex[:8]}@orphan.com",
             password_hash=get_password_hash(PASSWORD), role="candidate", first_name="Swati",
             last_name="Tester", is_active=True, email_verified=True, company_id=company.id)
    db.add(u)
    db.flush()
    return u


def _headers(client, user):
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.parametrize("stale_company", ["null", "other"])
def test_profile_with_stale_company_id_loads_and_is_adopted(client, db, stale_company):
    from src.models.models import CandidateProfileExt, CandidateSkill

    company = _company(db)
    user = _candidate(db, company)
    stale = None if stale_company == "null" else _company(db).id

    profile = CandidateProfileExt(user_id=user.id, company_id=stale, headline="Data analyst")
    db.add(profile)
    db.flush()
    db.add(CandidateSkill(profile_id=profile.id, company_id=stale, skill_name="SQL"))
    db.flush()

    resp = client.get("/api/v1/candidate/profile", headers=_headers(client, user))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The existing row, with its sections — not a fresh empty one.
    assert body["headline"] == "Data analyst"
    assert [s["skill_name"] for s in body["skills"]] == ["SQL"]

    db.expire_all()
    rows = db.query(CandidateProfileExt).filter(CandidateProfileExt.user_id == user.id).all()
    assert len(rows) == 1
    assert rows[0].company_id == company.id
    skill = db.query(CandidateSkill).filter(CandidateSkill.profile_id == profile.id).one()
    assert skill.company_id == company.id
