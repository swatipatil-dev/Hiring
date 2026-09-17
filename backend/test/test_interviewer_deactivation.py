"""Deactivating an interviewer must detach them from all ACTIVE assignments.

Before this, deactivation only flipped ``users.is_active``. Because ``src/auth.py`` then
refuses that user's login, everything still pointing at them became a black hole: they
stayed the default interviewer on their jobs, kept appearing in HR's dropdown and panel
picker, kept being auto-assigned to brand-new jobs, and kept receiving panel invites for
interviews they could never open.

The deliberate non-goal, pinned by ``test_completed_interview_and_feedback_untouched``:
COMPLETED interviews and InterviewFeedback keep the name. They record who evaluated whom
and are the hiring audit trail — blanking them would make past scorecards anonymous.
"""
import json
import uuid

import pytest

from test.test_tenant_isolation import (  # reuse the established fixtures/helpers
    _make_company,
    _make_user,
    _login,
    _make_job,
)


def _make_interviewer(db, company_id, email, *, department_id=None, active=True):
    """A user with role=interviewer plus the InterviewerProfile that carries assignments."""
    from src.models.models import InterviewerProfile, User

    user_id = _make_user(db, email, "interviewer", company_id)
    user = db.query(User).filter(User.id == user_id).first()
    user.is_active = active
    profile = InterviewerProfile(
        id=str(uuid.uuid4()),
        company_id=company_id,
        user_id=user_id,
        department_id=department_id,
        employee_id=f"IV-{uuid.uuid4().hex[:8]}",
        years_experience=5,
    )
    db.add(profile)
    db.flush()
    return user, profile


def _make_department(db, company_id, name="Engineering"):
    from src.models.models import Department

    dept = Department(id=str(uuid.uuid4()), company_id=company_id, name=name, description="d")
    db.add(dept)
    db.flush()
    return dept.id


def _make_hr(db, company_id, email):
    from src.models.models import HRProfile

    user_id = _make_user(db, email, "hr", company_id)
    profile = HRProfile(
        id=str(uuid.uuid4()),
        company_id=company_id,
        user_id=user_id,
        employee_id=f"HR-{uuid.uuid4().hex[:8]}",
    )
    db.add(profile)
    db.flush()
    return user_id, profile


def _make_scheduled_interview(db, company_id, job_id, panel, *, status="SCHEDULED", when_days=7):
    """A live panel interview `when_days` in the future."""
    from datetime import datetime, timedelta

    from src.models.models import Interview

    lead = panel[0] if panel else None
    iv = Interview(
        company_id=company_id,
        title="Panel Interview",
        job_id=job_id,
        status=status,
        is_scheduled_interview=True,
        scheduled_datetime=datetime.utcnow() + timedelta(days=when_days),
        duration_minutes=60,
        interviewer_name=lead["name"] if lead else "",
        interviewer_email=lead["email"] if lead else "",
        panel_members=json.dumps(panel) if panel else None,
    )
    db.add(iv)
    db.flush()
    return iv


def _panel_entry(user, profile):
    return {
        "id": str(profile.id),
        "name": f"{user.first_name} {user.last_name}".strip(),
        "email": user.email,
        "user_id": str(user.id),
    }


@pytest.fixture()
def deactivation_world(client, db):
    """One company: an admin, an HR owner, a department, and two interviewers."""
    from src.models.models import JobPosting

    cid = _make_company(db, "Deactivation Co")
    _make_user(db, "admin-deact@example.com", "admin", cid)
    hr_user_id, hr_profile = _make_hr(db, cid, "hr-deact@example.com")
    dept_id = _make_department(db, cid)

    leaver, leaver_profile = _make_interviewer(db, cid, "leaver@example.com", department_id=dept_id)
    stayer, stayer_profile = _make_interviewer(db, cid, "stayer@example.com", department_id=dept_id)

    job_id = _make_job(db, cid, title="Backend Dev")
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    job.interviewer_id = leaver_profile.id
    job.hr_id = hr_profile.id
    job.department_id = dept_id
    db.commit()

    return {
        "company_id": cid,
        "dept_id": dept_id,
        "job_id": job_id,
        "hr_user_id": hr_user_id,
        "leaver": leaver,
        "leaver_profile": leaver_profile,
        "stayer": stayer,
        "stayer_profile": stayer_profile,
    }


def _deactivate(client, db, world):
    """Deactivate the leaver through the real admin endpoint."""
    headers = _login(client, "admin-deact@example.com")
    resp = client.delete(f"/api/v1/admin/users/{world['leaver'].id}", headers=headers)
    assert resp.status_code == 200, resp.text
    db.expire_all()
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Job defaults
# ---------------------------------------------------------------------------

def test_deactivation_clears_job_default_interviewer(client, db, deactivation_world):
    from src.models.models import JobPosting

    body = _deactivate(client, db, deactivation_world)

    job = db.query(JobPosting).filter(JobPosting.id == deactivation_world["job_id"]).first()
    assert job.interviewer_id is None
    assert body["jobs_unassigned"] == 1


def test_deactivation_notifies_the_jobs_hr(client, db, deactivation_world):
    from src.models.models import Notification

    _deactivate(client, db, deactivation_world)

    notes = db.query(Notification).filter(
        Notification.user_id == deactivation_world["hr_user_id"],
        Notification.type == "interviewer_unassigned",
    ).all()
    assert notes, "the job's HR owner must be told their job lost its interviewer"


# ---------------------------------------------------------------------------
# 2. Upcoming panels
# ---------------------------------------------------------------------------

def test_upcoming_panel_drops_leaver_and_promotes_next_lead(client, db, deactivation_world):
    from src.models.models import Interview

    w = deactivation_world
    iv = _make_scheduled_interview(
        db, w["company_id"], w["job_id"],
        [_panel_entry(w["leaver"], w["leaver_profile"]),
         _panel_entry(w["stayer"], w["stayer_profile"])],
    )
    db.commit()
    iv_id = iv.id

    _deactivate(client, db, w)

    iv = db.query(Interview).filter(Interview.id == iv_id).first()
    panel = json.loads(iv.panel_members)
    assert [m["email"] for m in panel] == ["stayer@example.com"]
    # interviewer_name/email mirror panel[0] by design (models.py:604)
    assert iv.interviewer_email == "stayer@example.com"


def test_sole_panelist_removed_flags_interview_as_needing_one(client, db, deactivation_world):
    from src.models.models import Interview

    w = deactivation_world
    iv = _make_scheduled_interview(
        db, w["company_id"], w["job_id"],
        [_panel_entry(w["leaver"], w["leaver_profile"])],
    )
    db.commit()
    iv_id = iv.id

    body = _deactivate(client, db, w)

    iv = db.query(Interview).filter(Interview.id == iv_id).first()
    assert not iv.panel_members
    assert iv.interviewer_email == ""
    assert body["interviews_needing_interviewer"] == 1

    # ...and HR's interview list surfaces it
    hr_headers = _login(client, "hr-deact@example.com")
    rows = client.get("/api/v1/hr/interviews", headers=hr_headers).json()
    row = next(r for r in rows if r["id"] == iv_id)
    assert row["needs_interviewer"] is True


def test_lowercase_scheduled_status_is_still_picked_up(client, db, deactivation_world):
    """The app writes "SCHEDULED" but compares lowercase in several places, so rows of
    both casings exist. A plain == would silently skip these."""
    from src.models.models import Interview

    w = deactivation_world
    iv = _make_scheduled_interview(
        db, w["company_id"], w["job_id"],
        [_panel_entry(w["leaver"], w["leaver_profile"])],
        status="scheduled",
    )
    db.commit()
    iv_id = iv.id

    _deactivate(client, db, w)

    iv = db.query(Interview).filter(Interview.id == iv_id).first()
    assert iv.interviewer_email == "", "lowercase-status interview was skipped"


# ---------------------------------------------------------------------------
# 3. History is preserved - the deliberate non-goal
# ---------------------------------------------------------------------------

def test_completed_interview_and_feedback_untouched(client, db, deactivation_world):
    """Completed work records WHO evaluated the candidate. That must survive."""
    from src.models.models import Interview, InterviewFeedback

    w = deactivation_world
    iv = _make_scheduled_interview(
        db, w["company_id"], w["job_id"],
        [_panel_entry(w["leaver"], w["leaver_profile"])],
        status="COMPLETED", when_days=-7,
    )
    db.flush()
    fb = InterviewFeedback(
        id=str(uuid.uuid4()),
        company_id=w["company_id"],
        interview_id=iv.id,
        interviewer_id=w["leaver_profile"].id,
        overall_score=80,
        hire_recommendation="hire",
    )
    db.add(fb)
    db.commit()
    iv_id, fb_id = iv.id, fb.id

    _deactivate(client, db, w)

    iv = db.query(Interview).filter(Interview.id == iv_id).first()
    assert iv.interviewer_email == "leaver@example.com"
    assert json.loads(iv.panel_members)[0]["email"] == "leaver@example.com"

    fb = db.query(InterviewFeedback).filter(InterviewFeedback.id == fb_id).first()
    assert fb.interviewer_id == w["leaver_profile"].id


# ---------------------------------------------------------------------------
# 4. They cannot be re-assigned afterwards
# ---------------------------------------------------------------------------

def test_hr_interviewer_list_omits_deactivated(client, db, deactivation_world):
    _deactivate(client, db, deactivation_world)

    headers = _login(client, "hr-deact@example.com")
    rows = client.get("/api/v1/hr/interviewers", headers=headers).json()
    emails = {r["email"] for r in rows}
    assert "leaver@example.com" not in emails
    assert "stayer@example.com" in emails


def test_assign_interviewer_rejects_deactivated_with_400(client, db, deactivation_world):
    w = deactivation_world
    _deactivate(client, db, w)

    headers = _login(client, "hr-deact@example.com")
    resp = client.put(
        f"/api/v1/hr/jobs/{w['job_id']}/assign-interviewer",
        params={"interviewer_id": str(w["leaver_profile"].id)},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "deactivated" in resp.json()["detail"].lower()


def test_new_job_does_not_auto_assign_a_deactivated_interviewer(client, db, deactivation_world):
    """The auto-assign on job create filtered department_id only - it never joined User,
    so it happily picked someone who had just been switched off."""
    from src.models.models import JobPosting

    w = deactivation_world
    # Leave only the deactivated interviewer in the department.
    w["stayer_profile"].department_id = None
    db.commit()
    _deactivate(client, db, w)

    headers = _login(client, "hr-deact@example.com")
    resp = client.post("/api/v1/hr/jobs", json={
        "title": "Fresh Role",
        "description": "d",
        "requirements": "r",
        "department_id": w["dept_id"],
        "location": "Remote",
        "employment_type": "full_time",
        "experience_years": 1,
        "required_skills": ["python"],
    }, headers=headers)
    assert resp.status_code in (200, 201), resp.text

    job = db.query(JobPosting).filter(JobPosting.id == resp.json()["id"]).first()
    assert job.interviewer_id is None


def test_schedule_interview_rejects_deactivated_panelist(client, db, deactivation_world):
    from src.models.models import Candidate

    w = deactivation_world
    cand = Candidate(
        id=str(uuid.uuid4()),
        company_id=w["company_id"],
        job_id=w["job_id"],
        first_name="Cand",
        last_name="One",
        email=f"cand-{uuid.uuid4().hex[:6]}@example.com",
        current_stage="interview",
    )
    db.add(cand)
    db.commit()
    cand_id = cand.id
    _deactivate(client, db, w)

    headers = _login(client, "hr-deact@example.com")
    resp = client.post("/api/v1/hr/schedule-interview", json={
        "candidate_id": cand_id,
        "job_id": w["job_id"],
        "scheduled_datetime": "2030-01-01T10:00:00",
        "mode": "online",
        "meeting_link": "https://example.com/m",
        "interviewer_ids": [str(w["leaver_profile"].id)],
    }, headers=headers)
    assert resp.status_code == 400
    assert "deactivated" in resp.json()["detail"].lower()
