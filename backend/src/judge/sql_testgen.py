"""
SQL test-case synthesis & validation.

A SQL coding question is gradeable only if it has at least one test case whose
``input`` is a setup script (CREATE TABLE + INSERT) and whose ``expected_output``
is the result of the correct query. LLMs frequently omit the setup data or emit a
wrong ``expected_output``, so we don't trust them: we COMPUTE the expected output
by running the reference solution (the question's ``sample_answer``) against each
seed via the in-process SQLite runner. Output is therefore correct by construction.

Two entry points:
  * ``revalidate_testcases`` — recompute expected_output for existing cases, drop
    any whose setup/reference errors.
  * ``synthesize_testcases``  — when there are no usable cases, ask the LLM for
    seed datasets ONLY (plain SQL, no JSON — far more robust) and build cases.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

import httpx

from . import sql_runner

logger = logging.getLogger("judge.sql_testgen")

_DATASET_SEP = "-- DATASET --"
_SQL_HINT = re.compile(r"\b(SELECT|INSERT|CREATE|WITH|UPDATE|DELETE)\b", re.I)


def looks_like_sql(reference_sql: Optional[str], category: Optional[str] = None) -> bool:
    """Heuristic: is this coding question a SQL-query question?"""
    if category and category.strip().lower() in ("sql", "database", "databases"):
        return True
    r = (reference_sql or "").strip()
    if not r:
        return False
    # Reference begins with a SQL verb and isn't a Python/JS program.
    if not re.match(r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE)\b", r, re.I):
        return False
    return ("def " not in r) and ("import " not in r) and ("console." not in r)


def coerce_reference_sql(raw: Optional[str]) -> Optional[str]:
    """Extract the candidate-facing query from whatever the reference looks like.

    References come in several shapes: a bare ``SELECT``/``WITH``; a self-contained
    script that also CREATEs its own tables (``CREATE TEMPORARY TABLE …; SELECT …``);
    or a Python wrapper with the SQL in a string literal. We want only the final
    SELECT/WITH so it can run against our OWN synthesized seed (otherwise the
    reference's own CREATE collides with the seed → "table already exists").

    Returns the cleaned query, or None when there's no SQL to recover (e.g. a pure
    Python algorithm, or a wrapper that executes the candidate's stdin)."""
    if not raw:
        return None
    s = raw.replace("\\n", "\n").replace("\\t", "\t").replace("\\\"", '"').replace("\\'", "'")
    candidates: List[str] = []
    # 1) SQL inside string literals (Python/JS wrappers) — stops at the closing quote.
    candidates += re.findall(r'"""((?:WITH|SELECT)\b[\s\S]*?)"""', s, re.I)
    candidates += re.findall(r'"((?:WITH|SELECT)\b[\s\S]*?)"', s, re.I)
    candidates += re.findall(r"'((?:WITH|SELECT)\b[\s\S]*?)'", s, re.I)
    # 2) Statements terminated by ';' (handles self-contained CREATE …; SELECT …;).
    candidates += re.findall(r"((?:WITH|SELECT)\b[\s\S]*?);", s, re.I)
    # 3) Last resort: a trailing SELECT/WITH with no terminator.
    if not candidates:
        m = re.search(r"((?:WITH|SELECT)\b[\s\S]*)$", s, re.I)
        if m:
            candidates = [m.group(1)]
    if not candidates:
        return None
    # The full answer query is usually the longest SELECT (subqueries are shorter).
    best = max(candidates, key=len).strip().rstrip(";").strip()
    return best or None


def _gemini_seed_call(prompt: str) -> str:
    """Plain text completion from Gemini (no JSON parsing)."""
    from src.services.gemini_service import _generate_content
    return _generate_content(
        prompt, temperature=0.4, top_p=0.9, max_output_tokens=1500, timeout=180,
    ) or ""


def _strip_non_sql(block: str) -> str:
    """Drop obvious non-SQL lines (Python/JS/prose) the model sometimes mixes in."""
    keep = []
    for ln in block.splitlines():
        t = ln.strip()
        if not t:
            keep.append(ln)
            continue
        if re.match(r"^(def |class |import |from |return|print\(|console\.|#|//|\w+\s*=\s*[^=])", t):
            continue
        keep.append(ln)
    return "\n".join(keep)


def _split_redundant(part: str) -> List[str]:
    """Split a blob that concatenates several datasets (the model dropped the
    separator) at each point it re-creates the FIRST table — otherwise the second
    CREATE fails with "table already exists"."""
    m = re.search(r'CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?(\w+)', part, re.I)
    if not m:
        return [part]
    name = m.group(1)
    pat = re.compile(r'(?=CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?' + re.escape(name) + r'\b)', re.I)
    chunks = [c.strip() for c in pat.split(part) if c.strip()]
    return chunks or [part]


def _extract_datasets(text: str) -> List[str]:
    text = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "")
    out: List[str] = []
    for part in text.split(_DATASET_SEP):
        part = _strip_non_sql(part).strip()
        if not _SQL_HINT.search(part):
            continue
        out.extend(_split_redundant(part))
    return out


def _make_case(setup: str, reference_sql: str, *, is_sample: bool) -> Optional[dict]:
    """Run the reference against ``setup``; build a case with the COMPUTED output.
    Returns None if the setup or reference errors (unusable dataset)."""
    try:
        cols, rows = sql_runner._query_result(setup, reference_sql)
    except Exception as e:
        logger.info("sql dataset rejected: %s", str(e)[:120])
        return None
    return {
        "input": setup,
        "expected_output": sql_runner._format_rows(cols, rows),
        "is_sample": is_sample,
        "weight": 1,
    }


def _promote_nonempty_sample(cases: List[dict]) -> List[dict]:
    """Make the sample a case with a NON-EMPTY expected output when one exists, so
    the candidate never sees a blank 'Expected Output'. Falls back to the first."""
    if not cases:
        return cases
    for c in cases:
        c["is_sample"] = False
    nonempty = next((c for c in cases if (c.get("expected_output") or "").strip()), None)
    (nonempty or cases[0])["is_sample"] = True
    return cases


def revalidate_testcases(reference_sql: str, cases: List[dict]) -> List[dict]:
    """Recompute expected_output for each existing case from the reference query.
    Drops cases whose setup/reference errors. Ensures one non-empty is_sample."""
    out: List[dict] = []
    for c in cases:
        if not isinstance(c, dict):
            continue
        case = _make_case(c.get("input", "") or "", reference_sql, is_sample=False)
        if case:
            case["weight"] = int(c.get("weight", 1) or 1)
            out.append(case)
    return _promote_nonempty_sample(out)


def synthesize_testcases(question_text: str, reference_sql: str, *, max_cases: int = 5) -> List[dict]:
    """Generate seed datasets via the LLM and build validated cases. [] on failure.
    Tries twice: the second attempt nudges the model toward data that yields rows."""
    n_ask = max(3, max_cases)   # ask for at least 3 so dedup/validation has surplus
    base = (
        "You are creating TEST DATA for a SQL exercise.\n\n"
        f"PROBLEM:\n{question_text}\n\n"
        f"REFERENCE QUERY (the correct solution, do NOT repeat it):\n{reference_sql}\n\n"
        "Output ONLY valid SQLite setup scripts that CREATE the table(s) referenced by the "
        f"query and INSERT a handful of realistic rows. Produce {n_ask} INDEPENDENT datasets, "
        "each with DIFFERENT row values, each a complete CREATE TABLE + INSERT script. Separate "
        f"the datasets with a line containing exactly:\n{_DATASET_SEP}\n"
        "Each dataset MUST be chosen so the REFERENCE QUERY returns AT LEAST ONE ROW. "
        "Do not use CREATE TEMPORARY TABLE and do not include the query itself. "
        "No prose, no markdown fences, no comments other than the separator line."
    )
    for attempt in range(2):
        prompt = base if attempt == 0 else base + (
            "\n\nIMPORTANT: your data must make the query return rows — include values "
            "that satisfy every WHERE/HAVING condition in the reference query."
        )
        try:
            raw = _gemini_seed_call(prompt)
        except Exception as e:
            logger.warning("sql seed generation failed: %s", e)
            return []
        out: List[dict] = []
        for setup in _extract_datasets(raw):
            if len(out) >= max_cases:
                break
            case = _make_case(setup, reference_sql, is_sample=False)
            if case:
                out.append(case)
        out = _promote_nonempty_sample(out)
        # Accept if we got a non-empty sample, or on the final attempt take whatever we have.
        if out and ((out[0].get("expected_output") or "").strip() or attempt == 1):
            return out
    return out


def ensure_sql_testcases(question_text: str, reference_sql: str,
                         existing: Optional[List[dict]]) -> List[dict]:
    """Return validated SQL test cases: recompute existing ones, else synthesize.
    Coerces the reference to its candidate-facing SELECT first; returns [] when no
    SQL can be recovered (e.g. a pure-Python reference)."""
    ref = coerce_reference_sql(reference_sql)
    if not ref:
        return []
    cases = [c for c in (existing or []) if isinstance(c, dict)]
    fixed = revalidate_testcases(ref, cases) if cases else []
    # Use recomputed cases only if they yield a non-empty sample; else synthesize fresh.
    if fixed and (fixed[0].get("expected_output") or "").strip():
        return fixed
    synthesized = synthesize_testcases(question_text, ref)
    return synthesized or fixed
