"""Career-search routes — the ATS registry + application preferences.

Thin router (docs/PLAN_main-split.md): the structure lives in the top-level `ats_registry` and
`application_preferences` modules; this just exposes them so the cockpit can render the ATS
grouping (each ATS domain-like, with the companies known to use it) and the operator can read/add
application-preference notes attached to the career-search domain.
"""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import application_preferences as prefs_lib
import ats_accounts
import ats_registry

router = APIRouter()


def _match_create_account_fields(candidates: list) -> dict:
    """{email, password, verify, acknowledge, submit} backend_node_ids from a Workday Create-Account
    form's AX candidates. SKIPS the bot honeypot ('robot'/'website' inputs). Matches by accessible
    name — the churn-immune AX layer (WORKDAY_CREATE_ACCOUNT_RECIPE)."""
    out: dict = {}
    for c in candidates:
        role = (c.get("role") or "").lower()
        name = (c.get("name") or c.get("caption") or "").lower()
        nid = c.get("backend_node_id")
        if role == "textbox" and "robot" not in name and "website" not in name:
            if "email" in name and "email" not in out:
                out["email"] = nid
            elif "verify" in name:
                out["verify"] = nid
            elif "password" in name and "password" not in out:
                out["password"] = nid
        elif role == "checkbox" and any(k in name for k in ("confirm", "acknowledge", "agree", "read")):
            out["acknowledge"] = nid
        elif role == "button" and "create account" in name and "robot" not in name:
            out["submit"] = nid
    return out


@router.get("/api/career_search/ats")
def get_ats_registry() -> dict[str, Any]:
    """The career-search domain group + the ATS platforms (each with its recipe pointer, auth
    posture, and the companies known to use it) + the learned company→ATS map."""
    return ats_registry.ats_spec()


class RecordCompanyATS(BaseModel):
    company: str
    ats_id: str
    url: Optional[str] = ""


@router.post("/api/career_search/ats/company")
def record_company_ats(body: RecordCompanyATS) -> dict[str, Any]:
    """Learn that a company applies through an ATS (observed live). Generalizes that ATS's recipe +
    training to this company next time. NEVER auto-creates accounts — this is just the mapping."""
    return ats_registry.record_company_ats(body.company, body.ats_id, body.url or "")


@router.get("/api/career_search/application_preferences")
def get_application_preferences() -> dict[str, Any]:
    """The operator's application preferences (structured + notes), keyed by domain."""
    return prefs_lib.preferences_spec()


class AddPreferenceNote(BaseModel):
    text: str
    category: str = "fit"
    source: str = "operator"
    domain_id: str = prefs_lib.DEFAULT_DOMAIN


@router.post("/api/career_search/application_preferences/note")
def add_application_preference_note(body: AddPreferenceNote) -> dict[str, Any]:
    """Append an application-preference note (e.g. why a role was skipped)."""
    return prefs_lib.add_note(body.text, body.category, body.source, body.domain_id)


# --- ATS accounts (company-first) --------------------------------------------------------------
@router.get("/api/career_search/accounts")
def list_ats_accounts() -> dict[str, Any]:
    """Company-first view of ATS application accounts (each company → its ATS logins + status).
    Never returns a password — only the login id hint + whether creds are saved."""
    return ats_accounts.list_by_company()


class EnsureATSAccount(BaseModel):
    company: str
    ats_id: str
    login_url: Optional[str] = ""


@router.post("/api/career_search/accounts/ensure")
def ensure_ats_account(body: EnsureATSAccount) -> dict[str, Any]:
    """Register (idempotent) a company↔ATS login as PENDING and return it + the suggested
    credentials to CREATE it with. Does NOT create the account on the site (operator's step)."""
    return ats_accounts.ensure_account(body.company, body.ats_id, body.login_url or "")


@router.get("/api/career_search/accounts/credentials")
def ats_account_credentials(company: str, ats_id: str = "") -> dict[str, Any]:
    """The suggested login id + generated password for a company's ATS account — shown to the
    operator at the create-account step (localhost, operator-only)."""
    return ats_accounts.suggested_credentials(company, ats_id)


@router.get("/api/career_search/accounts/next-action")
def ats_account_next_action(company: str, ats_id: str) -> dict[str, Any]:
    """The Account Manager's one-loop decision: create-account vs sign-in leg for this company↔ATS,
    from the account's lifecycle state, plus the recipe + credentials to drive it."""
    return ats_accounts.next_account_action(company, ats_id)


class MarkCreated(BaseModel):
    company: str
    ats_id: str


@router.post("/api/career_search/accounts/mark-created")
def ats_account_mark_created(body: MarkCreated) -> dict[str, Any]:
    """Flip an account from the creation stage to 'active' after it's been created on the ATS —
    so next-action returns SIGN IN instead of CREATE."""
    return ats_accounts.mark_created(body.company, body.ats_id)


class CreateAccountOnSite(BaseModel):
    company: str
    ats_id: str = "workday"
    browser_url: str = "http://127.0.0.1:9322"
    tab_url: Optional[str] = "myworkdayjobs.com"
    tab_id: Optional[str] = None


@router.post("/api/career_search/accounts/create-account")
async def create_account_on_site(body: CreateAccountOnSite) -> dict[str, Any]:
    """OPERATOR-TRIGGERED account creation — the create-account leg of the Account Manager loop, the
    exact analogue of the panel's `/api/accounts/{id}/login` button. Resolves the GENERATED credential
    server-side (username + derived password — never returned), scans the live Workday Create-Account
    form, fills Email/Password/Verify + the acknowledge checkbox (SKIPPING the bot honeypot), clicks
    Create Account, then marks the account 'active'. Returns a STATUS ONLY. Does NOT solve email-verify
    /2FA/captcha — those escalate. If no Create-Account form is visible, it says so.

    BOUNDARY: this runs ONLY when the OPERATOR presses the UI button (like ▶ Login). The agent must
    not call it from its own tool-loop — the agent never creates accounts / enters credentials itself.
    """
    import httpx

    import accounts as accounts_mod
    from settings import settings

    creds = ats_accounts.suggested_credentials(body.company, body.ats_id)
    username, password = creds.get("username"), creds.get("suggested_password")
    if not username or not password:
        raise HTTPException(status_code=400,
                            detail="No generated credential (set ATS_ACCOUNT_PW_SUFFIX in .env).")
    scan_req = {"browser_url": body.browser_url, "tab_url": body.tab_url, "tab_id": body.tab_id}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            scan = (await client.post(f"{settings.capture_server_url}/ax_scan", json=scan_req)).json()
            fields = _match_create_account_fields(scan.get("candidates", []))
            if "password" not in fields or "submit" not in fields:
                return {"ok": False, "status": "no_create_form",
                        "detail": f"No Create-Account form visible (found {sorted(fields)}). Open the "
                                  "ATS 'Create Account' screen first, then press Create account."}

            async def _exec(action_id: str, node_id: int, value: Optional[str] = None) -> None:
                await client.post(f"{settings.capture_server_url}/execute", json={
                    "action_id": action_id, "backend_node_id": node_id, "target_bbox": {},
                    "value": value, "browser_url": body.browser_url, "tab_url": body.tab_url,
                    "tab_id": body.tab_id, "driver": "humanized"})

            if "email" in fields:
                await _exec("type", fields["email"], username)
            await _exec("type", fields["password"], password)
            if "verify" in fields:
                await _exec("type", fields["verify"], password)
            if "acknowledge" in fields:
                await _exec("click", fields["acknowledge"])
            await _exec("click", fields["submit"])
        # Persist the login into the vault + flip to active so future sign-ins resolve it.
        accounts_mod.set_credentials(ats_accounts.ats_account_id(body.company, body.ats_id), username, password)
        ats_accounts.mark_created(body.company, body.ats_id)
        return {"ok": True, "status": "submitted",
                "detail": "Create Account submitted. Any email-verification / 2FA step is yours."}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Create-account driver unreachable: {exc}")
