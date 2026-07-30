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
from typing import Callable, Optional

from controller import unexpected

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


# --- tab-level staleness ---------------------------------------------------------------------
#: Markers the capture server emits when the CDP target we pinned is GONE (an SSO redirect spawned
#: a new target, an OOPIF iframe form was replaced after submit, the operator reloaded the Sign-In
#: screen). Found live 2026-07-19: a stale tab does NOT raise and does NOT return an error from
#: /ax_scan — `propose_ax_candidates` swallows the discovery failure into `errors[]`, so the reply
#: is a *successful* scan with zero candidates, indistinguishable from "the form isn't open". That
#: is precisely how login died silently. We look for the reason instead of inferring from silence.
_STALE_TAB_MARKERS = ("no target with id", "target_discovery", "no attachable page target")

_STALE_TAB_REASON = ("The Sign-In tab I was driving no longer exists — it navigated away, was "
                     "reloaded, or was closed. Reopen the ATS Sign In screen in the Career-Search "
                     "browser, then press Login again.")


def _mentions_stale_tab(payload) -> bool:
    """True when a capture-server reply says the tab we addressed is gone. Reads the places the
    reason actually lands: `/ax_scan`'s `errors[]`, and the `detail` of the `ok:false` /
    `outcome:"error"` bodies returned by `/probe`, `/auth_state` and `/execute`."""
    if not isinstance(payload, dict):
        return False
    blobs: list[str] = []
    errors = payload.get("errors")
    if isinstance(errors, (list, tuple)):
        blobs.extend(str(e) for e in errors)
    for key in ("detail", "error", "message"):
        value = payload.get(key)
        if value:
            blobs.append(str(value))
    haystack = " ".join(blobs).lower()
    return any(marker in haystack for marker in _STALE_TAB_MARKERS)


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


#: Controls that lead TOWARD a sign-in, ordered most-direct first. Distinct from `find_login_fields`
#: on purpose: those find the credential inputs to fill, these find the way IN — the clicks a human
#: makes before any secret is involved, which is exactly the part the agent may own.
#:
#: `account` earns its place from a live scan (2026-07-24, session 19): Indeed's logged-out home
#: exposes NO "Sign in" control to AX at all — 173 candidates, and the only one on this list was a
#: button named "Account". Login sits behind a menu widget, so the way in is a two-step reveal
#: (`project_widget_protocol_layer`: AX finds elements, not widgets). A matcher that only looked
#: for "sign in" would report "no way to log in" on the page whose entire job is logging you in.
#: (hint, why, alternate). `alternate` marks a route AROUND the credential rather than through it —
#: SSO, an emailed code. The distinction only matters when a password form is already on screen: a
#: control named "Sign in" beside a password box IS that form's submit, and offering it as a "way
#: in" would have the agent submitting an empty credential form. "Continue with google" on the same
#: screen is a different thing entirely — a click that hands off to another site's own window.
#: LinkedIn's logged-out /jobs page carries BOTH, which is what forced the distinction.
SIGNIN_ENTRY_HINTS = (
    ("sign in with a code", "the emailed sign-in code (Indeed's fallback when SSO is not wanted)", True),
    ("continue with google", "Google SSO — opens Google's own window", True),
    ("continue with apple", "Apple SSO — opens Apple's own window", True),
    ("sign in with google", "Google SSO — opens Google's own window", True),
    ("sign in with apple", "Apple SSO — opens Apple's own window", True),
    ("google", "Google SSO — opens Google's own window", True),
    ("apple", "Apple SSO — opens Apple's own window", True),
    ("sign in", "the sign-in control", False),
    ("log in", "the log-in control", False),
    ("sign-in", "the sign-in control", False),
    ("account", "the account menu — sign-in usually hides behind it", False),
)


#: Names that CONTAIN a vendor word but are not a way in — the footer of any Google-adjacent page
#: is full of them. Measured live on Google's own sign-in popup, where the bare "google" hint
#: matched "Google Terms of Service" and "Open Google Account Help Center" and offered both as
#: routes into the account (2026-07-27).
_NOT_AN_ENTRY = ("terms of service", "privacy", "policy", "help cent", "help center", "learn more",
                 "about ", "cookie", "guidelines", "copyright", "opens in a new window",
                 "create account", "join now", "sign up", "forgot")


def _looks_like_an_entry(name: str) -> bool:
    """A control's NAME has to read like a way in, not like the legal footer beside it."""
    n = (name or "").strip().lower()
    return bool(n) and not any(bad in n for bad in _NOT_AN_ENTRY)


def find_signin_entries(candidates: list[dict], *, alternates_only: bool = False) -> list[dict]:
    """Every control that plausibly leads toward signing in, best-first.

    These are CLICKS, never credentials, which is what makes them safe for the agent to drive: the
    hard boundary is that we never type a password or clear a 2FA challenge, not that we refuse to
    open the login page. Returns [{name, role, backend_node_id, why}] — deduped by name so a page
    listing "Sign in" three times offers one option.

    `alternates_only` restricts the result to routes AROUND the credential (SSO, emailed code).
    Pass it whenever a password form is already on screen: there, the generic "Sign in" match is
    that form's own submit button, and clicking it submits an empty credential rather than
    offering a way in.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for hint, why, alternate in SIGNIN_ENTRY_HINTS:
        if alternates_only and not alternate:
            continue
        for c in candidates:
            role = (c.get("role") or "").lower()
            name = (c.get("name") or "").strip()
            if role not in ("link", "button", "menuitem") or not name:
                continue
            if hint not in name.lower() or name.lower() in seen:
                continue
            if not _looks_like_an_entry(name):
                continue
            if c.get("backend_node_id") is None:
                continue
            seen.add(name.lower())
            out.append({"name": name, "role": role, "alternate": alternate,
                        "backend_node_id": c.get("backend_node_id"), "why": why})
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


def _deterministic_policy(state: LoginState, candidates: list[dict],
                          *, has_creds: bool = True) -> LoginStep:
    """The safe fallback policy (also the baseline the Haiku reasoner is compared against). Encodes the
    gray-area handling as rules; the reasoner earns its keep on the genuinely ambiguous screens."""
    if state == "signin_form":
        # A PASSWORD FORM ON SCREEN IS NOT A REASON TO FILL IT. This branch used to return
        # `fill_credentials` unconditionally — it never looked at whether we HAVE a credential, and
        # never at the SSO button sitting beside the form. Measured live on LinkedIn's logged-out
        # /jobs page 2026-07-30: no stored password (this account signs in with Google), and the
        # policy still answered "fill the stored credentials and submit". Downstream that is either
        # a no-op or an empty submit against a real account, and neither is a login.
        #
        # `find_signin_entries(alternates_only=True)` exists for exactly this and was already
        # measured on this exact page — its own note says LinkedIn's logged-out /jobs carries BOTH a
        # credential form and "Continue with google". An ALTERNATE is a route AROUND the credential,
        # which is the only route available when we hold none.
        if has_creds and _has_password_field(candidates):
            return LoginStep(state, "fill_credentials", rung="recipe",
                             rationale="a sign-in form (email + password) is visible and we hold a "
                                       "stored credential — fill it and submit")
        alternates = find_signin_entries(candidates, alternates_only=True)
        if alternates:
            entry = alternates[0]
            return LoginStep(state, "click", control=entry, rung="recipe",
                             rationale=("no stored credential for this site, and the page offers a "
                                        f"route around one: {entry['name']!r} — {entry['why']}"))
        return LoginStep(state, "escalate", escalate=True, escalate_status="no_credentials",
                         rung="recipe",
                         rationale="a sign-in form is visible, we hold no credential for it, and "
                                   "the page offers no SSO / emailed-code alternative",
                         reason="This site wants a password I do not have, and there is no "
                                "'Continue with…' route beside it. Sign in on your side, or add "
                                "the credential in the Account Manager. I never type passwords.")
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


def _step_from_reasoner(state: LoginState, r: dict, candidates: list[dict],
                        *, has_creds: bool = True) -> Optional[LoginStep]:
    """Turn a reasoner's JSON into a validated LoginStep — with safety: a reasoner cannot invent a
    fill on a form with no password field OR with no credential to put in it, and an unresolved
    control name falls back rather than clicking nothing."""
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
        if not has_creds:
            return None  # …nor fill it from nothing. Same rail as the deterministic policy.
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
            step = _step_from_reasoner(state, reasoner(_observation(state, candidates, page_text, has_creds)),
                                       candidates, has_creds=has_creds)
        except Exception as exc:  # noqa: BLE001 — a reasoner failure falls back, never crashes login
            logger.warning("login reasoner error: %s", exc)
            step = None
        if step is not None:
            return step
    return _deterministic_policy(state, candidates, has_creds=has_creds)


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
    status: str                       # authenticated | captcha | mfa | bad_credentials | account_exists | unknown_state | stale_tab | max_steps
    steps: int = 0
    detail: str = ""
    trail: list[dict] = field(default_factory=list)   # per-step {state, action, rationale} — the reasoning trail


async def run_login(*, client, capture_url: str, browser_url: str, tab_id: str,
                    username: str, password: str, reasoner: Optional[Callable[[dict], dict]] = None,
                    journal: Optional[Callable[[LoginStep, str, int], None]] = None,
                    re_resolve: Optional[Callable[[], Optional[dict]]] = None,
                    max_steps: int = 5) -> LoginResult:
    """observe → classify → reason → act → verify, until authenticated / escalation / max steps. The
    reasoning is journaled per step (the Open Brain). `client` is an httpx.AsyncClient.

    `re_resolve` is the BACKUP PLAN for a stale tab: called at most once, it re-finds the account's
    live ATS tab (tab_finder) and returns `{"browser_url", "tab_id"}` — or None if it's really gone.
    It is injected rather than imported so this module stays free of the DB/tab_finder and remains
    unit-testable with a fake. Without it a stale tab still escalates honestly; it just can't recover.
    """
    import asyncio

    addr = {"browser_url": browser_url, "tab_id": tab_id}
    trail: list[dict] = []
    attempted_creds = False
    stale_retry_used = False

    def _record(step_obj: LoginStep, state: str, idx: int) -> None:
        trail.append({"step": idx, "state": state, "action": step_obj.action,
                      "rationale": step_obj.rationale})
        if journal is not None:
            journal(step_obj, state, idx)

    def _recover_from_stale(idx: int) -> Optional[LoginResult]:
        """The unexpected-state policy applied to a dead tab (controller/unexpected.py): RE_OBSERVE
        → re-resolve the tab ONCE and carry on; ESCALATE → stop and say so plainly. Returns a
        LoginResult to stop, or None to continue the loop against the freshly-resolved tab."""
        nonlocal stale_retry_used
        if unexpected.respond(unexpected.STALE_TAB,
                              already_retried=stale_retry_used) is unexpected.Response.RE_OBSERVE:
            fresh = re_resolve() if re_resolve is not None else None
            if fresh and fresh.get("tab_id"):
                stale_retry_used = True
                addr["browser_url"] = fresh.get("browser_url") or addr["browser_url"]
                addr["tab_id"] = fresh["tab_id"]
                _record(LoginStep("stale_tab", "re_observe", rung="recipe",
                                  rationale="the tab I was driving was gone (stale CDP target) — "
                                            "re-resolved the account's live ATS tab and carried on"),
                        "stale_tab", idx)
                return None
        step_obj = LoginStep("stale_tab", "escalate", escalate=True, escalate_status="stale_tab",
                             rung="recipe",
                             rationale="the tab being driven no longer exists and could not be re-found",
                             reason=_STALE_TAB_REASON)
        _record(step_obj, "stale_tab", idx)
        return LoginResult(False, "stale_tab", idx + 1, _STALE_TAB_REASON, trail)

    async def _post(path: str, payload: Optional[dict] = None) -> dict:
        """POST to the capture server and return the parsed body. A stale tab comes back as a
        200 with the reason inside, so the BODY is the signal — never discard it."""
        try:
            r = await client.post(f"{capture_url}{path}", json={**addr, **(payload or {})})
            return r.json() or {}
        except Exception:  # noqa: BLE001 — a transport/JSON failure is not a staleness signal
            return {}

    for step in range(max_steps):
        scan = await _post("/ax_scan")
        probe = await _post("/probe", {"expression": "(document.body.innerText||'').slice(0,3000)",
                                       "note": "login page text", "ats": "login"})
        auth = await _post("/auth_state")

        # Check the tab BEFORE reading the page: a stale tab yields an empty scan that otherwise
        # reads as "no form here", and re-resolving is the backup plan rather than a guess.
        if any(_mentions_stale_tab(r) for r in (scan, probe, auth)):
            stop = _recover_from_stale(step)
            if stop is not None:
                return stop
            continue

        candidates = scan.get("candidates", [])
        page_text = str(probe.get("value") or "")
        logged_in = auth.get("logged_in")

        state = classify_login_state(candidates, page_text, logged_in)
        decision = reason_step(state, candidates, page_text, has_creds=bool(username and password),
                               attempted_creds=attempted_creds, reasoner=reasoner)
        _record(decision, state, step)

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

            async def _exec(action_id, node_id, value=None) -> dict:
                return await _post("/execute", {"action_id": action_id, "backend_node_id": node_id,
                                                "target_bbox": {}, "value": value, "driver": "humanized"})

            replies = []
            if "email" in fields:
                replies.append(await _exec("clear", fields["email"]))
                replies.append(await _exec("type", fields["email"], username))
            replies.append(await _exec("clear", fields["password"]))
            replies.append(await _exec("type", fields["password"], password))
            replies.append(await _exec("click", fields["submit"]))
            if any(_mentions_stale_tab(r) for r in replies):
                # The tab died mid-fill, so the credentials never landed. Do NOT set
                # attempted_creds: that would make the next pass report bad_credentials and tell
                # the operator their stored password is wrong when the tab simply vanished.
                stop = _recover_from_stale(step)
                if stop is not None:
                    return stop
                continue
            attempted_creds = True
        elif decision.action == "click" and decision.control:
            reply = await _post("/execute", {"action_id": "click", "target_bbox": {},
                                             "backend_node_id": decision.control["backend_node_id"],
                                             "driver": "humanized"})
            if _mentions_stale_tab(reply):
                stop = _recover_from_stale(step)
                if stop is not None:
                    return stop
                continue

        await asyncio.sleep(1.4)  # settle before re-observing

    return LoginResult(False, "max_steps", max_steps,
                       f"did not reach the signed-in state in {max_steps} steps — handing to you.", trail)
