"""Versioned email encryption: round trips, rotation, legacy reads, loud failures."""
import pytest
from cryptography.fernet import Fernet

from src.email_api import crypto
from src.email_api.crypto import CredentialUnavailable

K1 = Fernet.generate_key().decode()
K2 = Fernet.generate_key().decode()


@pytest.fixture()
def keys(monkeypatch):
    def set_keys(spec):
        monkeypatch.setenv("EMAIL_CREDENTIAL_KEYS", spec)
    set_keys(f"k1:{K1}")
    return set_keys


def test_round_trip_records_version(keys):
    stored, version = crypto.encrypt_secret("app-password")
    assert version == "k1" and stored.startswith("k1$")
    assert "app-password" not in stored
    assert crypto.decrypt_secret(stored, version) == "app-password"


def test_rotation_reads_old_writes_new(keys):
    old, _ = crypto.encrypt_secret("pw")
    keys(f"k2:{K2},k1:{K1}")
    assert crypto.decrypt_secret(old) == "pw"
    new, version = crypto.encrypt_secret("pw")
    assert version == "k2" and crypto.needs_reencryption("k1")
    keys(f"k2:{K2}")
    assert crypto.decrypt_secret(new) == "pw"
    with pytest.raises(CredentialUnavailable):
        crypto.decrypt_secret(old)


def test_wrong_key_raises_never_blank(keys):
    stored, _ = crypto.encrypt_secret("pw")
    keys(f"k1:{K2}")  # same version label, different key
    with pytest.raises(CredentialUnavailable):
        crypto.decrypt_secret(stored)


def test_credentials_and_content_use_separate_subkeys(keys):
    stored, _ = crypto.encrypt_secret("pw")
    with pytest.raises(CredentialUnavailable):
        crypto.decrypt_content(stored)
    body, _ = crypto.encrypt_content('{"html": "<p>hi</p>"}')
    assert crypto.decrypt_content(body) == '{"html": "<p>hi</p>"}'


def test_missing_keys_refuse_to_store(monkeypatch):
    monkeypatch.setenv("EMAIL_CREDENTIAL_KEYS", "")
    assert not crypto.is_configured()
    with pytest.raises(CredentialUnavailable):
        crypto.encrypt_secret("pw")


def test_malformed_spec_is_rejected(monkeypatch):
    monkeypatch.setenv("EMAIL_CREDENTIAL_KEYS", "not-a-key")
    with pytest.raises(ValueError):
        crypto.is_configured()
    monkeypatch.setenv("EMAIL_CREDENTIAL_KEYS", f"legacy:{K1}")
    with pytest.raises(ValueError):
        crypto.is_configured()


def test_legacy_values_read_with_old_key_and_never_echoed(keys):
    from src.security import encrypt as legacy_encrypt
    token = legacy_encrypt("old-pw")
    assert crypto.looks_like_fernet_token(token)
    assert crypto.decrypt_secret(token, crypto.LEGACY) == "old-pw"
    # The old "decrypt(x) or x" handed the ciphertext itself to SMTP as a password.
    with pytest.raises(CredentialUnavailable):
        crypto.decrypt_secret("gAAAAA-not-a-real-token", crypto.LEGACY)
    assert not crypto.looks_like_fernet_token("plain-password")
