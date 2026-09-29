"""Candidate explanations, accommodations and retests.

- A candidate can explain their own submitted test; nobody else's, and not before.
- A candidate asks for an adjustment before starting; HR approves specific settings.
  Extra time scales that candidate's own questions, from the original minutes, so
  changing the percentage never compounds and declining restores them.
- HR can grant a retest: a NEW attempt cloned from the same template, pointing at the
  old one, which is left untouched. A reason is required and recorded.
"""
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.models.models import (
    User, Candidate, JobPosting, Interview, CandidateStageHistory, AssessmentExplanation,
)
from src.models.question_models import Question

CAND_EMAIL = "candidate_test@test.com"


def _hr(db):
    return db.query(User).filter(User.email == "hr_test@test.com").first()


@pytest.fixture
def setup(db, hr_headers, candidate_headers):
    from routes.hr_routes import get_hr_profile
    hr = _hr(db)
    hr_profile = get_hr_profile(db, hr)          # the test HR owns the job below
    cand_user = db.query(User).filter(User.email == CAND_EMAIL).first()
    # The stock fixtures put the candidate in the platform org and HR in a
    # separate "Test Company"; a candidate taking this company's test belongs to it.
    cand_user.company_id = hr.company_id
    db.flush()
    job = JobPosting(id=str(uuid.uuid4()), company_id=hr.company_id, title="Backend Engineer",
                     description="Build APIs", requirements="Python", status="active",
                     hr_id=hr_profile.id)
    db.add(job)
    db.flush()
    cand = Candidate(id=str(uuid.uuid4()), company_id=hr.company_id, user_id=cand_user.id,
                     job_id=job.id, email=CAND_EMAIL, first_name="Cand", last_name="User",
                     current_stage="test_completed")
    db.add(cand)
    template = Interview(company_id=hr.company_id, title="Backend test", job_id=job.id, status="READY",
                         security_settings_data=json.dumps({"enable_webcam_monitoring": True}))
    db.add(template)
    db.flush()
    for i, (qtype, mins) in enumerate([("mcq", 2), ("mcq", 2), ("coding", 10)]):
        db.add(Question(interview_id=template.id, question_text=f"Q{i}", question_type=qtype,
                        difficulty_level="medium", time_limit_minutes=mins, is_approved=True))
    db.flush()

    def attempt(status="COMPLETED", **kw):
        iv = Interview(company_id=hr.company_id, title="Backend test", job_id=job.id, candidate_id=cand.id,
                       status=status, test_score=3, total_marks=10,
                       interview_config_data=json.dumps({"_source_test_id": template.id}),
                       security_settings_data=template.security_settings_data, **kw)
        db.add(iv)
        db.flush()
        for i, (qtype, mins) in enumerate([("mcq", 2), ("mcq", 2), ("coding", 10)]):
            db.add(Question(interview_id=iv.id, question_text=f"Q{i}", question_type=qtype,
                            difficulty_level="medium", time_limit_minutes=mins, is_approved=True))
        db.flush()
        return iv

    return SimpleNamespace(job=job, cand=cand, template=template, attempt=attempt)


def _minutes(db, iv):
    return sorted(q.time_limit_minutes for q in db.query(Question).filter(Question.interview_id == iv.id).all())


# ── Explanations ─────────────────────────────────────────────────────────────

def test_candidate_explains_a_submitted_test(client, candidate_headers, setup, db):
    iv = setup.attempt()
    r = client.post(f"/api/v1/candidate/interviews/{iv.id}/explanation", headers=candidate_headers,
                    json={"text": "My cat walked across the desk."})
    assert r.status_code == 200, r.text
    mine = client.get(f"/api/v1/candidate/interviews/{iv.id}/explanations", headers=candidate_headers).json()
    assert [e["text"] for e in mine] == ["My cat walked across the desk."]


def test_no_explanation_before_submitting(client, candidate_headers, setup):
    iv = setup.attempt(status="SCHEDULED")
    r = client.post(f"/api/v1/candidate/interviews/{iv.id}/explanation", headers=candidate_headers,
                    json={"text": "Early."})
    assert r.status_code == 409


def test_no_explaining_someone_elses_test(client, candidate2_headers, setup):
    iv = setup.attempt()
    r = client.post(f"/api/v1/candidate/interviews/{iv.id}/explanation", headers=candidate2_headers,
                    json={"text": "Not mine."})
    assert r.status_code == 404


def test_explanations_reach_the_report(client, candidate_headers, hr_headers, setup):
    pytest.importorskip("cv2", reason="proctoring extras not installed")
    iv = setup.attempt()
    client.post(f"/api/v1/candidate/interviews/{iv.id}/explanation", headers=candidate_headers,
                json={"text": "Power cut at minute 12."})
    rep = client.get(f"/api/v1/proctor/results/{iv.id}", headers=hr_headers).json()
    assert [e["text"] for e in rep["candidate_explanations"]] == ["Power cut at minute 12."]


# ── Accommodations ───────────────────────────────────────────────────────────

def test_request_then_approve_extra_time(client, candidate_headers, hr_headers, setup, db):
    iv = setup.attempt(status="SCHEDULED")
    r = client.post(f"/api/v1/candidate/interviews/{iv.id}/accommodation-request", headers=candidate_headers,
                    json={"text": "I have dyslexia and need extra reading time."})
    assert r.status_code == 200, r.text
    assert r.json()["accommodation_status"] == "requested"

    r = client.patch(f"/api/v1/hr/interviews/{iv.id}/accommodations", headers=hr_headers,
                     json={"status": "approved", "extra_time_percent": 50, "audio_monitoring_off": True})
    assert r.status_code == 200, r.text
    assert _minutes(db, iv) == [3, 3, 15]
    assert r.json()["duration_minutes"] == 21
    assert "50% extra time" in r.json()["accommodations_summary"]

    # Changing the percentage scales from the ORIGINAL minutes, never compounds.
    client.patch(f"/api/v1/hr/interviews/{iv.id}/accommodations", headers=hr_headers,
                 json={"status": "approved", "extra_time_percent": 100})
    assert _minutes(db, iv) == [4, 4, 20]

    # Declining restores the original times.
    client.patch(f"/api/v1/hr/interviews/{iv.id}/accommodations", headers=hr_headers,
                 json={"status": "declined"})
    assert _minutes(db, iv) == [2, 2, 10]


def test_no_adjustments_once_started(client, hr_headers, setup):
    iv = setup.attempt(status="IN_PROGRESS")
    r = client.patch(f"/api/v1/hr/interviews/{iv.id}/accommodations", headers=hr_headers,
                     json={"status": "approved", "extra_time_percent": 25})
    assert r.status_code == 409


def test_extra_time_is_bounded(client, hr_headers, setup):
    iv = setup.attempt(status="SCHEDULED")
    r = client.patch(f"/api/v1/hr/interviews/{iv.id}/accommodations", headers=hr_headers,
                     json={"status": "approved", "extra_time_percent": 500})
    assert r.status_code == 422


# ── Retests ──────────────────────────────────────────────────────────────────

def test_retest_creates_a_new_attempt_and_keeps_the_old(client, hr_headers, setup, db):
    old = setup.attempt()
    r = client.post(f"/api/v1/hr/test-results/{old.id}/retest", headers=hr_headers,
                    json={"reason": "Power cut during the test, confirmed by the candidate.",
                          "accommodations": {"extra_time_percent": 25}})
    assert r.status_code == 200, r.text
    body = r.json()
    new = db.query(Interview).filter(Interview.id == body["retest_interview_id"]).first()

    assert new.status == "SCHEDULED"
    assert new.attempt_number == 2 and new.previous_attempt_id == old.id
    assert new.accommodation_status == "approved"
    db.refresh(old)
    assert old.status == "COMPLETED" and old.test_score == 3      # untouched
    assert len(_minutes(db, new)) == 3

    note = db.query(CandidateStageHistory).filter(
        CandidateStageHistory.candidate_id == setup.cand.id).order_by(CandidateStageHistory.created_at.desc()).first()
    assert "Retest granted (attempt 2)" in note.notes and "Power cut" in note.notes


def test_one_pending_retest_at_a_time(client, hr_headers, setup):
    old = setup.attempt()
    payload = {"reason": "Candidate's laptop crashed mid-test."}
    assert client.post(f"/api/v1/hr/test-results/{old.id}/retest", headers=hr_headers, json=payload).status_code == 200
    assert client.post(f"/api/v1/hr/test-results/{old.id}/retest", headers=hr_headers, json=payload).status_code == 409


def test_retest_needs_a_reason(client, hr_headers, setup):
    old = setup.attempt()
    r = client.post(f"/api/v1/hr/test-results/{old.id}/retest", headers=hr_headers, json={"reason": "redo"})
    assert r.status_code == 422


def test_only_submitted_tests_can_be_retaken(client, hr_headers, setup):
    iv = setup.attempt(status="SCHEDULED")
    r = client.post(f"/api/v1/hr/test-results/{iv.id}/retest", headers=hr_headers,
                    json={"reason": "Not submitted yet, should fail."})
    assert r.status_code == 409


# ── Pipeline enforcement ─────────────────────────────────────────────────────

def test_pipeline_drops_accommodated_flags():
    pytest.importorskip("cv2", reason="proctoring extras not installed")
    from proctoring.pipeline import ProctorPipeline
    p = ProctorPipeline.__new__(ProctorPipeline)
    p.gaze_flags_off, p.audio_monitoring_off = True, True
    assert {"SUSPICIOUS_GAZE", "HEAD_TURNED", "MULTIPLE_VOICES", "BACKGROUND_NOISE"} <= p.suppressed_flags()
    assert "PHONE_DETECTED" not in p.suppressed_flags()

    face = SimpleNamespace(face_detected=True, face_count=1, head_turned=True, gaze_deviation_score=0.9,
                           has_reference=True, flags=["HEAD_TURNED", "SUSPICIOUS_GAZE"])
    assert ProctorPipeline._calc_face_score(p, face) == 0
    p.gaze_flags_off = False
    assert ProctorPipeline._calc_face_score(p, face) > 0
