"""Checking the resume ranking against independent human review.

- A blind sample is drawn only from below the cutoff, reproducibly from a seed, and shows
  the reviewer no score, rank or explanation.
- The report's agreement, kappa and miss rate match a hand-worked example.
- A suitable applicant below the cutoff is a "missed candidate"; recording why needs a
  cause (and notes when the cause is "other").
- /advanced compare and scorecard no longer blend resume, test and interview scores.
"""
import json
import uuid

import pytest

from src.models.models import ATSResult, Candidate, JobPosting, User


@pytest.fixture
def job(db, hr_headers):
    from routes.hr_routes import get_hr_profile
    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    j = JobPosting(id=str(uuid.uuid4()), company_id=hr.company_id, title="Analyst", description="d",
                   requirements="r", status="active", hr_id=get_hr_profile(db, hr).id)
    db.add(j)
    db.flush()
    return j


@pytest.fixture
def ranked(db, job):
    """Ten applicants scored 95, 85, ..., 5; one more unreadable."""
    out = []
    for i, score in enumerate(range(95, 0, -10)):
        c = Candidate(id=str(uuid.uuid4()), company_id=job.company_id, job_id=job.id,
                      email=f"a{i}@t.com", first_name=f"A{i}", last_name="X", current_stage="hr_review")
        db.add(c)
        db.flush()
        db.add(ATSResult(candidate_id=c.id, job_id=job.id, company_id=job.company_id, ats_score=score,
                         parse_status="ok", explanation=json.dumps({"experience": {"relevant_status": "unknown"}})))
        out.append(c)
    held = Candidate(id=str(uuid.uuid4()), company_id=job.company_id, job_id=job.id,
                     email="h@t.com", first_name="Held", last_name="X", current_stage="hr_review")
    db.add(held)
    db.flush()
    db.add(ATSResult(candidate_id=held.id, job_id=job.id, company_id=job.company_id, ats_score=0,
                     parse_status="needs_review"))
    db.flush()
    return out, held


def _url(job, *p):
    return "/api/v1/hr/jobs/" + job.id + "".join("/" + x for x in p)


def test_blind_sample_is_below_cutoff_reproducible_and_scoreless(client, hr_headers, job, ranked):
    people, held = ranked
    below = {c.id for c in people[5:]} | {held.id}           # scores 45..5, plus the unreadable one
    r1 = client.post(_url(job, "review-samples"), headers=hr_headers,
                     json={"cutoff_score": 50, "size": 4, "seed": 7}).json()
    ids = [c["candidate_id"] for c in r1["candidates"]]
    assert len(ids) == 4 and set(ids) <= below
    assert r1["below_cutoff_population"] == 6
    assert all(set(c) == {"candidate_id", "name", "resume_path", "my_judgement"} for c in r1["candidates"])

    other = client.post(_url(job, "review-samples"), headers=hr_headers,
                        json={"cutoff_score": 50, "size": 4, "seed": 7}).json()
    assert [c["candidate_id"] for c in other["candidates"]] == ids   # same seed, same sample


def test_report_metrics_match_a_hand_worked_example(client, hr_headers, job, ranked):
    people, _ = ranked
    judge = {0: "suitable", 1: "suitable", 2: "not_suitable",          # above cutoff (95, 85, 75)
             6: "suitable", 7: "not_suitable", 8: "not_suitable"}      # below cutoff (35, 25, 15)
    for i, j in judge.items():
        r = client.post(_url(job, "candidates", people[i].id, "screening-review"), headers=hr_headers,
                        json={"judgement": j, "reason": "Read the whole resume carefully."})
        assert r.status_code == 200, r.text
    rep = client.get(_url(job, "screening-report") + "?cutoff_score=50", headers=hr_headers).json()
    assert rep["confusion"] == {"suitable_above": 2, "not_suitable_above": 1,
                                "suitable_below": 1, "not_suitable_below": 2}
    assert rep["agreement"] == pytest.approx(4 / 6, abs=0.001)
    # kappa: po = 4/6; pe = (3*3 + 3*3)/36 = 0.5 → (0.667 - 0.5) / 0.5 = 0.333
    assert rep["kappa"] == pytest.approx(0.333, abs=0.001)
    assert rep["miss_rate"] == pytest.approx(1 / 3, abs=0.001)
    assert [m["name"] for m in rep["missed"]] == ["A6 X"]
    assert rep["missed"][0]["suggested_cause"] == "experience_not_detected"


def test_investigating_a_miss_needs_a_cause(client, hr_headers, job, ranked):
    people, _ = ranked
    miss = people[7]
    url = _url(job, "candidates", miss.id, "missed-investigation")
    assert client.post(url, headers=hr_headers, json={"cause": "experience_not_detected"}).status_code == 409

    client.post(_url(job, "candidates", miss.id, "screening-review"), headers=hr_headers,
                json={"judgement": "suitable", "reason": "Strong internship work in the same domain."})
    assert client.post(url, headers=hr_headers, json={"cause": "bad"}).status_code == 422
    assert client.post(url, headers=hr_headers, json={"cause": "other"}).status_code == 422
    assert client.post(url, headers=hr_headers, json={"cause": "experience_not_detected",
                                                      "notes": "Dates written as 'Summer 2023'."}).status_code == 200
    rep = client.get(_url(job, "screening-report") + "?cutoff_score=50", headers=hr_headers).json()
    assert rep["causes"] == {"experience_not_detected": 1}


def test_a_reason_is_required(client, hr_headers, job, ranked):
    people, _ = ranked
    r = client.post(_url(job, "candidates", people[0].id, "screening-review"), headers=hr_headers,
                    json={"judgement": "suitable", "reason": "ok"})
    assert r.status_code == 422


# ── no blended composite ────────────────────────────────────────────────────

@pytest.fixture
def advanced_db(client, db):
    from routes import advanced_features_routes as adv

    def _db():
        yield db
    client.app.dependency_overrides[adv.get_db] = _db
    yield
    client.app.dependency_overrides.pop(adv.get_db, None)


def test_compare_and_scorecard_keep_scores_separate(client, hr_headers, job, ranked, advanced_db):
    people, _ = ranked
    ids = [people[2].id, people[0].id]
    r = client.post("/api/v1/advanced/candidates/compare", headers=hr_headers, json={"candidate_ids": ids})
    if r.status_code == 422:      # the endpoint may take ids as a query parameter
        r = client.get("/api/v1/advanced/candidates/compare", headers=hr_headers,
                       params=[("candidate_ids", i) for i in ids])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "composite_score" not in json.dumps(body) and "ranking_formula" not in body
    assert [c["candidate_id"] for c in body["comparison"]] == ids      # order kept, not re-ranked

    r = client.get(f"/api/v1/advanced/candidates/{people[0].id}/scorecard", headers=hr_headers)
    assert r.status_code == 200, r.text
    card = r.json()
    assert "composite" not in json.dumps(card)
    assert card["recommendation"] is None          # no interviewer has recommended anything yet
    assert card["resume_match"]["score"] == 95
