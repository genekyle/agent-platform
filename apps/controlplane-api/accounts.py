"""Account registry — configure MULTIPLE accounts per domain, in-app, without ever putting a
secret in the registry.

Why this exists: training and running against more than one Indeed (or Facebook) login is how we
generalize across accounts. But an account is two very different things glued together:

  * **metadata** — a label, which domain it's for, which persistent Chrome profile isolates it,
    and a *reference* to where its secret lives. This is operator-owned config, safe to inspect
    and edit in the UI, and safe to sit in a JSON doc next to the other operator state
    (inventory.json, domain_settings.json). It contains NO secret.
  * **the secret** — the username + password. This stays in the gitignored ``.env`` (or, later, the
    macOS Keychain). The registry only holds a ``secret_ref`` like ``env:INDEED_PRIMARY`` that
    names where to look; the value is resolved on demand, never stored here and never returned by
    the API. The most the API ever exposes is a masked hint (``g***@gmail.com``) and a
    ``has_creds`` boolean.

The isolation guarantee: each account maps to its OWN persistent Chrome profile (``profile``).
Two accounts with two profiles are two independent user-data-dirs, so their cookies/sessions can
never bleed into each other — which is precisely what makes "generalize across accounts" safe.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from settings import settings

_lock = threading.Lock()

# Built-in accounts derived from the legacy single-account .env keys, so nothing that already
# works (FB_USERNAME/PASSWORD, INDEED_USERNAME/PASSWORD, ...) breaks. They always appear in the
# registry; if their env keys are empty they simply show has_creds=false — honest, not hidden.
_BUILTIN_ACCOUNTS: dict[str, dict[str, Any]] = {
    "facebook_default": {
        "account_id": "facebook_default", "domain_id": "facebook_marketplace",
        "label": "Facebook — default", "profile": "facebook", "secret_ref": "env:FB",
        "status": "active", "notes": "Legacy FB_USERNAME / FB_PASSWORD.", "builtin": True,
    },
    "indeed_default": {
        "account_id": "indeed_default", "domain_id": "indeed_jobs",
        "label": "Indeed — default", "profile": "indeed", "secret_ref": "env:INDEED",
        "status": "active", "notes": "Legacy INDEED_USERNAME / INDEED_PASSWORD.", "builtin": True,
    },
    "linkedin_default": {
        "account_id": "linkedin_default", "domain_id": "linkedin_jobs",
        "label": "LinkedIn — default", "profile": "linkedin", "secret_ref": "env:LINKEDIN",
        "status": "active",
        "notes": "LINKEDIN_USERNAME / LINKEDIN_PASSWORD, or type the login in the workspace's "
                 "Accounts tab to store it in the vault instead.",
        "builtin": True,
    },
    "gmail_default": {
        "account_id": "gmail_default", "domain_id": "gmail",
        "label": "Gmail — default",
        # The PROVIDER's profile, not a per-domain one — this is the whole point of the google
        # group. One sign-in in the `google` profile authenticates Gmail and every member that
        # follows it (Docs, Sheets, Drive). This read `"gmail"` until 2026-07-27, which would have
        # launched those members into a second, signed-OUT Chrome profile sitting right beside the
        # signed-in one — a failure that looks like "Google logged us out" rather than like a
        # config split. Keep it equal to providers.google["profile"]; a test pins the two together.
        "profile": "google", "secret_ref": "env:GMAIL",
        "status": "active",
        "notes": "GMAIL_USERNAME / GMAIL_PASSWORD. The agent NEVER types these — Google's password "
                 "and 2FA screens are human-required by policy (the credential cascades into every "
                 "domain that signs in with Google). They exist for the operator's one-time "
                 "supervised sign-in; the shared profile keeps the session afterwards.",
        "builtin": True,
    },
}

# `kind` distinguishes a domain login (default) from a per-employer ATS account (e.g. "workday"),
# and `login_url` is that ATS tenant's sign-in URL. Both are plain metadata — the PASSWORD still only
# ever lives encrypted in the secrets vault (secret_ref="vault:..."), never on the record.
# `company` + `ats_id` tie a per-employer ATS login to its company and platform (the company-first
# org). `username_hint` is the safe-to-show login id (e.g. genomags@gmail.com) — NOT a secret.
_EDITABLE_KEYS = ("domain_id", "label", "profile", "secret_ref", "status", "notes", "kind",
                  "login_url", "company", "ats_id", "username_hint")
# "pending" = a per-employer ATS account we've registered but whose login the operator hasn't
# created on the site yet (the human-does-the-signup step). Becomes "active" once creds are saved.
_STATUSES = ("active", "disabled", "pending")


# --------------------------------------------------------------------------- storage
def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "accounts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        stored = data.get("accounts") if isinstance(data, dict) else None
        return stored if isinstance(stored, dict) else {}
    except Exception:
        return {}


def _save(accounts: dict[str, Any]) -> None:
    _path().write_text(json.dumps({"accounts": accounts}, indent=2), encoding="utf-8")


def _merged() -> dict[str, dict[str, Any]]:
    """Built-in defaults overlaid with stored edits. Stored values win so an operator can rename or
    re-point a built-in; a stored-only account (a genuinely new login) is included as-is."""
    out: dict[str, dict[str, Any]] = {aid: dict(rec) for aid, rec in _BUILTIN_ACCOUNTS.items()}
    for aid, rec in _load().items():
        if not isinstance(rec, dict):
            continue
        base = dict(out.get(aid) or {})
        base.update(rec)
        base["account_id"] = aid
        out[aid] = base
    return out


# --------------------------------------------------------------------------- secrets
def _slugify(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _read_env_value(key: str) -> str:
    """A real environment variable wins; otherwise read the gitignored .env directly. pydantic
    Settings only loads *declared* fields, so per-account keys (INDEED_PRIMARY_USERNAME, ...) won't
    be on `settings` or in os.environ — we parse the file ourselves, like scripts/haiku_smoke_test.
    """
    if key in os.environ:
        return os.environ[key].strip()
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _resolve_secret_ref(secret_ref: str) -> Optional[tuple[str, str]]:
    """(username, password) for a ``secret_ref``, or None if either half is missing. Dispatches on
    the scheme so a stronger backend (keychain:) can slot in later without touching callers."""
    ref = (secret_ref or "").strip()
    scheme, _, name = ref.partition(":")
    if not name:  # bare name -> treat as env prefix, the common case
        scheme, name = "env", ref
    if scheme == "env":
        user = _read_env_value(f"{name}_USERNAME")
        pw = _read_env_value(f"{name}_PASSWORD")
        return (user, pw) if user and pw else None
    if scheme == "vault":
        # UI-entered creds, encrypted at rest in the secrets vault (envelope encryption, pluggable
        # key provider). `name` is the vault key (the account_id).
        import secrets_vault
        creds = secrets_vault.get_secret(name)
        if not creds:
            return None
        user, pw = str(creds.get("username") or ""), str(creds.get("password") or "")
        return (user, pw) if user and pw else None
    # keychain:<service> and other backends could slot in the same way; unknown/unsupported
    # schemes resolve to nothing rather than guessing.
    return None


def _mask(username: str) -> str:
    """A safe-to-display hint. Emails keep the first char + domain; other logins keep the ends."""
    u = (username or "").strip()
    if not u:
        return ""
    if "@" in u:
        local, _, domain = u.partition("@")
        head = local[0] if local else ""
        return f"{head}***@{domain}"
    if len(u) <= 2:
        return "***"
    return f"{u[0]}***{u[-1]}"


# --------------------------------------------------------------------------- public API
def _public(rec: dict[str, Any]) -> dict[str, Any]:
    """A registry record enriched for the API: adds has_creds + a masked username hint, and
    guarantees NO raw secret is present. Vault-backed accounts are checked WITHOUT decrypting —
    presence is a cheap file check and the hint was pre-masked and stored at set-time."""
    secret_ref = rec.get("secret_ref", "")
    if secret_ref.startswith("vault:"):
        import secrets_vault
        has_creds = secrets_vault.has_secret(secret_ref.partition(":")[2])
        hint = rec.get("username_hint", "")
    else:
        creds = _resolve_secret_ref(secret_ref)
        has_creds = creds is not None
        hint = _mask(creds[0]) if creds else rec.get("username_hint", "")
    return {
        "account_id": rec.get("account_id"),
        "domain_id": rec.get("domain_id"),
        "label": rec.get("label") or rec.get("account_id"),
        "profile": rec.get("profile") or rec.get("account_id"),
        "secret_ref": secret_ref,
        "secret_backend": secret_ref.partition(":")[0] if ":" in secret_ref else "env",
        "status": rec.get("status", "active"),
        "notes": rec.get("notes", ""),
        "kind": rec.get("kind", "domain"),
        "login_url": rec.get("login_url", ""),
        "company": rec.get("company", ""),
        "ats_id": rec.get("ats_id", ""),
        "builtin": bool(rec.get("builtin")),
        "has_creds": has_creds,
        "username_hint": hint,
        "created_at": rec.get("created_at"),
        "updated_at": rec.get("updated_at"),
    }


def list_accounts(domain_id: Optional[str] = None) -> list[dict[str, Any]]:
    recs = [_public(r) for r in _merged().values()]
    if domain_id:
        recs = [r for r in recs if r["domain_id"] == domain_id]
    return sorted(recs, key=lambda r: (r["domain_id"] or "", r["account_id"] or ""))


def get_account(account_id: str) -> Optional[dict[str, Any]]:
    rec = _merged().get(account_id)
    return _public(rec) if rec else None


def profile_for(account_id: str) -> Optional[str]:
    """The persistent Chrome profile name for an account — the isolation key a session launches
    against. None if the account is unknown."""
    rec = _merged().get(account_id)
    if not rec:
        return None
    return rec.get("profile") or account_id


def resolve_creds(account_id: str) -> Optional[tuple[str, str]]:
    """(username, password) for an account, resolved from its secret backend. NEVER log or return
    this over the API. Returns None when the account is unknown, disabled, or its creds are unset."""
    rec = _merged().get(account_id)
    if not rec or rec.get("status") == "disabled":
        return None
    return _resolve_secret_ref(rec.get("secret_ref", ""))


def put_account(account_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Create or update an account's METADATA (never its secret). Returns the public view.

    ``account_id`` is slugified so it's a stable, filesystem/profile-safe key. Only known keys are
    accepted; ``status`` is validated. A missing ``profile`` defaults to the account id, so a new
    account is isolated by default instead of silently sharing another's Chrome."""
    aid = _slugify(account_id)
    if not aid:
        raise ValueError("account_id must contain at least one alphanumeric character")
    with _lock:
        stored = _load()
        current = dict(stored.get(aid) or _BUILTIN_ACCOUNTS.get(aid) or {})
        current["account_id"] = aid
        for key in _EDITABLE_KEYS:
            if key in patch and patch[key] is not None:
                current[key] = patch[key]
        if current.get("status") not in _STATUSES:
            current["status"] = "active"
        if not current.get("profile"):
            current["profile"] = aid
        now = datetime.now(timezone.utc).isoformat()
        current.setdefault("created_at", now)
        current["updated_at"] = now
        # A stored copy of a built-in shouldn't masquerade as un-editable.
        current.pop("builtin", None)
        stored[aid] = current
        _save(stored)
        return _public({**current, "account_id": aid})


def delete_account(account_id: str) -> bool:
    """Remove a STORED account. Built-ins can't be deleted (they'd just re-seed); disable them via
    status instead. Returns True if a stored row was removed. Also clears any vault secret so a
    deleted account never leaves credentials behind."""
    import secrets_vault
    with _lock:
        stored = _load()
        if account_id not in stored:
            return False
        del stored[account_id]
        _save(stored)
    secrets_vault.delete_secret(account_id)
    return True


def set_credentials(account_id: str, username: str, password: str) -> Optional[dict[str, Any]]:
    """Store a login for an account by ENCRYPTING it into the secrets vault (never the registry).

    Points the account's ``secret_ref`` at the vault and stores only a pre-masked hint for display.
    The raw username/password go through the vault's key provider and are never persisted in the
    registry, returned by the API, or logged. Returns the account's public view.
    """
    aid = _slugify(account_id)
    if not aid:
        raise ValueError("account_id must contain at least one alphanumeric character")
    if not (username and password):
        raise ValueError("username and password are both required")
    import secrets_vault
    secrets_vault.set_secret(aid, {"username": username, "password": password})
    put_account(aid, {"secret_ref": f"vault:{aid}"})   # ensure a stored record pointing at the vault
    with _lock:                                          # then stamp the SAFE, pre-masked hint
        stored = _load()
        if aid in stored:
            stored[aid]["username_hint"] = _mask(username)
            _save(stored)
    return get_account(aid)


def clear_credentials(account_id: str) -> bool:
    """Delete an account's vault secret + its stored hint (metadata stays). Returns True if a
    secret was removed."""
    import secrets_vault
    aid = _slugify(account_id)
    removed = secrets_vault.delete_secret(aid)
    with _lock:
        stored = _load()
        if aid in stored and "username_hint" in stored[aid]:
            stored[aid].pop("username_hint", None)
            _save(stored)
    return removed
