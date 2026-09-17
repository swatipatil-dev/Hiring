"""A user's identity survives deactivation, and cannot be blanked by an edit.

Deactivating must switch off access without erasing who someone is or what they did.
Three separate ways their name could be destroyed are pinned here:

* The account itself. ``DELETE /api/v1/admin/users/{id}`` is a DELETE in name only — the
  row, the name and the profile all survive, which is what makes Activate work and what
  keeps completed interviews attributed to a real person.
* The edit form. ``users.first_name`` is NOT NULL, but ``""`` satisfies that in MySQL, so
  an admin could permanently erase a colleague's name by saving an empty field.
* Another tenant's data. Panel membership is matched on EMAIL, and users are unique on
  ``(company_id, email)`` — so the same address legitimately exists in two companies, and
  an unscoped query let one company's deactivation blank the other's interviews.
"""
import json
import uuid

import pytest

from test.test_tenant_isolation import (
    _make_company,
    _make_user,
    _login,
    _make_job,
)
from test.test_interviewer_deactivation import (
    _make_interviewer,
    _make_hr,
    _make_department,
    _make_scheduled_interview,
    _panel_entry,
)


@pytest.fixture()
def identity_world(client, db):
    from src.models.models import JobPosting

    cid = _make_company(db, "Identity Co")
    _make_user(db, "admin-id@example.com", "admin", cid)
    _hr_user_id, hr_profile = _make_hr(db, cid, "hr-id@example.com")
    dept_id = _make_department(db, cid)

    leaver, leaver_profile = _make_interviewer(db, cid, "leaver-id@example.com",
                                               department_id=dept_id)
    job_id = _make_job(db, cid, title="Backend Dev")
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    job.interviewer_id = leaver_profile.id
    job.hr_id = hr_profile.id
    job.department_id = dept_id
    db.commit()

    return {
        "company_id": cid,
        "job_id": job_id,
        "leaver": leaver,
        "leaver_profile": leaver_profile,
        "admin": _login(client, "admin-id@example.com"),
    }


# ---------------------------------------------------------------------------
# 1. The account survives deactivation
# ---------------------------------------------------------------------------

def test_deactivation_keeps_the_user_row_name_and_profile(client, db, identity_world):
    from src.models.models import InterviewerProfile, User

    w = identity_world
    leaver_id = w["leaver"].id
    before = (w["leaver"].first_name, w["leaver"].last_name, w["leaver"].email)

    resp = client.delete(f"/api/v1/admin/users/{leaver_id}", headers=w["admin"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is False

    db.expire_all()
    user = db.query(User).filter(User.id == leaver_id).first()
    assert user is not None, "the row must never be removed"
    assert (user.first_name, user.last_name, user.email) == before
    assert user.is_active is False

    profile = db.query(InterviewerProfile).filter(
        InterviewerProfile.user_id == leaver_id
    ).first()
    assert profile is not None, "the InterviewerProfile must survive too"


def test_deactivated_user_can_be_reactivated(client, db, identity_world):
    from src.models.models import User

    w = identity_world
    leaver_id = w["leaver"].id
    client.delete(f"/api/v1/admin/users/{leaver_id}", headers=w["admin"])

    resp = client.put(
        f"/api/v1/admin/users/{leaver_id}/status",
        params={"is_active": True},
        headers=w["admin"],
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.query(User).filter(User.id == leaver_id).first().is_active is True


# ---------------------------------------------------------------------------
# 2. A name cannot be blanked through the edit form
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blank", ["", "   "])
def test_admin_cannot_blank_a_users_name(client, db, identity_world, blank):
    from src.models.models import User

    w = identity_world
    leaver_id = w["leaver"].id
    before = w["leaver"].first_name

    resp = client.put(
        f"/api/v1/admin/users/{leaver_id}",
        json={"first_name": blank},
        headers=w["admin"],
    )
    assert resp.status_code == 422, resp.text

    db.expire_all()
    assert db.query(User).filter(User.id == leaver_id).first().first_name == before


def test_a_real_name_edit_still_works_and_is_trimmed(client, db, identity_world):
    from src.models.models import User

    w = identity_world
    resp = client.put(
        f"/api/v1/admin/users/{w['leaver'].id}",
        json={"first_name": "  Renamed  "},
        headers=w["admin"],
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.query(User).filter(User.id == w["leaver"].id).first().first_name == "Renamed"


# ---------------------------------------------------------------------------
# 3. Completed work keeps its attribution
# ---------------------------------------------------------------------------

def test_completed_interview_keeps_the_deactivated_lead_name(client, db, identity_world):
    from src.models.models import Interview

    w = identity_world
    iv = _make_scheduled_interview(
        db, w["company_id"], w["job_id"],
        [_panel_entry(w["leaver"], w["leaver_profile"])],
        status="COMPLETED", when_days=-3,
    )
    db.commit()
    iv_id = iv.id

    client.delete(f"/api/v1/admin/users/{w['leaver'].id}", headers=w["admin"])

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == iv_id).first()
    assert iv.interviewer_name, "a completed interview must still say who ran it"
    assert iv.interviewer_email == "leaver-id@example.com"
    assert json.loads(iv.panel_members)[0]["email"] == "leaver-id@example.com"


# ---------------------------------------------------------------------------
# 4. One tenant's deactivation cannot touch another's records
# ---------------------------------------------------------------------------

@pytest.mark.security
def test_deactivation_does_not_blank_another_companys_interviews(client, db, identity_world):
    """Panels match on email, and (company_id, email) is the uniqueness key — so the same
    address exists in two tenants and an unscoped query blanked the wrong company's rows."""
    from src.models.models import Interview

    w = identity_world
    shared_email = "leaver-id@example.com"

    other_cid = _make_company(db, "Bystander Co")
    other_user, other_profile = _make_interviewer(db, other_cid, shared_email)
    other_job = _make_job(db, other_cid, title="Their Role")
    other_iv = _make_scheduled_interview(
        db, other_cid, other_job, [_panel_entry(other_user, other_profile)],
    )
    db.commit()
    other_iv_id = other_iv.id

    resp = client.delete(f"/api/v1/admin/users/{w['leaver'].id}", headers=w["admin"])
    assert resp.status_code == 200, resp.text

    db.expire_all()
    untouched = db.query(Interview).filter(Interview.id == other_iv_id).first()
    assert untouched.interviewer_email == shared_email
    assert untouched.panel_members, "the other company's panel must be intact"
    assert json.loads(untouched.panel_members)[0]["email"] == shared_email
