"""
End-to-end judge tests that actually run code in the Docker sandbox.

Auto-skipped unless the Docker daemon is reachable. They also require the
hardened language images to be built first:

    ./scripts/build_judge_images.ps1      # Windows
    ./scripts/build_judge_images.sh       # Linux/mac

Each test verifies one verdict path (AC/WA/TLE/MLE/RE/CE) plus a security probe.
"""
import pytest

from src.judge import verdict as V
from src.judge.runner import judge_submission, TestCaseInput
from src.judge.sandbox import docker_available

pytestmark = pytest.mark.skipif(
    not docker_available(),
    reason="Docker daemon not reachable; start Docker Desktop and build judge images.",
)

# Problem: read two integers from stdin, print their sum.
SUM_CASES = [
    TestCaseInput(id="1", input="2 3\n", expected_output="5", ordinal=0, is_hidden=False),
    TestCaseInput(id="2", input="10 20\n", expected_output="30", ordinal=1, is_hidden=True),
]

PY_CORRECT = "a,b=map(int,input().split())\nprint(a+b)\n"
PY_WRONG = "a,b=map(int,input().split())\nprint(a-b)\n"
PY_TLE = "while True:\n    pass\n"
PY_MLE = "x=[0]*(10**9)\nprint(len(x))\n"
PY_RE = "raise SystemExit(1)\n"


def test_accepted():
    r = judge_submission("python", PY_CORRECT, SUM_CASES, time_limit_ms=2000, memory_limit_mb=256)
    assert r.verdict == V.AC
    assert r.passed_count == 2 and r.total_count == 2


def test_wrong_answer():
    r = judge_submission("python", PY_WRONG, SUM_CASES, time_limit_ms=2000, memory_limit_mb=256)
    assert r.verdict == V.WA


def test_time_limit_exceeded():
    r = judge_submission("python", PY_TLE, SUM_CASES, time_limit_ms=1000, memory_limit_mb=256)
    assert r.verdict == V.TLE


def test_memory_limit_exceeded():
    r = judge_submission("python", PY_MLE, SUM_CASES, time_limit_ms=4000, memory_limit_mb=64)
    assert r.verdict in (V.MLE, V.RE)  # OOM kill; RE if the runtime traps it first


def test_runtime_error():
    r = judge_submission("python", PY_RE, SUM_CASES, time_limit_ms=2000, memory_limit_mb=256)
    assert r.verdict == V.RE


def test_compilation_error_cpp():
    bad_cpp = "int main(){ this is not c++ }\n"
    r = judge_submission("cpp", bad_cpp, SUM_CASES, time_limit_ms=2000, memory_limit_mb=256)
    assert r.verdict == V.CE
    assert r.compile_output


# ── C++ (compile + run) ──────────────────────────────────────────────────────
CPP_CORRECT = "#include <iostream>\nint main(){ long a,b; std::cin>>a>>b; std::cout<<a+b; return 0; }\n"
CPP_WRONG = "#include <iostream>\nint main(){ long a,b; std::cin>>a>>b; std::cout<<a-b; return 0; }\n"


def test_accepted_cpp():
    r = judge_submission("cpp", CPP_CORRECT, SUM_CASES, time_limit_ms=2000, memory_limit_mb=256)
    assert r.verdict == V.AC
    assert r.passed_count == 2 and r.total_count == 2


def test_wrong_answer_cpp():
    r = judge_submission("cpp", CPP_WRONG, SUM_CASES, time_limit_ms=2000, memory_limit_mb=256)
    assert r.verdict == V.WA


# ── JavaScript (Node 20) ─────────────────────────────────────────────────────
JS_CORRECT = (
    "const d = require('fs').readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\n"
    "console.log(d[0] + d[1]);\n"
)
JS_RE = "throw new Error('boom');\n"


def test_accepted_javascript():
    r = judge_submission("javascript", JS_CORRECT, SUM_CASES, time_limit_ms=2000, memory_limit_mb=256)
    assert r.verdict == V.AC
    assert r.passed_count == 2 and r.total_count == 2


def test_runtime_error_javascript():
    r = judge_submission("javascript", JS_RE, SUM_CASES, time_limit_ms=2000, memory_limit_mb=256)
    assert r.verdict == V.RE


# ── Java (JDK 21: compile + run; candidate code defines `public class Main`) ──
JAVA_CORRECT = (
    "import java.util.*;\n"
    "public class Main {\n"
    "  public static void main(String[] args) {\n"
    "    Scanner s = new Scanner(System.in);\n"
    "    long a = s.nextLong(), b = s.nextLong();\n"
    "    System.out.print(a + b);\n"
    "  }\n"
    "}\n"
)
JAVA_WRONG = JAVA_CORRECT.replace("a + b", "a - b")
JAVA_BAD = "public class Main { this is not java }\n"


def test_accepted_java():
    r = judge_submission("java", JAVA_CORRECT, SUM_CASES, time_limit_ms=4000, memory_limit_mb=256)
    assert r.verdict == V.AC
    assert r.passed_count == 2 and r.total_count == 2


def test_wrong_answer_java():
    r = judge_submission("java", JAVA_WRONG, SUM_CASES, time_limit_ms=4000, memory_limit_mb=256)
    assert r.verdict == V.WA


def test_compilation_error_java():
    r = judge_submission("java", JAVA_BAD, SUM_CASES, time_limit_ms=4000, memory_limit_mb=256)
    assert r.verdict == V.CE
    assert r.compile_output


def test_network_is_disabled():
    # Outbound network must fail inside the sandbox (--network none).
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=2); print('NET_OK')\n"
        "except Exception:\n"
        "    print('NET_BLOCKED')\n"
    )
    cases = [TestCaseInput(id="n", input="", expected_output="NET_BLOCKED", ordinal=0, is_hidden=False)]
    r = judge_submission("python", code, cases, time_limit_ms=4000, memory_limit_mb=128)
    assert r.verdict == V.AC, "network should be blocked inside the sandbox"
