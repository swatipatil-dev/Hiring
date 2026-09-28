"""HR → HR job transfer.

A job belongs to an HR through `job_postings.hr_id`, but the owning HR is also written on
the job's offers (`offers.hr_id`, which the offers list and the candidate's accept/decline
email follow) and interviews (`interviews.assigned_hr_id`). A transfer has to move all
three, or the previous HR keeps half the job.
"""
import uuid
from datetime import date, timedelta

import pytest

from conftest import _register_and_login


def _uid():
    return str(uuid.uuid4())


@pytest.fixture()
def two_hrs(client, db, hr_headers):
    """HR A (the hr_headers user) and HR B in the same company, each with a profile."""
    from src.models.models import HRProfile, User

    hr_a = db.query(User).filter(User.email == "hr_test@test.com").first()
    headers_b = _register_and_login(client, "hr_b_test@test.com", "Pass@1234", "hr",
                                    "Beth", "Bee", db=db)
    hr_b = db.query(User).filter(User.email == "hr_b_test@test.com").first()
    hr_b.company_id = hr_a.company_id
    db.flush()

    prof_a = HRProfile(id=_uid(), company_id=hr_a.company_id, user_id=hr_a.id, employee_id="HR-A")
    prof_b = HRProfile(id=_uid(), company_id=hr_a.company_id, user_id=hr_b.id, employee_id="HR-B")
    db.add_all([prof_a, prof_b])
    db.commit()
    return {
        "a": hr_a, "b": hr_b, "prof_a": prof_a, "prof_b": prof_b,
        "headers_a": hr_headers, "headers_b": headers_b,
    }


def _job(db, company_id, hr_profile_id, *, title="Backend Engineer", status="active"):
    from src.models.models import JobPosting
    job = JobPosting(id=_uid(), company_id=company_id, title=title, description="d",
                     requirements="r", status=status, hr_id=hr_profile_id)
    db.add(job)
    db.flush()
    return job


def _job_with_offer_and_interview(db, company_id, hr_profile_id):
    from src.models.models import Candidate, Interview, Offer
    job = _job(db, company_id, hr_profile_id)
    cand = Candidate(id=_uid(), company_id=company_id, job_id=job.id,
                     email=f"c-{_uid()[:8]}@example.com", first_name="C", last_name="D")
    db.add(cand)
    db.flush()
    offer = Offer(id=_uid(), company_id=company_id, candidate_id=cand.id, job_id=job.id,
                  hr_id=hr_profile_id, salary=100000, start_date=date.today(),
                  expiry_date=date.today() + timedelta(days=7))
    iv = Interview(company_id=company_id, title="Test", job_id=job.id,
                   assigned_hr_id=hr_profile_id)
    db.add_all([offer, iv])
    db.commit()
    return job, offer, iv


def _job_ids(client, headers):
    resp = client.get("/api/v1/hr/jobs", headers=headers)
    assert resp.status_code == 200, resp.text
    return {j["id"] for j in resp.json()}


def test_transfer_moves_job_offers_interviews_and_records_it(client, db, two_hrs):
    from src.models.models import Interview, JobTransferHistory, Notification, Offer, JobPosting

    t = two_hrs
    job, offer, iv = _job_with_offer_and_interview(db, t["a"].company_id, t["prof_a"].id)
    assert job.id in _job_ids(client, t["headers_a"])

    resp = client.post(f"/api/v1/hr/jobs/{job.id}/transfer",
                       json={"to_hr_id": t["prof_b"].id, "reason": "Going on leave"},
                       headers=t["headers_a"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["offers_moved"] == 1 and body["interviews_moved"] == 1

    db.expire_all()
    assert db.get(JobPosting, job.id).hr_id == t["prof_b"].id
    assert db.get(Offer, offer.id).hr_id == t["prof_b"].id
    assert db.get(Interview, iv.id).assigned_hr_id == t["prof_b"].id

    # It leaves A's list and lands in B's.
    assert job.id not in _job_ids(client, t["headers_a"])
    assert job.id in _job_ids(client, t["headers_b"])

    rows = db.query(JobTransferHistory).filter(JobTransferHistory.job_id == job.id).all()
    assert len(rows) == 1
    assert rows[0].from_hr_id == t["prof_a"].id and rows[0].to_hr_id == t["prof_b"].id
    assert rows[0].reason == "Going on leave"

    # B is told; A did the transfer, so A is not notified about their own action.
    notes = db.query(Notification).filter(Notification.type == "job_transferred").all()
    assert [n.user_id for n in notes] == [t["b"].id]

    hist = client.get(f"/api/v1/hr/jobs/{job.id}/transfer-history", headers=t["headers_b"])
    assert hist.status_code == 200 and len(hist.json()) == 1


def test_owner_hr_is_reported_on_the_job(client, db, two_hrs):
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    jobs = client.get("/api/v1/hr/jobs", headers=t["headers_a"]).json()
    mine = next(j for j in jobs if j["id"] == job.id)
    assert mine["hr_id"] == t["prof_a"].id
    assert mine["hr_name"] == "HR User"


def test_only_the_owner_can_transfer(client, db, two_hrs):
    """Transfer is the owner's (or an admin's) call — another HR can't take the job."""
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    resp = client.post(f"/api/v1/hr/jobs/{job.id}/transfer",
                       json={"to_hr_id": t["prof_b"].id}, headers=t["headers_b"])
    assert resp.status_code == 403
    resp = client.post("/api/v1/hr/jobs/transfer-bulk",
                       json={"to_hr_id": t["prof_b"].id, "from_hr_id": t["prof_a"].id},
                       headers=t["headers_b"])
    assert resp.status_code == 403


def test_transfer_to_current_owner_is_rejected(client, db, two_hrs):
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    resp = client.post(f"/api/v1/hr/jobs/{job.id}/transfer",
                       json={"to_hr_id": t["prof_a"].id}, headers=t["headers_a"])
    assert resp.status_code == 400


def test_transfer_to_deactivated_hr_is_rejected(client, db, two_hrs):
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    t["b"].is_active = False
    db.commit()
    resp = client.post(f"/api/v1/hr/jobs/{job.id}/transfer",
                       json={"to_hr_id": t["prof_b"].id}, headers=t["headers_a"])
    assert resp.status_code == 400
    assert "deactivated" in resp.json()["detail"]


def test_transfer_to_unknown_hr_is_404(client, db, two_hrs):
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    resp = client.post(f"/api/v1/hr/jobs/{job.id}/transfer",
                       json={"to_hr_id": _uid()}, headers=t["headers_a"])
    assert resp.status_code == 404


def test_transfer_to_hr_in_another_company_is_404(client, db, two_hrs):
    from src.models.models import Company
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    other = Company(id=_uid(), name="Other Co", slug=f"other-{_uid()[:8]}")
    db.add(other)
    db.flush()
    t["b"].company_id = other.id
    t["prof_b"].company_id = other.id
    db.commit()
    resp = client.post(f"/api/v1/hr/jobs/{job.id}/transfer",
                       json={"to_hr_id": t["prof_b"].id}, headers=t["headers_a"])
    assert resp.status_code == 404


def test_bulk_transfer_moves_open_jobs_and_sends_one_summary(client, db, two_hrs):
    from src.models.models import JobPosting, JobTransferHistory, Notification

    t = two_hrs
    cid = t["a"].company_id
    open1 = _job(db, cid, t["prof_a"].id, title="One")
    open2 = _job(db, cid, t["prof_a"].id, title="Two", status="draft")
    closed = _job(db, cid, t["prof_a"].id, title="Closed", status="closed")
    db.commit()

    resp = client.post("/api/v1/hr/jobs/transfer-bulk",
                       json={"to_hr_id": t["prof_b"].id}, headers=t["headers_a"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["transferred"] == 2

    db.expire_all()
    assert db.get(JobPosting, open1.id).hr_id == t["prof_b"].id
    assert db.get(JobPosting, open2.id).hr_id == t["prof_b"].id
    assert db.get(JobPosting, closed.id).hr_id == t["prof_a"].id
    assert db.query(JobTransferHistory).filter(JobTransferHistory.bulk == True).count() == 2  # noqa: E712

    notes = db.query(Notification).filter(Notification.type == "job_transferred").all()
    assert len(notes) == 1 and notes[0].user_id == t["b"].id

    # Asking for closed jobs explicitly moves them too.
    resp = client.post("/api/v1/hr/jobs/transfer-bulk",
                       json={"to_hr_id": t["prof_b"].id, "statuses": ["closed"]},
                       headers=t["headers_a"])
    assert resp.json()["transferred"] == 1


def test_bulk_transfer_to_self_is_rejected(client, db, two_hrs):
    t = two_hrs
    resp = client.post("/api/v1/hr/jobs/transfer-bulk",
                       json={"to_hr_id": t["prof_a"].id}, headers=t["headers_a"])
    assert resp.status_code == 400


def test_legacy_assign_hr_also_moves_offers_and_records_history(client, db, two_hrs):
    from src.models.models import JobTransferHistory, Offer

    t = two_hrs
    job, offer, _ = _job_with_offer_and_interview(db, t["a"].company_id, t["prof_a"].id)
    resp = client.put(f"/api/v1/hr/jobs/{job.id}/assign-hr",
                      params={"hr_id": t["prof_b"].id}, headers=t["headers_a"])
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.get(Offer, offer.id).hr_id == t["prof_b"].id
    assert db.query(JobTransferHistory).filter(JobTransferHistory.job_id == job.id).count() == 1

    # Re-assigning the current owner stays a harmless no-op on this endpoint. B owns the
    # job now, so B is the one allowed to call it.
    again = client.put(f"/api/v1/hr/jobs/{job.id}/assign-hr",
                       params={"hr_id": t["prof_b"].id}, headers=t["headers_b"])
    assert again.status_code == 200


def test_permanent_delete_still_works_after_a_transfer(client, db, two_hrs):
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    client.post(f"/api/v1/hr/jobs/{job.id}/transfer",
                json={"to_hr_id": t["prof_b"].id}, headers=t["headers_a"])
    resp = client.delete(f"/api/v1/hr/jobs/{job.id}/permanent", headers=t["headers_b"])
    assert resp.status_code == 200, resp.text
