"""
Tests for GET /api/v1/hr/my-activity — the HR sections of the My Profile page.

The load-bearing ones here are the scoping tests: this endpoint exists to say
"your work", so anything it shows that belongs to someone else is a bug with a
user-visible lie attached.
"""

import uuid
from datetime import datetime, timedelta

import pytest


def _seed_hr(db, company_id, email=None, employee_id="HR-X"):
    """A second HR in the SAME company, so the leak tests are meaningful."""
    from src.auth import get_password_hash
    from src.models.models import HRProfile, User

    email = email or f"hr_{uuid.uuid4().hex[:6]}@test.com"
    user = User(
        id=str(uuid.uuid4()), company_id=company_id, email=email,
        password_hash=get_password_hash("Pass@1234"), role="hr",
        first_name="Other", last_name="HR", is_active=True, email_verified=True,
    )
    db.add(user)
    db.flush()
    profile = HRProfile(
        id=str(uuid.uuid4()), company_id=company_id, user_id=user.id,
        employee_id=employee_id,
    )
    db.add(profile)
    db.flush()
    return user, profile


def _seed_job(db, company_id, hr_profile_id, title="A Job", created_at=None):
    from src.models.models import JobPosting

    job = JobPosting(
        id=str(uuid.uuid4()), company_id=company_id, hr_id=hr_profile_id,
        title=title, description="d", requirements="r", status="active",
    )
    if created_at:
        job.created_at = created_at
    db.add(job)
    db.flush()
    return job


def _seed_candidate(db, company_id, job_id, first="Asha", created_at=None):
    from src.models.models import Candidate

    cand = Candidate(
        id=str(uuid.uuid4()), company_id=company_id, job_id=job_id,
        email=f"c_{uuid.uuid4().hex[:6]}@test.com",
        first_name=first, last_name="Rao", current_stage="applied",
    )
    if created_at:
        cand.created_at = created_at
    db.add(cand)
    db.flush()
    return cand


def _seed_history(db, company_id, candidate_id, changed_by_id,
                  from_stage="screening", to_stage="interview", created_at=None):
    from src.models.models import CandidateStageHistory

    row = CandidateStageHistory(
        id=str(uuid.uuid4()), company_id=company_id, candidate_id=candidate_id,
        from_stage=from_stage, to_stage=to_stage, changed_by_id=changed_by_id,
    )
    if created_at:
        row.created_at = created_at
    db.add(row)
    db.flush()
    return row


def _hr_user(db):
    from src.models.models import User
    return db.query(User).filter(User.email == "hr_test@test.com").first()


def _hr_profile(db, user):
    """Create the caller's HRProfile the way the HR routes would."""
    from src.models.models import HRProfile

    profile = db.query(HRProfile).filter(HRProfile.user_id == user.id).first()
    if not profile:
        profile = HRProfile(
            id=str(uuid.uuid4()), company_id=user.company_id,
            user_id=user.id, employee_id="HR-SELF",
        )
        db.add(profile)
        db.flush()
    return profile


# ── role gating ───────────────────────────────────────────────────────────────

def test_my_activity_requires_authentication(client):
    assert client.get("/api/v1/hr/my-activity").status_code in (401, 403)


@pytest.mark.parametrize("fixture_name", ["candidate_headers", "interviewer_headers"])
def test_my_activity_rejects_non_hr_roles(client, request, fixture_name):
    headers = request.getfixturevalue(fixture_name)
    assert client.get("/api/v1/hr/my-activity", headers=headers).status_code == 403


def test_my_activity_rejects_admin(client, admin_headers):
    """
    Admin is rejected on purpose, unlike most /hr endpoints.

    AuthMiddleware.get_current_hr admits admins, but here that would (a) make
    get_hr_profile create an HRProfile row for the admin as a side effect of a
    GET, and (b) show company-wide totals under a heading reading "your work".
    """
    assert client.get("/api/v1/hr/my-activity", headers=admin_headers).status_code == 403


# ── at a glance ───────────────────────────────────────────────────────────────

def test_my_activity_counts_only_my_jobs(client, db, hr_headers):
    """The leak test: two HRs in the same company must not see each other's jobs."""
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    _seed_job(db, me.company_id, mine.id, title="Mine 1")
    _seed_job(db, me.company_id, mine.id, title="Mine 2")

    _, theirs = _seed_hr(db, me.company_id)
    for i in range(3):
        _seed_job(db, me.company_id, theirs.id, title=f"Theirs {i}")

    body = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()
    assert body["at_a_glance"]["jobs"] == 2
    assert body["at_a_glance"]["active_jobs"] == 2


def test_my_activity_matches_dashboard_counts(client, db, hr_headers):
    """Regression guard on the shared _dashboard_counts helper.

    If someone ever forks the counting logic, the profile page and the HR
    dashboard start disagreeing — this fails first.
    """
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    _seed_job(db, me.company_id, mine.id)

    activity = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()
    counts = client.get("/api/v1/hr/dashboard/counts", headers=hr_headers).json()
    assert activity["at_a_glance"] == counts


def test_my_activity_for_hr_with_no_profile_row(client, db, hr_headers):
    """A brand-new HR gets zeros and a 200 — and crucially no HRProfile is created."""
    from src.models.models import HRProfile

    before = db.query(HRProfile).count()
    resp = client.get("/api/v1/hr/my-activity", headers=hr_headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["at_a_glance"] == {"jobs": 0, "active_jobs": 0, "interviews": 0, "offers": 0}
    assert body["recent_activity"] == []
    assert all(v == 0 for k, v in body["last_30_days"].items() if k not in ("days", "since"))

    # A GET must not write.
    assert db.query(HRProfile).count() == before


# ── the 30-day window ─────────────────────────────────────────────────────────

def test_last_30_days_excludes_older_jobs(client, db, hr_headers):
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    _seed_job(db, me.company_id, mine.id, title="Recent")
    _seed_job(db, me.company_id, mine.id, title="Ancient",
              created_at=datetime.utcnow() - timedelta(days=40))

    body = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()
    assert body["at_a_glance"]["jobs"] == 2          # lifetime
    assert body["last_30_days"]["jobs_created"] == 1  # windowed


def test_last_30_days_candidates_is_window_scoped(client, db, hr_headers):
    """Guards against copying admin_routes.get_hr_analytics verbatim — its
    candidate count has no date filter, so it reports a lifetime total inside a
    block labelled as a period."""
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    job = _seed_job(db, me.company_id, mine.id)
    _seed_candidate(db, me.company_id, job.id, first="New")
    _seed_candidate(db, me.company_id, job.id, first="Old",
                    created_at=datetime.utcnow() - timedelta(days=45))

    body = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()
    assert body["last_30_days"]["candidates"] == 1


def test_last_30_days_reports_its_window(client, db, hr_headers):
    body = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()
    assert body["last_30_days"]["days"] == 30
    assert body["last_30_days"]["since"]


# ── the activity feed ─────────────────────────────────────────────────────────

def test_activity_feed_shows_my_stage_changes(client, db, hr_headers):
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    job = _seed_job(db, me.company_id, mine.id, title="Backend Engineer")
    cand = _seed_candidate(db, me.company_id, job.id, first="Asha")
    _seed_history(db, me.company_id, cand.id, me.id)

    feed = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()["recent_activity"]
    assert len(feed) == 1
    assert feed[0]["candidate_name"] == "Asha Rao"
    assert feed[0]["job_title"] == "Backend Engineer"
    assert feed[0]["to_stage"] == "interview"


def test_activity_feed_excludes_note_sentinel(client, db, hr_headers):
    """advanced_features_routes writes private candidate notes into this table
    with from_stage='__note__'. Those are not stage changes."""
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    job = _seed_job(db, me.company_id, mine.id)
    cand = _seed_candidate(db, me.company_id, job.id)

    _seed_history(db, me.company_id, cand.id, me.id, from_stage="__note__", to_stage="private")
    _seed_history(db, me.company_id, cand.id, me.id, from_stage="applied", to_stage="screening")

    feed = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()["recent_activity"]
    assert len(feed) == 1
    assert feed[0]["to_stage"] == "screening"


def test_activity_feed_includes_rows_with_null_from_stage(client, db, hr_headers):
    """
    The NULL trap.

    A naive `from_stage != '__note__'` also drops every row where from_stage is
    NULL, because SQL evaluates NULL != 'x' as NULL rather than true — and
    candidate applications legitimately write from_stage=NULL. Without this test
    that bug is invisible.
    """
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    job = _seed_job(db, me.company_id, mine.id)
    cand = _seed_candidate(db, me.company_id, job.id)
    _seed_history(db, me.company_id, cand.id, me.id, from_stage=None, to_stage="applied")

    feed = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()["recent_activity"]
    assert len(feed) == 1
    assert feed[0]["from_stage"] is None
    assert feed[0]["to_stage"] == "applied"


def test_activity_feed_only_shows_my_own_actions(client, db, hr_headers):
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    job = _seed_job(db, me.company_id, mine.id)
    cand = _seed_candidate(db, me.company_id, job.id)

    other_user, _ = _seed_hr(db, me.company_id)
    _seed_history(db, me.company_id, cand.id, other_user.id, to_stage="rejected")
    _seed_history(db, me.company_id, cand.id, me.id, to_stage="interview")

    feed = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()["recent_activity"]
    assert len(feed) == 1
    assert feed[0]["to_stage"] == "interview"


def test_activity_feed_is_newest_first_and_capped(client, db, hr_headers):
    me = _hr_user(db)
    mine = _hr_profile(db, me)
    job = _seed_job(db, me.company_id, mine.id)
    cand = _seed_candidate(db, me.company_id, job.id)

    base = datetime.utcnow()
    for i in range(15):
        _seed_history(db, me.company_id, cand.id, me.id,
                      to_stage=f"stage{i}", created_at=base - timedelta(minutes=i))

    feed = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()["recent_activity"]
    assert len(feed) == 10, "feed must be capped"
    assert feed[0]["to_stage"] == "stage0", "newest first"
    assert feed[-1]["to_stage"] == "stage9"


def test_activity_feed_handles_candidate_without_a_job(client, db, hr_headers):
    """Candidates can have job_id NULL; the row must still appear rather than
    being silently dropped by a join."""
    me = _hr_user(db)
    _hr_profile(db, me)
    cand = _seed_candidate(db, me.company_id, None)
    _seed_history(db, me.company_id, cand.id, me.id)

    feed = client.get("/api/v1/hr/my-activity", headers=hr_headers).json()["recent_activity"]
    assert len(feed) == 1
    assert feed[0]["job_title"] is None
    assert feed[0]["candidate_name"] == "Asha Rao"
