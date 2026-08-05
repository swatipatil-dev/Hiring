"""
Ensure a coding question has enough HIDDEN test cases.

A coding question is robust against hard-coding only if it carries several
*hidden* cases (``is_sample: false``) in addition to the 1-2 visible samples.
The generator and a one-off backfill both call :func:`ensure_hidden_testcases`,
which — given the question's *runnable* reference solution (``sample_answer``) —
tops the case list up to ``min_hidden`` hidden cases.

Trust model (shared with :mod:`src.judge.sql_testgen`): the LLM is asked only for
INPUTS; every ``expected_output`` is COMPUTED by running the reference solution,
so cases are correct by construction.

  * SQL questions reuse ``sql_testgen`` (expected output computed in-process via
    SQLite — no sandbox needed).
  * stdin/stdout questions ask Ollama for new stdin payloads, then compute the
    expected output by running the reference in the active sandbox (Docker
    locally, or the remote Judge0 backend).

Never raises: any failure degrades to returning the existing cases unchanged
(or ``None`` when the question has no runnable reference at all).
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from . import sql_testgen as _sql
from . import verdict as _V

logger = logging.getLogger("judge.hidden_testgen")

# Sentinel the model uses to separate the independent stdin payloads it returns.
_INPUT_SEP = "-- INPUT --"


# ──────────────────────────── shared helpers ────────────────────────────

def _norm(s: str) -> str:
    """Whitespace-insensitive key for de-duplicating inputs / setup scripts."""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _hidden_count(cases: List[dict]) -> int:
    return sum(1 for c in cases if isinstance(c, dict) and not c.get("is_sample"))


def _looks_runnable_program(sample_answer: str) -> bool:
    """Heuristic: does ``sample_answer`` look like a real stdin/stdout program
    (Python/JS/C++) rather than open-ended prose? Used to skip non-coding
    "coding" questions whose reference is just a written model answer."""
    s = sample_answer or ""
    if not s.strip():
        return False
    markers = ("input(", "sys.stdin", "readline", "readFileSync", "Scanner",
               "cin", "console.", "print(", "println", "def ", "import ",
               "#include", "function ", "=>")
    return any(m in s for m in markers)


def _guess_language(sample_answer: str) -> str:
    s = sample_answer or ""
    if "#include" in s or "std::" in s or "cout" in s:
        return "cpp"
    if "console." in s or "readFileSync" in s or "function " in s or "=>" in s:
        return "javascript"
    return "python"


def _canonical(case: dict) -> dict:
    """Normalize a case to the stored shape used everywhere else."""
    return {
        "input": case.get("input", "") or "",
        "expected_output": case.get("expected_output", "") or "",
        "is_sample": bool(case.get("is_sample")),
        "weight": int(case.get("weight", 1) or 1),
    }


# ──────────────────────────── SQL branch ────────────────────────────

def _ensure_sql_hidden(question_text: str, sample_answer: str,
                       existing: List[dict], min_hidden: int) -> List[dict]:
    """Validate/recompute SQL cases and top up to ``min_hidden`` hidden ones.
    Falls back to whatever it has on any failure. Always returns >=0 cases."""
    base = _sql.ensure_sql_testcases(question_text, sample_answer, existing) or []
    ref = _sql.coerce_reference_sql(sample_answer)
    if not ref:
        return [_canonical(c) for c in base]

    seen = {_norm(c.get("input", "")) for c in base}
    attempts = 0
    while _hidden_count(base) < min_hidden and attempts < 2:
        attempts += 1
        try:
            extra = _sql.synthesize_testcases(question_text, ref, max_cases=min_hidden + 2)
        except Exception as e:  # never raise out of a top-up
            logger.info("sql hidden top-up failed: %s", str(e)[:120])
            break
        added = False
        for c in extra:
            k = _norm(c.get("input", ""))
            if k in seen:
                continue
            seen.add(k)
            base.append(c)
            added = True
        if not added:
            break

    # Exactly one (non-empty) sample, the rest hidden.
    base = _sql._promote_nonempty_sample(base)
    return [_canonical(c) for c in base]


# ──────────────────────────── stdin/stdout branch ────────────────────────────

def _gen_inputs(question_text: str, sample_answer: str, lang: str,
                existing: List[dict], n: int, *, nudge: bool = False) -> List[str]:
    """Ask Ollama for ``n`` new stdin payloads (INPUTS ONLY)."""
    fmt_examples = "\n".join(
        f"{_INPUT_SEP}\n{(c.get('input') or '').strip()}"
        for c in existing[:2] if (c.get("input") or "").strip()
    )
    prompt = (
        "You are creating TEST INPUTS for an automated coding judge.\n\n"
        f"PROBLEM:\n{question_text}\n\n"
        f"Produce {n} NEW, DISTINCT stdin inputs for this problem. Output ONLY the raw "
        "input each program would read on standard input — NO expected output, NO "
        "explanation, NO code, NO markdown. Cover edge cases (empty/min/max, negatives, "
        "duplicates, larger sizes) and match the EXACT input format the problem describes.\n"
        f"Separate each input with a line containing exactly:\n{_INPUT_SEP}\n"
    )
    if fmt_examples:
        prompt += f"\nThe input format looks like this (match it exactly):\n{fmt_examples}\n"
    if nudge:
        prompt += ("\nIMPORTANT: make the inputs genuinely different from each other and "
                   "valid for the format — they must be parseable by a correct solution.\n")
    try:
        raw = _sql._ollama_seed_call(prompt)
    except Exception as e:
        logger.warning("stdin input generation failed: %s", e)
        return []
    raw = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "")
    parts = [p.strip("\n") for p in raw.split(_INPUT_SEP)]
    return [p for p in (p.strip("\n") for p in parts) if p.strip()]


def _ensure_stdin_hidden(question_text: str, sample_answer: str,
                         existing: List[dict], min_hidden: int,
                         max_total: int) -> Optional[List[dict]]:
    """Generate inputs, compute their expected output by running the reference in
    the sandbox, AC-gate the assembled suite, and top up to ``min_hidden`` hidden.
    Returns None only when the sandbox is unavailable (cannot compute outputs)."""
    from .runner import backend_available, run_once, judge_submission, TestCaseInput

    if not backend_available():
        return None  # caller keeps existing cases unchanged

    lang = _guess_language(sample_answer)
    existing = [_canonical(c) for c in existing if isinstance(c, dict)]
    seen = {_norm(c["input"]) for c in existing}

    new_hidden: List[dict] = []
    need = min_hidden - _hidden_count(existing)
    attempts = 0
    while len(new_hidden) < max(need, 0) and attempts < 2:
        want = (min_hidden + 2) - len(new_hidden)
        inputs = _gen_inputs(question_text, sample_answer, lang, existing,
                             want, nudge=(attempts > 0))
        attempts += 1
        if not inputs:
            break
        for inp in inputs:
            if len(new_hidden) >= max(need, 0):
                break
            k = _norm(inp)
            if k in seen:
                continue
            r = run_once(lang, sample_answer, stdin=inp, time_limit_ms=4000)
            if r.status != "ok" or r.exit_code != 0:
                continue
            seen.add(k)
            new_hidden.append({
                "input": inp,
                "expected_output": _V.normalize_output(r.stdout),
                "is_sample": False,
                "weight": 1,
            })

    merged = existing + new_hidden
    if not merged:
        return existing  # nothing to add; leave as-is

    # AC-gate: drop any case the reference doesn't reproduce (nondeterminism etc.).
    tcs = [TestCaseInput(id=str(i), input=c["input"],
                         expected_output=c["expected_output"],
                         is_sample=c["is_sample"], ordinal=i)
           for i, c in enumerate(merged)]
    try:
        res = judge_submission(lang, sample_answer, tcs, time_limit_ms=4000)
        kept = [merged[int(cr.test_case_id)] for cr in res.cases if cr.verdict == _V.AC]
        if kept:
            merged = kept
    except Exception as e:
        logger.info("stdin AC-gate skipped: %s", str(e)[:120])

    return _finalize_samples(merged, max_total)


def _finalize_samples(cases: List[dict], max_total: int) -> List[dict]:
    """Keep 1-2 samples (promote the first case if none are flagged), force the
    rest hidden, and cap the total — never dropping below all available hidden."""
    cases = [_canonical(c) for c in cases]
    samples = [c for c in cases if c["is_sample"]]
    hidden = [c for c in cases if not c["is_sample"]]
    if not samples and cases:
        cases[0]["is_sample"] = True
        samples = [cases[0]]
        hidden = cases[1:]
    samples = samples[:2]
    room = max(0, max_total - len(samples))
    return samples + hidden[:room]


# ──────────────────────────── dispatcher ────────────────────────────

def ensure_hidden_testcases(question_text: str, sample_answer: str,
                            category: Optional[str], existing: Optional[List[dict]],
                            *, min_hidden: int = 3, max_total: int = 6) -> Optional[List[dict]]:
    """Return a validated case list with at least ``min_hidden`` hidden cases when
    possible, or ``None`` when the question has no runnable reference (open-ended).

    SQL questions are detected via :func:`sql_testgen.looks_like_sql` and never need
    a sandbox. stdin/stdout questions need the active execution backend (Docker or
    Judge0) to compute outputs; if none is available the existing cases are returned
    unchanged rather than destroyed.
    """
    existing = [c for c in (existing or []) if isinstance(c, dict)]

    if _sql.looks_like_sql(sample_answer, category):
        try:
            return _ensure_sql_hidden(question_text, sample_answer, existing, min_hidden)
        except Exception as e:
            logger.warning("sql hidden generation failed: %s", e)
            return [_canonical(c) for c in existing] if existing else None

    if not _looks_runnable_program(sample_answer):
        return None  # open-ended / not auto-gradable

    try:
        out = _ensure_stdin_hidden(question_text, sample_answer, existing, min_hidden, max_total)
    except Exception as e:
        logger.warning("stdin hidden generation failed: %s", e)
        return [_canonical(c) for c in existing] if existing else None
    if out is None:  # sandbox unavailable
        return [_canonical(c) for c in existing] if existing else None
    return out
