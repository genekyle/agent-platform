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

IMPORTANT — the boundary, as the operator drew it (2026-07-24): account creation IS automated. This
is their own account, for their own job search, on their own machine. What holds are the REAL
external gates and only those — a CAPTCHA (never auto-solved, on any form) and an email/2FA
verification code (not ours to fabricate). The earlier "the agent never types a password, the
operator creates every account by hand" was MY caution hardcoded as their architecture, and it is
gone; `session_control.apply_account` defaults to `mode="auto"`. An account starts `status="pending"`
(registered, not yet created) and becomes `active` when a create run actually lands.

A created account's credential is also STORED, not just derived. Derivation (initials + suffix) is
how we CHOOSE a password; it is not a reliable way to recover one, because it depends on
ATS_ACCOUNT_PW_SUFFIX and on a company string that arrives from a job board and can come back
spelled differently. The site remembers what we typed, so we have to as well — `record_credentials`
puts the pair in the encrypted vault at the moment it is proven.
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
    """Stable id for a company↔ATS login, e.g. ats_u_s_bank_national_association_phenom.

    (The example above said `ats__…__phenom` until 2026-08-27 — `_slugify` collapses runs, so the
    doubled separators never existed. A stale example in a docstring is a claim like any other:
    it sent a test fixture to the wrong shape the first time somebody trusted it.)"""
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
    # DID THIS CALL MINT IT? The row is written on INTENT — the account rung calls this before the
    # signup form has been touched — so a create leg that never completes leaves a row behind
    # claiming a login this employer has never heard of. Reporting what we minted is what lets the
    # caller take it back (`discard_unclaimed`). Operator, 2026-08-13: "make sure the account
    # details don't stay until the full account creation actually goes through."
    return {"ok": True, "account": rec, "created": existing is None,
            "credentials": suggested_credentials(company, ats_id)}


def discard_unclaimed(company: str, ats_id: str) -> dict[str, Any]:
    """Take back an account row whose signup never happened. The mirror of `ensure_account`.

    A PENDING row is working state for an attempt in progress, not a record. `ensure_account`
    writes it before the form is driven, because the leg needs somewhere to hang the login_url and
    the derivation key — but when the attempt ends without an account existing, that row outlives
    its meaning and becomes a claim about another system that nobody made. The residue is real and
    accumulates: three such rows were sitting in the store on 2026-08-13, one of them
    (`ats_odyssey_consulting_workday`) minted from a platform PREDICTION that turned out wrong,
    beside the `_icims` row that was the truth.

    REFUSES anything that could be real, and says which:
      * `active` — the account exists; that is `reset_account`'s job, deliberately and separately.
      * has a stored credential — something was written down against this login, and a row we can
        rebuild for free is never worth destroying a secret we cannot.
    Nothing is lost either way: the id is derived from company+ats and the password from the
    company name, so a discarded row is one `ensure_account` away from returning.
    """
    aid = ats_account_id(company, ats_id)
    rec = accounts_mod.get_account(aid)
    if rec is None:
        return {"ok": True, "discarded": False, "detail": "nothing to discard"}
    if rec.get("status") == "active":
        return {"ok": False, "discarded": False,
                "detail": f"{aid} is active — the account exists. Use reset_account to un-say it."}
    if rec.get("has_creds"):
        return {"ok": False, "discarded": False,
                "detail": f"{aid} holds a stored credential — not discarding a secret to tidy a row."}
    return {"ok": True, "discarded": bool(accounts_mod.delete_account(aid)), "account_id": aid}


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
               "icims": {"create_account": "ICIMS_CREATE_PROFILE_RECIPE"},
               # SuccessFactors: the create form was mapped field-by-field off the live page
               # (Teradyne, 2026-07-28) and its sign-in leg off the gate that precedes it. Both
               # legs resolve through apply_fields, so both are named — but see the account loop
               # in apply_recipe for which of them has actually been WALKED.
               "successfactors": {"create_account": "SUCCESSFACTORS_ACCOUNT_LOOP.needs_creation",
                                  "sign_in": "SUCCESSFACTORS_ACCOUNT_LOOP.created"}}
    # The BUTTON is the ATS's own words, and a wrong label here becomes a wrong instruction in the
    # operator's handoff card. iCIMS says "Submit Profile" (it creates the account and commits step
    # 1 of the application at once) and "Log back in!"; the Workday-flavoured pair is the default
    # only because it is the most common, not because it is generic.
    buttons = {"icims": ("Submit Profile", "Log back in!"),
               # PowerSchool's Auth0 login is identifier-first, so BOTH legs open on an email box
               # and a "Continue" — there is no "Create Account" control anywhere on the screen the
               # operator is being sent to. The card is an instruction, and one that names a button
               # the page does not have reads as "the button is missing" rather than "the label is
               # wrong", which is the more expensive way round to be mistaken.
               "schoolspring": ("Continue", "Continue")}
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


def record_credentials(company: str, ats_id: str, username: str, password: str) -> dict[str, Any]:
    """Store the credential this company↔ATS account was ACTUALLY created (or signed in) with,
    encrypted in the secrets vault.

    Why store what we can derive: `derive_password` is how we CHOOSE a password, and it is not a
    dependable way to RECOVER one. It is a pure function of two inputs that both drift —
    ATS_ACCOUNT_PW_SUFFIX is one operator edit away from changing every account's answer at once,
    and the company name comes from a job board, so "Teradyne" today can be "Teradyne, Inc."
    tomorrow and derive a different password for the same login. Either way the derivation keeps
    returning something plausible; it is the site that disagrees, at a sign-in, weeks later, with
    nothing to say except that the password is wrong.

    So the pair is written down at the one moment it is known to be true: the site just accepted it.
    Called on the sign-in leg too — that is the same proof arriving a second time, and it repairs
    accounts made before this existed.
    """
    aid = ats_account_id(company, ats_id)
    if not accounts_mod.get_account(aid):
        return {"ok": False, "detail": f"no account {aid}"}
    if not (username and password):
        return {"ok": False, "detail": "nothing to store: username and password are both required"}
    rec = accounts_mod.set_credentials(aid, username, password)
    return {"ok": True, "account_id": aid, "account": rec}


def mark_created(company: str, ats_id: str) -> dict[str, Any]:
    """Flip a company↔ATS account from the creation stage to 'active' — call after the account has
    actually been created on the ATS (the operator's step, for now). Then next_account_action returns
    the SIGN-IN leg instead of CREATE."""
    aid = ats_account_id(company, ats_id)
    if not accounts_mod.get_account(aid):
        return {"ok": False, "detail": f"no account {aid}"}
    rec = accounts_mod.put_account(aid, {"status": "active"})
    return {"ok": True, "account": rec}


def reset_account(company: str, ats_id: str) -> dict[str, Any]:
    """Un-say `mark_created`: back to `pending`, and the stored credential deleted.

    Marking an account created is a CLAIM ABOUT ANOTHER SYSTEM — that a login now exists on the
    ATS — and this one was made on a report that turned out to be wrong (Teradyne, 2026-07-28: the
    step ledger said `account failed: could not click Accept`, the account was marked created
    anyway, and nothing on SAP had been made). An account wrongly marked active is worse than one
    wrongly marked pending, because `next_account_action` then offers the SIGN-IN leg forever and
    the create leg becomes unreachable — the system will keep trying to log in to something that
    does not exist, and read every rejection as a bad password.

    The credential goes with it, deliberately. It is derived, so nothing is lost — `derive_password`
    reproduces it on demand — and leaving it behind would keep `has_creds` true for an account that
    does not exist, which is the exact confusion the pending/active split is for.
    """
    aid = ats_account_id(company, ats_id)
    if not accounts_mod.get_account(aid):
        return {"ok": False, "detail": f"no account {aid}"}
    cleared = accounts_mod.clear_credentials(aid)
    rec = accounts_mod.put_account(aid, {"status": "pending"})
    return {"ok": True, "account": rec, "credential_cleared": cleared}


#: Words that describe what a company IS, not which company it is. A canonical match may never
#: rest on these alone — "Health Group" and "Health Systems Group" are not the same employer.
_GENERIC_COMPANY_TOKENS = frozenset({
    "the", "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation",
    "co", "company", "group", "holdings", "consulting", "consultants", "systems", "services",
    "solutions", "technologies", "technology", "partners", "associates", "international",
    "global", "national", "america", "american", "usa", "us",
})


def _company_tokens(name: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t)


def find_existing(db=None, *, company: str, ats_id: str) -> Optional[dict[str, Any]]:
    """Is there ALREADY an account for this employer on this ATS? Read-only; never creates.

    THE BUG THIS ANSWERS (2026-08-24, live): `ats_odyssey_consulting_icims` sat in the store WITH
    credentials while the flow opened `ats_odyssey_systems_consulting_group_ltd_icims` — pending,
    no creds — for the same employer, split by nothing but legal-suffix noise in the company
    string. Operator: *"we had the creds on file, should've checked there first."*

    Matching is two-tier and says which it used. `exact` is the derived account id. `canonical`
    holds when one company's distinctive tokens are a SUBSET of the other's — which is exactly
    the Odyssey shape — after generic words are set aside, so a match can never rest on
    "Group"/"Health"/"Systems" alone. Anything weaker is not a match: proposing the wrong
    employer's credential is worse than proposing none, because it looks like a bad password
    weeks later (the vault-overwrite lesson, from the other direction).

    Returns the account record plus `match`, or None. **It surfaces, it does not act** — the
    2026-08-24 lesson is "check first", not "log in automatically".
    """
    if not company or not ats_id:
        return None
    try:
        accounts = [a for a in accounts_mod.list_accounts()
                    if a.get("kind") == "ats" and a.get("ats_id") == ats_id]
    except Exception:
        return None

    want_id = ats_account_id(company, ats_id)
    for acct in accounts:
        if acct.get("account_id") == want_id:
            return {**acct, "match": "exact"}

    want = _company_tokens(company)
    want_distinct = want - _GENERIC_COMPANY_TOKENS
    if not want_distinct:
        return None
    for acct in accounts:
        have = _company_tokens(acct.get("company") or "")
        have_distinct = have - _GENERIC_COMPANY_TOKENS
        if not have_distinct:
            continue
        if want_distinct <= have_distinct or have_distinct <= want_distinct:
            return {**acct, "match": "canonical",
                    "matched_on": sorted(want_distinct & have_distinct),
                    "caveat": (f"matched {company!r} to {acct.get('company')!r} on shared "
                               f"distinctive tokens — confirm it is the same employer before "
                               f"using the credential")}
    return None


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
