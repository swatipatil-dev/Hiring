"""Co-HRs: two HRs working the SAME job.

The job is never copied — a `job_hr_collaborators` row is the whole grant. The things that
must not happen: the same HR added twice, the owner also listed as a co-HR, or a shared job
appearing twice in a list / being counted twice on the dashboard.
"""
import uuid
from datetime import date, timedelta

from conftest import _register_and_login
from test.test_job_transfer import (  # reuse the two-HR fixture and job builders
    _job,
    _job_with_offer_and_interview,
    two_hrs,  # noqa: F401 - imported so pytest resolves it as a fixture here
)


def _uid():
    return str(uuid.uuid4())


def _add(client, job_id, hr_id, headers):
    return client.post(f"/api/v1/hr/jobs/{job_id}/collaborators", json={"hr_id": hr_id},
                       headers=headers)


def _jobs(client, headers):
    resp = client.get("/api/v1/hr/jobs", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _counts(client, headers):
    resp = client.get("/api/v1/hr/dashboard/counts", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_co_hr_gets_full_access_to_the_same_job(client, db, two_hrs):
    from src.models.models import JobPosting, Notification

    t = two_hrs
    job, offer, iv = _job_with_offer_and_interview(db, t["a"].company_id, t["prof_a"].id)
    jobs_before = db.query(JobPosting).count()

    # Before sharing, B has no access.
    assert client.get(f"/api/v1/hr/jobs/{job.id}/candidates", headers=t["headers_b"]).status_code == 403

    resp = _add(client, job.id, t["prof_b"].id, t["headers_a"])
    assert resp.status_code == 200, resp.text

    # Shared, not copied.
    assert db.query(JobPosting).count() == jobs_before

    listed = [j for j in _jobs(client, t["headers_b"]) if j["id"] == job.id]
    assert len(listed) == 1
    assert listed[0]["my_role"] == "collaborator"
    assert listed[0]["my_hr_id"] == t["prof_b"].id   # what "Leave job" sends back
    assert listed[0]["hr_id"] == t["prof_a"].id

    owner_view = next(j for j in _jobs(client, t["headers_a"]) if j["id"] == job.id)
    assert owner_view["my_role"] == "owner"
    assert [c["hr_id"] for c in owner_view["collaborators"]] == [t["prof_b"].id]

    assert client.get(f"/api/v1/hr/jobs/{job.id}", headers=t["headers_b"]).status_code == 200
    assert client.get(f"/api/v1/hr/jobs/{job.id}/candidates", headers=t["headers_b"]).status_code == 200

    offers = client.get("/api/v1/hr/offers", headers=t["headers_b"]).json()
    assert offer.id in {o["id"] for o in offers}

    counts = _counts(client, t["headers_b"])
    assert counts["jobs"] == 1 and counts["offers"] == 1 and counts["interviews"] == 1

    notes = db.query(Notification).filter(Notification.type == "job_shared").all()
    assert [n.user_id for n in notes] == [t["b"].id]


def test_no_duplicates(client, db, two_hrs):
    from src.models.models import JobHRCollaborator

    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()

    assert _add(client, job.id, t["prof_b"].id, t["headers_a"]).status_code == 200
    again = _add(client, job.id, t["prof_b"].id, t["headers_a"])
    assert again.status_code == 409
    assert db.query(JobHRCollaborator).filter(JobHRCollaborator.job_id == job.id).count() == 1

    # The owner can't also be a co-HR.
    assert _add(client, job.id, t["prof_a"].id, t["headers_a"]).status_code == 400


def test_shared_job_is_listed_and_counted_once_with_several_co_hrs(client, db, two_hrs):
    from src.models.models import HRProfile, User

    t = two_hrs
    job, _, _ = _job_with_offer_and_interview(db, t["a"].company_id, t["prof_a"].id)
    _register_and_login(client, "hr_c_test@test.com", "Pass@1234", "hr", "Cal", "Sea", db=db)
    hr_c = db.query(User).filter(User.email == "hr_c_test@test.com").first()
    hr_c.company_id = t["a"].company_id
    prof_c = HRProfile(id=_uid(), company_id=hr_c.company_id, user_id=hr_c.id, employee_id="HR-C")
    db.add(prof_c)
    db.commit()

    assert _add(client, job.id, t["prof_b"].id, t["headers_a"]).status_code == 200
    assert _add(client, job.id, prof_c.id, t["headers_a"]).status_code == 200

    # A JOIN on the co-HR table would return this job three times.
    assert [j["id"] for j in _jobs(client, t["headers_a"])].count(job.id) == 1
    assert [j["id"] for j in _jobs(client, t["headers_b"])].count(job.id) == 1
    counts = _counts(client, t["headers_a"])
    assert counts["jobs"] == 1 and counts["offers"] == 1 and counts["interviews"] == 1


def test_bad_targets_are_rejected(client, db, two_hrs):
    from src.models.models import Company

    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    assert _add(client, job.id, _uid(), t["headers_a"]).status_code == 404

    t["b"].is_active = False
    db.commit()
    assert _add(client, job.id, t["prof_b"].id, t["headers_a"]).status_code == 400

    t["b"].is_active = True
    other = Company(id=_uid(), name="Other Co", slug=f"other-{_uid()[:8]}")
    db.add(other)
    db.flush()
    t["b"].company_id = other.id
    t["prof_b"].company_id = other.id
    db.commit()
    assert _add(client, job.id, t["prof_b"].id, t["headers_a"]).status_code == 404


def test_owner_only_actions_are_blocked_for_a_co_hr(client, db, two_hrs):
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    _add(client, job.id, t["prof_b"].id, t["headers_a"])

    # B can't manage the team, transfer or delete. (The owner check runs before the
    # "already owns it" check, so this is a 403, not a 400.)
    assert _add(client, job.id, t["prof_a"].id, t["headers_b"]).status_code == 403
    assert client.post(f"/api/v1/hr/jobs/{job.id}/transfer", json={"to_hr_id": t["prof_b"].id},
                       headers=t["headers_b"]).status_code == 403
    assert client.delete(f"/api/v1/hr/jobs/{job.id}/permanent",
                         headers=t["headers_b"]).status_code == 403


def test_co_hr_can_leave_and_then_loses_access(client, db, two_hrs):
    from src.models.models import Notification

    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    _add(client, job.id, t["prof_b"].id, t["headers_a"])

    resp = client.delete(f"/api/v1/hr/jobs/{job.id}/collaborators/{t['prof_b'].id}",
                         headers=t["headers_b"])
    assert resp.status_code == 200, resp.text
    assert client.get(f"/api/v1/hr/jobs/{job.id}", headers=t["headers_b"]).status_code == 403
    # Leaving yourself is not announced back to you.
    assert db.query(Notification).filter(Notification.title == "Removed from job").count() == 0


def test_transfer_to_a_co_hr_makes_them_owner_only(client, db, two_hrs):
    from src.models.models import JobHRCollaborator, JobPosting

    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    _add(client, job.id, t["prof_b"].id, t["headers_a"])

    resp = client.post(f"/api/v1/hr/jobs/{job.id}/transfer", json={"to_hr_id": t["prof_b"].id},
                       headers=t["headers_a"])
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.get(JobPosting, job.id).hr_id == t["prof_b"].id
    assert db.query(JobHRCollaborator).filter(JobHRCollaborator.job_id == job.id).count() == 0


def test_permanent_delete_of_a_shared_job(client, db, two_hrs):
    from src.models.models import JobHRCollaborator

    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    _add(client, job.id, t["prof_b"].id, t["headers_a"])

    resp = client.delete(f"/api/v1/hr/jobs/{job.id}/permanent", headers=t["headers_a"])
    assert resp.status_code == 200, resp.text
    assert db.query(JobHRCollaborator).filter(JobHRCollaborator.job_id == job.id).count() == 0


def test_offer_sent_by_co_hr_is_visible_to_owner(client, db, two_hrs):
    from src.models.models import Candidate, Offer

    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    cand = Candidate(id=_uid(), company_id=job.company_id, job_id=job.id,
                     email=f"c-{_uid()[:8]}@example.com", first_name="C", last_name="D")
    db.add(cand)
    db.flush()
    offer = Offer(id=_uid(), company_id=job.company_id, candidate_id=cand.id, job_id=job.id,
                  hr_id=t["prof_b"].id, salary=1, start_date=date.today(),
                  expiry_date=date.today() + timedelta(days=7))
    db.add(offer)
    db.commit()
    _add(client, job.id, t["prof_b"].id, t["headers_a"])

    offers = client.get("/api/v1/hr/offers", headers=t["headers_a"]).json()
    assert offer.id in {o["id"] for o in offers}


def test_collaborators_endpoint_lists_the_team(client, db, two_hrs):
    t = two_hrs
    job = _job(db, t["a"].company_id, t["prof_a"].id)
    db.commit()
    _add(client, job.id, t["prof_b"].id, t["headers_a"])

    body = client.get(f"/api/v1/hr/jobs/{job.id}/collaborators", headers=t["headers_b"]).json()
    assert body["owner"]["hr_id"] == t["prof_a"].id
    assert [c["hr_id"] for c in body["collaborators"]] == [t["prof_b"].id]
