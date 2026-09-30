"""Recruiter workflows: duplicates and merging, stages and reminders, bulk actions,
source tracking, independent scorecards, and candidate withdrawal.

The load-bearing tests are the ones that pin a promise made to a person:
* a panellist never sees a colleague's scores before submitting their own;
* a bulk action runs only on the count the recruiter confirmed;
* a merge can be undone and deletes nothing;
* a withdrawal cancels the interviews the candidate would otherwise be expected at.
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from test.test_tenant_isolation import _make_company, _make_job, _make_user, _login
from test.test_interviewer_deactivation import (
    _make_hr, _make_interviewer, _make_scheduled_interview, _panel_entry,
)

WF = "/api/v1/hr/workflow"
SC = "/api/v1/scorecards"


def _cand(db, company_id, job_id, first="Asha", last="Rao", email=None, phone=None,
          stage="hr_review", user_id=None, **extra):
    from src.models.models import Candidate
    c = Candidate(
        id=str(uuid.uuid4()), company_id=company_id, job_id=job_id, user_id=user_id,
        email=email or f"c_{uuid.uuid4().hex[:6]}@test.com", first_name=first, last_name=last,
        phone=phone, current_stage=stage, **extra,
    )
    db.add(c)
    db.flush()
    return c


def _history(db, cand, to_stage, days_ago, from_stage="applied"):
    from src.models.models import CandidateStageHistory
    row = CandidateStageHistory(
        id=str(uuid.uuid4()), company_id=cand.company_id, candidate_id=cand.id,
        from_stage=from_stage, to_stage=to_stage,
        created_at=datetime.utcnow().replace(microsecond=0) - timedelta(days=days_ago),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def world(client, db):
    from src.models.models import JobPosting

    cid = _make_company(db, "Workflow Co")
    _make_user(db, "admin-wf@example.com", "admin", cid)
    hr_id, hr_p = _make_hr(db, cid, "hr-wf@example.com")
    hr2_id, hr2_p = _make_hr(db, cid, "hr2-wf@example.com")
    alice, alice_p = _make_interviewer(db, cid, "alice-wf@example.com")
    bob, bob_p = _make_interviewer(db, cid, "bob-wf@example.com")
    carol, carol_p = _make_interviewer(db, cid, "carol-wf@example.com")

    job1 = _make_job(db, cid, title="Backend Dev")
    job2 = _make_job(db, cid, title="Designer")
    db.query(JobPosting).filter(JobPosting.id == job1).first().hr_id = hr_p.id
    db.query(JobPosting).filter(JobPosting.id == job2).first().hr_id = hr2_p.id
    db.flush()
    db.commit()
    return {
        "company_id": cid, "job1": job1, "job2": job2,
        "hr_id": hr_id, "hr2_id": hr2_id,
        "alice": alice, "alice_p": alice_p, "bob": bob, "bob_p": bob_p,
        "carol": carol, "carol_p": carol_p,
        "hr": _login(client, "hr-wf@example.com"),
        "hr2": _login(client, "hr2-wf@example.com"),
        "admin": _login(client, "admin-wf@example.com"),
    }


# ─── source tracking ─────────────────────────────────────────────────────────

class TestSource:
    def test_normalize_aliases_and_rejects_unknown(self):
        from fastapi import HTTPException
        from src.services.candidate_source import normalize_source

        assert normalize_source("LinkedIn")["source"] == "job_portal"
        assert normalize_source("LinkedIn")["source_detail"] == "LinkedIn"
        assert normalize_source(None)["source"] == "direct"
        ref = normalize_source("referral", referrer_name="Ravi", referrer_email="r@x.com")
        assert (ref["referrer_name"], ref["referrer_email"]) == ("Ravi", "r@x.com")
        # referrer fields are dropped for non-referrals rather than stored as noise
        assert normalize_source("agency", referrer_name="Ravi")["referrer_name"] is None
        with pytest.raises(HTTPException):
            normalize_source("carrier-pigeon")
        with pytest.raises(HTTPException):
            normalize_source("talent_pool", allowed={"direct", "referral"})

    def test_hr_sets_source_and_report_counts_conversion(self, client, db, world):
        a = _cand(db, world["company_id"], world["job1"], stage="hired")
        b = _cand(db, world["company_id"], world["job1"], stage="rejected")
        _cand(db, world["company_id"], world["job1"], stage="applied")   # not recorded
        db.commit()
        for c in (a, b):
            r = client.patch(f"{WF}/candidates/{c.id}/source", headers=world["hr"],
                             json={"source": "referral", "referrer_name": "Ravi"})
            assert r.status_code == 200, r.text
            assert r.json()["source_label"] == "Employee referral"

        rep = client.get(f"{WF}/sources/report", headers=world["hr"]).json()
        by = {s["source"]: s for s in rep["sources"]}
        assert by["referral"]["total"] == 2
        assert by["referral"]["hired"] == 1 and by["referral"]["hire_rate"] == 50.0
        assert by["unknown"]["label"] == "Not recorded"

    def test_hr_cannot_touch_another_hrs_candidate(self, client, db, world):
        other = _cand(db, world["company_id"], world["job2"])
        db.commit()
        r = client.patch(f"{WF}/candidates/{other.id}/source", headers=world["hr"],
                         json={"source": "agency"})
        assert r.status_code == 404


# ─── duplicates & merging ────────────────────────────────────────────────────

class TestDuplicates:
    def test_normalisers(self):
        from src.services.candidate_duplicates import (
            normalize_email, normalize_linkedin, normalize_phone)
        assert normalize_email("A.Sha+jobs@GMail.com") == "asha@gmail.com"
        assert normalize_email("a.sha@company.com") == "a.sha@company.com"
        assert normalize_phone("+91 98765-43210") == normalize_phone("09876543210")
        assert normalize_linkedin("https://www.linkedin.com/in/asha-rao/?x=1") == "in/asha-rao"

    def test_same_phone_different_email_is_a_group(self, client, db, world):
        a = _cand(db, world["company_id"], world["job1"], email="asha@x.com", phone="+91 98765 43210")
        b = _cand(db, world["company_id"], world["job1"], email="asha.rao@y.com", phone="9876543210")
        _cand(db, world["company_id"], world["job1"], first="Someone", last="Else")
        db.commit()

        groups = client.get(f"{WF}/duplicates", headers=world["hr"]).json()["groups"]
        assert len(groups) == 1
        assert {c["candidate_id"] for c in groups[0]["candidates"]} == {a.id, b.id}
        assert "phone" in groups[0]["reasons"] and groups[0]["same_job"]

        matches = client.get(f"{WF}/candidates/{a.id}/duplicates", headers=world["hr"]).json()["matches"]
        assert matches[0]["candidate_id"] == b.id and matches[0]["can_merge"]

    def test_cross_job_match_cannot_be_merged(self, client, db, world):
        a = _cand(db, world["company_id"], world["job1"], email="p@x.com", phone="9876500000")
        b = _cand(db, world["company_id"], world["job2"], email="q@x.com", phone="9876500000")
        db.commit()
        r = client.post(f"{WF}/merge/preview", headers=world["admin"],
                        json={"primary_id": a.id, "merged_id": b.id})
        assert r.status_code == 200
        assert not r.json()["can_merge"]
        assert any("same job" in x for x in r.json()["blockers"])

    def test_merge_needs_confirm_then_moves_interviews_and_undo_restores(self, client, db, world):
        from src.models.models import Candidate, CandidateMerge, Interview

        a = _cand(db, world["company_id"], world["job1"], email="a1@x.com", phone=None,
                  first="Asha", last="Rao", stage="shortlisted")
        b = _cand(db, world["company_id"], world["job1"], email="a2@x.com", phone="9000000001",
                  first="Asha", last="Rao", stage="hr_review",
                  linkedin_url="https://linkedin.com/in/asha")
        iv = _make_scheduled_interview(db, world["company_id"], world["job1"], [])
        iv.candidate_id = b.id
        db.commit()

        body = {"primary_id": a.id, "merged_id": b.id,
                "choices": {"phone": "merged", "linkedin_url": "merged"}, "reason": "same person"}
        assert client.post(f"{WF}/merge", headers=world["hr"], json=body).status_code == 400

        r = client.post(f"{WF}/merge", headers=world["hr"], json={**body, "confirm": True})
        assert r.status_code == 200, r.text
        merge_id = r.json()["merge"]["id"]
        db.expire_all()
        a2, b2 = db.get(Candidate, a.id), db.get(Candidate, b.id)
        assert a2.phone == "9000000001" and a2.email == "a1@x.com"   # email always kept
        assert b2.merged_into_id == a.id and b2.current_stage == "merged"
        assert db.get(Interview, iv.id).candidate_id == a.id
        # merged row no longer shows up as a duplicate
        assert client.get(f"{WF}/duplicates", headers=world["hr"]).json()["groups"] == []

        # merging again is refused
        again = client.post(f"{WF}/merge", headers=world["hr"], json={**body, "confirm": True})
        assert again.status_code == 409

        u = client.post(f"{WF}/merges/{merge_id}/undo", headers=world["hr"])
        assert u.status_code == 200, u.text
        db.expire_all()
        a3, b3 = db.get(Candidate, a.id), db.get(Candidate, b.id)
        assert a3.phone is None and a3.linkedin_url is None
        assert b3.merged_into_id is None and b3.current_stage == "hr_review"
        assert db.get(Interview, iv.id).candidate_id == b.id
        assert db.get(CandidateMerge, merge_id).undone_at is not None
        assert client.post(f"{WF}/merges/{merge_id}/undo", headers=world["hr"]).status_code == 409

    def test_merge_blocked_when_merged_away_side_has_an_offer(self, client, db, world):
        from src.models.models import Offer
        a = _cand(db, world["company_id"], world["job1"])
        b = _cand(db, world["company_id"], world["job1"])
        db.add(Offer(id=str(uuid.uuid4()), company_id=world["company_id"], candidate_id=b.id,
                     job_id=world["job1"], salary=100000, status="sent",
                     start_date=datetime.utcnow().date(), expiry_date=datetime.utcnow().date()))
        db.commit()
        r = client.post(f"{WF}/merge", headers=world["hr"],
                        json={"primary_id": a.id, "merged_id": b.id, "confirm": True})
        assert r.status_code == 409


# ─── stages, deadlines, pending actions, reminders ──────────────────────────

class TestStages:
    def _configure(self, client, world, **hr_review):
        stages = client.get(f"{WF}/stages", headers=world["hr"]).json()["stages"]
        payload = []
        for s in stages:
            item = {k: s[k] for k in ("key", "label", "owner_user_id", "sla_days", "is_active")}
            if s["key"] == "hr_review":
                item.update(hr_review)
            payload.append(item)
        return client.put(f"{WF}/stages", headers=world["hr"], json={"stages": payload})

    def test_defaults_then_configure_and_custom_stage(self, client, world):
        stages = client.get(f"{WF}/stages", headers=world["hr"]).json()["stages"]
        assert [s["key"] for s in stages][:3] == ["applied", "ats_screening", "hr_review"]
        assert all(s["sla_days"] is None for s in stages)

        payload = [{k: s[k] for k in ("key", "label", "owner_user_id", "sla_days", "is_active")}
                   for s in stages]
        payload.insert(3, {"key": "phone_screen", "label": "Phone screen", "sla_days": 2})
        r = client.put(f"{WF}/stages", headers=world["hr"], json={"stages": payload})
        assert r.status_code == 200, r.text
        keys = [s["key"] for s in r.json()["stages"]]
        assert keys.index("phone_screen") == 3

    def test_final_stage_cannot_have_deadline(self, client, world):
        stages = client.get(f"{WF}/stages", headers=world["hr"]).json()["stages"]
        payload = [{"key": s["key"], "label": s["label"],
                    "sla_days": 3 if s["key"] == "hired" else None} for s in stages]
        assert client.put(f"{WF}/stages", headers=world["hr"],
                          json={"stages": payload}).status_code == 400

    def test_owner_must_be_company_hr(self, client, world):
        r = self._configure(client, world, owner_user_id=str(world["alice"].id))
        assert r.status_code == 400

    def test_pending_actions_owner_fallback_and_scope(self, client, db, world):
        late = _cand(db, world["company_id"], world["job1"], first="Late")
        _history(db, late, "hr_review", days_ago=5)
        soon = _cand(db, world["company_id"], world["job1"], first="Soon")
        _history(db, soon, "hr_review", days_ago=2, )
        fine = _cand(db, world["company_id"], world["job1"], first="Fine")
        _history(db, fine, "hr_review", days_ago=0)
        theirs = _cand(db, world["company_id"], world["job2"], first="Theirs")
        _history(db, theirs, "hr_review", days_ago=9)
        db.commit()
        assert self._configure(client, world, sla_days=3).status_code == 200

        # hr_review has no stage owner, so each job's owning HR owns the action.
        mine = client.get(f"{WF}/pending-actions", headers=world["hr"]).json()
        names = {i["candidate_name"]: i for i in mine["items"]}
        assert set(names) == {"Late Rao", "Soon Rao"}
        assert names["Late Rao"]["status"] == "overdue"
        assert names["Soon Rao"]["status"] == "due_soon"
        assert names["Late Rao"]["owner_source"] == "job"

        everything = client.get(f"{WF}/pending-actions?scope=all&status=all",
                                headers=world["admin"]).json()
        assert {i["candidate_name"] for i in everything["items"]} == \
            {"Late Rao", "Soon Rao", "Fine Rao", "Theirs Rao"}

        # Assigning a stage owner moves every action to them.
        assert self._configure(client, world, sla_days=3,
                               owner_user_id=world["hr2_id"]).status_code == 200
        assert client.get(f"{WF}/pending-actions", headers=world["hr"]).json()["items"] == []

    def test_reminders_are_sent_once_per_day(self, client, db, world):
        from src.models.models import Notification, StageReminderLog

        late = _cand(db, world["company_id"], world["job1"])
        _history(db, late, "hr_review", days_ago=5)
        db.commit()
        assert self._configure(client, world, sla_days=3).status_code == 200

        first = client.post(f"{WF}/reminders/run", headers=world["hr"])
        assert first.status_code == 200, first.text
        assert first.json()["reminders"] == 1
        assert client.post(f"{WF}/reminders/run", headers=world["hr"]).json()["reminders"] == 0
        assert db.query(StageReminderLog).filter(StageReminderLog.candidate_id == late.id).count() == 1
        assert db.query(Notification).filter(Notification.user_id == world["hr_id"],
                                             Notification.type == "pending_action").count() == 1

    def test_reminder_endpoint_needs_auth(self, client, world):
        assert client.post(f"{WF}/reminders/run").status_code == 401


# ─── bulk actions ────────────────────────────────────────────────────────────

class TestBulk:
    def test_preview_classifies_then_confirmed_execute_applies(self, client, db, world):
        from src.models.models import AuditLog, Candidate, CandidateStageHistory

        a = _cand(db, world["company_id"], world["job1"], stage="hr_review")
        b = _cand(db, world["company_id"], world["job1"], stage="hr_review")
        already = _cand(db, world["company_id"], world["job1"], stage="rejected")
        hired = _cand(db, world["company_id"], world["job1"], stage="hired")
        other_hr = _cand(db, world["company_id"], world["job2"], stage="hr_review")
        db.commit()

        ids = [a.id, b.id, already.id, hired.id, other_hr.id]
        p = client.post(f"{WF}/bulk/preview", headers=world["hr"],
                        json={"action": "reject", "candidate_ids": ids, "notify_candidates": False})
        assert p.status_code == 200, p.text
        op = p.json()
        assert op["eligible_count"] == 2
        reasons = {s["candidate_id"]: s["reason"] for s in op["preview"]["skipped"]}
        assert "Already in rejected" in reasons[already.id]
        assert "Already hired" in reasons[hired.id]
        assert "Not found" in reasons[other_hr.id]
        # previewing changed nothing
        db.expire_all()
        assert db.get(Candidate, a.id).current_stage == "hr_review"

        wrong = client.post(f"{WF}/bulk/{op['id']}/execute", headers=world["hr"],
                            json={"confirm_count": 5})
        assert wrong.status_code == 400

        ok = client.post(f"{WF}/bulk/{op['id']}/execute", headers=world["hr"],
                         json={"confirm_count": 2})
        assert ok.status_code == 200, ok.text
        assert ok.json()["affected_count"] == 2
        db.expire_all()
        assert db.get(Candidate, a.id).current_stage == "rejected"
        assert db.get(Candidate, other_hr.id).current_stage == "hr_review"
        note = db.query(CandidateStageHistory).filter(
            CandidateStageHistory.candidate_id == a.id,
            CandidateStageHistory.to_stage == "rejected").one().notes
        assert op["id"][:8] in note
        assert db.query(AuditLog).filter(AuditLog.resource == f"bulk_operations/{op['id']}").count() == 1

        assert client.post(f"{WF}/bulk/{op['id']}/execute", headers=world["hr"],
                           json={"confirm_count": 2}).status_code == 409
        history = client.get(f"{WF}/bulk", headers=world["hr"]).json()["operations"]
        assert history[0]["id"] == op["id"] and history[0]["status"] == "executed"
        # another HR cannot see or run it
        assert client.get(f"{WF}/bulk/{op['id']}", headers=world["hr2"]).status_code == 404

    def test_execute_rechecks_state_changed_since_preview(self, client, db, world):
        from src.models.models import Candidate
        a = _cand(db, world["company_id"], world["job1"], stage="hr_review")
        b = _cand(db, world["company_id"], world["job1"], stage="hr_review")
        db.commit()
        op = client.post(f"{WF}/bulk/preview", headers=world["hr"],
                         json={"action": "shortlist", "candidate_ids": [a.id, b.id]}).json()
        db.get(Candidate, b.id).current_stage = "withdrawn"
        db.commit()
        r = client.post(f"{WF}/bulk/{op['id']}/execute", headers=world["hr"],
                        json={"confirm_count": 2}).json()
        assert r["affected_count"] == 1
        skipped = [o for o in r["result"] if o["outcome"] == "skipped"]
        assert skipped[0]["candidate_id"] == b.id

    def test_expired_preview_is_refused(self, client, db, world):
        from src.models.models import BulkOperation
        a = _cand(db, world["company_id"], world["job1"])
        db.commit()
        op = client.post(f"{WF}/bulk/preview", headers=world["hr"],
                         json={"action": "shortlist", "candidate_ids": [a.id]}).json()
        db.get(BulkOperation, op["id"]).expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
        assert client.post(f"{WF}/bulk/{op['id']}/execute", headers=world["hr"],
                           json={"confirm_count": 1}).status_code == 410

    def test_move_stage_validates_target(self, client, db, world):
        a = _cand(db, world["company_id"], world["job1"])
        db.commit()
        for bad in ("merged", "withdrawn", "nonsense"):
            r = client.post(f"{WF}/bulk/preview", headers=world["hr"],
                            json={"action": "move_stage", "target_stage": bad, "candidate_ids": [a.id]})
            assert r.status_code == 400, bad


# ─── independent scorecards ──────────────────────────────────────────────────

@pytest.fixture()
def panel(client, db, world):
    cand = _cand(db, world["company_id"], world["job1"], stage="interview_scheduled")
    iv = _make_scheduled_interview(db, world["company_id"], world["job1"], [
        _panel_entry(world["alice"], world["alice_p"]),
        _panel_entry(world["bob"], world["bob_p"]),
    ])
    iv.candidate_id = cand.id
    db.commit()
    return {
        "iv": iv.id, "cand": cand.id,
        "alice": _login(client, "alice-wf@example.com"),
        "bob": _login(client, "bob-wf@example.com"),
        "carol": _login(client, "carol-wf@example.com"),
    }


CARD = {"technical_score": 8, "communication_score": 7, "problem_solving_score": 9,
        "cultural_fit_score": 6, "hire_recommendation": "hire", "strengths": ["APIs"]}


class TestScorecards:
    def test_blind_until_submitted(self, client, world, panel):
        url = f"{SC}/interviews/{panel['iv']}"
        r = client.post(f"{url}/mine/submit", headers=panel["alice"], json=CARD)
        assert r.status_code == 200, r.text
        assert r.json()["my_scorecard"]["status"] == "submitted"

        bob_view = client.get(url, headers=panel["bob"]).json()
        assert bob_view["others_visible"] is False and bob_view["others"] is None
        assert bob_view["summary"] is None
        # Bob can see THAT Alice submitted, not what she said.
        assert {p["email"]: p["status"] for p in bob_view["panel"]}["alice-wf@example.com"] == "submitted"

        client.put(f"{url}/mine", headers=panel["bob"], json={"technical_score": 5})
        assert client.get(url, headers=panel["bob"]).json()["others"] is None   # a draft isn't a submission

        client.post(f"{url}/mine/submit", headers=panel["bob"],
                    json={**CARD, "technical_score": 5, "hire_recommendation": "no_hire"})
        after = client.get(url, headers=panel["bob"]).json()
        assert [o["panelist_email"] for o in after["others"]] == ["alice-wf@example.com"]
        assert after["summary"]["submitted"] == 2

    def test_hr_sees_submitted_but_never_drafts(self, client, world, panel):
        url = f"{SC}/interviews/{panel['iv']}"
        client.post(f"{url}/mine/submit", headers=panel["alice"], json=CARD)
        client.put(f"{url}/mine", headers=panel["bob"], json={"technical_score": 3})
        hr = client.get(url, headers=world["hr"]).json()
        assert [o["panelist_email"] for o in hr["others"]] == ["alice-wf@example.com"]
        assert hr["is_panelist"] is False

    def test_submitted_is_locked_and_incomplete_refused(self, client, panel):
        url = f"{SC}/interviews/{panel['iv']}"
        r = client.post(f"{url}/mine/submit", headers=panel["alice"], json={"technical_score": 8})
        assert r.status_code == 400
        assert "hire_recommendation" in r.json()["detail"]["missing"]
        client.post(f"{url}/mine/submit", headers=panel["alice"], json=CARD)
        assert client.put(f"{url}/mine", headers=panel["alice"],
                          json={"technical_score": 1}).status_code == 409

    def test_outsiders_get_404(self, client, world, panel):
        url = f"{SC}/interviews/{panel['iv']}"
        assert client.get(url, headers=panel["carol"]).status_code == 404
        assert client.get(url, headers=world["hr2"]).status_code == 404   # not on the job

    def test_pending_list(self, client, panel):
        pending = client.get(f"{SC}/pending", headers=panel["alice"]).json()["pending"]
        assert [p["interview_id"] for p in pending] == [panel["iv"]]
        client.post(f"{SC}/interviews/{panel['iv']}/mine/submit", headers=panel["alice"], json=CARD)
        assert client.get(f"{SC}/pending", headers=panel["alice"]).json()["pending"] == []

    def test_advanced_views_hide_consolidated_feedback_until_submitted(self, client, db, world, panel):
        """The candidate 360 / scorecard / compare views used to show an interviewer every
        colleague's scores for the candidate before they had scored them."""
        from src.models.models import InterviewFeedback
        db.add(InterviewFeedback(id=str(uuid.uuid4()), company_id=world["company_id"],
                                 interview_id=panel["iv"], interviewer_id=world["alice_p"].id,
                                 technical_score=90, communication_score=80,
                                 problem_solving_score=85, cultural_fit_score=70,
                                 overall_score=83, hire_recommendation="strong_hire"))
        db.commit()
        prof = f"/api/v1/advanced/candidates/{panel['cand']}/profile"

        before = client.get(prof, headers=panel["bob"])
        assert before.status_code == 200, before.text
        iv = before.json()["interviews"][0]
        assert iv["feedback"] is None and iv["feedback_hidden"] is True
        sc = client.get(f"/api/v1/advanced/candidates/{panel['cand']}/scorecard", headers=panel["bob"])
        assert sc.status_code == 200 and "strong_hire" not in sc.text

        client.post(f"{SC}/interviews/{panel['iv']}/mine/submit", headers=panel["bob"], json=CARD)
        after = client.get(prof, headers=panel["bob"]).json()["interviews"][0]
        assert after["feedback"]["hire_recommendation"] == "strong_hire"
        # Alice wrote the consolidated feedback, which counts as her submission.
        assert client.get(prof, headers=panel["alice"]).json()["interviews"][0]["feedback"] is not None

    def test_interviewer_cannot_filter_search_by_verdict(self, client, panel):
        r = client.get("/api/v1/advanced/candidates/search?hire_recommendation=hire", headers=panel["alice"])
        assert r.status_code == 403


# ─── candidate status & withdrawal ───────────────────────────────────────────

@pytest.fixture()
def applicant(client, db, world):
    uid = _make_user(db, "applicant-wf@example.com", "candidate", world["company_id"])
    app_ = _cand(db, world["company_id"], world["job1"], email="applicant-wf@example.com",
                 user_id=uid, stage="hr_review")
    iv = _make_scheduled_interview(db, world["company_id"], world["job1"], [])
    iv.candidate_id = app_.id
    db.commit()
    return {"user_id": uid, "app": app_.id, "iv": iv.id,
            "headers": _login(client, "applicant-wf@example.com")}


class TestWithdrawal:
    def test_status_label_is_plain_language(self, client, applicant):
        apps = client.get("/api/v1/candidate/applications", headers=applicant["headers"]).json()
        assert apps[0]["status_label"] == "Under review"
        assert apps[0]["can_withdraw"] is True

    def test_withdraw_cancels_interview_and_notifies_hr(self, client, db, world, applicant):
        from src.models.models import Candidate, Interview, Notification

        url = f"/api/v1/candidate/applications/{applicant['app']}/withdraw"
        assert client.post(url, headers=applicant["headers"], json={}).status_code == 400
        r = client.post(url, headers=applicant["headers"],
                        json={"confirm": True, "reason": "Accepted another offer"})
        assert r.status_code == 200, r.text
        assert r.json()["status_label"] == "Withdrawn"
        assert r.json()["cancelled_interviews"] == [applicant["iv"]]

        db.expire_all()
        c = db.get(Candidate, applicant["app"])
        assert c.current_stage == "withdrawn" and c.withdrawal_reason == "Accepted another offer"
        assert db.get(Interview, applicant["iv"]).status == "CANCELLED"
        assert db.query(Notification).filter(Notification.user_id == world["hr_id"],
                                             Notification.type == "application_withdrawn").count() == 1
        assert client.post(url, headers=applicant["headers"], json={"confirm": True}).status_code == 409

    def test_cannot_withdraw_someone_elses_application(self, client, db, world, applicant):
        other = _cand(db, world["company_id"], world["job1"], email="someone@else.com")
        db.commit()
        r = client.post(f"/api/v1/candidate/applications/{other.id}/withdraw",
                        headers=applicant["headers"], json={"confirm": True})
        assert r.status_code == 404

    def test_hired_cannot_withdraw(self, client, db, applicant):
        from src.models.models import Candidate
        db.get(Candidate, applicant["app"]).current_stage = "hired"
        db.commit()
        r = client.post(f"/api/v1/candidate/applications/{applicant['app']}/withdraw",
                        headers=applicant["headers"], json={"confirm": True})
        assert r.status_code == 409


def test_new_routes_refuse_candidates(client, applicant):
    for path in (f"{WF}/stages", f"{WF}/pending-actions", f"{WF}/duplicates", f"{WF}/bulk",
                 f"{SC}/pending"):
        assert client.get(path, headers=applicant["headers"]).status_code == 403, path
