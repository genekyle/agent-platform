"""Career-search routes — the ATS registry + application preferences.

Thin router (docs/PLAN_main-split.md): the structure lives in the top-level `ats_registry` and
`application_preferences` modules; this just exposes them so the cockpit can render the ATS
grouping (each ATS domain-like, with the companies known to use it) and the operator can read/add
application-preference notes attached to the career-search domain.
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import application_preferences as prefs_lib
import ats_accounts
import ats_registry
import event_log
from db import get_db
from models import ObservedJob, utcnow

router = APIRouter()


async def _cap_post(path: str, payload: dict, timeout: float = 30.0) -> dict:
    """POST to the capture server for the StepRunner's observations. Never raises — same contract
    as session_control's `_capture_post`: a browser that will not answer is an honest
    {ok: false}, which the StepRunner reads as "the eyes were unavailable"."""
    import httpx

    from settings import settings
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{settings.capture_server_url}{path}", json=payload)
            return r.json() or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _account_session_key(company: str, ats_id: str) -> str:
    """A stable per-account key for the transition corpus: account creation is operator-triggered
    and not bound to a training session, but its steps are still steps and still train."""
    import re
    return "account-" + re.sub(r"[^a-z0-9_-]+", "-", f"{company} {ats_id}".lower()).strip("-")


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


# AppVault Create-Account tag JS: MUI inputs carry no stable name/id (except the two password fields),
# so tag Email/First/Last by their FormControl floating-LABEL text → a data-av attribute we can then
# target with /execute's `selector`. Returns which fields got tagged (for diagnostics).
_APPVAULT_TAG_JS = (
    "(()=>{const m={email:/^email/i,first:/first name/i,last:/last name/i};const out={};"
    "document.querySelectorAll('.MuiFormControl-root,.MuiTextField-root').forEach(fc=>{"
    "const l=fc.querySelector('label'),inp=fc.querySelector('input');if(!l||!inp)return;"
    "const t=(l.textContent||'').trim();for(const k in m){if(m[k].test(t)){inp.setAttribute('data-av',k);out[k]=true;}}});"
    "return out;})()"
)


async def _create_appvault_account(body, username: str, password: str) -> dict[str, Any]:
    """AppVault create-account leg (Ahold Delhaize et al.). MUI form: tag Email/First/Last by label,
    fill by selector (passwords have stable ids), accept Terms (a LINK), click Continue.
    Uses the DIRECT driver: this is a form behind an account wall, where insertText registers with
    React/MUI and the humanized driver's per-char cadence both times out and truncates values. The
    humane path is about WHICH control we operate (the real widget, not a URL) — not about typing
    slowly at a form only the operator ever triggers.
    OPERATOR-TRIGGERED only — same boundary as the Workday leg."""
    import asyncio

    import httpx

    import accounts as accounts_mod
    from settings import settings

    first, last = ats_accounts.default_first_name(), ats_accounts.default_last_name()
    if not first or not last:
        return {"ok": False, "status": "no_name",
                "detail": "AppVault needs a name — set ATS_ACCOUNT_FIRST_NAME / ATS_ACCOUNT_LAST_NAME in .env."}
    tab_url = body.tab_url if (body.tab_url and "appvault" in body.tab_url) else "appvault.com"
    base = {"browser_url": body.browser_url, "tab_url": tab_url, "tab_id": body.tab_id}
    cap = settings.capture_server_url
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async def _probe(expr: str, note: str):
                """A page-state read via /probe (was /eval, which left no trace).

                These reads are journaled as PROBE, which is honest and slightly
                uncomfortable: they are recurring production assertions, not discovery, so
                they inflate `probe_share` — the metric that is supposed to fall as
                protocols land. That is the metric working. Each one is a proven check
                living as inline JS in a caller instead of as an endpoint (PRINCIPLES §8),
                and `/scan_required` should absorb them.
                """
                return (await client.post(f"{cap}/probe", json={
                    **base, "expression": expr, "note": note, "ats": "appvault",
                })).json().get("value")

            # Confirm the Create-Account form is up (confirm-password + Continue present).
            on_form = await _probe("!!document.querySelector('#outlined-adornment-re-password') "
                                   "&& /Continue/.test(document.body.innerText)",
                                   "is the AppVault create-account form up?")
            if not on_form:
                event_log.log_event("account", f"AppVault create skipped: no form for {body.company}",
                                    domain="career_search")
                return {"ok": False, "status": "no_create_form",
                        "detail": "AppVault 'Create an Account' form not visible (need Email/Password/confirm/"
                                  "First/Last + Continue). Open it on the AppVault tab first, then press Create account."}
            tagged = await _probe(_APPVAULT_TAG_JS,
                                  "tag AppVault email/first/last by floating-label text") or {}
            # Fail FAST if a label didn't tag: [data-av=…] would then match nothing and the fill would
            # no-op, surfacing later as a vague "didn't advance" — i.e. a SELECTOR-layer break wearing
            # a validation-layer costume. Name the layer here instead of paying to re-diagnose it.
            missing = [k for k in ("email", "first", "last") if not tagged.get(k)]
            if missing:
                event_log.log_event("account", f"AppVault label-tagging failed for {body.company}",
                                    domain="career_search", detail=f"untagged: {missing}")
                return {"ok": False, "status": "fields_not_found",
                        "detail": f"Could not find the AppVault {', '.join(missing)} field(s) by floating-label "
                                  "text — the form's labels likely changed. Nothing was typed. Re-map the "
                                  "labels in APPVAULT_CREATE_ACCOUNT_RECIPE before retrying."}

            async def _type(selector: str, value: str) -> None:
                # CLEAR then DIRECT-type: 'type' APPENDS (would double-fill on a re-run), so clear
                # first. DIRECT (insertText) registers with React/MUI onChange (proven on Workday text
                # fields), ~1-2s/field — avoids the humanized ~15-20s/field 5-field TIMEOUT.
                await client.post(f"{cap}/execute", json={
                    "action_id": "clear", "selector": selector, "target_bbox": {}, **base})
                await client.post(f"{cap}/execute", json={
                    "action_id": "type", "selector": selector, "value": value, "target_bbox": {},
                    "driver": "direct", **base})

            async def _click(role: str, name: str) -> None:
                await client.post(f"{cap}/execute", json={
                    "action_id": "click", "target_role": role, "target_name": name,
                    "target_bbox": {}, **base})

            async def _drive() -> dict[str, Any]:
                await _type("[data-av=email]", username)
                await _type("#outlined-adornment-password", password)
                await _type("#outlined-adornment-re-password", password)
                await _type("[data-av=first]", first)
                await _type("[data-av=last]", last)
                # Terms: the link opens a "Term of Use Policy" MODAL — must click Agree (buttons:
                # Print / Disagree / Agree). Only then does Continue enable (verified live
                # 2026-07-14, Ahold).
                await _click("link", "Click Here to Accept Terms of Use")
                await _click("button", "Agree")
                await _click("button", "Continue")
                # VERIFY it actually submitted before touching the account record — else a stall
                # (a field that didn't validate, terms not agreed) would falsely mark the account
                # 'created'. Success = the create form is gone OR we advanced to email-verification.
                await asyncio.sleep(2.0)
                adv = await _probe(
                    "!document.querySelector('#outlined-adornment-re-password') "
                    "|| /verif|confirm your email|check your email|code (was |has been )?sent/i.test(document.body.innerText)",
                    "did the AppVault create-account form actually submit?")
                return {"ok": bool(adv), "advanced": bool(adv)}

            # THE STEPRUNNER WRAPS THE DRIVE (PLAN_step_runner.md) — credential posture: a
            # password is typed, so `collect=False` (identity-only looks, no artifacts, §4) and
            # the row carries field names, never values. The `advanced` probe stays the claim's
            # authority; the wrapper adds the before/after pair and diff to the corpus.
            import step_runner as sr
            report = await sr.run_step(
                _drive,
                action={"action": "create_account", "ats": "appvault", "company": body.company},
                expect=sr.Expectation(kind="content_changed"),
                capture_post=_cap_post, browser_url=body.browser_url, tab_id=body.tab_id,
                tab_url=tab_url, session_id=_account_session_key(body.company, "appvault"),
                rung_id="account_create", collect=False)
            advanced = report.result["advanced"]
        if not advanced:
            event_log.log_event("account", f"AppVault create did NOT advance: {body.company}",
                                domain="career_search", detail="Continue no-op — account left pending")
            return {"ok": False, "status": "not_advanced",
                    "detail": "Filled the form + agreed to Terms + clicked Continue, but the Create-Account "
                              "form is still showing — it did NOT submit (a field likely didn't validate). "
                              "Account left PENDING (not marked created). Check the form / event log."}
        accounts_mod.set_credentials(ats_accounts.ats_account_id(body.company, body.ats_id), username, password)
        ats_accounts.mark_created(body.company, body.ats_id)
        event_log.log_event("account", f"AppVault create-account submitted: {body.company} (operator-triggered)",
                            domain="career_search", detail=f"tagged {sorted(tagged)}; advanced past create form")
        return {"ok": True, "status": "submitted",
                "detail": "AppVault Create Account submitted + verified it advanced. Any email-verification "
                          "step (code to your Gmail) is yours."}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"AppVault create-account driver unreachable: {exc}")


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
    res = ats_accounts.ensure_account(body.company, body.ats_id, body.login_url or "")
    if res.get("ok"):
        event_log.log_event("account", f"Registered account: {body.company} · {body.ats_id} (pending)",
                            domain="career_search")
    return res


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
    res = ats_accounts.mark_created(body.company, body.ats_id)
    if res.get("ok"):
        event_log.log_event("account", f"Account active: {body.company} · {body.ats_id}",
                            domain="career_search")
    return res


class CreateAccountOnSite(BaseModel):
    company: str
    ats_id: str = "workday"
    browser_url: str = "http://127.0.0.1:9322"
    tab_url: Optional[str] = "myworkdayjobs.com"
    tab_id: Optional[str] = None


@router.post("/api/career_search/accounts/create-account")
async def create_account_on_site(body: CreateAccountOnSite, db: Session = Depends(get_db)) -> dict[str, Any]:
    """OPERATOR-TRIGGERED account creation — the create-account leg of the Account Manager loop, the
    exact analogue of the panel's `/api/accounts/{id}/login` button. Resolves the GENERATED credential
    server-side (username + derived password — never returned), scans the live Workday Create-Account
    form, fills Email/Password/Verify + the acknowledge checkbox (SKIPPING the bot honeypot), clicks
    Create Account, then marks the account 'active'. Returns a STATUS ONLY. Does NOT solve email-verify
    /2FA/captcha — those escalate. If no Create-Account form is visible, it says so.

    BOUNDARY: this runs ONLY when the OPERATOR presses the UI button (like ▶ Login). The agent must
    not call it from its own tool-loop — the agent never creates accounts / enters credentials itself.
    """
    import asyncio

    import httpx

    import accounts as accounts_mod
    import tab_finder
    from settings import settings

    # ALREADY CREATED? Only an account at the 'active' checkpoint (created + verified on-site, via a
    # prior create run or an explicit mark-created) blocks Create — a merely-pending account with
    # creds set can still be created. status=="active" is the single 'fully created' gate (it's what
    # also unlocks Sign In), so the two stay consistent.
    aid = ats_accounts.ats_account_id(body.company, body.ats_id)
    existing = accounts_mod.get_account(aid) or {}
    if existing.get("status") == "active":
        return {"ok": False, "status": "already_exists",
                "detail": (f"A {body.company} · {body.ats_id} account is already created "
                           f"(username {existing.get('username_hint') or '?'}). Use ▶ Sign In, not Create "
                           "— creating it again will hit 'email already registered'.")}

    creds = ats_accounts.suggested_credentials(body.company, body.ats_id)
    username, password = creds.get("username"), creds.get("suggested_password")
    if not username or not password:
        raise HTTPException(status_code=400,
                            detail="No generated credential (set ATS_ACCOUNT_PW_SUFFIX in .env).")

    # Find the RIGHT live tab (fix for the dead 9322 default + multi-tab) and retarget body at it, so
    # BOTH legs (Workday here, AppVault below) drive the tab that actually holds the Create-Account form.
    tgt = tab_finder.resolve_target(db, existing, tab_id=body.tab_id, browser_url=body.browser_url)
    if not tgt["found"]:
        return {"ok": False, "status": "no_target_tab",
                "detail": (f"No live Career-Search tab for this ATS (looked for {tgt['looked_for']}). "
                           f"{tgt['hint']} Open the Create-Account screen there, then retry.")}
    body.browser_url, body.tab_id, body.tab_url = tgt["browser_url"], tgt["tab_id"], None
    scanned_url = (tgt.get("tab") or {}).get("tab_url", "")

    # Per-ATS create-account flow. Workday matches fields by AX name; AppVault's MUI form is nameless
    # (selector/label-based) with a Terms LINK + First/Last + Continue — its own leg.
    if body.ats_id == "appvault":
        return await _create_appvault_account(body, username, password)
    scan_req = {"browser_url": body.browser_url, "tab_url": body.tab_url, "tab_id": body.tab_id}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            scan = (await client.post(f"{settings.capture_server_url}/ax_scan", json=scan_req)).json()
            fields = _match_create_account_fields(scan.get("candidates", []))
            if "password" not in fields or "submit" not in fields:
                errs = scan.get("errors") or []
                event_log.log_event("account", f"Create-account skipped: no form for {body.company} · {body.ats_id}",
                                    domain="career_search", detail=f"matched {sorted(fields)}; tab {scanned_url[:60]}")
                return {"ok": False, "status": "no_create_form",
                        "detail": (f"Found the tab ({scanned_url[:70] or '?'}) but no Create-Account form "
                                   f"(saw {sorted(fields)}; {scan.get('count', 0)} controls). Open the ATS "
                                   f"'Create Account' screen on that tab, then retry."
                                   + (f" [scan: {errs}]" if errs else ""))}

            async def _exec(action_id: str, node_id: int, value: Optional[str] = None,
                            driver: str = "direct") -> None:
                # DIRECT, not humanized. Humanized cadence-types at ~15-20s/field on Workday, so a
                # 3-field form blew the 60s client timeout mid-fill: the creds went in, httpx raised,
                # and the Create Account click NEVER FIRED — surfacing as "driver unreachable" with a
                # filled form on screen (live 2026-07-15, Wellington). insertText registers with
                # Workday's React inputs and is ~1-2s. Bot-safety cadence buys nothing on an ATS form
                # behind an account wall. (The AppVault leg already knew this; the lesson never
                # propagated here — same shape as /select_prompt's open-path never reaching set_distance.)
                await client.post(f"{settings.capture_server_url}/execute", json={
                    "action_id": action_id, "backend_node_id": node_id, "target_bbox": {},
                    "value": value, "browser_url": body.browser_url, "tab_url": body.tab_url,
                    "tab_id": body.tab_id, "driver": driver})

            async def _type(node_id: int, value: str) -> None:
                # CLEAR first — `type` APPENDS, so a retry would double the value (cost us a
                # "0330103301" postal code once).
                await _exec("clear", node_id)
                await _exec("type", node_id, value)

            async def _drive() -> dict[str, Any]:
                if "email" in fields:
                    await _type(fields["email"], username)
                await _type(fields["password"], password)
                if "verify" in fields:
                    await _type(fields["verify"], password)
                if "acknowledge" in fields:
                    await _exec("click", fields["acknowledge"])
                await _exec("click", fields["submit"])

                # VERIFY the form actually submitted before touching the account record. This leg
                # used to mark the account 'active' purely because the click returned — so a
                # stalled form (bad password, unticked ack) would leave a phantom 'active' account
                # with no login behind it.
                await asyncio.sleep(2.0)
                g = (await client.post(f"{settings.capture_server_url}/probe", json={
                    "browser_url": body.browser_url, "tab_url": body.tab_url, "tab_id": body.tab_id,
                    "expression": ("(()=>{const f=document.querySelector('[data-automation-id=password]');"
                                   "const t=document.body.innerText||'';"
                                   "return !f || /verif|check your email|candidate home|my applications/i.test(t);})()"),
                    "note": "did the Workday create-account form actually submit?", "ats": "workday",
                })).json().get("value")
                return {"ok": bool(g), "advanced": bool(g)}

            # THE STEPRUNNER WRAPS THE DRIVE — credential posture (`collect=False`, §4): the row
            # keeps identity and diff, never a typed value. The `gone` probe stays the claim's
            # authority; the pair + verdict land in the transition corpus.
            import step_runner as sr
            report = await sr.run_step(
                _drive,
                action={"action": "create_account", "ats": body.ats_id, "company": body.company},
                expect=sr.Expectation(kind="content_changed"),
                capture_post=_cap_post, browser_url=body.browser_url, tab_id=body.tab_id,
                tab_url=body.tab_url, session_id=_account_session_key(body.company, body.ats_id),
                rung_id="account_create", collect=False)
            gone = report.result["advanced"]
            # If it did NOT advance, is it because the email is ALREADY REGISTERED? That's the common
            # "I think I already have an account" case — surface it distinctly so the operator edits
            # the password + signs in, instead of an opaque "didn't submit".
            already_msg = ""
            if not gone:
                already_msg = ((await client.post(f"{settings.capture_server_url}/probe", json={
                    "browser_url": body.browser_url, "tab_url": body.tab_url, "tab_id": body.tab_id,
                    "expression": ("(()=>{const t=document.body.innerText||'';const m=t.match("
                                   "/[^.\\n]*account already exists[^.\\n]*|[^.\\n]*already (?:have|has) an account[^.\\n]*"
                                   "|[^.\\n]*already (?:in use|registered|associated|been used)[^.\\n]*"
                                   "|[^.\\n]*email[^.\\n]*already[^.\\n]*/i);return m?m[0].trim().slice(0,160):'';})()"),
                    "note": "does the Workday create form show an 'account already exists' error?", "ats": "workday",
                })).json().get("value")) or ""
        if not gone:
            if already_msg:
                # The account DOES exist on the ATS → it's created. Flip to 'active' so Login becomes
                # available (and Create is now blocked); the operator just corrects the password.
                ats_accounts.mark_created(body.company, body.ats_id)
                event_log.log_event("account", f"Create-account: email already registered — {body.company} · {body.ats_id}",
                                    domain="career_search", detail=already_msg[:120])
                return {"ok": False, "status": "already_exists_on_site",
                        "detail": (f"Workday says an account already exists for this email — «{already_msg}». "
                                   "The account is already there, so I marked it CREATED — now use ▶ Sign In "
                                   "(not Create). Set the real password in the Account Manager first if it differs.")}
            event_log.log_event("account", f"Create-account did NOT advance: {body.company} · {body.ats_id}",
                                domain="career_search",
                                detail="submit clicked but the create form is still up — left PENDING")
            return {"ok": False, "status": "not_advanced",
                    "detail": "Filled the form and clicked Create Account, but the form is still showing — "
                              "it did not submit. Account left PENDING (not marked created). Check the tab."}
        # Persist the login into the vault + flip to active so future sign-ins resolve it.
        accounts_mod.set_credentials(ats_accounts.ats_account_id(body.company, body.ats_id), username, password)
        ats_accounts.mark_created(body.company, body.ats_id)
        event_log.log_event("account", f"Create-account submitted: {body.company} · {body.ats_id} (operator-triggered)",
                            domain="career_search", detail=f"filled {sorted(fields)}; verified advanced; marked active")
        return {"ok": True, "status": "submitted",
                "detail": "Create Account submitted + verified it advanced. Any email-verification / 2FA step is yours."}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Create-account driver unreachable: {exc}")


# --- The APPLY EPILOGUE — the seam that closes the loop back to search ---------------------------
# apply_recipe.APPLY_EPILOGUE described this in prose and MCP /close_tab was the primitive, but
# nothing ever WIRED them: every finished apply left an orphan ATS tab and an unrecorded outcome,
# so the loop couldn't tell "applied" from "still open" and the operator cleaned up by hand.
# One call, always the same shape whether we SUBMITTED or ABANDONED at a human-required wall.
class ApplyEpilogue(BaseModel):
    external_id: str                       # the engine's job id (Indeed jk)
    platform: str = "indeed"               # the ENGINE the prospect came from
    status: str = "applied"                # applied | abandoned | skipped
    ats_id: Optional[str] = None           # where it was actually applied (workday, appvault, …)
    tenant_id: Optional[str] = None        # the ATS-side req id (e.g. Workday R94007)
    notes: Optional[str] = None
    # Upsert fields — so an epilogue works even if the prospect was never extracted into the corpus.
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    # Tab hygiene
    browser_url: str = "http://127.0.0.1:9322"
    apply_tab_url: Optional[str] = None    # substring of the ATS tab to CLOSE
    search_tab_url: str = "indeed.com/jobs"  # substring of the search tab to REFOCUS
    close_tab: bool = True


@router.post("/api/career_search/apply/epilogue")
async def apply_epilogue(body: ApplyEpilogue, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Finish ONE prospect: record the outcome, close the apply tab, refocus the search tab.

    Runs on EVERY apply terminal — submitted or abandoned at a human-required wall — so the search
    and apply loop always returns to a clean single-tab search. Recording happens BEFORE the tab is
    closed: if the close fails we still know the outcome, whereas a closed tab with no record is
    unrecoverable. Only `status='applied'` stamps applied_at ([[apply = done means SUBMITTED]]).
    """
    import httpx

    from settings import settings

    job_id = f"{body.platform}:{body.external_id}"
    row = db.get(ObservedJob, job_id)
    if row is None:
        row = ObservedJob(job_id=job_id, platform=body.platform, external_id=body.external_id)
        db.add(row)
    for attr in ("title", "company", "location", "url"):
        val = getattr(body, attr)
        if val:
            setattr(row, attr, val)
    row.application_status = body.status
    if body.ats_id:
        row.application_platform = body.ats_id
        row.apply_type = "company_site" if body.ats_id != "indeed_quick_apply" else "quick_apply"
    if body.tenant_id:
        row.tenant_id = body.tenant_id
    if body.notes:
        row.notes = body.notes
    if body.status == "applied" and row.applied_at is None:
        row.applied_at = utcnow()
    db.commit()
    db.refresh(row)

    closed: dict[str, Any] = {"attempted": False}
    if body.close_tab and body.apply_tab_url:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(f"{settings.capture_server_url}/close_tab", json={
                    "browser_url": body.browser_url, "tab_url": body.apply_tab_url,
                    "focus_tab_url": body.search_tab_url})
                closed = {"attempted": True, **(r.json() if r.status_code == 200 else {"ok": False})}
        except httpx.HTTPError as exc:          # tab hygiene must never lose the RECORD
            closed = {"attempted": True, "ok": False, "detail": f"close_tab unreachable: {exc}"}

    event_log.log_event("apply", f"{body.status}: {row.title or body.external_id} · {row.company or ''}",
                        domain="career_search",
                        detail=f"ats={body.ats_id or '?'} req={body.tenant_id or '?'} "
                               f"tab_closed={closed.get('ok', False)}")
    return {"ok": True, "job_id": job_id, "status": row.application_status,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
            "tab": closed}
