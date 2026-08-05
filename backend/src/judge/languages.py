"""
Supported-language registry for the judge.

Each language maps to a Docker image and the commands used to compile (optional)
and run a submission. Source is written to ``/work/<source_file>`` inside the
container; compiled artifacts also land in ``/work``.

Image names are overridable via env (e.g. ``JUDGE_IMAGE_PYTHON``) so production
can point at the hardened, pre-built images in ``judge/images/`` while local dev
can fall back to official base images. Phase 1 ships Python, C++ and JavaScript;
Java and Go are wired but commented for Phase 2.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Language:
    key: str                       # stable identifier used by the API / DB
    label: str                     # human label for the UI
    image: str                     # docker image
    source_file: str               # filename written into /work
    run_cmd: List[str]             # argv to execute the program
    compile_cmd: Optional[List[str]] = None   # argv to compile (None = interpreted)
    compile_time_limit_ms: int = 10_000       # wall-clock budget for compilation
    monaco_lang: str = "plaintext"            # Monaco editor syntax id
    harness: Optional[str] = None             # non-stdin/stdout grader run in-sandbox (e.g. "sql" → sql_harness.py)


def _img(env_key: str, default: str) -> str:
    return os.getenv(env_key, default)


# NOTE: defaults point at the hardened images built from judge/images/* (see the
# Dockerfiles). They are tagged hireflow-judge-<lang>:latest. If you haven't built
# them, override with official images, e.g. JUDGE_IMAGE_PYTHON=python:3.11-slim.
_LANGUAGES = {
    "python": Language(
        key="python",
        label="Python 3.11",
        image=_img("JUDGE_IMAGE_PYTHON", "hireflow-judge-python:latest"),
        source_file="main.py",
        run_cmd=["python3", "main.py"],
        compile_cmd=None,
        monaco_lang="python",
    ),
    "cpp": Language(
        key="cpp",
        label="C++ 17 (g++)",
        image=_img("JUDGE_IMAGE_CPP", "hireflow-judge-cpp:latest"),
        source_file="main.cpp",
        # -O2 optimized, static where possible; output binary at /work/main
        compile_cmd=["g++", "-O2", "-std=c++17", "-o", "main", "main.cpp"],
        run_cmd=["./main"],
        monaco_lang="cpp",
    ),
    "javascript": Language(
        key="javascript",
        label="JavaScript (Node 20)",
        image=_img("JUDGE_IMAGE_NODE", "hireflow-judge-node:latest"),
        source_file="main.js",
        run_cmd=["node", "main.js"],
        compile_cmd=None,
        monaco_lang="javascript",
    ),
    "sql": Language(
        key="sql",
        label="SQL (SQLite)",
        # Graded by sql_harness.py INSIDE the Python sandbox (CPython ships SQLite),
        # so SQL gets the same CPU/memory/time isolation as the other languages.
        image=_img("JUDGE_IMAGE_PYTHON", "hireflow-judge-python:latest"),
        source_file="query.sql",
        run_cmd=[],
        compile_cmd=None,
        monaco_lang="sql",
        harness="sql",
    ),
    "java": Language(
        key="java",
        label="Java 21",
        image=_img("JUDGE_IMAGE_JAVA", "hireflow-judge-java:latest"),
        source_file="Main.java",
        compile_cmd=["javac", "Main.java"],
        # Memory/startup-friendly flags so the JVM fits the per-case cap (≈256 MB)
        # without a false MLE: serial GC, capped heap, no perfdata files in /tmp.
        run_cmd=["java", "-XX:-UsePerfData", "-XX:+UseSerialGC", "-Xss64m", "-Xmx200m", "Main"],
        compile_time_limit_ms=15_000,
        monaco_lang="java",
    ),
    # ── Phase 2 ──────────────────────────────────────────────────────────
    # "go": Language(
    #     key="go", label="Go 1.22",
    #     image=_img("JUDGE_IMAGE_GO", "hireflow-judge-go:latest"),
    #     source_file="main.go",
    #     compile_cmd=["go", "build", "-o", "main", "main.go"], run_cmd=["./main"],
    #     monaco_lang="go"),
}


def supported_languages() -> List[str]:
    return list(_LANGUAGES.keys())


def get_language(key: str) -> Language:
    try:
        return _LANGUAGES[key]
    except KeyError:
        raise ValueError(
            f"Unsupported language '{key}'. Supported: {', '.join(_LANGUAGES)}"
        )


def language_catalog() -> list[dict]:
    """Serializable list for the frontend language picker."""
    return [
        {"key": l.key, "label": l.label, "monaco": l.monaco_lang}
        for l in _LANGUAGES.values()
    ]
