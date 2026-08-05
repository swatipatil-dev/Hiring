"""Sanitize a coding question's stored ``code_template`` into a candidate-facing
starter stub.

The LLM generator is supposed to put only a ``# TODO`` stub in ``code_template`` (the
real solution lives in ``sample_answer``, which is never sent to candidates), but it
inconsistently leaks the full working solution into ``code_template``. Since the
candidate editor pre-fills from ``code_template``, that leaks the answer.

``sanitize_starter`` strips every solution body down to a ``# TODO: implement`` marker
while preserving the I/O scaffold (``input()`` / ``print()`` / ``sys.stdin``), imports,
and function/class signatures.

It is deterministic: any function-body statement that isn't recognizable I/O scaffold
is removed regardless of its content, so a solution cannot survive. Compound statements
(for/while/if/with/try) are always treated as solution logic and dropped.
"""

from __future__ import annotations

import ast
import re
from typing import Optional

from src.judge.sql_testgen import looks_like_sql

# Neutral fallback for non-Python templates / parse failures. Matches the default the
# generator already uses (gemini_service.py / generator_router.py).
NEUTRAL_STUB = (
    "import sys\n\n"
    "def main():\n"
    "    data = sys.stdin.read().split()\n"
    "    # TODO: read input and print the answer\n\n"
    "main()"
)
SQL_STUB = "-- Write your SQL query below\n"

# Placeholder statement we splice in for a dropped (solution) run; rendered as a bare
# string literal by ast.unparse, then rewritten into a comment afterwards.
_MARKER = "__TODO_IMPLEMENT__"
# ast.unparse may render the marker as a normal or (when it's the first body statement)
# triple-quoted string literal — match all four quote styles.
_MARKER_LINE = re.compile(r"^(\s*)('''|\"\"\"|'|\")" + _MARKER + r"\2\s*$", re.M)

_SQL_CATEGORIES = ("sql", "database", "databases")


def _references_io(node: ast.AST) -> bool:
    """True if the statement reads stdin or writes stdout (I/O scaffolding)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in ("input", "print"):
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in ("stdin", "stdout", "stderr"):
            return True
    return False


def _is_string_expr(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


# Builtins that compute an answer; a print() whose argument calls one of these is
# leaking the solution inline (e.g. print(sum(...)), print(sorted(x))).
_COMPUTE_BUILTINS = frozenset({
    "sum", "max", "min", "sorted", "reversed", "len", "any", "all", "map", "filter",
    "abs", "round", "set", "frozenset", "enumerate", "zip", "range", "pow", "divmod",
})


def _is_plain_ref(node: ast.AST) -> bool:
    return isinstance(node, (ast.Name, ast.Constant))


def _is_safe_print_arg(node: ast.AST) -> bool:
    """A print argument is scaffold only if it doesn't compute the answer inline."""
    if _is_plain_ref(node):
        return True
    if isinstance(node, ast.JoinedStr):  # f-string of plain references
        return all(
            isinstance(v, ast.Constant)
            or (isinstance(v, ast.FormattedValue) and _is_plain_ref(v.value))
            for v in node.values
        )
    # A call to a user helper (foo(x)) is fine — the helper body is blanked separately.
    # A call to a computational builtin, or with non-trivial args, is a leak.
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _COMPUTE_BUILTINS:
            return False
        return not node.keywords and all(_is_plain_ref(a) for a in node.args)
    return False


def _is_scaffold_print(call: ast.Call) -> bool:
    return all(_is_safe_print_arg(a) for a in call.args)


def _keep_stmt(stmt: ast.stmt) -> bool:
    """Decide whether a statement is scaffold (keep) or solution logic (drop)."""
    # Structure-defining statements and imports are always scaffold.
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                         ast.Import, ast.ImportFrom)):
        return True
    # Docstrings / string literals.
    if _is_string_expr(stmt):
        return True
    # Compound statements are control flow == solution logic. Always drop, even if they
    # happen to contain a print() nested inside.
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.If, ast.With,
                         ast.AsyncWith, ast.Try)):
        return False
    # Bare call statements.
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
        # print(...): keep only when it isn't computing the answer inline.
        if isinstance(call.func, ast.Name) and call.func.id == "print":
            return _is_scaffold_print(call)
        # Other stdin/stdout I/O calls.
        if _references_io(stmt):
            return True
        # Plain top-level function invocation (main(), solve()) — the helper it calls is
        # blanked separately. Drop method calls like strings.sort()/nums.append(...).
        return isinstance(call.func, ast.Name)
    # Remaining simple statements qualify as scaffold only when they touch stdin/stdout.
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return)):
        return _references_io(stmt)
    return False


def _marker() -> ast.Expr:
    return ast.Expr(value=ast.Constant(value=_MARKER))


class _Blanker(ast.NodeTransformer):
    """Replace solution statements with a TODO marker, keeping scaffold + signatures."""

    def __init__(self) -> None:
        self.modified = False

    def _rebuild(self, body: list, *, force_pass: bool) -> list:
        new_body: list = []
        kept = 0
        prev_dropped = False
        for stmt in body:
            if _keep_stmt(stmt):
                new_body.append(stmt)
                kept += 1
                prev_dropped = False
            else:
                self.modified = True
                if not prev_dropped:
                    new_body.append(_marker())  # one marker per contiguous dropped run
                prev_dropped = True
        # A function body that ends up with only a comment is a syntax error — keep a
        # pass so `# TODO: implement` has a valid body.
        if force_pass and kept == 0:
            new_body.append(ast.Pass())
        return new_body

    def visit_Module(self, node: ast.Module):
        self.generic_visit(node)
        node.body = self._rebuild(node.body, force_pass=False)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.generic_visit(node)
        node.body = self._rebuild(node.body, force_pass=True)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef


def _is_sql_template(code_template: str, category: Optional[str],
                     sample_answer: Optional[str]) -> bool:
    if looks_like_sql(code_template, category):
        return True
    if looks_like_sql(sample_answer, category):
        return True
    return code_template.lstrip().startswith("--")


def sanitize_starter(code_template: Optional[str], *, category: Optional[str] = None,
                     sample_answer: Optional[str] = None) -> str:
    """Return a candidate-safe starter stub for a coding question.

    Preserves the I/O scaffold, imports and signatures; blanks every solution body to
    ``# TODO: implement``. SQL questions get the SQL stub; non-Python templates or parse
    failures fall back to a neutral stub.
    """
    if not code_template or not str(code_template).strip():
        if (looks_like_sql(sample_answer, category)
                or (category or "").strip().lower() in _SQL_CATEGORIES):
            return SQL_STUB
        return NEUTRAL_STUB

    code = str(code_template)

    if _is_sql_template(code, category, sample_answer):
        return SQL_STUB

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return NEUTRAL_STUB

    blanker = _Blanker()
    tree = blanker.visit(tree)
    if not blanker.modified:
        return code  # already a clean stub — preserve original formatting/comments

    ast.fix_missing_locations(tree)
    try:
        out = ast.unparse(tree)
    except Exception:
        return NEUTRAL_STUB

    return _MARKER_LINE.sub(r"\1# TODO: implement", out)
