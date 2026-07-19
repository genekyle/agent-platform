"""Account-management routes — the multi-login registry (one+ account per domain).

Extracted from main.py (router split — docs/PLAN_main-split.md). Self-contained:
handlers defer `import accounts` inside the function. Credentials typed in the UI are
encrypted into the secrets vault by the accounts module — never stored on the account
record, never returned, never logged (responses only reflect has_creds + a masked hint).
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from deps import get_db

router = APIRouter()


class AccountBody(BaseModel):
    account_id: Optional[str] = None
    domain_id: Optional[str] = None
    label: Optional[str] = None
    profile: Optional[str] = None
    secret_ref: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    kind: Optional[str] = None          # "domain" (default) or "workday"/ATS per-employer account
    login_url: Optional[str] = None     # the ATS tenant sign-in URL (metadata only, never a secret)
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


class LoginRequest(BaseModel):
    # Targeting is DISCOVERED (tab_finder) from the account's ATS across live Career-Search sessions;
    # these are optional explicit overrides (an operator-picked tab_id wins).
    browser_url: str = "http://127.0.0.1:9322"
    tab_url: Optional[str] = None
    tab_id: Optional[str] = None
    reason: bool = True               # use the Haiku login reasoner (falls back to the deterministic policy)


def _journal_login_step(account_id: str, state: str, step) -> None:
    """Record one login reasoning step in the decision corpus — the Open Brain (PRINCIPLES §10), so
    login reasoning is transferable + shows in the reasoning feed. Best-effort; never raises."""
    from interaction import decision_journal
    from interaction.decision import Bundle, Decision
    _intent = {"fill_credentials": "set_text", "click": "click", "done": "observe", "escalate": "observe"}
    bundle = Bundle(task="ats_login", goal_text=f"log in — {account_id}", done=(step.action == "done"),
                    url="", route=f"ats_login/{account_id}", state=state, is_branch=False,
                    human_required=step.escalate)
    decision = Decision(intent=_intent.get(step.action, "observe"), params={}, confidence=1.0,
                        rung=step.rung, rationale=step.rationale, evidence=(f"login_state:{state}",),
                        escalate=step.escalate)
    decision_journal.log_decision(decision_journal.record_for(decision, bundle, session_id="ats_login"))


def _mark_ats_created(account_id: str) -> None:
    """A verified sign-in proves the account exists → flip it to the 'active' (created) checkpoint."""
    try:
        import accounts as accounts_mod
        import ats_accounts
        acct = accounts_mod.get_account(account_id) or {}
        if acct.get("kind") == "ats" and acct.get("company") and acct.get("ats_id"):
            ats_accounts.mark_created(acct["company"], acct["ats_id"])
    except Exception:  # noqa: BLE001
        pass


@router.post("/api/accounts/{account_id}/login")
async def account_login_ep(account_id: str, body: LoginRequest, db: Session = Depends(get_db)):
    """OPERATOR-TRIGGERED login (the panel's Login button). Resolves the account's credential from the
    vault SERVER-SIDE (never returned) and drives the currently-open login form with the humanized
    browser driver — the app's own password-manager-style auto-login, fired by the human's click.
    Returns a STATUS ONLY (never the password). Does NOT solve 2FA/captcha — those escalate to the
    human. FINDS the live ATS tab across sessions (no hardcoded browser); if it can't, or no form is
    on it, it says exactly why instead of an opaque 'no form'."""
    import httpx

    import accounts as accounts_mod
    import tab_finder
    from settings import settings

    creds = accounts_mod.resolve_creds(account_id)
    if not creds:
        raise HTTPException(status_code=400,
                            detail="No stored credentials for this account — add them in the panel first.")
    username, password = creds
    acct = accounts_mod.get_account(account_id) or {}

    # Find the RIGHT live tab (the fix for the dead 9322 default + multi-tab). An explicit tab_id in
    # the request wins; otherwise discover the account's ATS tab across live sessions.
    tgt = tab_finder.resolve_target(db, acct, tab_id=body.tab_id, browser_url=body.browser_url)
    if not tgt["found"]:
        return {"ok": False, "status": "no_target_tab",
                "detail": (f"No live Career-Search tab for this ATS (looked for {tgt['looked_for']}). "
                           f"{tgt['hint']} Open the Sign In screen there, then press Login.")}
    browser_url, tab_id = tgt["browser_url"], tgt["tab_id"]

    import login_reasoner

    def _journal(step, state, idx):  # each reasoning step -> the Open Brain
        _journal_login_step(account_id, state, step)

    reasoner = login_reasoner.haiku_login_reasoner() if body.reason else None
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            result = await login_reasoner.run_login(
                client=client, capture_url=settings.capture_server_url,
                browser_url=browser_url, tab_id=tab_id, username=username, password=password,
                reasoner=reasoner, journal=_journal)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Login driver unreachable: {exc}")

    # A VERIFIED sign-in is the created checkpoint — flip the account 'active' so Login stays
    # available + Create is blocked going forward (the two lifecycles stay consistent).
    if result.ok:
        _mark_ats_created(account_id)
    return {"ok": result.ok, "status": result.status, "steps": result.steps,
            "detail": result.detail, "trail": result.trail}


@router.delete("/api/accounts/{account_id}")
def delete_account_ep(account_id: str):
    import accounts as accounts_mod
    if not accounts_mod.delete_account(account_id):
        raise HTTPException(
            status_code=404,
            detail="No stored account to delete (built-ins can't be deleted — disable via status).",
        )
    return {"ok": True, "deleted": account_id}
