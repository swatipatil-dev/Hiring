"""The Docker CLI's stderr must not be presented as the candidate's.

`docker run` streams the container's stderr onto its own, so both arrive in one
pipe. Anything the CLI says about the *host* therefore lands in the candidate's
error box mid-exam, looking like a fault in their code.

That is not hypothetical. `ProtectHome=true` in hireflow-judge-worker.service made
/home/hfjudge unreadable, so every run printed

    WARNING: Error loading config file: open /home/hfjudge/.docker/config.json:
    permission denied

to candidates. The unit is fixed, but image pulls, platform mismatches and kernel
warnings all reach the candidate by the same route.
"""
from src.judge.sandbox import _split_docker_noise

CONFIG_WARNING = (
    "WARNING: Error loading config file: open /home/hfjudge/.docker/config.json: "
    "permission denied\n"
)


# ── CLI diagnostics are removed and reported ─────────────────────────────────

def test_config_warning_is_removed():
    program, noise = _split_docker_noise(CONFIG_WARNING)
    assert program == ""
    assert len(noise) == 1 and "config.json" in noise[0]


def test_program_stderr_survives_alongside_it():
    program, noise = _split_docker_noise(
        CONFIG_WARNING + 'Traceback (most recent call last):\n  ZeroDivisionError\n'
    )
    assert program == 'Traceback (most recent call last):\n  ZeroDivisionError\n'
    assert len(noise) == 1


def test_image_pull_chatter_is_removed():
    program, noise = _split_docker_noise(
        "Unable to find image 'hireflow-judge-python:latest' locally\n"
        "latest: Pulling from library/python\n"
        "real error\n"
    )
    assert program == "real error\n"
    assert len(noise) == 2


def test_noise_after_program_output_is_still_caught():
    """Ordering is not guaranteed — the CLI can speak after the container exits."""
    program, noise = _split_docker_noise("my error\n" + CONFIG_WARNING)
    assert program == "my error\n"
    assert len(noise) == 1


# ── the candidate's own output is never touched ──────────────────────────────

def test_clean_stderr_passes_through_unchanged():
    s = "Traceback (most recent call last):\n  File \"sol.py\", line 1\n"
    assert _split_docker_noise(s) == (s, [])


def test_empty_stderr():
    assert _split_docker_noise("") == ("", [])


def test_candidate_warning_lines_are_not_stripped():
    """Why the patterns are specific rather than a blanket ^WARNING: match."""
    s = "WARNING: input list was empty\nWARNING: falling back to brute force\n"
    program, noise = _split_docker_noise(s)
    assert program == s
    assert noise == []


def test_blank_lines_in_program_output_are_preserved():
    s = "line one\n\nline three\n"
    assert _split_docker_noise(CONFIG_WARNING + s)[0] == s


# ── the reason this runs before the OOM heuristic ────────────────────────────

def test_noise_cannot_fabricate_an_oom_signature():
    """run_in_sandbox greps stderr for 'Killed' to corroborate exit 137. A host
    message containing it must not reach that check and invent an MLE verdict."""
    program, noise = _split_docker_noise(
        "docker: Error response from daemon: no space left on device Killed\n"
    )
    assert "Killed" not in program
    assert len(noise) == 1
