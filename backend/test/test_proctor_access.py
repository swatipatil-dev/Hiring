"""
Who may start, drive and read a proctoring session (proctoring/routes.py).

Session start used to take the candidate id and interview id from the request body
with no login, session ids were 8 hex characters, and every per-session endpoint and
WebSocket only checked that the id existed. Anyone could plant sessions and violations
in any company's report, stop a candidate's test, enroll their own face, or read the
logs. These pin the fix: the caller's own interview, a full uuid, the owner checked on
every endpoint, a per-session token on every WebSocket, and no NULL-company rows in
the staff report.
"""
import contextlib
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("cv2", reason="proctoring extras not installed")

import proctoring.routes as routes  # noqa: E402

PASSWORD = "Pass@1234"


def _company(db, name):
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"pa-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c


def _user(db, role, company):
    from src.auth import get_password_hash
    from src.models.models import User
    u = User(id=str(uuid.uuid4()), email=f"{role}_{uuid.uuid4().hex[:8]}@proctoraccess.com",
             password_hash=get_password_hash(PASSWORD), role=role, first_name=role.title(),
             last_name="Tester", is_active=True, email_verified=True, company_id=company.id)
    db.add(u)
    db.flush()
    return u


def _headers(client, user):
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class FakePipeline:
    gaze_flags_off = False
    audio_monitoring_off = False
    cfg = SimpleNamespace(TERMINATE_STRIKES=3, evidence_dir="proctoring_evidence")
    _last_frame_result = None

    def __init__(self):
        self.audio = []

    def ingest_audio(self, data):
        self.audio.append(data)

    def ingest_behavioral_signals(self, payload):
        pass

    def get_summary(self):
        return {"avg_cheating_score": 0.0, "max_cheating_score": 0.0, "frames_processed": 0}

    def shutdown(self):
        pass


@pytest.fixture()
def proctor(db, monkeypatch):
    """Session start without models: the pipeline, the photo registration and the
    accommodations lookup are stubbed, and proctoring's own DB sessions use the test's."""
    import proctoring.warmup as warmup
    monkeypatch.setattr(warmup, "acquire", lambda: FakePipeline())
    monkeypatch.setattr(routes, "_fire_and_forget", lambda *a, **k: None)
    monkeypatch.setattr(routes, "_approved_accommodations_for", lambda _id: {})
    monkeypatch.setattr(routes, "_db_session", contextlib.contextmanager(lambda: (yield db)))
    before = set(routes._sessions)
    yield
    for sid in set(routes._sessions) - before:
        routes._sessions.pop(sid, None)


@pytest.fixture()
def world(db):
    from src.models.models import Candidate, Interview, JobPosting
    a, b = _company(db, "Proctor A"), _company(db, "Proctor B")
    job = JobPosting(id=str(uuid.uuid4()), company_id=a.id, title="Dev", description="d",
                     requirements="r", status="active")
    db.add(job)
    db.flush()
    cand = _user(db, "candidate", a)
    app = Candidate(id=str(uuid.uuid4()), company_id=a.id, user_id=cand.id, job_id=job.id,
                    email=cand.email, first_name="C", last_name="D", current_stage="test_scheduled")
    db.add(app)
    db.flush()
    iv = Interview(company_id=a.id, title="Test", job_id=job.id, candidate_id=app.id,
                   status="SCHEDULED")
    db.add(iv)
    db.flush()
    return SimpleNamespace(a=a, b=b, cand=cand, iv=iv, other_cand=_user(db, "candidate", a),
                           a_hr=_user(db, "hr", a), b_hr=_user(db, "hr", b))


def _start(client, headers, iv_id, **body):
    return client.post("/api/v1/proctor/sessions/start", headers=headers,
                       json={"test_id": iv_id, "interview_id": iv_id, **body})


# ── starting a session ──────────────────────────────────────────────────────

@pytest.mark.security
def test_start_requires_login(client, world, proctor):
    assert _start(client, {}, world.iv.id).status_code in (401, 403)


@pytest.mark.security
def test_start_refuses_someone_elses_interview(client, world, proctor):
    assert _start(client, _headers(client, world.other_cand), world.iv.id).status_code == 404


@pytest.mark.security
def test_start_refuses_staff(client, world, proctor):
    assert _start(client, _headers(client, world.a_hr), world.iv.id).status_code == 403


@pytest.mark.security
def test_start_binds_the_session_to_the_caller(client, world, proctor, db):
    r = _start(client, _headers(client, world.cand), world.iv.id, candidate_id="someone-else")
    assert r.status_code == 200, r.text
    body = r.json()
    sid = body["session_id"]
    assert len(sid) == 36 and uuid.UUID(sid)
    entry = routes._sessions[sid]
    assert entry["owner_user_id"] == world.cand.id
    assert entry["candidate_id"] == world.cand.id, "client-supplied candidate_id ignored"
    assert entry["company_id"] == world.a.id
    token = entry["ws_token"]
    assert body["ws_video_url"].endswith(f"{sid}?token={token}")
    assert body["ws_audio_url"].endswith(f"{sid}?token={token}")


@pytest.mark.security
def test_a_candidate_holds_at_most_one_session(client, world, proctor):
    """Repeated starts replace the session instead of filling every slot (a 503 for
    every other candidate on the box)."""
    h = _headers(client, world.cand)
    sids = [_start(client, h, world.iv.id).json()["session_id"] for _ in range(3)]
    mine = [sid for sid, e in routes._sessions.items() if e["owner_user_id"] == world.cand.id]
    assert mine == [sids[-1]]


# ── per-session endpoints ───────────────────────────────────────────────────

@pytest.fixture()
def live(client, world, proctor):
    h = _headers(client, world.cand)
    r = _start(client, h, world.iv.id)
    assert r.status_code == 200, r.text
    return SimpleNamespace(sid=r.json()["session_id"], headers=h, body=r.json())


@pytest.mark.security
@pytest.mark.parametrize("path,body", [
    ("tab-switch", {"count": 1}),
    ("fullscreen-exit", {"count": 1}),
    ("fullscreen-unavailable", {"reason": "unsupported"}),
    ("behavior-check", {}),
    ("register-face", None),
    ("stop", None),
])
def test_only_the_owner_drives_a_session(client, world, live, path, body):
    url = f"/api/v1/proctor/sessions/{live.sid}/{path}"
    for intruder in (world.other_cand, world.b_hr):
        r = client.post(url, headers=_headers(client, intruder), json=body)
        assert r.status_code in (403, 404), (intruder.role, r.text)
    assert live.sid in routes._sessions, "an intruder's stop must not end the session"
    assert client.post(url, headers=live.headers, json=body).status_code == 200


@pytest.mark.security
def test_status_is_for_the_owner_and_the_companys_staff(client, world, live):
    url = f"/api/v1/proctor/sessions/{live.sid}/status"
    assert client.get(url, headers=live.headers).status_code == 200
    assert client.get(url, headers=_headers(client, world.a_hr)).status_code == 200
    assert client.get(url, headers=_headers(client, world.b_hr)).status_code == 404
    assert client.get(url, headers=_headers(client, world.other_cand)).status_code == 404
    assert client.get(url).status_code in (401, 403)


# ── WebSockets ──────────────────────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.parametrize("stream", ["video", "audio", "behavioral"])
def test_websocket_needs_the_session_token(client, live, stream):
    from starlette.websockets import WebSocketDisconnect
    base = f"/api/v1/proctor/ws/{stream}/{live.sid}"
    for url in (base, base + "?token=wrong"):
        with client.websocket_connect(url) as ws:
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_text()
            assert e.value.code == 4401


@pytest.mark.security
def test_websocket_accepts_the_owner_token(client, live):
    url = live.body["ws_audio_url"]
    with client.websocket_connect(url) as ws:
        ws.send_bytes(b"\x00\x00" * 160)   # audio WS never answers; no close means accepted


# ── staff report ────────────────────────────────────────────────────────────

@pytest.mark.security
def test_report_excludes_other_companies_and_null_company_sessions(client, world, db):
    from src.models.models import ProctoringSession
    db.add_all([
        ProctoringSession(company_id=world.a.id, candidate_id=world.cand.id, test_id=world.iv.id,
                          status="completed"),
        ProctoringSession(company_id=None, candidate_id=world.cand.id, test_id=world.iv.id,
                          status="completed"),
    ])
    db.flush()
    r = client.get(f"/api/v1/proctor/results/{world.iv.id}", headers=_headers(client, world.a_hr))
    assert r.status_code == 200, r.text
    assert r.json()["session_count"] == 1, "the NULL-company session belongs to no tenant"
    r = client.get(f"/api/v1/proctor/results/{world.iv.id}", headers=_headers(client, world.b_hr))
    assert r.status_code == 404


@pytest.mark.security
def test_evidence_of_a_null_company_session_is_not_served(client, world, db):
    from src.models.models import ProctoringSession
    s = ProctoringSession(company_id=None, candidate_id=world.cand.id, test_id=world.iv.id,
                          status="completed")
    db.add(s)
    db.flush()
    r = client.get(f"/api/v1/proctor/evidence/{s.id}/x.jpg", headers=_headers(client, world.a_hr))
    assert r.status_code == 404
