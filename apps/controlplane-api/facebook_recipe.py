"""Facebook Marketplace recipes — the login flow + the create-listing flow.

Mirrors `apply_recipe.py`: each recipe is the expected linear spine of a task as a list
of steps (page-state id → the action that advances it → the state(s) it may lead to), plus
the BRANCHES that peel off the spine and need a human. This is the SEED knowledge for a
brand-new domain the runtime loop hasn't practiced yet — a first, honest hypothesis of the
state machine that the loop then confirms/refines against live captures (the same
teacher→distill loop the Indeed recipes went through).

Facebook is deliberately hostile to automation and obfuscates its DOM, so selectors here
are ACCESSIBLE-NAME / visible-text hints (stable-ish) rather than class names, and are
marked as needing live verification. Login is the known sharp edge: FB interleaves
checkpoints, 2FA, and a reCAPTCHA that can appear even at a /two_step_verification URL — all
of which are human-required stop-states, never auto-solved.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# --- LOGIN spine ------------------------------------------------------------------
# Terminal state = fb_home_feed (the authed feed). Everything on the checkpoint/2FA/captcha
# side is a human-required branch — we drive to the wall, the operator authenticates.
FACEBOOK_LOGIN_RECIPE = [
    {"step": 0, "state": "fb_login_wall", "action": "enter email + password, click Log In",
     "selectors": {"email": "input[name=email]", "password": "input[name=pass]",
                   "submit": "button[name=login]"},
     "expect": ["fb_home_feed", "fb_login_checkpoint", "fb_login_2fa", "captcha"]},
    {"step": 1, "state": "fb_home_feed", "action": "done — authed feed", "expect": []},
]

FACEBOOK_LOGIN_BRANCHES = {
    "fb_login_checkpoint": {"human_required": True,  "note": "security checkpoint (unusual-login / identity) — operator clears it"},
    "fb_login_2fa":        {"human_required": True,  "note": "two-factor code — operator enters it (code may arrive via an errand: SMS/authenticator/email)"},
    "captcha":             {"human_required": True,  "note": "reCAPTCHA — can appear even at a /two_step_verification URL; operator solves, never auto-solve"},
    "fb_login_save_device":{"human_required": False, "note": "'Save this browser?' — choose per policy; not blocking"},
}

# --- CREATE-LISTING spine (Marketplace) -------------------------------------------
# Fields are filled from a ListingDraft (see listing_draft.py). PUBLISH is the irreversible
# action — the consequential gate holds it for operator approval (like Submit on an apply).
FACEBOOK_CREATE_LISTING_RECIPE = [
    {"step": 0, "state": "fb_marketplace_home", "action": "click 'Create new listing'",
     "selectors": {"create": "[aria-label='Create new listing'], a[href*='/marketplace/create']"},
     "expect": ["fb_listing_type_picker", "fb_create_listing_form"]},
    {"step": 1, "state": "fb_listing_type_picker", "action": "choose 'Item for sale'",
     "selectors": {"item_for_sale": "text=Item for sale"},
     "expect": ["fb_create_listing_form"]},
    {"step": 2, "state": "fb_create_listing_form",
     "action": "fill title, price, category, condition, description from the ListingDraft",
     "selectors": {"title": "[aria-label='Title']", "price": "[aria-label='Price']",
                   "category": "[aria-label='Category']", "condition": "[aria-label='Condition']",
                   "description": "[aria-label='Description']"},
     "expect": ["fb_listing_add_photos", "fb_create_listing_form"]},
    {"step": 3, "state": "fb_listing_add_photos", "action": "upload photo(s) from the draft (file input)",
     "selectors": {"add_photos": "[aria-label='Add photos'], input[type=file][accept*=image]"},
     "expect": ["fb_create_listing_form", "fb_listing_review"]},
    {"step": 4, "state": "fb_listing_review", "action": "click Next → review the listing preview",
     "selectors": {"next": "[aria-label='Next']"},
     "expect": ["fb_listing_publish"]},
    {"step": 5, "state": "fb_listing_publish",
     "action": "PUBLISH — consequential gate holds this for the operator's approval",
     "selectors": {"publish": "[aria-label='Publish']"},
     "expect": ["fb_listing_published", "captcha"]},
    {"step": 6, "state": "fb_listing_published", "action": "done — listing live", "expect": []},
]

FACEBOOK_CREATE_LISTING_BRANCHES = {
    "captcha":                 {"human_required": True,  "note": "anti-bot challenge on publish — operator solves"},
    "fb_listing_policy_warning": {"human_required": True, "note": "commerce-policy / prohibited-item warning — operator decides"},
    "fb_listing_duplicate":    {"human_required": True,  "note": "'similar listing already exists' prompt — operator decides"},
}

# --- URL -> state (cheap, no model) -----------------------------------------------
_URL_STATES = [
    (r"facebook\.com/marketplace/item/",         "fb_listing_published"),
    (r"marketplace/create/item",                 "fb_create_listing_form"),
    (r"marketplace/create",                      "fb_listing_type_picker"),
    (r"marketplace/you/selling",                 "fb_marketplace_selling"),
    (r"facebook\.com/marketplace",               "fb_marketplace_home"),
    (r"/checkpoint",                             "fb_login_checkpoint"),
    (r"two_step_verification|/two_factor",       "fb_login_2fa"),
    (r"/login|/auth",                            "fb_login_wall"),
    (r"facebook\.com/(\?sk=|home|$)",            "fb_home_feed"),
]


def map_url_to_state(url: str) -> str:
    u = url or ""
    for pattern, state in _URL_STATES:
        if re.search(pattern, u):
            return state
    return "unknown"


def _all_branches() -> dict:
    return {**FACEBOOK_LOGIN_BRANCHES, **FACEBOOK_CREATE_LISTING_BRANCHES}


def _recipe_entry(state: str) -> Optional[dict]:
    for recipe in (FACEBOOK_LOGIN_RECIPE, FACEBOOK_CREATE_LISTING_RECIPE):
        entry = next((s for s in recipe if s["state"] == state), None)
        if entry is not None:
            return entry
    return None


def describe_tab(url: str, page_text: str = "") -> dict[str, Any]:
    """Map ONE live tab onto the Facebook recipes: state, recipe step, expected next, and
    whether it's a human-required branch. The per-tab 'where are we' readout, like the
    Indeed describe_tab."""
    state = map_url_to_state(url)
    branch = _all_branches().get(state)
    entry = _recipe_entry(state)
    return {
        "url": (url or "")[:90],
        "state": state,
        "recipe_step": entry["step"] if entry else None,
        "next_action": entry["action"] if entry else None,
        "expected_next": entry["expect"] if entry else [],
        "is_branch": branch is not None,
        "human_required": bool(branch and branch["human_required"]),
        "branch_note": branch["note"] if branch else None,
    }


def recipe_spec() -> dict[str, Any]:
    return {
        "domain": "facebook_marketplace",
        "login": {"recipe": FACEBOOK_LOGIN_RECIPE, "branches": FACEBOOK_LOGIN_BRANCHES,
                  "terminal_state": "fb_home_feed"},
        "create_listing": {"recipe": FACEBOOK_CREATE_LISTING_RECIPE,
                           "branches": FACEBOOK_CREATE_LISTING_BRANCHES,
                           "terminal_state": "fb_listing_published"},
        "status": "seeded — not yet live-verified. Selectors are accessible-name/text hints and "
                  "will refine as the runtime loop captures the real Facebook flow. Login "
                  "checkpoints/2FA/captcha are human-required stop-states; publish is gated for "
                  "operator approval.",
    }
