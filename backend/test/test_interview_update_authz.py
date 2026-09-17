"""PUT /api/v1/interviewer/interviews/{id} — who may change what.

This endpoint took only ``get_current_user`` and looked the row up by ``Interview.id``
alone, on sequential integer ids. Three holes, each pinned below:

* **Role.** Any authenticated user — a CANDIDATE included — could rewrite any interview.
  The sharp edge is ``security_settings``: a candidate could disable webcam monitoring,
  tab-switch detection and fullscreen enforcement on their own in-flight attempt and then
  sit the exam unproctored. ``evaluation_rules`` (cutoffs, total marks) was equally open.
* **Company.** No tenant filter at all.
* **State.** Templates, candidate attempts and scheduled meetings were all equally mutable.

The endpoint has three real callers and they are NOT equivalent — two save whole wizard
configs onto DRAFT templates (one of them from HR's copy of the wizard), while
``submitMeetingFeedback`` flips a scheduled meeting to COMPLETED. Locking it to templates
alone would 404 the latter, so ``test_panel_member_may_complete_a_scheduled_interview``
and ``test_hr_may_still_save_a_template`` guard both shapes staying open.
"""
import json
import uuid

import pytest

from test.test_tenant_isolation import (
    _make_company,
    _make_user,
    _login,
    _make_job,
    _make_interview,
)


def _make_scheduled(db, company_id, job_id, *, candidate_id=None, panel_email=None,
                    status="SCHEDULED"):
    from datetime import datetime, timedelta

    from src.models.models import Interview

    panel = [{"id": None, "name": "Panelist", "email": panel_email, "user_id": None}] \
        if panel_email else None
    iv = Interview(
        company_id=company_id,
        title="Panel Interview",
        job_id=job_id,
        candidate_id=candidate_id,
        status=status,
        is_scheduled_interview=True,
        scheduled_datetime=datetime.utcnow() + timedelta(days=3),
        duration_minutes=60,
        interviewer_email=panel_email or "",
        panel_members=json.dumps(panel) if panel else None,
    )
    db.add(iv)
    db.flush()
    return iv


def _make_attempt(db, company_id, job_id, candidate_id):
    """A candidate's cloned test attempt — not scheduled, has a candidate."""
    from src.models.models import Interview

    iv = Interview(
        company_id=company_id,
        title="MCQ Attempt",
        job_id=job_id,
        candidate_id=candidate_id,
        status="READY",
        is_scheduled_interview=False,
        security_settings_data=json.dumps({
            "enable_webcam_monitoring": True,
            "enable_tab_switch_detection": True,
            "require_fullscreen": True,
            "copy_paste_allowed": False,
        }),
    )
    db.add(iv)
    db.flush()
    return iv


@pytest.fixture()
def update_world(client, db):
    from src.models.models import Candidate

    cid = _make_company(db, "Update Co")
    _make_user(db, "hr-upd@example.com", "hr", cid)
    _make_user(db, "iv-upd@example.com", "interviewer", cid)
    _make_user(db, "cand-upd@example.com", "candidate", cid)
    job_id = _make_job(db, cid, title="Backend Dev")
    template_id = _make_interview(db, cid, job_id=job_id)

    cand = Candidate(
        id=str(uuid.uuid4()),
        company_id=cid,
        job_id=job_id,
        first_name="Cand",
        last_name="One",
        email="cand-upd@example.com",
        current_stage="interview",
    )
    db.add(cand)
    db.flush()
    attempt = _make_attempt(db, cid, job_id, cand.id)
    scheduled = _make_scheduled(db, cid, job_id, candidate_id=cand.id,
                                panel_email="iv-upd@example.com")
    db.commit()

    return {
        "company_id": cid,
        "job_id": job_id,
        "template_id": template_id,
        "attempt_id": attempt.id,
        "scheduled_id": scheduled.id,
        "hr": _login(client, "hr-upd@example.com"),
        "interviewer": _login(client, "iv-upd@example.com"),
        "candidate": _login(client, "cand-upd@example.com"),
    }


_RELAXED_PROCTORING = {
    "security_settings": {
        "enable_webcam_monitoring": False,
        "enable_tab_switch_detection": False,
        "require_fullscreen": False,
        "copy_paste_allowed": True,
    }
}


@pytest.mark.security
def test_anonymous_cannot_update(client, update_world):
    resp = client.put(
        f"/api/v1/interviewer/interviews/{update_world['template_id']}",
        json={"title": "hijacked"},
    )
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.security
def test_candidate_cannot_update_anything(client, db, update_world):
    from src.models.models import Interview

    resp = client.put(
        f"/api/v1/interviewer/interviews/{update_world['template_id']}",
        json={"title": "hijacked"},
        headers=update_world["candidate"],
    )
    assert resp.status_code == 403, resp.text

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == update_world["template_id"]).first()
    assert iv.title != "hijacked"


@pytest.mark.security
def test_candidate_cannot_disable_proctoring_on_their_own_attempt(client, db, update_world):
    """The proctoring-bypass case: relax the settings on the exam you are about to sit."""
    from src.models.models import Interview

    resp = client.put(
        f"/api/v1/interviewer/interviews/{update_world['attempt_id']}",
        json=_RELAXED_PROCTORING,
        headers=update_world["candidate"],
    )
    assert resp.status_code == 403, resp.text

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == update_world["attempt_id"]).first()
    settings = json.loads(iv.security_settings_data)
    assert settings["enable_webcam_monitoring"] is True
    assert settings["require_fullscreen"] is True


@pytest.mark.security
def test_candidate_test_attempt_is_not_editable_even_by_hr(client, db, update_world):
    from src.models.models import Interview

    resp = client.put(
        f"/api/v1/interviewer/interviews/{update_world['attempt_id']}",
        json=_RELAXED_PROCTORING,
        headers=update_world["hr"],
    )
    assert resp.status_code == 409, resp.text

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == update_world["attempt_id"]).first()
    assert json.loads(iv.security_settings_data)["enable_webcam_monitoring"] is True


def test_hr_may_still_save_a_template(client, db, update_world):
    """The create-test wizard (HR's copy) must keep working."""
    from src.models.models import Interview

    resp = client.put(
        f"/api/v1/interviewer/interviews/{update_world['template_id']}",
        json={"title": "Reworked test", "notes": "n"},
        headers=update_world["hr"],
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == update_world["template_id"]).first()
    assert iv.title == "Reworked test"


def test_panel_member_may_complete_a_scheduled_interview(client, db, update_world):
    """submitMeetingFeedback's {status:'COMPLETED'} call must survive the lockdown."""
    from src.models.models import Interview

    resp = client.put(
        f"/api/v1/interviewer/interviews/{update_world['scheduled_id']}",
        json={"status": "COMPLETED"},
        headers=update_world["interviewer"],
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == update_world["scheduled_id"]).first()
    assert (iv.status or "").upper() == "COMPLETED"


@pytest.mark.security
def test_scheduled_interview_refuses_config_blobs(client, db, update_world):
    """A scheduled meeting accepts status/notes/rating — not the proctoring switchboard."""
    resp = client.put(
        f"/api/v1/interviewer/interviews/{update_world['scheduled_id']}",
        json=_RELAXED_PROCTORING,
        headers=update_world["hr"],
    )
    assert resp.status_code == 400, resp.text
    assert "security_settings" in resp.json()["detail"]


@pytest.mark.security
def test_cross_tenant_update_is_not_found(client, db, update_world):
    other_cid = _make_company(db, "Other Co")
    _make_user(db, "hr-other-upd@example.com", "hr", other_cid)
    db.commit()
    other_hr = _login(client, "hr-other-upd@example.com")

    resp = client.put(
        f"/api/v1/interviewer/interviews/{update_world['template_id']}",
        json={"title": "cross tenant"},
        headers=other_hr,
    )
    assert resp.status_code == 404, resp.text

    from src.models.models import Interview
    db.expire_all()
    iv = db.query(Interview).filter(Interview.id == update_world["template_id"]).first()
    assert iv.title != "cross tenant"
