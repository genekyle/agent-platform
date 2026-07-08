"""Secrets vault — encrypted-at-rest credential storage with a PLUGGABLE key provider.

The design goal is cloud-agnostic separation of the *key* from the *lockbox*:

  * **lockbox** — ``<artifacts>/cache/secrets_vault.json`` holds each account's credentials as
    AES-256-GCM ciphertext. Useless without the key, so it's safe to sit on the local box, a
    server, or be synced to the cloud.
  * **key** — supplied by a ``KeyProvider``. Swapping the provider moves the key into a different
    trust domain WITHOUT changing the vault format or any caller:
      - ``LocalProvider`` (today): master key in a keyfile outside the repo
        (``~/.agent-platform/vault.key``, 0600). Honest scope: locally the key lives near the
        box, so this is "encrypted + organized + not-plaintext", not yet cryptographic
        separation.
      - ``KmsProvider`` (stub): the key lives in a cloud KMS (AWS/GCP/Vault); ciphertext is sent
        to be unwrapped and the key never leaves the KMS. This is where real key/lockbox
        separation arrives — a config swap, not a code change.

Honest limits: to USE a login the app must decrypt it into memory transiently (unavoidable for
any secret you actually type — true of KMS-backed systems too). The vault protects secrets at
rest and enables the separation above; it is not a defense against a compromised running process.

Nothing here is ever logged or returned over the API. The only thing the API exposes is a
pre-computed masked hint, stored by the caller in the account registry (never a decrypt-to-display).
"""

from __future__ import annotations

import base64
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from settings import settings

_lock = threading.Lock()


# --------------------------------------------------------------------------- key providers
class KeyProvider(Protocol):
    provider_id: str

    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, token: str) -> str: ...


def _local_key_path() -> Path:
    """Where the local master key lives. Default is OUTSIDE the repo so an accidental commit or a
    repo copy never carries the key. Overridable via env for tests / relocation."""
    override = os.environ.get("AGENT_VAULT_KEY_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-platform" / "vault.key"


class LocalProvider:
    """AES-256-GCM with a master key from a local keyfile (auto-created on first use, 0600)."""

    provider_id = "local-aesgcm-v1"

    def __init__(self, key_path: Optional[Path] = None):
        self._key_path = key_path or _local_key_path()
        self._key: Optional[bytes] = None

    def _load_key(self) -> bytes:
        if self._key is not None:
            return self._key
        p = self._key_path
        if p.exists():
            self._key = base64.b64decode(p.read_text(encoding="utf-8").strip())
        else:
            key = os.urandom(32)  # AES-256
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Write 0600 without a read window where it's world-readable.
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(base64.b64encode(key).decode())
            self._key = key
        if len(self._key) != 32:
            raise ValueError("vault master key must be 32 bytes (AES-256)")
        return self._key

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ct = AESGCM(self._load_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token)
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(self._load_key()).decrypt(nonce, ct, None).decode("utf-8")


class KmsProvider:
    """Cloud-KMS-backed provider — the seam for real key/lockbox separation. Not wired yet: it's
    here so the shape is explicit and swapping to it later is a config change, not a redesign."""

    provider_id = "kms-v1"

    def encrypt(self, plaintext: str) -> str:  # pragma: no cover - stub
        raise NotImplementedError(
            "KmsProvider is the planned cloud upgrade — set the key in a cloud KMS and implement "
            "wrap/unwrap here. Until then the app uses LocalProvider.")

    def decrypt(self, token: str) -> str:  # pragma: no cover - stub
        raise NotImplementedError("KmsProvider not wired yet — see the module docstring.")


_provider: Optional[KeyProvider] = None


def get_provider() -> KeyProvider:
    """The active key provider. ``VAULT_KEY_PROVIDER=kms`` will select the cloud backend once it's
    implemented; today everything resolves to the local keyfile provider."""
    global _provider
    if _provider is None:
        choice = os.environ.get("VAULT_KEY_PROVIDER", "local").lower()
        _provider = KmsProvider() if choice == "kms" else LocalProvider()
    return _provider


def reset_provider_cache() -> None:
    """Test hook — drop the memoized provider so a monkeypatched key path is picked up."""
    global _provider
    _provider = None


# --------------------------------------------------------------------------- lockbox (vault store)
def _vault_path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "secrets_vault.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    p = _vault_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(doc: dict[str, Any]) -> None:
    p = _vault_path()
    # The file only ever holds ciphertext, but keep it 0600 anyway — defense in depth.
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=2))


def set_secret(key: str, payload: dict[str, Any]) -> None:
    """Encrypt ``payload`` (a small dict, e.g. {username, password}) into the lockbox under ``key``."""
    provider = get_provider()
    token = provider.encrypt(json.dumps(payload))
    with _lock:
        doc = _load()
        doc[key] = {"ciphertext": token, "provider": provider.provider_id,
                    "updated_at": datetime.now(timezone.utc).isoformat()}
        _save(doc)


def get_secret(key: str) -> Optional[dict[str, Any]]:
    """Decrypt and return the payload for ``key``, or None if absent/undecryptable."""
    rec = _load().get(key)
    if not isinstance(rec, dict) or not rec.get("ciphertext"):
        return None
    try:
        return json.loads(get_provider().decrypt(rec["ciphertext"]))
    except Exception:
        return None


def has_secret(key: str) -> bool:
    rec = _load().get(key)
    return isinstance(rec, dict) and bool(rec.get("ciphertext"))


def delete_secret(key: str) -> bool:
    with _lock:
        doc = _load()
        if key not in doc:
            return False
        del doc[key]
        _save(doc)
        return True
