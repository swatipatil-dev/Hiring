"""Monitoring notice in test invitations (routes/hr_routes.py).

A candidate should learn that a test is proctored from the invitation itself, whatever
HR typed in the email body. The server decides from the test's effective settings: the
template's security settings, with any per-batch override on top.
"""
import json
from types import SimpleNamespace

from routes.hr_routes import MONITORING_EMAIL_NOTICE, _test_is_proctored


def _test(**settings):
    return SimpleNamespace(security_settings_data=json.dumps(settings) if settings else None)


def test_webcam_on_is_proctored():
    assert _test_is_proctored(_test(enable_webcam_monitoring=True)) is True


def test_webcam_off_or_unset_is_not():
    assert _test_is_proctored(_test(enable_webcam_monitoring=False)) is False
    assert _test_is_proctored(_test()) is False          # default for old tests: off
    assert _test_is_proctored(None) is False


def test_batch_override_wins_over_the_template():
    assert _test_is_proctored(_test(enable_webcam_monitoring=False),
                              {"enable_webcam_monitoring": True}) is True
    assert _test_is_proctored(_test(enable_webcam_monitoring=True),
                              {"enable_webcam_monitoring": False}) is False


def test_notice_says_what_matters():
    text = MONITORING_EMAIL_NOTICE.lower()
    assert "proctored" in text
    assert "photo" in text
    assert "reviewed by a person" in text
    assert "nothing rejects you automatically" in text
