"""Tests for src/judge/starter_sanitizer.sanitize_starter.

The sanitizer must strip leaked solution logic from a coding question's code_template
(what the candidate editor pre-fills) while keeping the I/O scaffold and signatures.
"""

import ast

from src.judge.starter_sanitizer import sanitize_starter, NEUTRAL_STUB, SQL_STUB


# Real leaked template observed in the DB (question fd658b49): the helper body is the
# full max-subarray solution.
LEAKED = """\
def main():
    n = int(input())
    nums = list(map(int, input().split()))
    # TODO: find the maximum sum of a contiguous subarray
    print(max_subarray_sum(nums))

def max_subarray_sum(nums):
    max_so_far = float('-inf')
    current_max = 0
    for i in range(len(nums)):
        current_max += nums[i]
        if current_max > max_so_far:
            max_so_far = current_max
        if current_max < 0:
            current_max = 0
    return max_so_far
"""


def test_leaked_solution_is_stripped():
    out = sanitize_starter(LEAKED)

    # Result is still valid Python.
    ast.parse(out)

    # Signatures and I/O scaffold survive.
    assert "def main():" in out
    assert "def max_subarray_sum(nums):" in out
    assert "input()" in out
    assert "print(max_subarray_sum(nums))" in out

    # The actual algorithm is gone.
    assert "max_so_far" not in out
    assert "for i in range" not in out
    assert "current_max" not in out

    # The blanked helper carries a TODO marker.
    assert "# TODO: implement" in out


def test_helper_body_becomes_todo_pass():
    out = sanitize_starter(LEAKED)
    # The helper is reduced to a comment + pass (valid empty body).
    helper = out.split("def max_subarray_sum(nums):", 1)[1]
    assert "# TODO: implement" in helper
    assert "pass" in helper


def test_clean_stub_is_preserved_unchanged():
    clean = (
        "def is_sorted(nums: list) -> bool:\n"
        "    # Write your solution here\n"
        "    pass\n"
    )
    out = sanitize_starter(clean)
    ast.parse(out)
    assert "def is_sorted(nums: list) -> bool:" in out
    # No solution logic to leak in the first place.
    assert "return False" not in out


def test_inline_solution_in_print_is_dropped():
    # The answer is computed inline inside print() — must not survive.
    leaked = (
        "def main():\n"
        "    s = input()\n"
        "    print(sum(int(char) for char in s if char.isdigit()))\n"
    )
    out = sanitize_starter(leaked)
    ast.parse(out)
    assert "input()" in out          # scaffold kept
    assert "sum(" not in out          # inline solution gone
    assert "isdigit" not in out
    assert "# TODO: implement" in out


def test_print_of_helper_call_is_kept():
    # print(helper(x)) is legitimate scaffold (helper body is blanked elsewhere).
    src = (
        "def main():\n"
        "    nums = list(map(int, input().split()))\n"
        "    print(solve(nums))\n"
        "def solve(nums):\n"
        "    total = 0\n"
        "    for x in nums:\n"
        "        total += x\n"
        "    return total\n"
    )
    out = sanitize_starter(src)
    ast.parse(out)
    assert "print(solve(nums))" in out   # scaffold call preserved
    assert "total += x" not in out        # helper body blanked
    assert "for x in nums" not in out


def test_method_call_solution_is_dropped():
    # strings.sort() is solution logic, not scaffolding.
    src = (
        "def main():\n"
        "    strings = input().split()\n"
        "    strings.sort()\n"
        "    for s in strings:\n"
        "        print(s)\n"
    )
    out = sanitize_starter(src)
    ast.parse(out)
    assert "input().split()" in out
    assert ".sort()" not in out
    assert "for s in strings" not in out


def test_sql_question_returns_sql_stub():
    out = sanitize_starter("SELECT name FROM users WHERE active = 1;", category="SQL")
    assert out == SQL_STUB


def test_sql_by_category_even_with_comment_template():
    out = sanitize_starter("-- write query here\n", category="database")
    assert out == SQL_STUB


def test_syntax_error_falls_back_to_neutral_stub():
    out = sanitize_starter("def main( this is not python !!!")
    assert out == NEUTRAL_STUB


def test_empty_template_returns_neutral_stub():
    assert sanitize_starter("") == NEUTRAL_STUB
    assert sanitize_starter(None) == NEUTRAL_STUB


def test_idempotent_on_sanitized_output():
    once = sanitize_starter(LEAKED)
    twice = sanitize_starter(once)
    # Sanitizing an already-clean stub must not reintroduce or further mangle it.
    assert "max_so_far" not in twice
    ast.parse(twice)
