"""
Hardened Docker sandbox: run ONE command inside a throwaway, isolated container.

Security model (defense in depth) — every execution gets:
  --network none              no internet / no localhost access
  --read-only                 root filesystem is immutable
  --tmpfs /tmp                only /tmp is writable (size-capped, the workdir is RO at run time)
  --memory / --memory-swap    hard memory cap, swap disabled (swap==mem) → OOM kill on excess
  --cpus                      CPU quota
  --pids-limit                blocks fork bombs
  --cap-drop ALL              no Linux capabilities
  --security-opt no-new-privileges   no setuid escalation
  --user 65534:65534          run as `nobody`, never root
  ulimits (nofile, fsize)     bound open files and file size
  unique --name + kill        wall-clock timeout enforced by the host; container force-killed

On Linux/production also set JUDGE_RUNTIME=runsc (gVisor) for kernel-level isolation
and JUDGE_SECCOMP=/path/to/profile.json. These are no-ops if unset.

This module shells out to the `docker` CLI (no host bind of the Docker SDK needed).
It deliberately does NOT mount the application code or any host path except the
per-submission work directory.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


class SandboxUnavailable(RuntimeError):
    """Raised when the Docker daemon is not reachable."""


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    wall_ms: int
    timed_out: bool
    oom_killed: bool


_DOCKER = os.getenv("JUDGE_DOCKER_BIN", "docker")
_OUTPUT_CAP = int(os.getenv("JUDGE_OUTPUT_CAP_BYTES", str(256 * 1024)))  # 256 KB
# Daemon-reachability probe. A POSITIVE result is cached for _DOCKER_TTL seconds
# (cheap to serve); a NEGATIVE result is re-probed on every call so the judge
# SELF-HEALS the moment Docker Desktop is (re)started — no app restart needed.
# (Previously a single permanent cache latched "unavailable" until restart.)
_docker_ok: Optional[bool] = None
_docker_checked_at: float = 0.0
_DOCKER_TTL = float(os.getenv("JUDGE_DOCKER_TTL_S", "10"))


def _probe_docker() -> bool:
    if shutil.which(_DOCKER) is None:
        return False
    try:
        r = subprocess.run(
            [_DOCKER, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def docker_available() -> bool:
    """Best-effort check that the Docker daemon is reachable.

    Positive results are cached for ``_DOCKER_TTL`` seconds; negative results are
    re-probed every call, so the judge recovers automatically once Docker starts.
    """
    global _docker_ok, _docker_checked_at
    now = time.monotonic()
    if _docker_ok is True and (now - _docker_checked_at) < _DOCKER_TTL:
        return True
    _docker_ok = _probe_docker()
    _docker_checked_at = now
    return _docker_ok


def _mount_source(path: str) -> str:
    """Render a host path as a Docker-friendly bind source (Windows-safe)."""
    p = os.path.abspath(path)
    # Docker Desktop accepts forward-slash Windows paths (C:/Users/...).
    return p.replace("\\", "/")


def run_in_sandbox(
    image: str,
    argv: List[str],
    *,
    workdir: str,
    stdin: str = "",
    time_limit_ms: int = 2000,
    memory_mb: int = 256,
    cpus: float = 1.0,
    writable: bool = False,
    pids_limit: int = 64,
) -> SandboxResult:
    """Execute ``argv`` in a hardened container mounting ``workdir`` at /work.

    ``writable=True`` mounts /work read-write (used for the compile step so the
    compiler can emit a binary); the run step mounts it read-only.
    Wall-clock timeout = time_limit_ms + a fixed margin for container startup.
    """
    if not docker_available():
        raise SandboxUnavailable(
            "Docker daemon is not reachable. Start Docker Desktop / dockerd, "
            "or set JUDGE_SYNC=0 and run the worker where Docker is available."
        )

    name = f"judge_{uuid.uuid4().hex[:16]}"
    mount = _mount_source(workdir)
    ro = "" if writable else ":ro"

    cmd: List[str] = [
        _DOCKER, "run", "--rm", "-i",
        "--name", name,
        # Deterministic execution: Python randomizes its hash seed per process, so
        # set/dict iteration order changes every container run. With exact-string
        # output matching that made identical code flip AC↔WA across re-runs. Pin
        # the seed so output is stable (no-op for non-Python images). PYTHONUNBUFFERED
        # keeps stdout flushed. See plans/why-im-getting-test-* for the diagnosis.
        "-e", "PYTHONHASHSEED=0",
        "-e", "PYTHONUNBUFFERED=1",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,exec,size=64m",
        "--memory", f"{memory_mb}m",
        "--memory-swap", f"{memory_mb}m",   # swap == mem ⇒ no swap
        "--cpus", str(cpus),
        "--pids-limit", str(pids_limit),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--ulimit", "nofile=256:256",
        "--ulimit", "fsize=33554432:33554432",  # 32 MB max file size
        "-v", f"{mount}:/work{ro}",
        "-w", "/work",
    ]

    runtime = os.getenv("JUDGE_RUNTIME")            # e.g. "runsc" (gVisor) on Linux
    if runtime:
        cmd += ["--runtime", runtime]
    seccomp = os.getenv("JUDGE_SECCOMP")            # path to a seccomp profile
    if seccomp:
        cmd += ["--security-opt", f"seccomp={seccomp}"]

    cmd += [image, *argv]

    # Host-enforced wall clock. Margin covers container create/start overhead.
    margin_s = float(os.getenv("JUDGE_TIMEOUT_MARGIN_S", "5"))
    timeout_s = (time_limit_ms / 1000.0) + margin_s

    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            input=stdin.encode("utf-8", "replace"),
            capture_output=True,
            timeout=timeout_s,
        )
        exit_code = proc.returncode
        stdout = proc.stdout[:_OUTPUT_CAP].decode("utf-8", "replace")
        stderr = proc.stderr[:_OUTPUT_CAP].decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        timed_out = True
        exit_code = 124
        stdout = (e.stdout or b"")[:_OUTPUT_CAP].decode("utf-8", "replace")
        stderr = (e.stderr or b"")[:_OUTPUT_CAP].decode("utf-8", "replace")
        # The docker client was killed by the timeout, but the container may live on.
        _force_kill(name)

    wall_ms = int((time.monotonic() - start) * 1000)

    # OOM heuristic: exit 137 (128+SIGKILL) without a host timeout almost always
    # means the cgroup memory cap killed it; corroborate with common signatures.
    oom = (not timed_out) and (
        exit_code == 137
        or "MemoryError" in stderr
        or "bad_alloc" in stderr
        or "Killed" in stderr
    )

    return SandboxResult(
        stdout=stdout, stderr=stderr, exit_code=exit_code,
        wall_ms=wall_ms, timed_out=timed_out, oom_killed=oom,
    )


def _force_kill(name: str) -> None:
    """Best-effort kill+remove of a container that outlived its timeout."""
    for args in (["kill", name], ["rm", "-f", name]):
        try:
            subprocess.run([_DOCKER, *args], capture_output=True, timeout=10)
        except Exception:
            pass
