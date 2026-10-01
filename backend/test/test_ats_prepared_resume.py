"""
Tests for the prepared-resume optimisation in EnhancedATSScoringService.

Scoring one resume against N jobs used to redo the resume-only half of the work N
times — sanitise, clean, normalise, detect sections, and a full parse including a
spaCy transformer pass — to produce N identical results. `prepare_resume()` hoists
that out, and `enhanced_score_candidate(prepared=...)` consumes it.

The whole refactor rests on one claim: **the score does not change**. So the first
test compares full results field-for-field rather than eyeballing the diff. The
second pins the actual win (one parse, not N) so the regression cannot creep back.

Run with:
    pytest test/test_ats_prepared_resume.py -v
"""

from unittest.mock import patch

import pytest

from src.services.enhanced_ats_service import (
    EnhancedATSScoringService,
    PreparedResume,
    _EMBEDDING_CACHE,
    _get_text_embedding,
)


RESUME_BACKEND = """
Priya Raman
priya.raman@example.com | +91 98765 43210

SUMMARY
Backend engineer with 4 years building Python services.

SKILLS
Python, FastAPI, PostgreSQL, Docker, Kubernetes, Redis, SQLAlchemy

EXPERIENCE
Senior Backend Engineer, Acme Corp, 2021 - Present
  Built payment APIs handling 2M requests/day with FastAPI and PostgreSQL.
Backend Engineer, Beta Ltd, 2019 - 2021
  Migrated a monolith to containerised services on Kubernetes.

EDUCATION
B.Tech Computer Science, Anna University, 2019, CGPA 8.4

CERTIFICATIONS
AWS Certified Developer

PROJECTS
Open-source rate limiter in Python, 400 GitHub stars.
"""

RESUME_SPARSE = "Ravi Kumar\nJava developer. 2 years.\nSpring Boot, MySQL."

JD_BACKEND = (
    "We are hiring a Senior Backend Engineer. Python, FastAPI and PostgreSQL required. "
    "Experience with Docker and Kubernetes preferred. 4+ years."
)
JD_FRONTEND = (
    "Frontend Engineer wanted. React, TypeScript, CSS. Build accessible interfaces. 3+ years."
)
JD_DATA = "Data Engineer. Python, Airflow, Spark, SQL warehousing. 5 years experience."

CASES = [
    (RESUME_BACKEND, JD_BACKEND, ["python", "fastapi", "postgresql"], 4),
    (RESUME_BACKEND, JD_FRONTEND, ["react", "typescript"], 3),
    (RESUME_BACKEND, JD_DATA, ["python", "airflow", "spark"], 5),
    (RESUME_SPARSE, JD_BACKEND, ["python", "fastapi"], 4),
    (RESUME_SPARSE, JD_FRONTEND, ["react"], 3),
]


@pytest.fixture(scope="module")
def svc():
    return EnhancedATSScoringService()


# ── The claim: preparing changes nothing ──────────────────────────────────────

@pytest.mark.parametrize("resume,jd,skills,exp", CASES)
def test_prepared_scores_identically_to_inline(svc, resume, jd, skills, exp):
    """Full result equality, not just ats_score — a drift in any sub-score is a bug."""
    inline = svc.enhanced_score_candidate(
        resume_text=resume, job_description=jd,
        required_skills=skills, required_experience=exp,
    )
    prepared = svc.enhanced_score_candidate(
        resume_text=resume, job_description=jd,
        required_skills=skills, required_experience=exp,
        prepared=svc.prepare_resume(resume),
    )
    assert prepared.model_dump() == inline.model_dump()


def test_prepared_result_reused_across_jobs_matches_each_inline_score(svc):
    """The real usage: prepare once, score against several jobs."""
    prep = svc.prepare_resume(RESUME_BACKEND)
    for jd, skills, exp in [(JD_BACKEND, ["python", "fastapi"], 4),
                            (JD_FRONTEND, ["react"], 3),
                            (JD_DATA, ["python", "spark"], 5)]:
        inline = svc.enhanced_score_candidate(
            resume_text=RESUME_BACKEND, job_description=jd,
            required_skills=skills, required_experience=exp)
        reused = svc.enhanced_score_candidate(
            resume_text=RESUME_BACKEND, job_description=jd,
            required_skills=skills, required_experience=exp, prepared=prep)
        assert reused.model_dump() == inline.model_dump()


def test_passing_prepared_does_not_mutate_it(svc):
    """A shared PreparedResume is reused across jobs — scoring must not consume it."""
    prep = svc.prepare_resume(RESUME_BACKEND)
    before = (prep.text, dict(prep.sections), prep.parsed.model_dump())
    svc.enhanced_score_candidate(
        resume_text=RESUME_BACKEND, job_description=JD_BACKEND,
        required_skills=["python"], required_experience=4, prepared=prep)
    assert prep.text == before[0]
    assert dict(prep.sections) == before[1]
    assert prep.parsed.model_dump() == before[2]


# ── The win: one parse, not N ─────────────────────────────────────────────────

def test_score_against_jobs_parses_the_resume_once(svc):
    """Without the optimisation this is 5 — one spaCy transformer pass per job."""
    from routes.hr_routes import _score_against_jobs

    class _Job:
        def __init__(self, i):
            self.id, self.title = f"job-{i}", f"Role {i}"

    jobs = [{"job": _Job(i), "jd_text": jd, "skills": ["python"],
             "experience": 3, "department": None}
            for i, jd in enumerate([JD_BACKEND, JD_FRONTEND, JD_DATA, JD_BACKEND, JD_DATA])]

    real = EnhancedATSScoringService.enhanced_parse_resume_from_text
    with patch.object(EnhancedATSScoringService, "enhanced_parse_resume_from_text",
                      autospec=True, side_effect=real) as spy:
        ranked, skills, experience = _score_against_jobs(RESUME_BACKEND, jobs, label="t.pdf")

    assert len(ranked) == 5, "every job should still be scored"
    assert spy.call_count == 1, f"resume parsed {spy.call_count}x for 5 jobs; expected once"


def test_score_against_jobs_still_skips_only_the_failing_job(svc):
    """A bad job must not take the batch down — the per-job guard still applies."""
    from routes.hr_routes import _score_against_jobs

    class _Job:
        def __init__(self, i):
            self.id, self.title = f"job-{i}", f"Role {i}"

    jobs = [{"job": _Job(i), "jd_text": JD_BACKEND, "skills": ["python"],
             "experience": 3, "department": None} for i in range(3)]

    real = EnhancedATSScoringService.enhanced_score_candidate
    calls = {"n": 0}

    def _boom(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("scoring blew up for this job")
        return real(self, *a, **kw)

    with patch.object(EnhancedATSScoringService, "enhanced_score_candidate",
                      autospec=True, side_effect=_boom):
        ranked, _, _ = _score_against_jobs(RESUME_BACKEND, jobs, label="t.pdf")

    assert len(ranked) == 2, "the two healthy jobs should still be ranked"


def test_prepare_failure_returns_empty_triple():
    """Callers already handle ([], [], None); a failed prepare must produce it."""
    from routes.hr_routes import _score_against_jobs

    with patch.object(EnhancedATSScoringService, "prepare_resume",
                      autospec=True, side_effect=RuntimeError("spaCy died")):
        assert _score_against_jobs(RESUME_BACKEND, [{"job": None, "jd_text": JD_BACKEND,
                                                     "skills": [], "experience": 0,
                                                     "department": None}]) == ([], [], None)


# ── prepare_resume itself ─────────────────────────────────────────────────────

def test_prepare_resume_is_deterministic(svc):
    a, b = svc.prepare_resume(RESUME_BACKEND), svc.prepare_resume(RESUME_BACKEND)
    assert a.text == b.text
    assert a.sections == b.sections
    assert a.parsed.model_dump() == b.parsed.model_dump()


def test_prepare_resume_returns_processed_not_raw_text(svc):
    """`text` must be the post-normalisation resume: the scorer keeps using it for
    keyword match, education, experience and JD-overlap, so handing back raw text
    here would silently change scores."""
    raw = "  Priya Raman  \n\n\nﬁnance analyst  "
    prep = svc.prepare_resume(raw)
    assert prep.text != raw
    assert " " not in prep.text          # sanitised
    assert "ﬁ" not in prep.text          # ligature repaired by clean_resume_text


def test_prepare_resume_shape(svc):
    prep = svc.prepare_resume(RESUME_BACKEND)
    assert isinstance(prep, PreparedResume)
    assert isinstance(prep.text, str) and prep.text
    assert isinstance(prep.sections, dict) and prep.sections
    assert prep.parsed.skills, "a resume listing skills should parse some"


def test_prepare_resume_handles_empty_text(svc):
    prep = svc.prepare_resume("")
    assert isinstance(prep, PreparedResume)
    assert prep.parsed.skills == []


# ── Embedding cache is LRU, not FIFO ──────────────────────────────────────────
# These test the eviction policy, not the model, so they use a fake encoder: no
# download, no torch, and they cannot skip. (They used to guard on
# SENTENCE_TRANSFORMERS_AVAILABLE, which is False until the model is lazily loaded,
# so they skipped whenever no earlier test had happened to load it.)

class _FakeEncoder:
    def __init__(self):
        self.encoded = []

    def encode(self, text, convert_to_tensor=False, **kwargs):
        self.encoded.append(text)
        return f"emb:{text}"


@pytest.fixture
def fake_encoder(monkeypatch):
    import src.services.enhanced_ats_service as svc_mod

    encoder = _FakeEncoder()
    monkeypatch.setattr(svc_mod, "_get_semantic_model", lambda: encoder)
    svc_mod._EMBEDDING_CACHE.clear()
    yield encoder
    svc_mod._EMBEDDING_CACHE.clear()


def test_embedding_cache_evicts_least_recently_used(monkeypatch, fake_encoder):
    """The bug this replaces: FIFO eviction dropped the job-description embeddings a
    batch re-reads, because a burst of resume-section inserts aged them out."""
    import src.services.enhanced_ats_service as svc_mod

    monkeypatch.setattr(svc_mod, "_EMBEDDING_CACHE_MAXSIZE", 4)
    _get_text_embedding("hot")                       # the entry we keep re-reading
    for i in range(3):
        _get_text_embedding(f"filler-{i}")
        _get_text_embedding("hot")                   # touch it, keeping it fresh
    _get_text_embedding("overflow")                  # forces an eviction

    import hashlib
    hot = hashlib.sha256("hot".encode()).hexdigest()
    assert hot in svc_mod._EMBEDDING_CACHE, "recently-used entry was evicted (FIFO?)"
    assert len(svc_mod._EMBEDDING_CACHE) <= 4
    assert fake_encoder.encoded.count("hot") == 1, "hot entry was re-encoded"
    # filler-0 was the least recently used when "overflow" arrived.
    assert hashlib.sha256(b"filler-0").hexdigest() not in svc_mod._EMBEDDING_CACHE


def test_embedding_cache_respects_maxsize(monkeypatch, fake_encoder):
    import src.services.enhanced_ats_service as svc_mod

    monkeypatch.setattr(svc_mod, "_EMBEDDING_CACHE_MAXSIZE", 3)
    for i in range(10):
        _get_text_embedding(f"text-{i}")
    assert len(svc_mod._EMBEDDING_CACHE) == 3
