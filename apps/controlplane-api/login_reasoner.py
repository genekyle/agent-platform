"""Reasoning-driven ATS login — a bounded observe → classify → reason → act → verify loop whose only
goal is reaching the AUTHENTICATED state.

Why reasoning and not a recipe (operator directive 2026-07-19): login is the ideal ENCLOSED space
for reasoning — one clear goal, few steps, but gray areas a fixed recipe can't survive:
  * an "account already exists" banner means SIGN IN, not create;
  * a wrong-password error must ESCALATE, not retry;
  * MFA / verification codes / captcha are ALWAYS the human's;
  * a create-vs-signin fork, an SSO redirect, a stale form.
The old login path just matched fields → filled → clicked submit → declared "submitted" with NO
verification of the landed state — which is exactly why it thought a create succeeded when the account
already existed. Here we CLASSIFY the true state each step and REASON the next action, verify we
actually reached authenticated, and escalate honestly.

Credential boundary: the reasoner NEVER sees the password. The loop resolves it server-side (vault)
and the driver types it; the reasoner only chooses the next ACTION from an observation. And it fills
credentials AT MOST ONCE — an error after that is a wrong password, which escalates (never a retry
loop against someone's real account).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("login_reasoner")

# --- page-text markers (lowercased). Precedence matters; see classify_login_state. ----------------
_AUTH = ("sign out", "log out", "logout", "my applications", "candidate home", "my account",
         "welcome back", "you are signed in", "your applications")
_MFA = ("verification code", "two-step", "two factor", "two-factor", "authenticator",
        "code we sent", "code we've sent", "enter the code", "one-time", "otp", "6-digit")
_CAPTCHA = ("recaptcha", "hcaptcha", "i'm not a robot", "verify you are human", "are you a robot",
            "captcha")
_EXISTS = ("account already exists", "already have an account", "already in use", "already registered",
           "already associated", "email is already", "an account with this email", "already been used")
_ERROR = ("incorrect", "invalid", "wrong password", "does not match", "doesn't match", "couldn't find",
          "could not find", "no account", "authentication failed", "password you entered",
          "unable to sign", "check your", "not recognized")

# States: authenticated | captcha | mfa | account_exists | login_error | signin_form | create_form | unknown
LoginState = str

# Actions: done | fill_credentials | click | escalate
ACTIONS = ("done", "fill_credentials", "click", "escalate")


def _has(text: str, subs) -> bool:
    return any(s in text for s in subs)


def _named(candidates) -> list[dict]:
    return [c for c in candidates if (c.get("name") or "").strip()]


def _has_password_field(candidates) -> bool:
    return any((c.get("role") or "").lower() in ("textbox", "searchbox")
               and "password" in (c.get("name") or "").lower() for c in candidates)


def _has_verify_field(candidates) -> bool:
    return any("verify" in (c.get("name") or "").lower() or "confirm password" in (c.get("name") or "").lower()
               or "re-enter" in (c.get("name") or "").lower() for c in candidates)


def classify_login_state(candidates: list[dict], page_text: str, logged_in: Optional[bool] = None) -> LoginState:
    """The true login state, by strongest-signal-first precedence. This is the intelligence the old
    `gone` probe lacked: it tells 'authenticated' apart from 'account already exists' apart from a
    'wrong password' error apart from a plain sign-in form — so we never mistake one for another."""
    t = (page_text or "").lower()
    if logged_in or _has(t, _AUTH):
        return "authenticated"
    if _has(t, _CAPTCHA):
        return "captcha"
    if _has(t, _MFA):
        return "mfa"
    if _has(t, _EXISTS):
        return "account_exists"
    has_pw = _has_password_field(candidates)
    if _has(t, _ERROR) and has_pw:
        return "login_error"
    if has_pw and _has_verify_field(candidates):
        return "create_form"
    if has_pw:
        return "signin_form"
    return "unknown"


# --- control finders --------------------------------------------------------------------------------
def find_login_fields(candidates: list[dict]) -> dict:
    """{email, password, submit} backend_node_ids from AX candidates. Skips the create-only 'verify'
    field and the honeypot inputs. Submit is a sign-in-ish button if named, ELSE the last real button
    on the form — a rigid 'name must say Sign In' matcher is exactly what broke on Workday, whose
    submit's accessible name varied (found email+password but 'no submit', live 2026-07-19)."""
    out: dict = {}
    buttons: list[tuple] = []    # (nid, name) for every real button, in order
    for c in candidates:
        role = (c.get("role") or "").lower()
        name = (c.get("name") or "").lower()
        nid = c.get("backend_node_id")
        if nid is None:
            continue
        if role in ("textbox", "searchbox") and "robot" not in name and "website" not in name:
            if "email" in name and "email" not in out:
                out["email"] = nid
            elif "password" in name and "verify" not in name and "confirm" not in name and "password" not in out:
                out["password"] = nid
        elif role == "button" and "robot" not in name:
            buttons.append((nid, name))
    named = [nid for nid, name in buttons
             if any(k in name for k in ("sign in", "log in", "login", "submit", "continue", "next", "go"))]
    if named:
        out["submit"] = named[-1]
    elif buttons:
        out["submit"] = buttons[-1][0]      # the primary on a login form is almost always the last button
    return out


def find_control(candidates: list[dict], substrs, roles=("link", "button")) -> Optional[dict]:
    """The first candidate whose name contains any of `substrs` and whose role is in `roles`.
    Returns {name, backend_node_id, role} or None."""
    for c in candidates:
        role = (c.get("role") or "").lower()
        name = (c.get("name") or "").lower()
        if role in roles and any(s in name for s in substrs):
            return {"name": c.get("name"), "backend_node_id": c.get("backend_node_id"), "role": role}
    return None


# --- the reasoned step -----------------------------------------------------------------------------
@dataclass
class LoginStep:
    state: LoginState
    action: str                       # done | fill_credentials | click | escalate
    rationale: str
    control: Optional[dict] = None    # {name, backend_node_id} for action == click
    escalate: bool = False
    escalate_status: str = ""         # captcha | mfa | bad_credentials | account_exists | unknown_state | ...
    reason: str = ""                  # operator-facing escalation reason
    rung: str = "model"               # model (reasoned) | recipe (deterministic) — valid journal rungs


def _deterministic_policy(state: LoginState, candidates: list[dict]) -> LoginStep:
    """The safe fallback policy (also the baseline the Haiku reasoner is compared against). Encodes the
    gray-area handling as rules; the reasoner earns its keep on the genuinely ambiguous screens."""
    if state == "signin_form":
        return LoginStep(state, "fill_credentials", rung="recipe",
                         rationale="a sign-in form (email + password) is visible — fill the stored credentials and submit")
    if state in ("account_exists", "create_form"):
        ctrl = find_control(candidates, ("sign in", "log in", "already have", "sign-in"))
        if ctrl and ctrl.get("backend_node_id") is not None:
            return LoginStep(state, "click", control=ctrl, rung="recipe",
                             rationale=f"{state}: the account is already there — switch to the sign-in form via '{ctrl['name']}'")
        return LoginStep(state, "escalate", escalate=True, escalate_status="account_exists", rung="recipe",
                         rationale="account already exists; no Sign In control found",
                         reason="This email already has an account, but I can't find the Sign In control to switch to it. "
                                "Sign in on your side (or fix the password), then mark it created.")
    return LoginStep(state, "escalate", escalate=True, escalate_status="unknown_state", rung="recipe",
                     rationale=f"unrecognized login screen ({state})",
                     reason=f"I can't confidently classify this login screen ({state}) — handing it to you rather than guessing.")


def _step_from_reasoner(state: LoginState, r: dict, candidates: list[dict]) -> Optional[LoginStep]:
    """Turn a reasoner's JSON into a validated LoginStep — with safety: a reasoner cannot invent a
    fill on a form with no password field, and an unresolved control name falls back rather than
    clicking nothing."""
    action = str(r.get("action") or "").strip()
    rationale = str(r.get("rationale") or "")[:200] or f"reasoned action for {state}"
    if action == "done":
        return LoginStep(state, "done", rationale=rationale)
    if action == "escalate":
        return LoginStep(state, "escalate", escalate=True, escalate_status="reasoner_escalate",
                         rationale=rationale, reason=str(r.get("escalate_reason") or rationale)[:200])
    if action == "fill_credentials":
        if not _has_password_field(candidates):
            return None  # can't fill what isn't there — fall back
        return LoginStep(state, "fill_credentials", rationale=rationale)
    if action == "click":
        want = str(r.get("control") or "").lower()
        ctrl = None
        for c in candidates:
            if want and want in (c.get("name") or "").lower() and c.get("backend_node_id") is not None:
                ctrl = {"name": c.get("name"), "backend_node_id": c.get("backend_node_id")}
                break
        if ctrl:
            return LoginStep(state, "click", control=ctrl, rationale=rationale)
        return None  # named control not found — fall back
    return None


def reason_step(state: LoginState, candidates: list[dict], page_text: str, *, has_creds: bool,
                attempted_creds: bool, reasoner: Optional[Callable[[dict], dict]] = None) -> LoginStep:
    """Choose the next action toward 'authenticated'. HARD SAFETY RAILS first (never reasoned around):
    captcha/MFA/error/attempted-and-still-not-in → escalate. Otherwise REASON the action (Haiku if
    given), with the deterministic policy as the validated fallback."""
    if state == "authenticated":
        return LoginStep(state, "done", rationale="the page shows an authenticated / home state — signed in")
    if state == "captcha":
        return LoginStep(state, "escalate", escalate=True, escalate_status="captcha",
                         rationale="a captcha is present", reason="A captcha is blocking sign-in — you solve it (never auto-solved).")
    if state == "mfa":
        return LoginStep(state, "escalate", escalate=True, escalate_status="mfa",
                         rationale="an MFA / verification-code prompt is present",
                         reason="Sign-in needs a 2FA / verification code — that step is yours.")
    if state == "login_error" or attempted_creds:
        return LoginStep(state, "escalate", escalate=True, escalate_status="bad_credentials",
                         rationale="the ATS rejected the credentials",
                         reason="The ATS rejected the sign-in — the stored password looks wrong. Fix it in the "
                                "Account Manager, then retry. (Not retrying automatically against your real account.)")
    if reasoner is not None:
        try:
            step = _step_from_reasoner(state, reasoner(_observation(state, candidates, page_text, has_creds)), candidates)
        except Exception as exc:  # noqa: BLE001 — a reasoner failure falls back, never crashes login
            logger.warning("login reasoner error: %s", exc)
            step = None
        if step is not None:
            return step
    return _deterministic_policy(state, candidates)


def _observation(state, candidates, page_text, has_creds) -> dict:
    """The compact, PASSWORD-FREE observation handed to the reasoner."""
    return {
        "state": state, "goal": "reach the authenticated (signed-in) state", "has_stored_credentials": has_creds,
        "controls": [{"role": c.get("role"), "name": (c.get("name") or "")[:60]}
                     for c in _named(candidates)][:25],
        "page_excerpt": (page_text or "")[:600],
    }


# --- the Haiku login reasoner (optional; injected) -------------------------------------------------
_LOGIN_SYSTEM = (
    "You are the LOGIN reasoner for an ATS (Workday, Greenhouse, iCIMS, ...). GOAL: reach the "
    "AUTHENTICATED (signed-in) state in the fewest, safest steps. You never see or type the password — "
    "you choose the next ACTION and the driver fills credentials from the vault.\n"
    "Actions: fill_credentials (a sign-in form with email+password is visible → fill+submit); "
    "click (click a visible control by its exact name, e.g. a 'Sign in' link to leave a create/SSO "
    "screen); done (already signed in); escalate (hand to the human).\n"
    "ALWAYS escalate for MFA / verification codes / captcha, for a wrong-password / invalid error "
    "(never retry), and whenever unsure. An 'account already exists' banner means the account IS "
    "there — do NOT create; click 'Sign in' if present, else escalate. Fewest safe steps."
)

_LOGIN_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "control": {"type": "string"},
        "rationale": {"type": "string"},
        "escalate_reason": {"type": "string"},
    },
    "required": ["action", "rationale"],
    "additionalProperties": False,
}


def haiku_login_reasoner(*, budget_limit: Optional[float] = None) -> Callable[[dict], dict]:
    """A reasoner backed by Haiku (budget-gated). Returns an escalate action on any failure — never
    a raise, never a guess. Injectable so tests + the deterministic fallback need no network."""
    def _reason(observation: dict) -> dict:
        import anthropic_usage
        try:
            anthropic_usage.enforce_budget(budget_limit)
        except Exception as exc:  # noqa: BLE001
            return {"action": "escalate", "rationale": "budget gate", "escalate_reason": str(exc)}
        try:
            import anthropic
            user = "Observation:\n" + json.dumps(observation)[:3500] + "\n\nEmit one login action as JSON."
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model="claude-haiku-4-5", max_tokens=250,
                system=[{"type": "text", "text": _LOGIN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
                output_config={"format": {"type": "json_schema", "schema": _LOGIN_SCHEMA}},
            )
            anthropic_usage.record_from_response(resp, purpose="ats_login",
                                                 meta={"state": observation.get("state")})
            text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "{}")
            return json.loads(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("login reasoner call failed: %s", exc)
            return {"action": "escalate", "rationale": "reasoner error", "escalate_reason": str(exc)}
    return _reason


# --- the loop --------------------------------------------------------------------------------------
@dataclass
class LoginResult:
    ok: bool
    status: str                       # authenticated | captcha | mfa | bad_credentials | account_exists | unknown_state | max_steps
    steps: int = 0
    detail: str = ""
    trail: list[dict] = field(default_factory=list)   # per-step {state, action, rationale} — the reasoning trail


async def run_login(*, client, capture_url: str, browser_url: str, tab_id: str,
                    username: str, password: str, reasoner: Optional[Callable[[dict], dict]] = None,
                    journal: Optional[Callable[[LoginStep, str, int], None]] = None,
                    max_steps: int = 5) -> LoginResult:
    """observe → classify → reason → act → verify, until authenticated / escalation / max steps. The
    reasoning is journaled per step (the Open Brain). `client` is an httpx.AsyncClient."""
    import asyncio

    addr = {"browser_url": browser_url, "tab_id": tab_id}
    trail: list[dict] = []
    attempted_creds = False

    async def _probe_text() -> str:
        try:
            r = (await client.post(f"{capture_url}/probe", json={
                **addr, "expression": "(document.body.innerText||'').slice(0,3000)",
                "note": "login page text", "ats": "login"})).json()
            return str(r.get("value") or "")
        except Exception:  # noqa: BLE001
            return ""

    for step in range(max_steps):
        scan = (await client.post(f"{capture_url}/ax_scan", json=addr)).json()
        candidates = scan.get("candidates", [])
        page_text = await _probe_text()
        try:
            auth = (await client.post(f"{capture_url}/auth_state", json=addr)).json()
            logged_in = auth.get("logged_in")
        except Exception:  # noqa: BLE001
            logged_in = None

        state = classify_login_state(candidates, page_text, logged_in)
        decision = reason_step(state, candidates, page_text, has_creds=bool(username and password),
                               attempted_creds=attempted_creds, reasoner=reasoner)
        trail.append({"step": step, "state": state, "action": decision.action, "rationale": decision.rationale})
        if journal is not None:
            journal(decision, state, step)

        if state == "authenticated" or decision.action == "done":
            return LoginResult(True, "authenticated", step + 1, "signed in", trail)
        if decision.escalate:
            return LoginResult(False, decision.escalate_status or "escalated", step + 1, decision.reason, trail)

        # ACT
        if decision.action == "fill_credentials":
            fields = find_login_fields(candidates)
            if "password" not in fields or "submit" not in fields:
                return LoginResult(False, "no_login_form", step + 1,
                                   f"expected a sign-in form but couldn't address it (found {sorted(fields)}).", trail)

            async def _exec(action_id, node_id, value=None):
                await client.post(f"{capture_url}/execute", json={
                    **addr, "action_id": action_id, "backend_node_id": node_id, "target_bbox": {},
                    "value": value, "driver": "humanized"})

            if "email" in fields:
                await _exec("clear", fields["email"]); await _exec("type", fields["email"], username)
            await _exec("clear", fields["password"]); await _exec("type", fields["password"], password)
            await _exec("click", fields["submit"])
            attempted_creds = True
        elif decision.action == "click" and decision.control:
            await client.post(f"{capture_url}/execute", json={
                **addr, "action_id": "click", "backend_node_id": decision.control["backend_node_id"],
                "target_bbox": {}, "driver": "humanized"})

        await asyncio.sleep(1.4)  # settle before re-observing

    return LoginResult(False, "max_steps", max_steps,
                       f"did not reach the signed-in state in {max_steps} steps — handing to you.", trail)
