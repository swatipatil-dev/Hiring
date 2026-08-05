"""
On-demand judge VM control (cost optimization).

The Judge0 sandbox runs on a GCE VM that we DON'T keep warm 24/7. Instead:

  * ``ensure_judge_running()`` boots the VM the moment a candidate starts a test
    that contains a coding question (called from the ``/judge/warmup`` endpoint).
    Because the candidate works through the MCQ section first, the ~1 min boot is
    hidden — by the time they reach the coding question the judge is ready.
  * ``touch_activity()`` writes a heartbeat (``JudgeRuntimeState.last_active_at``)
    on every warmup / grade so *any* active assessment keeps the VM alive.
  * ``reap_if_idle()`` (called periodically by Cloud Scheduler → ``/judge/reap``)
    stops the VM once the heartbeat is older than ``JUDGE_IDLE_STOP_MIN`` minutes.
    This is the safety net: an abandoned/crashed test simply lets the heartbeat go
    stale, so the VM can't run forever and quietly burn money.

VM start/stop uses the GCE REST API authenticated with the Cloud Run service
account's token from the metadata server — no extra Python dependency. EVERY call
here is best-effort and non-fatal: a failure to start the VM must never break the
test flow (the existing "saved — graded later" degradation covers a slow/failed
boot, and the deferred re-grade fills in the scores afterwards).

Disabled unless ``JUDGE_AUTOSTART=1`` (local dev / non-GCP deployments are no-ops).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger("judge.provisioner")

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
_COMPUTE = "https://compute.googleapis.com/compute/v1"

_token: Optional[str] = None
_token_exp: float = 0.0


def autostart_enabled() -> bool:
    return os.getenv("JUDGE_AUTOSTART", "0").strip().lower() in ("1", "true", "yes")


def _cfg():
    return (os.getenv("JUDGE_GCP_PROJECT", "").strip(),
            os.getenv("JUDGE_GCP_ZONE", "").strip(),
            os.getenv("JUDGE_VM_NAME", "").strip())


def _configured() -> bool:
    return all(_cfg())


def _access_token() -> Optional[str]:
    """Fetch (and briefly cache) the service-account token from the metadata server."""
    global _token, _token_exp
    if _token and time.monotonic() < _token_exp:
        return _token
    try:
        r = requests.get(_METADATA_TOKEN_URL,
                         headers={"Metadata-Flavor": "Google"}, timeout=3)
        r.raise_for_status()
        data = r.json()
        _token = data.get("access_token")
        _token_exp = time.monotonic() + max(0, int(data.get("expires_in", 0)) - 60)
        return _token
    except Exception as exc:
        logger.warning("provisioner: could not get metadata token: %s", exc)
        return None


def _instance_url(verb: str = "") -> Optional[str]:
    project, zone, name = _cfg()
    if not (project and zone and name):
        return None
    url = f"{_COMPUTE}/projects/{project}/zones/{zone}/instances/{name}"
    return f"{url}/{verb}" if verb else url


def instance_status() -> Optional[str]:
    """GCE instance status (RUNNING / TERMINATED / STOPPING / …) or None on error."""
    url = _instance_url()
    token = _access_token()
    if not url or not token:
        return None
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        if r.status_code != 200:
            logger.warning("provisioner: instance.get %s: %s", r.status_code, r.text[:200])
            return None
        return r.json().get("status")
    except Exception as exc:
        logger.warning("provisioner: instance.get failed: %s", exc)
        return None


def _power(verb: str) -> bool:
    """POST start/stop to the instance. Returns True if the request was accepted."""
    url = _instance_url(verb)
    token = _access_token()
    if not url or not token:
        return False
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        ok = r.status_code in (200, 204)
        if not ok:
            logger.warning("provisioner: instance.%s %s: %s", verb, r.status_code, r.text[:200])
        return ok
    except Exception as exc:
        logger.warning("provisioner: instance.%s failed: %s", verb, exc)
        return False


def ensure_judge_running() -> dict:
    """Start the judge VM if it isn't already running. Best-effort, never raises."""
    if not autostart_enabled():
        return {"autostart": False}
    if not _configured():
        logger.warning("provisioner: JUDGE_AUTOSTART set but project/zone/vm not configured")
        return {"autostart": True, "configured": False}
    status = instance_status()
    if status == "RUNNING":
        return {"autostart": True, "status": "RUNNING", "action": "none"}
    started = _power("start")
    return {"autostart": True, "status": status, "action": "start" if started else "start_failed"}


# ── heartbeat / reaper state (single row in judge_runtime_state) ──

def _state(db):
    from src.models.judge_models import JudgeRuntimeState
    row = db.query(JudgeRuntimeState).filter(JudgeRuntimeState.name == "judge_vm").first()
    if not row:
        row = JudgeRuntimeState(name="judge_vm", desired_running=False)
        db.add(row)
        db.flush()
    return row


def touch_activity(db) -> None:
    """Record assessment activity so the reaper keeps the VM warm. Non-fatal."""
    try:
        row = _state(db)
        row.last_active_at = datetime.utcnow()
        row.desired_running = True
        db.commit()
    except Exception as exc:
        logger.warning("provisioner: touch_activity failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


def reap_if_idle(db) -> dict:
    """Stop the VM if no assessment activity within the idle window. Non-fatal."""
    idle_min = int(os.getenv("JUDGE_IDLE_STOP_MIN", "30"))
    out = {"autostart": autostart_enabled(), "action": "none", "idle_min": idle_min}
    if not autostart_enabled() or not _configured():
        return out
    try:
        row = _state(db)
        last = row.last_active_at
        out["last_active_at"] = last.isoformat() if last else None
        if last and (datetime.utcnow() - last) < timedelta(minutes=idle_min):
            out["action"] = "kept_warm"
            return out
        # idle (or never active) → stop if it's up
        status = instance_status()
        out["status"] = status
        if status in ("RUNNING", "STAGING", "PROVISIONING"):
            if _power("stop"):
                row.desired_running = False
                row.last_stopped_at = datetime.utcnow()
                db.commit()
                out["action"] = "stopped"
            else:
                out["action"] = "stop_failed"
        else:
            row.desired_running = False
            db.commit()
            out["action"] = "already_stopped"
    except Exception as exc:
        logger.warning("provisioner: reap_if_idle failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        out["action"] = "error"
    return out
