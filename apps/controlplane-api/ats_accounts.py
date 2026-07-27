"""ATS accounts — company-first application logins (the account-wall workaround).

Most external applications hand off to an ATS that gates the apply behind a per-employer candidate
account. We organize those accounts COMPANY-FIRST, then ATS: each company is assigned an ATS
(ats_registry.company_ats), and each company↔ATS pair gets ONE reusable login so re-applying (or
applying to another role at the same company) uses the same account.

This module is the thin company-first layer over the existing infra:
  * accounts.py — the registry + the ENCRYPTED secrets vault (kind="ats", login_url, set_credentials
    → vault). We never store a plaintext password on the record.
  * ats_registry.py — company→ATS mapping + the ATS platform definitions.

Credential CONVENTION (operator's scheme): username is a single shared address
(ATS_ACCOUNT_USERNAME, default genomags@gmail.com); password is the company's INITIALS (first letter
of each word in the company name, uppercased — "U.S. Bank National Association" → "USBNA") followed
by a shared suffix kept in the gitignored .env (ATS_ACCOUNT_PW_SUFFIX). derive_password() computes
it on demand so the operator can SEE the exact credentials at the create-account step.

IMPORTANT — the boundary: this module GENERATES + ORGANIZES credentials and can drive the flow up to
the signup/login form. It does NOT (and the agent does not) type a password into a site or submit an
account creation — that one step is the operator's (the "pause at the creation point"). The account
starts `status="pending"` (registered, not yet created) and the operator flips it to active once the
login exists.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import accounts as accounts_mod
import ats_registry

DEFAULT_USERNAME = "genomags@gmail.com"


def default_username() -> str:
    return accounts_mod._read_env_value("ATS_ACCOUNT_USERNAME") or DEFAULT_USERNAME


def default_first_name() -> str:
    """Candidate first name for account-creation forms that require it (e.g. AppVault). Kept in the
    gitignored .env (ATS_ACCOUNT_FIRST_NAME) — PII stays out of code, like the username."""
    return accounts_mod._read_env_value("ATS_ACCOUNT_FIRST_NAME") or ""


def default_last_name() -> str:
    return accounts_mod._read_env_value("ATS_ACCOUNT_LAST_NAME") or ""


def _password_suffix() -> str:
    return accounts_mod._read_env_value("ATS_ACCOUNT_PW_SUFFIX")


def derive_initials(company: str) -> str:
    """First letter of each alphanumeric token in the company name, uppercased. Splits on spaces
    AND punctuation so "U.S. Bank National Association" → U,S,B,N,A → "USBNA"."""
    tokens = re.findall(r"[A-Za-z0-9]+", company or "")
    return "".join(t[0] for t in tokens).upper()


def derive_password(company: str) -> Optional[str]:
    """The full suggested password = INITIALS + shared suffix. None if the suffix isn't configured
    in .env (so the caller shows the operator the initials + a 'set ATS_ACCOUNT_PW_SUFFIX' hint)."""
    initials = derive_initials(company)
    suffix = _password_suffix()
    if not initials or not suffix:
        return None
    return f"{initials}{suffix}"


def ats_account_id(company: str, ats_id: str) -> str:
    """Stable id for a company↔ATS login, e.g. ats__u_s_bank_national_association__phenom."""
    return accounts_mod._slugify(f"ats__{company}__{ats_id}")


def suggested_credentials(company: str, ats_id: str = "") -> dict[str, Any]:
    """The credentials the operator should USE to create/sign in to this company's ATS account.
    Returns the derived password in the clear FOR DISPLAY (operator-only, localhost) — this is the
    'help me create a password' feature, not a stored secret being read back."""
    initials = derive_initials(company)
    pw = derive_password(company)
    return {
        "company": company,
        "ats_id": ats_id or (ats_registry.ats_for_company(company) or ""),
        "username": default_username(),
        "password_initials": initials,
        "suggested_password": pw,               # None if ATS_ACCOUNT_PW_SUFFIX unset
        "suffix_configured": bool(_password_suffix()),
        "account_id": ats_account_id(company, ats_id) if ats_id else None,
        "note": "Operator creates/logs in with these; the agent never types them into the site.",
    }


def ensure_account(company: str, ats_id: str, login_url: str = "") -> dict[str, Any]:
    """Register (idempotently) the company↔ATS login as a PENDING account and record company→ATS.
    No secret is stored — the operator saves creds via the Accounts UI once the login is created."""
    if not company or ats_id not in {a["ats_id"] for a in ats_registry.ATS_PLATFORMS}:
        return {"ok": False, "detail": f"bad company/ats ({company!r}, {ats_id!r})"}
    ats_registry.record_company_ats(company, ats_id, login_url)
    aid = ats_account_id(company, ats_id)
    existing = accounts_mod.get_account(aid)
    ats = ats_registry.get_ats(ats_id) or {}
    patch = {
        "kind": "ats",
        "domain_id": ats_id,
        "company": company,
        "ats_id": ats_id,
        "label": f"{company} · {ats.get('display_name', ats_id)}",
        "username_hint": default_username(),
        "login_url": login_url or (existing or {}).get("login_url", ""),
        # KEEP AN EXISTING ACCOUNT'S STATUS; only a brand-new one is 'pending'. This used to be
        # gated on `has_creds`, which asks a different question and answers it wrong: under this
        # module's own convention the password is DERIVED on demand and never stored, so has_creds
        # is False for essentially every ATS account we make. The effect was that `ensure_account` —
        # which the ladder's account rung calls on every crank — flipped `active` back to `pending`,
        # and the very next read said "this ATS needs an account" about an account we had just
        # created. Found live on iCIMS 2026-07-27, right after a successful signup.
        "status": (existing or {}).get("status") or "pending",
    }
    rec = accounts_mod.put_account(aid, patch)
    return {"ok": True, "account": rec, "credentials": suggested_credentials(company, ats_id)}


def next_account_action(company: str, ats_id: str) -> dict[str, Any]:
    """The Account Manager's ONE-LOOP decision for a company↔ATS login: which leg runs next, from
    the account's lifecycle state. `pending`/no-creds → CREATE the account (button "Create Account");
    `active` → SIGN IN (button "Sign In"); then hand to the apply spine. Returns the leg, the recipe
    to drive, and the credentials to use. The Account Manager (operator-run) executes it — not the
    agent's loop (see the boundary note in apply_recipe.py)."""
    aid = ats_account_id(company, ats_id)
    acct = accounts_mod.get_account(aid) or {}
    status = acct.get("status", "pending")
    # 'Login' (the sign_in leg) becomes available ONLY at the fully-created checkpoint —
    # status=='active', set by a VERIFIED create-account run or an explicit mark-created. Merely
    # having creds saved does NOT count: the operator can stage the intended password before the
    # account exists on the ATS, and Login must not light up until it's really been created
    # (operator directive 2026-07-19).
    created = status == "active"
    leg = "sign_in" if created else "create_account"
    recipes = {"workday": {"create_account": "WORKDAY_CREATE_ACCOUNT_RECIPE",
                           "sign_in": "WORKDAY_SIGN_IN_RECIPE"},
               "appvault": {"create_account": "APPVAULT_CREATE_ACCOUNT_RECIPE",
                            "sign_in": "APPVAULT_SIGN_IN_RECIPE"},
               # iCIMS has no sign-in RECIPE yet — the returning-candidate leg has not been
               # driven. Absent rather than pointed at the create recipe: a leg we have not
               # walked should read as unknown, not as covered.
               "icims": {"create_account": "ICIMS_CREATE_PROFILE_RECIPE"}}
    # The BUTTON is the ATS's own words, and a wrong label here becomes a wrong instruction in the
    # operator's handoff card. iCIMS says "Submit Profile" (it creates the account and commits step
    # 1 of the application at once) and "Log back in!"; the Workday-flavoured pair is the default
    # only because it is the most common, not because it is generic.
    buttons = {"icims": ("Submit Profile", "Log back in!")}
    create_button, signin_button = buttons.get(ats_id, ("Create Account", "Sign In"))
    return {
        "company": company,
        "ats_id": ats_id,
        "account_id": aid,
        "account_status": status,
        "leg": leg,                                  # create_account | sign_in
        "state": f"{ats_id}_{leg}",
        "recipe": recipes.get(ats_id, {}).get(leg),  # None if this ATS has no recipe yet
        "button": signin_button if created else create_button,
        "credentials": suggested_credentials(company, ats_id),
        "next": "hand to the apply spine (e.g. WORKDAY_APPLY_RECIPE) once authenticated",
        "note": "One loop, run by the operator/Account Manager — the agent never enters the creds.",
    }


def mark_created(company: str, ats_id: str) -> dict[str, Any]:
    """Flip a company↔ATS account from the creation stage to 'active' — call after the account has
    actually been created on the ATS (the operator's step, for now). Then next_account_action returns
    the SIGN-IN leg instead of CREATE."""
    aid = ats_account_id(company, ats_id)
    if not accounts_mod.get_account(aid):
        return {"ok": False, "detail": f"no account {aid}"}
    rec = accounts_mod.put_account(aid, {"status": "active"})
    return {"ok": True, "account": rec}


def list_by_company() -> dict[str, Any]:
    """Company-first view: every registered ATS account grouped by company, each with its ATS,
    login status, has_creds, and the suggested login id (never the password)."""
    companies: dict[str, dict[str, Any]] = {}
    for acct in accounts_mod.list_accounts():
        if acct.get("kind") != "ats":
            continue
        company = acct.get("company") or acct.get("label") or acct.get("account_id")
        bucket = companies.setdefault(company, {"company": company, "accounts": []})
        bucket["accounts"].append({
            "account_id": acct["account_id"],
            "ats_id": acct.get("ats_id"),
            "login_url": acct.get("login_url"),
            "status": acct.get("status"),
            "has_creds": acct.get("has_creds"),
            "username_hint": acct.get("username_hint"),
        })
    return {
        "username": default_username(),
        "suffix_configured": bool(_password_suffix()),
        "companies": sorted(companies.values(), key=lambda c: c["company"].lower()),
    }
