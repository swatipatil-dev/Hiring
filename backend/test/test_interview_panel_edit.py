"""PUT /api/v1/hr/interviews/{id}/panel — swapping who runs a scheduled interview.

``panel_members`` used to be write-once: set when the interview was created, and otherwise
touched only by the deactivation cascade. Changing an interviewer meant cancelling and
re-booking.

Two behaviours are deliberate and pinned here:
* Only the DELTA is notified. Re-inviting the whole panel on every edit trains people to
  ignore panel invites, and the unchanged members already have the meeting.
* ``interviewer_name``/``interviewer_email`` are re-synced to ``panel[0]``, the lead
  invariant documented at models.py:604 — the interviewer-side list matches on those.
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
def panel_world(client, db):
    from src.models.models import Candidate, JobPosting

    cid = _make_company(db, "Panel Co")
    hr_user_id, hr_profile = _make_hr(db, cid, "hr-panel@example.com")
    dept_id = _make_department(db, cid)

    alice, alice_p = _make_interviewer(db, cid, "alice@example.com", department_id=dept_id)
    bob, bob_p = _make_interviewer(db, cid, "bob@example.com", department_id=dept_id)
    carol, carol_p = _make_interviewer(db, cid, "carol@example.com", department_id=dept_id)

    job_id = _make_job(db, cid, title="Backend Dev")
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    job.hr_id = hr_profile.id
    job.department_id = dept_id

    cand = Candidate(
        id=str(uuid.uuid4()),
        company_id=cid,
        job_id=job_id,
        first_name="Cand",
        last_name="One",
        email=f"cand-{uuid.uuid4().hex[:6]}@example.com",
        current_stage="technical_interview",
    )
    db.add(cand)
    db.flush()

    iv = _make_scheduled_interview(
        db, cid, job_id,
        [_panel_entry(alice, alice_p), _panel_entry(bob, bob_p)],
    )
    iv.candidate_id = cand.id
    db.commit()

    return {
        "company_id": cid,
        "job_id": job_id,
        "interview_id": iv.id,
        "alice_p": alice_p, "bob_p": bob_p, "carol_p": carol_p,
        "carol": carol,
        "hr": _login(client, "hr-panel@example.com"),
    }


def _put(client, w, body, headers=None):
    return client.put(
        f"/api/v1/hr/interviews/{w['interview_id']}/panel",
        json=body,
        headers=headers or w["hr"],
    )


def test_swapping_a_panelist_rewrites_the_roster_and_lead(client, db, panel_world):
    from src.models.models import Interview

    w = panel_world
    resp = _put(client, w, {"interviewer_ids": [str(w["carol_p"].id), str(w["bob_p"].id)]})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["added"] == ["carol@example.com"]
    assert body["removed"] == ["alice@example.com"]
    assert body["panel"] == ["carol@example.com", "bob@example.com"]

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == w["interview_id"]).first()
    panel = json.loads(iv.panel_members)
    assert [m["email"] for m in panel] == ["carol@example.com", "bob@example.com"]
    # lead mirror re-synced to panel[0]
    assert iv.interviewer_email == "carol@example.com"


def test_added_member_is_notified_and_unchanged_member_is_not(client, db, panel_world):
    """Only the delta gets told."""
    from src.models.models import Notification

    w = panel_world
    _put(client, w, {"interviewer_ids": [str(w["bob_p"].id), str(w["carol_p"].id)]})
    db.expire_all()

    carol_notes = db.query(Notification).filter(
        Notification.user_id == w["carol"].id,
    ).all()
    assert carol_notes, "newly added panelist should be notified"

    # Bob was on the panel before and after — no new notification for him.
    bob_user_id = db.query(type(w["carol"])).filter(
        type(w["carol"]).email == "bob@example.com"
    ).first().id
    bob_notes = db.query(Notification).filter(Notification.user_id == bob_user_id).all()
    assert not bob_notes, "unchanged panelist must not be re-notified"


def test_notify_false_suppresses_notifications(client, db, panel_world):
    from src.models.models import Notification

    w = panel_world
    resp = _put(client, w, {
        "interviewer_ids": [str(w["carol_p"].id)],
        "notify": False,
    })
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert not db.query(Notification).filter(
        Notification.user_id == w["carol"].id
    ).all()


def test_emptying_the_panel_flags_needs_interviewer(client, db, panel_world):
    from src.models.models import Interview

    w = panel_world
    resp = _put(client, w, {"interviewer_ids": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["needs_interviewer"] is True

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == w["interview_id"]).first()
    assert not iv.panel_members
    assert iv.interviewer_email == ""


def test_deactivated_pick_is_refused(client, db, panel_world):
    from src.models.models import Interview, User

    w = panel_world
    carol_user = db.query(User).filter(User.email == "carol@example.com").first()
    carol_user.is_active = False
    db.commit()

    resp = _put(client, w, {"interviewer_ids": [str(w["carol_p"].id)]})
    assert resp.status_code == 400, resp.text
    assert "deactivated" in resp.json()["detail"].lower()

    # ...and the existing panel is untouched
    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == w["interview_id"]).first()
    assert [m["email"] for m in json.loads(iv.panel_members)] == [
        "alice@example.com", "bob@example.com"
    ]


def test_cannot_repanel_a_completed_interview(client, db, panel_world):
    from src.models.models import Interview

    w = panel_world
    iv = db.query(Interview).filter(Interview.id == w["interview_id"]).first()
    iv.status = "COMPLETED"
    db.commit()

    resp = _put(client, w, {"interviewer_ids": [str(w["carol_p"].id)]})
    assert resp.status_code == 409, resp.text


def test_cannot_repanel_a_test_template(client, db, panel_world):
    from test.test_tenant_isolation import _make_interview

    w = panel_world
    template_id = _make_interview(db, w["company_id"], job_id=w["job_id"])
    db.commit()

    resp = client.put(
        f"/api/v1/hr/interviews/{template_id}/panel",
        json={"interviewer_ids": [str(w["carol_p"].id)]},
        headers=w["hr"],
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.security
def test_cross_tenant_panel_edit_is_not_found(client, db, panel_world):
    w = panel_world
    other_cid = _make_company(db, "Other Panel Co")
    _make_user(db, "hr-other-panel@example.com", "hr", other_cid)
    db.commit()

    resp = _put(client, w, {"interviewer_ids": []},
                headers=_login(client, "hr-other-panel@example.com"))
    assert resp.status_code == 404, resp.text


def test_manual_panelist_can_be_added_by_email(client, db, panel_world):
    from src.models.models import Interview

    w = panel_world
    resp = _put(client, w, {
        "interviewer_ids": [str(w["bob_p"].id)],
        "manual_panelists": [{"name": "Ext Ernal", "email": "ext@example.com"}],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["panel"] == ["bob@example.com", "ext@example.com"]

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == w["interview_id"]).first()
    panel = json.loads(iv.panel_members)
    assert panel[1]["name"] == "Ext Ernal"
    assert panel[1]["user_id"] is None
