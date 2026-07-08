"""Tests for the secrets vault — encrypted-at-rest storage with a pluggable key provider.

Golden rules under test: what lands on disk is ciphertext (never the plaintext secret); a wrong
key or a tampered blob fails closed (returns None, never garbage); round-trips are exact.
"""

from __future__ import annotations

import pytest

import secrets_vault


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_KEY_PATH", str(tmp_path / "vault.key"))
    monkeypatch.setenv("VAULT_KEY_PROVIDER", "local")
    monkeypatch.setattr(secrets_vault, "_vault_path", lambda: tmp_path / "secrets_vault.json")
    secrets_vault.reset_provider_cache()
    yield tmp_path
    secrets_vault.reset_provider_cache()


def test_round_trip():
    secrets_vault.set_secret("acct1", {"username": "gene@example.com", "password": "hunter2"})
    assert secrets_vault.get_secret("acct1") == {"username": "gene@example.com", "password": "hunter2"}
    assert secrets_vault.has_secret("acct1") is True


def test_ciphertext_on_disk_not_plaintext(_isolate):
    secrets_vault.set_secret("acct1", {"username": "gene@example.com", "password": "SuperSecretPw"})
    raw = (_isolate / "secrets_vault.json").read_text()
    assert "SuperSecretPw" not in raw
    assert "gene@example.com" not in raw
    assert "ciphertext" in raw and "local-aesgcm-v1" in raw


def test_key_file_created_0600(_isolate):
    secrets_vault.set_secret("acct1", {"username": "u", "password": "p"})
    keyfile = _isolate / "vault.key"
    assert keyfile.exists()
    assert (keyfile.stat().st_mode & 0o777) == 0o600


def test_wrong_key_fails_closed(_isolate, monkeypatch):
    secrets_vault.set_secret("acct1", {"username": "u", "password": "p"})
    # Point at a different key file → decrypt must fail closed, not return garbage.
    monkeypatch.setenv("AGENT_VAULT_KEY_PATH", str(_isolate / "other.key"))
    secrets_vault.reset_provider_cache()
    assert secrets_vault.get_secret("acct1") is None


def test_tamper_fails_closed(_isolate):
    secrets_vault.set_secret("acct1", {"username": "u", "password": "p"})
    import json
    p = _isolate / "secrets_vault.json"
    doc = json.loads(p.read_text())
    ct = doc["acct1"]["ciphertext"]
    doc["acct1"]["ciphertext"] = ("A" if ct[0] != "A" else "B") + ct[1:]   # flip a byte
    p.write_text(json.dumps(doc))
    assert secrets_vault.get_secret("acct1") is None   # GCM auth tag rejects the tamper


def test_delete():
    secrets_vault.set_secret("acct1", {"username": "u", "password": "p"})
    assert secrets_vault.delete_secret("acct1") is True
    assert secrets_vault.has_secret("acct1") is False
    assert secrets_vault.delete_secret("acct1") is False
