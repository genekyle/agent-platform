"""Account-management routes — the multi-login registry (one+ account per domain).

Extracted from main.py (router split — docs/PLAN_main-split.md). Self-contained:
handlers defer `import accounts` inside the function. Credentials typed in the UI are
encrypted into the secrets vault by the accounts module — never stored on the account
record, never returned, never logged (responses only reflect has_creds + a masked hint).
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class AccountBody(BaseModel):
    account_id: Optional[str] = None
    domain_id: Optional[str] = None
    label: Optional[str] = None
    profile: Optional[str] = None
    secret_ref: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    # Credentials typed in the UI — encrypted straight into the secrets vault, never stored on the
    # account record. Both must be present to set; omitted (edit) = leave existing creds untouched.
    username: Optional[str] = None
    password: Optional[str] = None


class CredentialsBody(BaseModel):
    username: str
    password: str


def _apply_account_credentials(account_id: str, body: AccountBody):
    """If the create/update form carried a username+password, encrypt them into the vault so the
    whole account can be configured in one save. Metadata never carries the secret."""
    import accounts as accounts_mod
    if body.username and body.password:
        accounts_mod.set_credentials(account_id, body.username, body.password)


@router.get("/api/accounts")
def get_accounts(domain_id: Optional[str] = None):
    import accounts as accounts_mod
    return {"accounts": accounts_mod.list_accounts(domain_id)}


@router.get("/api/accounts/{account_id}")
def get_account_ep(account_id: str):
    import accounts as accounts_mod
    acct = accounts_mod.get_account(account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return acct


@router.post("/api/accounts")
def create_account_ep(body: AccountBody):
    import accounts as accounts_mod
    if not body.account_id:
        raise HTTPException(status_code=400, detail="account_id is required")
    # Metadata (never carries the secret); model_dump keeps only registry-editable keys.
    meta = {k: v for k, v in body.model_dump(exclude_none=True).items() if k not in ("username", "password")}
    try:
        accounts_mod.put_account(body.account_id, meta)
        _apply_account_credentials(body.account_id, body)   # encrypt creds into the vault if provided
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return accounts_mod.get_account(accounts_mod._slugify(body.account_id))


@router.patch("/api/accounts/{account_id}")
def update_account_ep(account_id: str, body: AccountBody):
    import accounts as accounts_mod
    meta = {k: v for k, v in body.model_dump(exclude_none=True).items() if k not in ("username", "password")}
    try:
        accounts_mod.put_account(account_id, meta)
        _apply_account_credentials(account_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return accounts_mod.get_account(accounts_mod._slugify(account_id))


@router.put("/api/accounts/{account_id}/credentials")
def set_account_credentials_ep(account_id: str, body: CredentialsBody):
    """Encrypt a login into the secrets vault for an account. Never stored on the account record,
    never returned, never logged — the response only reflects has_creds + a masked hint."""
    import accounts as accounts_mod
    try:
        acct = accounts_mod.set_credentials(account_id, body.username, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return acct


@router.delete("/api/accounts/{account_id}/credentials")
def clear_account_credentials_ep(account_id: str):
    """Remove an account's vault secret (its metadata stays)."""
    import accounts as accounts_mod
    accounts_mod.clear_credentials(account_id)
    acct = accounts_mod.get_account(accounts_mod._slugify(account_id))
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return acct


@router.delete("/api/accounts/{account_id}")
def delete_account_ep(account_id: str):
    import accounts as accounts_mod
    if not accounts_mod.delete_account(account_id):
        raise HTTPException(
            status_code=404,
            detail="No stored account to delete (built-ins can't be deleted — disable via status).",
        )
    return {"ok": True, "deleted": account_id}
