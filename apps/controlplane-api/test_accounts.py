"""Tests for the account registry — multi-account metadata, secret references, and masking.

The golden rule under test: the registry never stores or returns a raw secret. It resolves creds
from a referenced backend on demand and, to the outside world, only ever exposes has_creds + a
masked hint.
"""

from __future__ import annotations

import pytest

import accounts


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # Store in a temp file, and fake the secret backend so the real .env is never read.
    monkeypatch.setattr(accounts, "_path", lambda: tmp_path / "accounts.json")
    fake_env: dict[str, str] = {}
    monkeypatch.setattr(accounts, "_read_env_value", lambda key: fake_env.get(key, ""))
    # Isolate the secrets vault too (temp key + temp lockbox), so vault-backed accounts round-trip
    # without touching the real key/vault.
    import secrets_vault
    monkeypatch.setenv("AGENT_VAULT_KEY_PATH", str(tmp_path / "vault.key"))
    monkeypatch.setenv("VAULT_KEY_PROVIDER", "local")
    monkeypatch.setattr(secrets_vault, "_vault_path", lambda: tmp_path / "secrets_vault.json")
    secrets_vault.reset_provider_cache()
    return fake_env


def test_builtins_present_but_credless_when_env_empty():
    recs = {a["account_id"]: a for a in accounts.list_accounts()}
    assert "indeed_default" in recs and "facebook_default" in recs
    assert recs["indeed_default"]["has_creds"] is False
    assert recs["indeed_default"]["username_hint"] == ""


def test_resolve_creds_reads_env_prefix(_isolate):
    _isolate["INDEED_USERNAME"] = "gene@example.com"
    _isolate["INDEED_PASSWORD"] = "s3cret"
    assert accounts.resolve_creds("indeed_default") == ("gene@example.com", "s3cret")
    # public view masks the username and never carries the password
    pub = accounts.get_account("indeed_default")
    assert pub["has_creds"] is True
    assert pub["username_hint"] == "g***@example.com"
    assert "password" not in pub and "s3cret" not in str(pub)


def test_new_account_defaults_profile_to_id_and_persists():
    accounts.put_account("Indeed Secondary!", {
        "domain_id": "indeed_jobs", "label": "Indeed — secondary", "secret_ref": "env:INDEED_2",
    })
    acct = accounts.get_account("indeed_secondary")     # slugified
    assert acct is not None
    assert acct["profile"] == "indeed_secondary"        # isolated by default
    assert acct["domain_id"] == "indeed_jobs"


def test_second_account_resolves_its_own_secret(_isolate):
    accounts.put_account("indeed_secondary", {"domain_id": "indeed_jobs", "secret_ref": "env:INDEED_2"})
    _isolate["INDEED_2_USERNAME"] = "second@example.com"
    _isolate["INDEED_2_PASSWORD"] = "pw2"
    assert accounts.resolve_creds("indeed_secondary") == ("second@example.com", "pw2")
    # the two accounts map to two different profiles → isolated Chrome
    assert accounts.profile_for("indeed_default") != accounts.profile_for("indeed_secondary")


def test_disabled_account_yields_no_creds(_isolate):
    _isolate["INDEED_USERNAME"] = "gene@example.com"
    _isolate["INDEED_PASSWORD"] = "s3cret"
    accounts.put_account("indeed_default", {"status": "disabled"})
    assert accounts.resolve_creds("indeed_default") is None


def test_filter_by_domain():
    accounts.put_account("indeed_secondary", {"domain_id": "indeed_jobs", "secret_ref": "env:X"})
    indeed = accounts.list_accounts(domain_id="indeed_jobs")
    assert {a["account_id"] for a in indeed} >= {"indeed_default", "indeed_secondary"}
    assert all(a["domain_id"] == "indeed_jobs" for a in indeed)


def test_builtin_cannot_be_deleted_but_stored_can():
    assert accounts.delete_account("indeed_default") is False   # builtin, nothing stored yet
    accounts.put_account("indeed_secondary", {"domain_id": "indeed_jobs", "secret_ref": "env:X"})
    assert accounts.delete_account("indeed_secondary") is True
    assert accounts.get_account("indeed_secondary") is None


def test_bare_secret_ref_treated_as_env_prefix(_isolate):
    accounts.put_account("acme", {"domain_id": "d", "secret_ref": "ACME"})  # no scheme
    _isolate["ACME_USERNAME"] = "u"
    _isolate["ACME_PASSWORD"] = "p"
    assert accounts.resolve_creds("acme") == ("u", "p")


def test_unknown_secret_scheme_resolves_to_none():
    accounts.put_account("kc", {"domain_id": "d", "secret_ref": "keychain:kc"})
    assert accounts.resolve_creds("kc") is None  # keychain backend not implemented yet


def test_set_credentials_encrypts_into_vault_not_registry(tmp_path):
    acct = accounts.set_credentials("facebook_alt", "seller@gmail.com", "topsecretpw")
    # public view: has_creds + masked hint, never the raw secret
    assert acct["has_creds"] is True
    assert acct["username_hint"] == "s***@gmail.com"
    assert acct["secret_ref"] == "vault:facebook_alt"
    assert acct["secret_backend"] == "vault"
    assert "topsecretpw" not in str(acct)
    # the ACCOUNT REGISTRY file must never contain the plaintext secret
    registry_raw = (tmp_path / "accounts.json").read_text()
    assert "topsecretpw" not in registry_raw
    assert "seller@gmail.com" not in registry_raw   # not even the username lives in the registry
    # but resolve_creds (login path) can recover them from the vault
    assert accounts.resolve_creds("facebook_alt") == ("seller@gmail.com", "topsecretpw")


def test_clear_credentials_removes_creds_keeps_metadata():
    accounts.set_credentials("facebook_alt", "seller@gmail.com", "pw")
    accounts.put_account("facebook_alt", {"label": "Facebook — reseller"})
    assert accounts.clear_credentials("facebook_alt") is True
    acct = accounts.get_account("facebook_alt")
    assert acct["has_creds"] is False
    assert acct["username_hint"] == ""
    assert acct["label"] == "Facebook — reseller"    # metadata survives
    assert accounts.resolve_creds("facebook_alt") is None


def test_set_credentials_requires_both():
    with pytest.raises(ValueError):
        accounts.set_credentials("facebook_alt", "", "pw")
