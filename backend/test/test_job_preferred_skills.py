"""Mandatory vs preferred requirements.

A job's `required_skills` are its mandatory requirements; `preferred_skills` are
nice-to-haves. HR states them separately. The scorer never counts a mandatory skill as
preferred too, uses an explicit list as given (an empty list means "none", not "infer"),
and reports how many mandatory requirements a candidate meets. Missing ones lower the
score but never trigger an automatic rejection (see test_ats_review_and_override).
"""
import json
import uuid

import pytest

from src.models.models import JobPosting, User
from src.services.enhanced_ats_service import EnhancedATSScoringService

JD = ("We are hiring a backend engineer. Must have Python and FastAPI. "
      "Nice to have: Docker, Kubernetes and Python scripting experience.")
RESUME = ("Backend engineer, 4 years. Built REST APIs with Python and FastAPI, deployed with "
          "Docker on AWS. B.Tech Computer Science. Skills: Python, FastAPI, Docker, SQL, Git.")


@pytest.fixture(scope="module")
def svc():
    return EnhancedATSScoringService()


def _score(svc, preferred):
    return svc.enhanced_score_candidate(resume_text=RESUME, job_description=JD,
                                        required_skills=["Python", "FastAPI"], required_experience=2,
                                        preferred_skills=preferred)


def test_inferred_preferred_never_repeats_a_mandatory_skill(svc):
    r = _score(svc, None)
    prefs = [p.lower() for p in r.scoring_breakdown["preferred_skills"]]
    assert "python" not in prefs and "fastapi" not in prefs
    assert r.preferred_source in ("inferred", "none")


def test_explicit_empty_list_means_none(svc):
    r = _score(svc, [])
    assert r.scoring_breakdown["preferred_skills"] == []
    assert r.preferred_source == "none"


def test_explicit_list_is_used_as_given_minus_mandatory(svc):
    r = _score(svc, ["Docker", "Python", "Terraform"])
    assert [p.lower() for p in r.scoring_breakdown["preferred_skills"]] == ["docker", "terraform"]
    assert r.preferred_source == "explicit"


def test_mandatory_met_is_reported(svc):
    r = svc.enhanced_score_candidate(resume_text=RESUME, job_description=JD,
                                     required_skills=["Python", "FastAPI", "Kubernetes"],
                                     required_experience=2, preferred_skills=[])
    assert r.mandatory_total == 3
    assert r.mandatory_met == 2
    assert r.missing_mandatory == ["Kubernetes"]


# ── API ──────────────────────────────────────────────────────────────────────

def _payload(**kw):
    base = {"title": "Backend Engineer", "description": JD, "requirements": "Python, FastAPI",
            "location": "Remote", "employment_type": "full_time", "experience_years": 2,
            "required_skills": ["Python", "FastAPI"]}
    base.update(kw)
    return base


def test_create_and_update_keep_the_two_lists_separate(client, hr_headers, db):
    r = client.post("/api/v1/hr/jobs", headers=hr_headers,
                    json=_payload(preferred_skills=["Docker", "python", "Docker"]))
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["required_skills"] == ["Python", "FastAPI"]
    assert body["preferred_skills"] == ["Docker"]           # deduped, mandatory removed

    job_id = body["id"]
    # Omitting preferred on update leaves it alone.
    r = client.put(f"/api/v1/hr/jobs/{job_id}", headers=hr_headers, json=_payload())
    assert r.json()["preferred_skills"] == ["Docker"]
    # An explicit empty list clears it.
    r = client.put(f"/api/v1/hr/jobs/{job_id}", headers=hr_headers, json=_payload(preferred_skills=[]))
    assert r.json()["preferred_skills"] == []


def test_job_without_preferred_reports_none_not_empty(client, hr_headers):
    r = client.post("/api/v1/hr/jobs", headers=hr_headers, json=_payload())
    assert r.status_code in (200, 201), r.text
    assert r.json()["preferred_skills"] is None
