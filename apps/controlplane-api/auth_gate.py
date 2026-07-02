"""Domain-aware authentication gate — "no automation until logged in".

The loop can observe and decide on a login wall, but it can't *do* a real task it isn't
signed in for — and flailing on a login form is both useless and bot-flag-raising. So
before a live run we check whether the session's browser is authenticated for the domain,
and if it plainly isn't, we hand off ("log in first") instead of running.

The signal is domain-specific and deliberately conservative — it returns True/False only
when the page clearly says so, else None (unknown → don't block, let the loop's normal
handoff catch any problem). The mcp `/auth_state` probe covers Indeed (path/gnav based);
Facebook can't be read that way (facebook.com serves both states at the same URL), so we
read the login-wall text vs. the authed composer text — the same lesson the terminal-state
fix taught us.
"""

from __future__ import annotations

from typing import Optional

import facebook_recipe

# States that ARE the login flow — definitively not authenticated.
_FB_LOGIN_STATES = {"fb_login_wall", "fb_login_checkpoint", "fb_login_2fa"}
# Text only present once signed in (the feed composer / story bar).
_FB_AUTHED_TEXT = ("what's on your mind", "create a post", "create story", "create a story")
# Text of the logged-OUT wall (the login/create-account panel).
_FB_LOGGED_OUT_TEXT = ("log into facebook", "log in to facebook", "create new account",
                       "forgotten account", "forgotten password", "forgot account")


def _facebook_authed(url: str, page_text: str) -> Optional[bool]:
    state = facebook_recipe.map_url_to_state(url)
    t = (page_text or "").lower()
    if state in _FB_LOGIN_STATES:
        return False
    if any(s in t for s in _FB_LOGGED_OUT_TEXT):
        return False
    if any(s in t for s in _FB_AUTHED_TEXT):
        return True
    return None  # ambiguous (e.g. Marketplace is browseable logged-out) → unknown


def is_authenticated(domain_id: str, url: str, page_text: str = "") -> Optional[bool]:
    """True / False / None(unknown) for whether the browser is signed in to `domain_id`.
    Only Facebook is handled here today; other domains return None (Indeed has its own
    mcp `/auth_state` probe used elsewhere), so this gate never false-blocks them."""
    if (domain_id or "").startswith("facebook"):
        return _facebook_authed(url, page_text)
    return None


def auth_status(domain_id: str, url: str, page_text: str = "") -> dict:
    """UI-friendly readout: the auth verdict + the state it was read from."""
    authed = is_authenticated(domain_id, url, page_text)
    state = facebook_recipe.map_url_to_state(url) if (domain_id or "").startswith("facebook") else None
    return {
        "authed": authed,
        "state": state,
        "url": (url or "")[:120],
        "guidance": ("Log in to this site in the session's browser window, then run again."
                     if authed is False else None),
    }
