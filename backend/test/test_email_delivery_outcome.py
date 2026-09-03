"""
Unit tests for email_service.delivery_outcome().

Delivery is three-state — sent, queued, failed — because _send() sends inline and
returns {"sent": bool, ...} while dispatch() may hand the message to RQ and return
{"mode": "async", "job_id": ...} with no "sent" key at all. Callers used to assume
"no exception" meant "delivered", which reported success for mail that never left.
These tests pin both directions: a reported failure must not read as success, and a
queued message must not read as a failure.

Run with:
    pytest test/test_email_delivery_outcome.py -v
"""

import pytest

from src.services.email_service import delivery_outcome


# ---------------------------------------------------------------------------
# Sent — _send() confirmed SMTP accepted the message
# ---------------------------------------------------------------------------

def test_sent_true_is_sent():
    out = delivery_outcome({"sent": True, "error": None, "error_type": None})
    assert out["status"] == "sent"
    assert out["ok"] is True
    assert out["error"] is None


def test_sync_dispatch_reporting_sent_is_sent():
    """enqueue_email() falls back to inline send and tags the mode."""
    out = delivery_outcome({"mode": "sync", "sent": True, "account": "hr@acme.com"})
    assert out["status"] == "sent"
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# Failed — the regression this module exists for
# ---------------------------------------------------------------------------

def test_email_disabled_is_failed_not_sent():
    """EMAIL_ENABLED=false: the most common silent failure in practice."""
    out = delivery_outcome({
        "sent": False,
        "error": "EMAIL_ENABLED is false or SMTP user not set",
        "error_type": "disabled",
    })
    assert out["status"] == "failed"
    assert out["ok"] is False
    assert out["error_type"] == "disabled"
    assert "EMAIL_ENABLED" in out["error"]


@pytest.mark.parametrize("error_type", ["auth", "smtp", "network", "unknown"])
def test_send_failures_are_failed(error_type):
    out = delivery_outcome({"sent": False, "error": "boom", "error_type": error_type})
    assert out["status"] == "failed"
    assert out["ok"] is False
    assert out["error_type"] == error_type
    assert out["error"] == "boom"


def test_failure_without_detail_still_fails():
    out = delivery_outcome({"sent": False})
    assert out["status"] == "failed"
    assert out["ok"] is False


# ---------------------------------------------------------------------------
# Queued — accepted for delivery; outcome not known yet. Must NOT read as failure.
# ---------------------------------------------------------------------------

def test_async_enqueue_is_queued_not_failed():
    """The RQ path carries no "sent" key; treating that as failure would
    mis-report every healthy send on a Redis-backed deployment."""
    out = delivery_outcome({"mode": "async", "job_id": "abc123"})
    assert out["status"] == "queued"
    assert out["ok"] is True
    assert out["error"] is None


def test_rate_limit_deferral_is_queued():
    out = delivery_outcome({"deferred": True, "account": "hr@acme.com"})
    assert out["status"] == "queued"
    assert out["ok"] is True


def test_transient_retry_is_queued():
    out = delivery_outcome({"retry": True, "attempt": 2, "error": "timeout"})
    assert out["status"] == "queued"
    assert out["ok"] is True


def test_unrecognised_dict_is_queued_not_failed():
    """Never invent a failure from a shape we don't recognise."""
    out = delivery_outcome({"something": "else"})
    assert out["status"] == "queued"
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# Legacy senders that return nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("result", [None, "ok", 1, True])
def test_non_dict_results_do_not_regress_to_failure(result):
    out = delivery_outcome(result)
    assert out["status"] == "sent"
    assert out["ok"] is True
