"""
Verdict vocabulary + output comparison for the judge.

Verdicts:
  AC  Accepted             — output matches expected
  WA  Wrong Answer         — ran cleanly, output differs
  TLE Time Limit Exceeded  — wall clock exceeded the problem limit
  MLE Memory Limit Exceeded— container OOM-killed / exceeded memory cap
  RE  Runtime Error        — non-zero exit / killed by signal (not OOM/timeout)
  CE  Compilation Error    — compiler returned non-zero
"""
from __future__ import annotations

AC = "AC"
WA = "WA"
TLE = "TLE"
MLE = "MLE"
RE = "RE"
CE = "CE"
PENDING = "PENDING"

# Ordering used to pick the overall verdict from per-case verdicts:
# the "worst" non-AC verdict wins; CE/MLE/TLE/RE rank above WA so the candidate
# sees the most informative failure first.
_SEVERITY = {AC: 0, WA: 1, RE: 2, TLE: 3, MLE: 4, CE: 5}


def normalize_output(text: str) -> str:
    """Canonicalize program output for comparison.

    Mirrors what most online judges do for "standard" checking:
      * normalize line endings,
      * strip trailing whitespace on each line,
      * drop trailing blank lines / surrounding whitespace.
    This makes a missing/extra final newline or trailing spaces NOT cause a WA,
    while still catching genuinely different output.
    """
    if text is None:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    # remove trailing empty lines
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def outputs_match(actual: str, expected: str) -> bool:
    return normalize_output(actual) == normalize_output(expected)


def aggregate_verdict(case_verdicts: list[str]) -> str:
    """Reduce per-test-case verdicts to one overall verdict.

    AC only if every case is AC; otherwise the highest-severity failure.
    """
    if not case_verdicts:
        return PENDING
    worst = AC
    for v in case_verdicts:
        if _SEVERITY.get(v, 1) > _SEVERITY.get(worst, 0):
            worst = v
    return worst
