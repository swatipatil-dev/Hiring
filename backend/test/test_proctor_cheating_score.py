"""Composite cheating-score weighting (proctoring/pipeline.py).

Regression guard for a live scoring bug: the behavioral module ran every 5s whether
or not any client fed it signals, and with no signals it reported a non-anomalous
0.0. Because _compute_cheating_score sums only the weights of modules that actually
reported, that constant zero was counted as a real observation and dragged every
composite down by the module's 0.10 weight — a face-only 100 read 30.0 instead of
33.3, and 75.0 instead of 100.0 when face was the only live module.

The fix is that a disabled module contributes no weight at all. These tests pin the
weighting rule rather than the disable switch, so they still hold if behavioral is
ever revived with real data.

No ML extras needed — pipeline.py imports only numpy at module scope.
"""
import pytest

pytest.importorskip("numpy", reason="numpy required for the proctoring pipeline")

from proctoring.pipeline import MODULE_WEIGHTS, ProctorPipeline


@pytest.fixture()
def score():
    """_compute_cheating_score is pure — bind it without constructing a pipeline
    (which would load models)."""
    return lambda scores: ProctorPipeline._compute_cheating_score(None, scores)


def test_single_module_is_not_diluted(score):
    """The bug in its starkest form: one module reporting 100 means 100."""
    assert score({"face": 100.0}) == 100.0


def test_absent_module_contributes_no_weight(score):
    """Adding behavioral back as a constant zero is what broke this."""
    assert score({"face": 100.0}) > score({"face": 100.0, "behavioral": 0.0})


def test_all_vision_modules_reporting(score):
    """A face-only violation across the four real modules — 0.30/0.90, not 0.30/1.00."""
    got = score({"face": 100.0, "object": 0.0, "audio": 0.0, "pose": 0.0})
    assert got == pytest.approx(33.3, abs=0.05)


def test_zero_scoring_module_still_counts_when_it_really_reported(score):
    """A module that genuinely observed nothing suspicious must still weigh in —
    the fix is about absent modules, not about ignoring zeros."""
    assert score({"face": 100.0, "object": 0.0}) == pytest.approx(54.5, abs=0.05)


def test_empty_scores_is_zero_not_a_crash(score):
    assert score({}) == 0.0


def test_unknown_module_keys_are_ignored(score):
    assert score({"face": 100.0, "not_a_module": 100.0}) == 100.0


def test_weights_still_sum_to_one():
    """If a module is added without rebalancing, the ratios above stop meaning
    what the comments say they mean."""
    assert sum(MODULE_WEIGHTS.values()) == pytest.approx(1.0)


def test_behavioral_is_disabled_by_default():
    """It must stay off until the contamination/windowing problems in
    core/behavioral_engine.py are fixed — see that module's docstring."""
    from proctoring.config import BehavioralConfig
    assert BehavioralConfig().enabled is False
