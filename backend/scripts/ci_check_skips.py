#!/usr/bin/env python
"""Fail a CI stage if any test was skipped without an approved reason.

A skipped test is a test that did not run, and a green run hides it. This reads the
JUnit XML pytest wrote and fails unless every skipped test is listed in
test/ci_allowed_skips.txt with the reason it cannot run in CI.

Keys are ``<file>::<test name>`` (class names dropped), e.g.
``test/test_cover_letter.py::test_cover_letter_real_generation``. Parametrised
tests match on their base name.

    python scripts/ci_check_skips.py test-results.xml
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ALLOWLIST = Path(__file__).resolve().parent.parent / "test" / "ci_allowed_skips.txt"


def _allowed() -> set:
    keys = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        key = line.split("#", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def _key(case: ET.Element) -> str:
    # classname is "test.test_x" or "test.test_x.TestClass"; keep the module part.
    parts = (case.get("classname") or "").split(".")
    module = "/".join(p for p in parts if not p[:1].isupper()) + ".py"
    name = (case.get("name") or "").split("[", 1)[0]
    return f"{module}::{name}"


def main(path: str) -> int:
    root = ET.parse(path).getroot()
    allowed = _allowed()
    skipped, unexpected = [], []
    for case in root.iter("testcase"):
        skip = case.find("skipped")
        if skip is None:
            continue
        key = _key(case)
        reason = (skip.get("message") or "").strip()
        skipped.append(key)
        if key not in allowed:
            unexpected.append(f"{key}  -  {reason}")

    print(f"{len(skipped)} skipped, {len(skipped) - len(unexpected)} on the allowlist")
    if unexpected:
        print("\nThese tests did not run. Make them run in CI, move them to the stage "
              "that has what they need (a pytest marker), or add them to "
              "test/ci_allowed_skips.txt with the reason:\n")
        for line in unexpected:
            print(f"  {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "test-results.xml"))
