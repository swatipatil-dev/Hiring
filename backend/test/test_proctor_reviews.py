"""Reviewer outcomes for proctoring flags (POST /api/v1/proctor/reviews).

A person, not the software, decides what a flag means. These pin:
- the three outcomes, and that confirming needs remarks (it can count against someone);
- one review covering every reading in a folded timeline entry;
- append-only history with the latest outcome shown;
- marker rows (consent, live enrollment) and fact rows (no fullscreen) are not reviewable;
- tenant isolation;
- the report's `unreviewed_count` and per-event `review`.
"""
import uuid
from datetime import datetime, timedelta

import pytest

pytest.importorskip("cv2", reason="proctoring extras not installed")

from src.models.models import (  # noqa: E402
    Interview, ProctoringSession, ProctoringViolation, User, Company,
)

URL = "/api/v1/proctor/reviews"


def _hr_company(db):
    return db.query(User).filter(User.email == "hr_test@test.com").first().company_id


@pytest.fixture
def session_with_flags(db, hr_headers):
    company_id = _hr_company(db)
    cand = User(id=str(uuid.uuid4()), email=f"c{uuid.uuid4().hex[:6]}@t.com", password_hash="x",
                role="candidate", first_name="Cand", last_name="One", is_active=True)
    db.add(cand)
    iv = Interview(company_id=company_id, title="Proctored test", status="COMPLETED")
    db.add(iv)
    db.flush()
    s = ProctoringSession(id=str(uuid.uuid4()), company_id=company_id, candidate_id=cand.id,
                          test_id=iv.id, status="completed", started_at=datetime.utcnow())
    db.add(s)
    db.flush()
    t0 = datetime.utcnow()

    def v(kind, sec, **kw):
        row = ProctoringViolation(id=str(uuid.uuid4()), company_id=company_id, session_id=s.id,
                                  violation_type=kind, severity=kw.get("sev", "MEDIUM"),
                                  module=kw.get("module", "face"), timestamp=t0 + timedelta(seconds=sec))
        db.add(row)
        return row

    rows = {
        "phone": v("PHONE_DETECTED", 5, sev="CRITICAL", module="object"),
        "turn1": v("HEAD_TURNED", 20), "turn2": v("HEAD_TURNED", 30),   # one folded entry
        "consent": v("MONITORING_CONSENT", 0, sev="LOW", module="proctor"),
        "nofs": v("FULLSCREEN_UNAVAILABLE", 1, sev="LOW", module="behavioral"),
    }
    db.flush()
    return {"interview": iv, "session": s, **rows}


def _report(client, hr_headers, iv):
    r = client.get(f"/api/v1/proctor/results/{iv.id}", headers=hr_headers)
    assert r.status_code == 200, r.text
    return r.json()["sessions"][0]


def test_confirm_needs_remarks(client, hr_headers, session_with_flags):
    f = session_with_flags
    r = client.post(URL, headers=hr_headers, json={
        "session_id": f["session"].id, "violation_ids": [f["phone"].id], "outcome": "confirmed"})
    assert r.status_code == 422
    r = client.post(URL, headers=hr_headers, json={
        "session_id": f["session"].id, "violation_ids": [f["phone"].id], "outcome": "confirmed",
        "remarks": "Phone clearly visible in the evidence frame."})
    assert r.status_code == 200, r.text


def test_unknown_outcome_is_rejected(client, hr_headers, session_with_flags):
    f = session_with_flags
    r = client.post(URL, headers=hr_headers, json={
        "session_id": f["session"].id, "violation_ids": [f["phone"].id], "outcome": "cheating"})
    assert r.status_code == 422


def test_one_review_covers_a_folded_entry_and_counts_drop(client, hr_headers, session_with_flags):
    f = session_with_flags
    before = _report(client, hr_headers, f["interview"])
    # phone + two head turns; consent is a marker and no-fullscreen is a fact
    assert before["unreviewed_count"] == 3
    assert before["total_violations"] == 4          # consent excluded, no-fullscreen shown

    r = client.post(URL, headers=hr_headers, json={
        "session_id": f["session"].id, "violation_ids": [f["turn1"].id, f["turn2"].id],
        "outcome": "dismissed", "remarks": "Glancing at allowed notes."})
    assert r.status_code == 200, r.text
    assert len(r.json()["reviews"]) == 2

    after = _report(client, hr_headers, f["interview"])
    assert after["unreviewed_count"] == 1
    turns = [v for v in after["violations"] if v["type"] == "HEAD_TURNED"]
    assert all(v["review"]["outcome"] == "dismissed" for v in turns)
    assert all(v["review"]["reviewer_name"] == "HR User" for v in turns)


def test_history_is_append_only_and_latest_wins(client, hr_headers, session_with_flags):
    f = session_with_flags
    for outcome in ("inconclusive", "dismissed"):
        client.post(URL, headers=hr_headers, json={
            "session_id": f["session"].id, "violation_ids": [f["phone"].id], "outcome": outcome})
    rep = _report(client, hr_headers, f["interview"])
    phone = next(v for v in rep["violations"] if v["type"] == "PHONE_DETECTED")
    assert phone["review"]["outcome"] == "dismissed"
    assert [h["outcome"] for h in rep["review_history"]] == ["dismissed", "inconclusive"]


def test_session_verdict(client, hr_headers, session_with_flags):
    f = session_with_flags
    r = client.post(URL, headers=hr_headers, json={
        "session_id": f["session"].id, "outcome": "inconclusive", "remarks": "Need a retest."})
    assert r.status_code == 200, r.text
    rep = _report(client, hr_headers, f["interview"])
    assert rep["review"]["outcome"] == "inconclusive"
    assert rep["review"]["violation_id"] is None


def test_markers_and_facts_are_not_reviewable(client, hr_headers, session_with_flags):
    f = session_with_flags
    for key in ("consent", "nofs"):
        r = client.post(URL, headers=hr_headers, json={
            "session_id": f["session"].id, "violation_ids": [f[key].id], "outcome": "dismissed"})
        assert r.status_code == 422, key


def test_events_from_another_session_are_refused(client, hr_headers, session_with_flags, db):
    f = session_with_flags
    other = ProctoringSession(id=str(uuid.uuid4()), company_id=f["session"].company_id,
                              candidate_id=f["session"].candidate_id, test_id=f["interview"].id,
                              status="completed")
    db.add(other)
    db.flush()
    r = client.post(URL, headers=hr_headers, json={
        "session_id": other.id, "violation_ids": [f["phone"].id], "outcome": "dismissed"})
    assert r.status_code == 404


def test_another_company_cannot_review(client, hr_headers, session_with_flags, db):
    f = session_with_flags
    foreign = Company(id=str(uuid.uuid4()), name="Other Co", slug=f"other-{uuid.uuid4().hex[:6]}")
    db.add(foreign)
    db.flush()
    f["session"].company_id = foreign.id
    db.flush()
    r = client.post(URL, headers=hr_headers, json={
        "session_id": f["session"].id, "violation_ids": [f["phone"].id], "outcome": "dismissed"})
    assert r.status_code == 404


def test_candidates_cannot_review(client, candidate_headers, session_with_flags):
    f = session_with_flags
    r = client.post(URL, headers=candidate_headers, json={
        "session_id": f["session"].id, "violation_ids": [f["phone"].id], "outcome": "dismissed"})
    assert r.status_code == 403
