"""The judge worker only runs the judge's own jobs, and only from JSON.

The worker is root-equivalent (docker group). Stock RQ runs any importable function a
job names and unpickles its arguments, so anyone who could write to Redis could run
commands through it. These pin the two defences: an allow-list of job functions, and
JSON instead of pickle on the queue.

Jobs are built directly and executed through ``Job._execute`` — the step where the
worker calls the job's function, and where SafeJob checks it — so no Redis server is
needed.
"""
import os
import pickle
from pathlib import Path

import pytest
import redis
from rq.serializers import JSONSerializer

from src.judge import queue as jq
from src.judge import rq_safety
from src.judge.rq_safety import ForbiddenJob, SafeJob
from src.judge.runner import CaseResult, JudgeResult, RunOnceResult, TestCaseInput

_CONN = redis.Redis(host="127.0.0.1", port=1)     # never contacted


def _job(func_path, *args, **kwargs):
    return SafeJob.create(func_path, args=args, kwargs=kwargs, connection=_CONN,
                          serializer=JSONSerializer)


def test_a_job_naming_any_other_function_is_refused(monkeypatch):
    ran = []
    monkeypatch.setattr(os, "system", lambda cmd: ran.append(cmd) or 0)
    with pytest.raises(ForbiddenJob):
        _job("os.system", "touch /tmp/owned")._execute()
    assert ran == []


def test_a_judge_job_runs_and_returns_plain_data(monkeypatch):
    import src.judge.runner as runner
    monkeypatch.setattr(runner, "run_once", lambda *a, **k: RunOnceResult(
        status="ok", stdout="3\n", stderr="", exit_code=0, time_ms=5, timed_out=False,
        compile_error=None, error_message=None))
    result = _job("src.judge.worker.run_once_job", "python", "print(3)", "")._execute()
    assert result["stdout"] == "3\n"
    # And it survives the queue as JSON, then comes back as the dataclass.
    wire = JSONSerializer.loads(JSONSerializer.dumps(result))
    assert jq.rebuild_result("src.judge.worker.run_once_job", wire).stdout == "3\n"


def test_jobs_travel_as_json_not_pickle():
    job = _job("src.judge.worker.judge_code_job", "python", "print(1)",
               rq_safety.to_plain([TestCaseInput(id="0", input="1", expected_output="1")]))
    func, instance, args, kwargs = JSONSerializer.loads(job.data)
    assert func == "src.judge.worker.judge_code_job" and instance is None
    assert args[2][0]["expected_output"] == "1"


class _Detonator:
    fired = []

    def __reduce__(self):
        return (_Detonator.fired.append, ("unpickled!",))


def test_a_forged_pickle_payload_is_never_unpickled():
    job = _job("src.judge.worker.run_once_job", "python", "x")
    job.data = pickle.dumps(("src.judge.worker.run_once_job", None, (_Detonator(),), {}))
    with pytest.raises(Exception):
        job._execute()
    assert _Detonator.fired == []


def test_judge_results_round_trip():
    res = JudgeResult(verdict="WA", score=1.0, max_score=2.0, passed_count=1, total_count=2,
                      total_time_ms=9, max_memory_kb=None, compile_output=None, error_message=None,
                      cases=[CaseResult(test_case_id="0", verdict="AC", time_ms=4, memory_kb=None,
                                        exit_code=0, stdout="1", stderr="", ordinal=0,
                                        is_hidden=False)])
    wire = JSONSerializer.loads(JSONSerializer.dumps(rq_safety.to_plain(res)))
    assert jq.rebuild_result("src.judge.worker.judge_sql_job", wire) == res


def test_api_refuses_to_enqueue_unknown_jobs():
    with pytest.raises(ForbiddenJob):
        jq.run_in_worker("subprocess.run", ["id"])


def test_sync_mode_goes_through_the_same_conversion(monkeypatch):
    import src.judge.runner as runner
    seen = {}

    def fake_judge(lang, src, cases, **kw):
        seen["cases"] = cases
        return JudgeResult(verdict="AC", score=1.0, max_score=1.0, passed_count=1, total_count=1,
                           total_time_ms=1, max_memory_kb=None, compile_output=None,
                           error_message=None, cases=[])
    monkeypatch.setattr(runner, "judge_submission", fake_judge)
    monkeypatch.setenv("JUDGE_SYNC", "1")
    out = jq.run_in_worker("src.judge.worker.judge_code_job", "python", "x",
                           [TestCaseInput(id="0", input="", expected_output="")])
    assert isinstance(out, JudgeResult) and out.verdict == "AC"
    assert isinstance(seen["cases"][0], TestCaseInput)


def test_worker_is_started_with_both_defences():
    sh = (Path(__file__).resolve().parents[1] / "entrypoint.sh").read_text(encoding="utf-8")
    worker = sh[sh.index('if [ "$ROLE" = "worker" ]'):sh.index('if [ "$ROLE" = "proctor" ]')]
    assert "--job-class src.judge.rq_safety.SafeJob" in worker
    assert "--serializer rq.serializers.JSONSerializer" in worker
    unset = worker[worker.index("unset "):worker.index('echo "Starting coding-judge')]
    for secret in ("SECRET_KEY", "EMAIL_ENCRYPTION_KEY", "SMTP_PASS", "JUDGE_REGRADE_TOKEN"):
        assert secret in unset
    assert "DATABASE_URL" not in unset and "GEMINI_API_KEY" not in unset


def test_redis_password_detection():
    assert jq.redis_url_has_password("redis://:s3cret@127.0.0.1:6379/0")
    assert not jq.redis_url_has_password("redis://127.0.0.1:6379/0")
