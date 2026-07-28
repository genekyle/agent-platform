"""Google as an IDENTITY, separate from Google as a set of surfaces.

Two things were tangled together and the LinkedIn drive pulled them apart (session #22, live
2026-07-27). They look similar and they are not the same:

  * **The Google account / identity** — the sign-in screens, the account chooser, the OAuth consent
    screen. Shared by every Google surface AND by every third-party site that offers "Continue with
    Google". Lives on `accounts.google.com`, usually in a POPUP window owned by whoever asked.
    That is this module.
  * **A Google surface** — Gmail's inbox, a thread, Calendar. Its own domain, its own recipe, its
    own training data. That is `gmail_recipe` (and the ones after it).

Keeping them apart matters because the CALLER is usually neither. LinkedIn's login is a LinkedIn
process — its own logged-out wall, its own "Continue with google" button, its own signed-in check —
that happens to route through Google's identity for one step. Modelling that step as "LinkedIn's
login screen #3" would duplicate Google's flow into every domain that offers SSO, and each copy
would learn the boundary separately. Modelling it here means the next domain gets it for free.

THE BOUNDARY IS THE STATE, NOT THE HOST. This is the correction the live drive forced. "Never drive
accounts.google.com" is too blunt: it refuses the very thing that makes SSO one click. On that one
host we meet three genuinely different asks, and they deserve three different answers:

  * `AUTO`     — the ACCOUNT CHOOSER and the ACCOUNT ADDRESS. Picking which of your own signed-in
                 accounts to use is a tile click; typing the address is typing a username that is
                 already a display hint here and already printed on every tile. Neither is a
                 secret, and refusing them turns a one-click login into a human interruption.
  * `APPROVAL` — the OAUTH CONSENT screen. This grants a third party access to the account. It is
                 reversible but consequential, so it is gated the same way applying and publishing
                 are: the operator says yes, per instance.
  * `HUMAN`    — the SECRET screens: password, 2FA, captcha, "verify it's you", and any refusal.
                 Never ours, on any site, for any reason. LEARNINGS 2026-07-09 put the line in
                 exactly this place — "everything up to and after Google's auth we drive; the
                 password + 2FA keystrokes are the deliberate hand-off" — because that credential
                 cascades everywhere if it is lost, on the most bot-fingerprinted page on the web.

`gmail_recipe.map_url_to_state` still answers for these URLs and still returns the same ids — it
predates this split and other callers read it. This module is the AUTHORITY on what those states
MEAN and what may be done about them; it does not fork the vocabulary.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: Hosts that ARE the identity provider. A window on one of these is Google asking who you are —
#: whoever opened it. Used to spot the SSO popup among a session's tabs.
SSO_HOSTS: tuple[str, ...] = ("accounts.google.com", "accounts.youtube.com")

# --- states -----------------------------------------------------------------------------------
#: Ids are SHARED with gmail_recipe/seed.py on purpose: the same screen must not have two names
#: depending on who looked at it, or the corpus splits and neither half trains.
EMAIL = "google_signin_email"
PASSWORD = "google_signin_password"
TWO_FACTOR = "google_signin_2fa"
CONSENT = "google_signin_consent"
CHOOSER = "google_account_chooser"
BLOCKED = "google_signin_blocked"
UNKNOWN = "unknown"

# Order is load-bearing, exactly as in gmail_recipe: every challenge path lives UNDER /signin, so a
# bare /signin rule placed first swallows the password and 2FA screens.
_URL_STATES: list[tuple[str, str]] = [
    # The CHOOSER is the one-click path and has to be recognised before the generic signin rules.
    # Google serves it at several endpoints depending on whether the caller used GIS or classic
    # OAuth; `gsi/select` is the one LinkedIn's button lands on.
    (r"/gsi/select|/AccountChooser|/signinchooser|/o/oauth2/auth.*prompt=select_account", CHOOSER),
    (r"/signin/oauth|/o/oauth2/auth|/oauth/consent", CONSENT),
    # `pk` is the PASSKEY challenge — measured live, session #22, and missing from the original
    # list, so `/v3/signin/challenge/pk` fell through to the generic /signin rule and reported the
    # address screen we had just left. The URL is the strongest signal we get here; a challenge
    # path that is not listed is a challenge we will drive straight into.
    (r"/challenge/(totp|ipp|dp|sms|az|iap|pk|skotp|ootp)|two[-_]?step|/webauthn", TWO_FACTOR),
    (r"/challenge/pwd|/signin/v2/challenge/password", PASSWORD),
    (r"/signin/rejected|/deniedsigninrejected|/signin/v2/usernamerecovery", BLOCKED),
    (r"/signin/identifier|/signin/v2/identifier", EMAIL),
    (r"accounts\.google\.com/(v3/)?signin", EMAIL),
]

# Text tells, for the screens whose URL does not move. Google re-renders the chooser and the
# consent screen under the SAME identifier URL often enough that URL alone under-reports them.
_TEXT_STATES: list[tuple[tuple[str, ...], str]] = [
    (("choose an account", "select an account", "use another account"), CHOOSER),
    (("wants access to your google account", "wants additional access",
      "to continue, google will share"), CONSENT),
    (("couldn't sign you in", "this browser or app may not be secure",
      "try using a different browser"), BLOCKED),
    # MEASURED, not guessed (live, session #22): after the address is accepted Google renders the
    # passkey challenge UNDER THE IDENTIFIER URL with "Verifying it's you… Complete sign-in using
    # your passkey". The first version of this list matched "verify it's you" and the screen says
    # "VerifyING" — so a screen we may never touch classified as one we may drive. Stems, not whole
    # phrases, and "passkey" on its own is decisive.
    (("passkey", "security key", "verifying it", "verify it", "2-step verification",
      "enter the code", "get a verification code"), TWO_FACTOR),
    (("enter your password", "forgot password"), PASSWORD),
]

# --- policy -----------------------------------------------------------------------------------
AUTO = "auto"            # ours to click
APPROVAL = "approval"    # ours to click, once the operator says yes to THIS one
HUMAN = "human"          # never ours

#: What may be done about each state. The default for anything unrecognised is HUMAN — on the
#: identity provider, an unknown screen is the one place a confident guess is least affordable.
POLICY: dict[str, str] = {
    CHOOSER: AUTO,
    # The ACCOUNT ADDRESS is not the secret. It is the username — already stored as a display hint,
    # already printed on every chooser tile, already visible in the cockpit. LEARNINGS 2026-07-09
    # drew the line in exactly this place: "everything up to and after Google's auth we drive; the
    # PASSWORD + 2FA keystrokes are the deliberate hand-off." Refusing the identifier too was my
    # line, not the operator's, and it stopped the flow one screen early for no gain.
    EMAIL: AUTO,
    CONSENT: APPROVAL,
    PASSWORD: HUMAN,
    TWO_FACTOR: HUMAN,
    BLOCKED: HUMAN,
    UNKNOWN: HUMAN,
}

#: Operator-facing reason per state. These are read aloud in the panel, so they say what is being
#: asked for and whose it is — never just "blocked".
WHY: dict[str, str] = {
    CHOOSER: "Google is asking which of your signed-in accounts to use. That is a click on a tile, "
             "not a credential — we can take it.",
    CONSENT: "Google is asking whether to grant this site access to your account. Reversible, but "
             "it is a grant — you approve it, then we click it.",
    EMAIL: "Google is asking which account. The address is not the secret — we type it and press "
           "Next; the password screen behind it is yours.",
    PASSWORD: "Google is asking for the password. Never ours, on any site.",
    TWO_FACTOR: "Google wants a verification code. Always yours — we never clear a second factor.",
    BLOCKED: "Google refused this sign-in attempt (it can flag an automated browser). Open the "
             "window and finish it yourself; nothing here will retry it.",
    UNKNOWN: "This screen on the Google account is one we have not met. We stop rather than guess "
             "on the identity provider.",
}


def is_sso_url(url: str) -> bool:
    host = (url or "").lower()
    return any(h in host for h in SSO_HOSTS)


def map_url_to_state(url: str) -> str:
    for pattern, state in _URL_STATES:
        if re.search(pattern, url or "", re.I):
            return state
    return UNKNOWN


def text_from(candidates: list[dict[str, Any]]) -> str:
    """The visible text of a screen, reconstructed from its ACCESSIBLE NAMES.

    `/ax_scan` returns candidates and no `page_text` — the key is simply absent, so every caller
    doing `scan.get("page_text")` has been classifying on an empty string. It went unnoticed
    because the signals that matter most on a login form (is there a password field?) are read
    from the candidate ROLES, which were fine. The text-only tells — a captcha notice, an MFA
    prompt, "account already exists" — were silently dead on that path.
    Found live when Google's passkey challenge, whose whole tell is the heading "Verifying it's
    you...", classified as the address screen we had just left (session #22, 2026-07-27).
    """
    return " ".join((c.get("name") or "") for c in candidates or [])


def classify(url: str = "", page_text: str = "",
             candidates: Optional[list[dict[str, Any]]] = None) -> str:
    """The Google identity screen in front of us.

    URL first (it is the strongest signal and survives a text-less scan), then the text tells for
    the screens Google renders under an unchanged URL. A page that is not on an identity host is
    not this module's business and comes back `unknown`.
    """
    if url and not is_sso_url(url):
        return UNKNOWN
    state = map_url_to_state(url)
    # Accessible names ARE the visible text here, and on this popup they are all we get.
    text = ((page_text or "") + " " + text_from(candidates or [])).lower()
    # Text can PROMOTE an under-reported screen (chooser/consent rendered on the identifier URL),
    # but must never override a challenge the URL is explicit about — the URL is the thing Google
    # cannot fake by re-rendering.
    if state in (EMAIL, UNKNOWN):
        for needles, candidate in _TEXT_STATES:
            if any(n in text for n in needles):
                return candidate
    return state


def policy_for(state: str) -> str:
    return POLICY.get(state, HUMAN)


def may_drive(state: str, *, approved: bool = False) -> bool:
    """Can WE act on this screen? `approved` carries the operator's per-instance yes for the
    states that need one."""
    p = policy_for(state)
    return p == AUTO or (p == APPROVAL and approved)


# --- what to click ----------------------------------------------------------------------------
#: A chooser tile's accessible name is the account itself — an address, or a display name with the
#: address underneath. Matching on the ADDRESS is what makes "pick the right account" a fact rather
#: than a guess about ordering.
def find_account_tile(candidates: list[dict[str, Any]],
                      username: str = "") -> Optional[dict[str, Any]]:
    """The chooser tile for `username`, or None.

    Deliberately refuses to fall back to "the first account" when a username was asked for. A
    session that signs into the WRONG Google account does not fail loudly — it succeeds, and every
    downstream capture is attributed to the wrong identity.
    """
    want = (username or "").strip().lower()
    tiles = [c for c in candidates
             if (c.get("role") or "").lower() in ("link", "button", "listitem", "menuitem")
             and (c.get("name") or "").strip()]
    if want:
        for c in tiles:
            if want in (c.get("name") or "").lower():
                return c
        return None
    # No username asked for: only answer if the choice is unambiguous.
    addressed = [c for c in tiles if "@" in (c.get("name") or "")]
    return addressed[0] if len(addressed) == 1 else None


#: The consent screen's affirmative control, in Google's own words.
_CONSENT_NAMES = ("continue", "allow", "accept")


def find_consent_button(candidates: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for c in candidates:
        if (c.get("role") or "").lower() != "button":
            continue
        name = (c.get("name") or "").strip().lower()
        if name in _CONSENT_NAMES or any(name.startswith(n) for n in _CONSENT_NAMES):
            return c
    return None


def next_action(state: str, candidates: list[dict[str, Any]], *, username: str = "",
                approved: bool = False) -> dict[str, Any]:
    """What to do about this Google screen: {action, target, policy, why}.

    `action` is one of `click` | `escalate` | `done`. Everything the caller needs to either act or
    hand back is in the return — no second call to work out whose turn it is.
    """
    p = policy_for(state)
    base = {"state": state, "policy": p, "why": WHY.get(state, WHY[UNKNOWN])}
    if p == HUMAN:
        return {**base, "action": "escalate", "target": None}
    if p == APPROVAL and not approved:
        return {**base, "action": "escalate", "target": None,
                "needs_approval": True,
                "why": WHY[CONSENT] + " Approve it and I will click it."}
    if state == CHOOSER:
        tile = find_account_tile(candidates, username)
        if tile is None:
            return {**base, "action": "escalate", "target": None,
                    "why": (f"Google is offering a choice of accounts and none of them is "
                            f"{username!r}." if username else
                            "Google is offering a choice of accounts and no single one is "
                            "identifiable. Pick it yourself rather than have us guess.")}
        return {**base, "action": "click", "target": tile}
    if state == EMAIL:
        field = find_field(candidates, EMAIL)
        if field is None:
            return {**base, "action": "escalate", "target": None,
                    "why": "Google is asking for an account address but no address field is "
                           "visible to the accessibility tree."}
        if not username:
            return {**base, "action": "escalate", "target": None,
                    "why": "Google is asking which account, and no Google login is stored to "
                           "answer with. Add one, or type it in the window."}
        step = GOOGLE_LOGIN_RECIPE[EMAIL]
        submit = find_submit(candidates, EMAIL)
        if submit is None:
            return {**base, "action": "escalate", "target": None,
                    "why": "The address field is here but Next is not visible to the "
                           "accessibility tree — we would type and strand it."}
        return {**base, "action": "type", "target": field, "value_of": "username",
                "type_style": step["type_style"], "submit": submit}
    if state == CONSENT:
        btn = find_consent_button(candidates)
        if btn is None:
            return {**base, "action": "escalate", "target": None,
                    "why": "The consent screen is up but its confirm button is not visible to the "
                           "accessibility tree."}
        return {**base, "action": "click", "target": btn}
    return {**base, "action": "escalate", "target": None}



# --- the recipe -------------------------------------------------------------------------------
# WHY A PER-DOMAIN RECIPE AND NOT ONE GENERIC FORM-FILLER. Pages differ because their stacks
# differ, and the differences are not cosmetic — they decide whether an action lands at all:
#
#   * Google's identifier is a controlled input inside a Closure/Wiz view. Setting `.value` and
#     firing one event leaves its internal model empty and Next re-renders the same screen, so the
#     address must be TYPED as keystrokes.
#   * Submitting is a CLICK ON NEXT. The first live attempt pressed Enter instead, on my assertion
#     that Enter was "steadier because the button's hit target moves" — which was speculation
#     written as fact. There is no `press` in the interaction vocabulary at all (contract.py:
#     set_text / click / submit / select_option / ...), so the dispatch went nowhere: the address
#     typed correctly, the screen never moved, and only a screenshot showed which half had worked.
#     A per-stack claim that has not been driven is a guess; this one is now measured.
#   * The whole popup is one view that swaps content in place — no navigation between identifier
#     and password — so "did it advance?" is a CONTENT question (the state changed), never a load.
#
# LinkedIn's own form is a different stack again and is driven by the domain's own path. Keeping
# the two recipes apart is the point: one generic filler tuned to whichever page broke last is how
# both end up subtly wrong.
TYPE_STYLE_KEYSTROKES = "keystrokes"   # per-character; the framework must see each one
TYPE_STYLE_SET = "set"                 # authoritative value assignment (React-style inputs)

#: state -> how to advance it. `submit` is how the form is committed once the field is filled.
GOOGLE_LOGIN_RECIPE: dict[str, dict[str, Any]] = {
    EMAIL: {
        "field_roles": ("textbox", "combobox"),
        "field_names": ("email or phone", "email", "phone"),
        "value": "username",           # resolved by the caller from the vault, never stored here
        "type_style": TYPE_STYLE_KEYSTROKES,
        "submit": {"button_names": ("next",)},
        "advances_to": (PASSWORD, TWO_FACTOR, CHOOSER, CONSENT),
        "why": "Type the account address and press Next. The address is not the secret.",
    },
    CHOOSER: {
        "click": "account_tile",
        "advances_to": (PASSWORD, TWO_FACTOR, CONSENT),
        "why": "Pick the account. A tile click, no credential.",
    },
    CONSENT: {
        "click": "consent_button",
        "advances_to": (),
        "why": "Grant the site access — only once the operator has approved this instance.",
    },
}


def find_field(candidates: list[dict[str, Any]], state: str) -> Optional[dict[str, Any]]:
    """The input this state wants filled, by role + accessible name."""
    step = GOOGLE_LOGIN_RECIPE.get(state) or {}
    roles = step.get("field_roles") or ()
    names = step.get("field_names") or ()
    for c in candidates:
        if (c.get("role") or "").lower() not in roles:
            continue
        name = (c.get("name") or "").strip().lower()
        if any(n in name for n in names):
            return c
    return None


def find_submit(candidates: list[dict[str, Any]], state: str) -> Optional[dict[str, Any]]:
    step = (GOOGLE_LOGIN_RECIPE.get(state) or {}).get("submit") or {}
    for c in candidates:
        if (c.get("role") or "").lower() != "button":
            continue
        name = (c.get("name") or "").strip().lower()
        if any(name == n or name.startswith(n) for n in step.get("button_names") or ()):
            return c
    return None


# --- how long a challenge stays real ----------------------------------------------------------
# MEASURED, session #22, 2026-07-27. Google's passkey challenge spawns a NATIVE OS prompt — no DOM
# node, no AX node — and that prompt expires on its own in well under a minute. When it does, the
# page behind it does not change: same URL (`/challenge/pk`), same accessible tree, same "Verifying
# it's you..." heading, and `/native_dialog` reports the renderer clear. So:
#
#   AN EXPIRED CHALLENGE IS INDISTINGUISHABLE FROM A LIVE ONE, by every probe we own.
#
# There is no observation that tells them apart, which means the only honest signal is ELAPSED
# TIME. Anything built on "look again and see" will keep reporting a challenge that has been dead
# for ten minutes, and an operator who acts on that is being sent to a screen that cannot succeed.
#
# The design consequence is bigger than the constant: a factor only a human can clear must not be
# ENTERED unless that human is already at the keyboard. Driving the address step and then waiting
# for someone to notice burns the challenge every time. `attended` on the step request is that
# precondition, made explicit rather than hoped for.
CHALLENGE_TTL_SECONDS = 60
CHALLENGE_STALE_AFTER = 90          # past this we say so plainly rather than implying it is live

#: The documented fork on a challenge screen. It is a CLICK, not a credential — it opens Google's
#: list of other ways to verify (another device, a code, a password). We surface it and let the
#: operator choose; picking a verification method on someone's behalf is not ours to do.
ALTERNATIVE_CONTROL_NAMES = ("try another way", "more ways to verify", "use another method")


def find_alternative_control(candidates: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for c in candidates or []:
        if (c.get("role") or "").lower() not in ("button", "link"):
            continue
        name = (c.get("name") or "").strip().lower()
        if any(n in name for n in ALTERNATIVE_CONTROL_NAMES):
            return c
    return None


def challenge_age_note(age_seconds: Optional[float]) -> str:
    """What to say about a challenge of this age. Empty when it is fresh enough to be worth trying."""
    if age_seconds is None or age_seconds < CHALLENGE_STALE_AFTER:
        return ""
    mins = int(age_seconds // 60)
    return (f"This challenge has been open about {mins} minute(s) and Google's expire in under "
            f"one — it has almost certainly timed out. The screen looks identical either way, so "
            f"there is nothing to read: use 'Try another way', or restart the sign-in.")


def spec() -> dict[str, Any]:
    """The whole policy, for the cockpit and for anyone asking 'what will you do on Google?'."""
    return {
        "hosts": list(SSO_HOSTS),
        "states": [{"state": s, "policy": policy_for(s), "why": WHY.get(s, "")}
                   for s in (CHOOSER, CONSENT, EMAIL, PASSWORD, TWO_FACTOR, BLOCKED, UNKNOWN)],
        "note": "The boundary is the STATE, not the host. Choosing among your own signed-in "
                "accounts is a click; granting access is approval-gated; credentials are never "
                "ours. Unknown screens on the identity provider stop.",
    }
