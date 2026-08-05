"""Unit tests for the per-step pipeline notification resolver.

These exercise the pure merge/dedup/validation and mailbox-sender logic without a
live database by stubbing the DB-touching helpers (`_load_row`, `_role_emails`,
`_mailbox_smtp_config`).
"""
import sys
import os
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services import pipeline_notifications as pn


def _row(**kw):
    """Build a fake PipelineStepConfig-like row."""
    defaults = dict(
        enabled=True, extra_emails=None, notify_roles=None, as_cc=True,
        mailbox_id=None, step_key="interview_scheduled",
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


# CC is only ever the explicitly-typed extra_emails — no role expansion exists.


def _mailbox(**kw):
    defaults = dict(
        id="mb1", is_active=True, smtp_host="smtp.acme.com", smtp_port=465,
        smtp_user="offers@acme.com", smtp_pass="plain-not-fernet",
        smtp_from_email="noreply@acme.com", hourly_limit=None,
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_json_list_parsing():
    assert pn._json_list(None) == []
    assert pn._json_list("not json") == []
    assert pn._json_list('["a@x.com", " b@x.com "]') == ["a@x.com", "b@x.com"]
    assert pn._json_list('{"x":1}') == []  # not a list


def test_is_valid_step():
    assert pn.is_valid_step("interview_scheduled")
    assert not pn.is_valid_step("nope")


def test_disabled_step_returns_no_cc(monkeypatch):
    monkeypatch.setattr(pn, "_load_row", lambda db, c, s: _row(enabled=False,
                        extra_emails='["x@x.com"]'))
    out = pn.resolve_step_dispatch(object(), "c1", "interview_scheduled")
    assert out == {"cc": [], "smtp_config": None, "enabled": False}


def test_missing_step_returns_default(monkeypatch):
    monkeypatch.setattr(pn, "_load_row", lambda db, c, s: None)
    out = pn.resolve_step_dispatch(object(), "c1", "interview_scheduled")
    assert out == {"cc": [], "smtp_config": None, "enabled": True}


def test_invalid_step_short_circuits():
    out = pn.resolve_step_dispatch(object(), "c1", "bogus_step")
    assert out == {"cc": [], "smtp_config": None, "enabled": True}


def test_cc_is_only_typed_emails_deduped_and_validated(monkeypatch):
    # notify_roles is present in the row but MUST be ignored (no role expansion).
    monkeypatch.setattr(pn, "_load_row", lambda db, c, s: _row(
        extra_emails='["typed@x.com", "DUP@x.com", "dup@x.com", "not-an-email", ""]',
        notify_roles='["hr", "admin"]',
    ))
    out = pn.resolve_step_dispatch(object(), "c1", "interview_scheduled")
    assert out["enabled"] is True
    assert out["smtp_config"] is None
    # "not-an-email"/blank dropped, DUP/dup collapsed, order preserved, NO role emails.
    assert out["cc"] == ["typed@x.com", "DUP@x.com"]


def test_role_helper_is_gone():
    # The role-expansion helper/constant must no longer exist.
    assert not hasattr(pn, "_role_emails")
    assert not hasattr(pn, "NOTIFY_ROLES")


def test_mailbox_selected_builds_smtp(monkeypatch):
    monkeypatch.setattr(pn, "_load_row", lambda db, c, s: _row(mailbox_id="mb1"))

    class _DB:
        def query(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def first(self): return _mailbox()
    out = pn.resolve_step_dispatch(_DB(), "c1", "interview_scheduled")
    cfg = out["smtp_config"]
    assert cfg["host"] == "smtp.acme.com"
    assert cfg["port"] == 465
    assert cfg["user"] == "offers@acme.com"
    assert cfg["from_email"] == "noreply@acme.com"
    # decrypt() returns "" for a non-Fernet value → falls back to the raw stored value
    assert cfg["password"] == "plain-not-fernet"


def test_no_mailbox_means_default_sender(monkeypatch):
    monkeypatch.setattr(pn, "_load_row", lambda db, c, s: _row(mailbox_id=None))
    out = pn.resolve_step_dispatch(object(), "c1", "interview_scheduled")
    assert out["smtp_config"] is None


def test_mailbox_config_helper_guards():
    # No id → None; inactive / missing creds → None.
    assert pn._mailbox_smtp_config(object(), None) is None

    class _DB:
        def __init__(self, mb): self._mb = mb
        def query(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def first(self): return self._mb
    assert pn._mailbox_smtp_config(_DB(_mailbox(is_active=False)), "mb1") is None
    assert pn._mailbox_smtp_config(_DB(_mailbox(smtp_pass=None)), "mb1") is None
    assert pn._mailbox_smtp_config(_DB(None), "mb1") is None


def test_serialize_row_shape():
    row = _row(extra_emails='["a@x.com"]', notify_roles='["hr"]', mailbox_id="mb1")
    data = pn.serialize_row(row)
    assert "smtp_pass" not in data
    assert "notify_roles" not in data          # role-based CC removed from the API
    assert data["mailbox_id"] == "mb1"
    assert data["extra_emails"] == ["a@x.com"]


def test_default_step_payload_shape():
    data = pn.default_step_payload("offer_letter")
    assert "notify_roles" not in data
    assert data["mailbox_id"] is None
    assert data["label"] == pn.STEP_LABELS["offer_letter"]
    assert data["enabled"] is True
