"""Resume scores that can be trusted, traced and corrected.

- An unreadable resume is marked needs_review, not scored 0 and ranked.
- No automatic reject or shortlist ever touches a needs_review applicant.
- Missing mandatory skills and unknown experience never auto-reject.
- Every result records its rules version, job-description version and when it was scored.
- HR can override a score with a reason; the model score and the history are kept, and
  rankings use the override.
- Editing what scoring reads creates a new job-description version, and older scores show
  as scored against an earlier one until HR rescores.
"""
import json
import uuid
from types import SimpleNamespace

import pytest

from src.models.models import (
    ATSResult, ATSScoreEvent, Candidate, JobDescriptionVersion, JobPosting, User,
)
from src.services import parse_quality
from src.services.ats_scoring_pipeline import (
    ScoringInputs, needs_review_candidate_ids, persist_ats_result, score_for_job,
)
from src.services.enhanced_ats_service import SCORING_RULES_VERSION

GOOD_RESUME = (
    "Priya Sharma. Software engineer with 4 years of experience building Python and "
    "FastAPI services. Experience: Backend Engineer at Acme, 2021 to 2025, designed REST "
    "APIs in Python, FastAPI and PostgreSQL, deployed on AWS with Docker, and led a team "
    "of three engineers. Projects: built a job-matching engine in Python with scikit-learn. "
    "Education: B.Tech in Computer Science, CGPA 8.4. Skills: Python, FastAPI, SQL, Docker, "
    "AWS, Git, REST APIs, testing with pytest, CI/CD pipelines, Linux administration. "
    "Certifications: AWS Certified Developer Associate. Achievements: won the college "
    "hackathon in 2020; maintainer of an open source Python logging library with 300 stars."
)


# ── parse quality ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,extraction,issue", [
    ("", {}, "no_text"),
    ("Python developer", {}, "too_short"),
    ("x" * 50, {"pages": 3}, "likely_scanned"),
    ("(cid:12)(cid:15) " * 40 + GOOD_RESUME, {}, "garbled"),
    (GOOD_RESUME, {"error": "broken file"}, "extraction_error"),
])
def test_unreadable_text_needs_review(text, extraction, issue):
    q = parse_quality.assess(text, extraction)
    assert q["status"] == "needs_review"
    assert issue in q["issues"]


def test_a_short_profile_is_not_unreadable():
    # Built from structured profile fields, never parsed from a file.
    q = parse_quality.assess("Python developer, 3 years, B.Tech", {}, source="profile")
    assert q["status"] == "ok"
    assert parse_quality.assess("", {}, source="profile")["status"] == "needs_review"


def test_a_clean_resume_is_ok():
    q = parse_quality.assess(GOOD_RESUME, {"confidence": 0.9, "pages": 1})
    assert q["status"] == "ok" and q["issues"] == []
    assert q["confidence"] == 0.9


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def job(db, hr_headers):
    from routes.hr_routes import get_hr_profile
    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    j = JobPosting(id=str(uuid.uuid4()), company_id=hr.company_id, title="Backend Engineer",
                   description="Build Python APIs", requirements="Python, FastAPI",
                   required_skills=json.dumps(["Python", "FastAPI"]), experience_years=2,
                   status="active", hr_id=get_hr_profile(db, hr).id)
    db.add(j)
    db.flush()
    return j


def _candidate(db, job, name, stage="ats_screening", **kw):
    c = Candidate(id=str(uuid.uuid4()), company_id=job.company_id, job_id=job.id,
                  email=f"{name}@t.com", first_name=name, last_name="X", current_stage=stage, **kw)
    db.add(c)
    db.flush()
    return c


def _scored(db, job, name, text, **kw):
    c = _candidate(db, job, name, **kw)
    outcome = score_for_job(ScoringInputs(scoring_text=text, resume_text=text,
                                          extraction={"confidence": 0.9, "pages": 1}), job)
    persist_ats_result(db, candidate=c, job=job, outcome=outcome)
    db.flush()
    return c


# ── pipeline + provenance ────────────────────────────────────────────────────

def test_result_records_provenance_and_history(db, job):
    c = _scored(db, job, "good", GOOD_RESUME)
    row = db.query(ATSResult).filter(ATSResult.candidate_id == c.id).first()
    assert row.parse_status == "ok"
    assert row.scoring_rules_version == SCORING_RULES_VERSION
    assert row.jd_version_id == job.current_jd_version_id
    assert row.scored_at is not None and row.input_hash
    assert json.loads(row.model_version)["semantic"] in ("tfidf", "all-MiniLM-L6-v2")
    events = db.query(ATSScoreEvent).filter(ATSScoreEvent.candidate_id == c.id).all()
    assert [e.event_type for e in events] == ["scored"]


def test_unreadable_resume_is_held_not_ranked(db, job):
    c = _scored(db, job, "blank", "")
    row = db.query(ATSResult).filter(ATSResult.candidate_id == c.id).first()
    assert row.parse_status == "needs_review"
    assert "no_text" in json.loads(row.parse_issues)
    assert c.id in needs_review_candidate_ids(db, job.id)


def test_scorer_failure_becomes_needs_review(db, job, monkeypatch):
    from src.services import ats_scoring_pipeline as p
    boom = SimpleNamespace(enhanced_score_candidate=lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    outcome = p.score_for_job(ScoringInputs(scoring_text=GOOD_RESUME), job, service=boom)
    assert outcome.parse["status"] == "needs_review"
    assert "scoring_error" in outcome.parse["issues"]


# ── gates: nobody unreadable is auto-rejected ────────────────────────────────

def test_select_top_n_skips_and_never_rejects_needs_review(client, hr_headers, db, job):
    good = _scored(db, job, "good", GOOD_RESUME, )
    blank = _scored(db, job, "blank", "")
    other = _scored(db, job, "weak", GOOD_RESUME.replace("Python", "Cobol"))
    r = client.post(f"/api/v1/hr/jobs/{job.id}/select-candidates", headers=hr_headers, json={
        "candidate_ids": [], "selection_type": "top_n", "top_n": 1,
        "reject_others": True, "next_stage": "screening"})
    assert r.status_code == 200, r.text
    assert r.json()["held_for_review"] == 1
    db.refresh(blank)
    db.refresh(other)
    assert blank.current_stage != "rejected"
    assert other.current_stage == "rejected"


def test_move_to_screening_never_rejects_needs_review(client, hr_headers, db, job):
    good = _scored(db, job, "good", GOOD_RESUME)
    blank = _scored(db, job, "blank", "")
    r = client.post(f"/api/v1/hr/jobs/{job.id}/move-to-screening", headers=hr_headers,
                    json={"candidate_ids": [good.id], "reject_others": True})
    assert r.status_code == 200, r.text
    assert r.json()["held_for_review"] == 1
    db.refresh(blank)
    assert blank.current_stage != "rejected"


@pytest.fixture
def advanced_db(client, db):
    """advanced_features_routes declares its own get_db; point it at the test session."""
    from routes import advanced_features_routes as adv

    def _db():
        yield db
    client.app.dependency_overrides[adv.get_db] = _db
    yield
    client.app.dependency_overrides.pop(adv.get_db, None)


def test_auto_reject_refuses_missing_skills_and_holds_unknowns(client, hr_headers, db, job, advanced_db):
    r = client.post(f"/api/v1/advanced/jobs/{job.id}/auto-reject", headers=hr_headers,
                    json={"required_skills": ["Python"], "dry_run": True})
    assert r.status_code == 400

    _scored(db, job, "blank", "")
    _candidate(db, job, "noexp", experience_years=None)
    r = client.post(f"/api/v1/advanced/jobs/{job.id}/auto-reject", headers=hr_headers,
                    json={"min_experience_years": 3, "dry_run": True})
    assert r.status_code == 200, r.text
    held = {h["reason"] for h in r.json()["held_for_review"]}
    assert "Resume needs manual review" in held
    assert "Experience not found on resume" in held
    assert all(c["name"] not in ("blank X", "noexp X") for c in r.json()["candidates"])


# ── overrides ────────────────────────────────────────────────────────────────

def _list(client, hr_headers, job):
    r = client.get(f"/api/v1/hr/jobs/{job.id}/candidates", headers=hr_headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_override_needs_reason_changes_ranking_and_is_recorded(client, hr_headers, db, job):
    strong = _scored(db, job, "strong", GOOD_RESUME)
    weak = _scored(db, job, "weak", GOOD_RESUME.replace("Python", "Cobol").replace("FastAPI", "CICS"))
    base = f"/api/v1/hr/jobs/{job.id}/candidates/{weak.id}/ats-override"

    assert client.post(base, headers=hr_headers, json={"new_score": 99, "reason": "short"}).status_code == 422
    r = client.post(base, headers=hr_headers, json={"new_score": 99, "reason": "Portfolio shows strong Python work."})
    assert r.status_code == 200, r.text

    listing = _list(client, hr_headers, job)
    top = listing["candidates"][0]
    assert top["candidate_id"] == weak.id and top["ats_score"] == 99 and top["is_overridden"]
    assert top["model_score"] < 99

    hist = client.get(f"/api/v1/hr/jobs/{job.id}/candidates/{weak.id}/ats-history", headers=hr_headers).json()
    assert [h["event_type"] for h in hist] == ["override", "scored"]
    assert hist[0]["reason"] == "Portfolio shows strong Python work."

    r = client.request("DELETE", base, headers=hr_headers, json={"reason": "Checked again, model was right."})
    assert r.status_code == 200, r.text
    assert r.json()["effective_score"] == top["model_score"]


def test_rescore_keeps_the_override(client, hr_headers, db, job):
    c = _scored(db, job, "keep", GOOD_RESUME, resume_text=GOOD_RESUME)
    client.post(f"/api/v1/hr/jobs/{job.id}/candidates/{c.id}/ats-override", headers=hr_headers,
                json={"new_score": 42, "reason": "Adjusted after phone screen."})
    r = client.post(f"/api/v1/hr/jobs/{job.id}/candidates/{c.id}/rescore", headers=hr_headers)
    assert r.status_code == 200, r.text
    assert r.json()["effective_score"] == 42 and r.json()["is_overridden"]


# ── job-description versions ─────────────────────────────────────────────────

def test_editing_the_jd_makes_scores_stale_but_a_title_edit_does_not(client, hr_headers, db, job):
    _scored(db, job, "v1", GOOD_RESUME, resume_text=GOOD_RESUME)
    payload = {"title": "Senior Backend Engineer", "description": job.description,
               "requirements": job.requirements, "required_skills": ["Python", "FastAPI"],
               "experience_years": 2, "department_id": None, "location": "", "employment_type": "full_time"}
    r = client.put(f"/api/v1/hr/jobs/{job.id}", headers=hr_headers, json=payload)
    assert r.status_code == 200, r.text
    assert not _list(client, hr_headers, job)["candidates"][0]["is_stale"]
    assert db.query(JobDescriptionVersion).filter(JobDescriptionVersion.job_id == job.id).count() == 1

    payload["required_skills"] = ["Python", "FastAPI", "Kubernetes"]
    r = client.put(f"/api/v1/hr/jobs/{job.id}", headers=hr_headers, json=payload)
    assert r.status_code == 200, r.text
    row = _list(client, hr_headers, job)["candidates"][0]
    assert row["is_stale"] and row["stale_reason"] == "jd_changed"
    assert row["jd_version_no"] == 1

    r = client.post(f"/api/v1/hr/jobs/{job.id}/rescore-stale", headers=hr_headers, json={"limit": 10})
    assert r.status_code == 200 and r.json()["rescored"] == 1
    row = _list(client, hr_headers, job)["candidates"][0]
    assert not row["is_stale"] and row["jd_version_no"] == 2
