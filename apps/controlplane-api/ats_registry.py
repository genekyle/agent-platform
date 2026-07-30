"""ATS registry — the organized structure for third-party application portals.

Applying to a job is CROSS-SITE: you find the posting on a *career-search engine* (Indeed,
LinkedIn, ZipRecruiter, …) and then, for most postings, you leave it and land in the employer's
own *Applicant Tracking System* (ATS) — Workday, iCIMS, Oracle Taleo, Greenhouse, Lever, … This
module names that structure so the apply task and its training data are ORGANIZED instead of a
per-company pile.

The two groupings (defined live with the operator 2026-07-12):

  * CAREER_SEARCH — the domain CATEGORY for job engines. Its members (indeed_jobs, linkedin_jobs,
    ziprecruiter, …) are where we SEARCH + triage. "Indeed" isn't really the domain; the domain is
    "career-search engine", and Indeed/LinkedIn/ZipRecruiter are members of it.
  * ATS_PLATFORMS — the third-party apply portals you hand off TO. Each ATS is treated like its
    OWN domain: it gets its own recipe (apply_recipe.WORKDAY_APPLY_RECIPE, …) and its own
    training-data bucket (captures tagged domain_id=<ats_id>, so TrainingCapture rollups accrue
    per-ATS, not per-company).

Why per-ATS and not per-company: an ATS renders the SAME component library across every tenant
(Workday's data-automation-ids are identical for State Street, Takeda, Point32Health). So training
GENERALIZES across companies that share an ATS. `company_ats` records which ATS each employer uses;
the first time we drive Company X's Workday we can already reuse everything learned on every other
Workday. That is the whole point of organizing by ATS.

Shape mirrors providers.py: a backend-owned CONSTANT (config, not training data) plus a small
JSON-persisted learning store for the company→ATS mapping (the one part that grows at runtime),
persisted next to the other operator-owned state (domain_settings.json, job_search_targets.json).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import re
from urllib.parse import parse_qs, urlparse

from settings import settings

_lock = threading.Lock()


# --- CAREER_SEARCH: the domain category for job engines ------------------------------------------
# member_domains are domain_ids in command_center.DOMAINS (live) or planned (not seeded yet). The
# ATS platforms below are the downstream apply targets these engines hand off to.
CAREER_SEARCH: dict[str, Any] = {
    "group_id": "career_search",
    "display_name": "Career Search",
    "icon": "🧭",
    "what": "Job engines / career-search engines — where we search + triage postings.",
    "member_domains": ["indeed_jobs", "linkedin_jobs", "ziprecruiter", "glassdoor", "dice", "monster"],
    "apply_targets": "ats",  # postings hand off to the ATS group for the actual application
}


# --- ATS_PLATFORMS: each is domain-like (own recipe + own training bucket) -----------------------
# hosts: substrings matched against the apply-destination URL host (classify_ats).
# recipe: pointer to the driving recipe in apply_recipe.py, or "seed"/"none" when not built yet.
# auth: "account" = needs a per-employer candidate account (the wall — a persistent pre-authed
#       profile OR the operator; NEVER auto-create); "none" = no sign-in to apply.
# seed_companies: employers we already know use this ATS (grows via record_company_ats()).
ATS_PLATFORMS: list[dict[str, Any]] = [
    {"ats_id": "workday", "display_name": "Workday", "icon": "🟠",
     "hosts": ["myworkdayjobs.com", "myworkday.com", "wd1.", "wd3.", "wd5."],
     "recipe": "apply_recipe.WORKDAY_APPLY_RECIPE", "auth": "account",
     "notes": "data-automation-id selectors are stable across tenants (see WORKDAY_LESSONS). "
              "'Use My Last Application' prefills from the candidate account.",
     "seed_companies": ["State Street", "Takeda", "Point32Health"]},
    {"ats_id": "icims", "display_name": "iCIMS", "icon": "🔵",
     "hosts": ["icims.com"], "recipe": "seed", "auth": "account",
     "notes": "careers-<tenant>.icims.com; account-gated.", "seed_companies": []},
    {"ats_id": "taleo", "display_name": "Oracle Taleo", "icon": "🔴",
     "hosts": ["taleo.net"], "recipe": "seed", "auth": "account",
     "notes": "legacy Oracle ATS; heavy multi-step, account-gated.", "seed_companies": []},
    {"ats_id": "greenhouse", "display_name": "Greenhouse", "icon": "🌱",
     "hosts": ["greenhouse.io", "boards.greenhouse.io"], "recipe": "seed", "auth": "none",
     "notes": "boards.greenhouse.io/<tenant>; usually no account to apply (resume + fields).",
     "seed_companies": []},
    {"ats_id": "lever", "display_name": "Lever", "icon": "🎚️",
     "hosts": ["lever.co", "jobs.lever.co"], "recipe": "seed", "auth": "none",
     "notes": "jobs.lever.co/<tenant>; single-page apply, usually no account.", "seed_companies": []},
    {"ats_id": "successfactors", "display_name": "SAP SuccessFactors", "icon": "🟦",
     # `rmkcdn`/`successfactors.eu` are SAP's own hosts, but MOST tenants serve the career site
     # from their OWN domain (jobs.teradyne.com) with no SAP string anywhere in the url — which is
     # why Teradyne classified as `company_site` on the first encounter (2026-07-27). The path
     # shape and the page's own furniture are the tells; see `_SUCCESSFACTORS_PATH_TELLS`.
     "hosts": ["successfactors.com", "successfactors.eu", "rmkcdn.successfactors.com"],
     "recipe": "apply_recipe.SUCCESSFACTORS_APPLY_RECIPE", "auth": "account",
     "notes": "SAP ATS, usually on the employer's OWN domain. RAISES A BLOCKING alert() on the job "
              "page ('Join our talent community…') that freezes the tab's renderer and cannot be "
              "dismissed once open — start /dialog_guard BEFORE driving. Apply is a STAGED MENU "
              "('Apply Now' vs 'Start applying with LinkedIn'), not a direct link.",
     "seed_companies": ["Teradyne"]},
    {"ats_id": "smartrecruiters", "display_name": "SmartRecruiters", "icon": "🟢",
     "hosts": ["smartrecruiters.com"], "recipe": "seed", "auth": "none", "notes": "",
     "seed_companies": []},
    {"ats_id": "ashby", "display_name": "Ashby", "icon": "⬛",
     "hosts": ["ashbyhq.com", "jobs.ashbyhq.com"], "recipe": "seed", "auth": "none", "notes": "",
     "seed_companies": []},
    {"ats_id": "workable", "display_name": "Workable", "icon": "🟣",
     "hosts": ["workable.com"], "recipe": "seed", "auth": "none", "notes": "", "seed_companies": []},
    {"ats_id": "brassring", "display_name": "IBM Kenexa BrassRing", "icon": "🟫",
     "hosts": ["brassring.com", "kenexa.com"], "recipe": "seed", "auth": "account",
     "notes": "legacy Kenexa; account-gated.", "seed_companies": []},
    {"ats_id": "jobvite", "display_name": "Jobvite", "icon": "🟨",
     "hosts": ["jobvite.com"], "recipe": "seed", "auth": "none", "notes": "", "seed_companies": []},
    {"ats_id": "adp", "display_name": "ADP Workforce Now", "icon": "🔶",
     "hosts": ["workforcenow.adp.com", "myjobs.adp.com"], "recipe": "seed", "auth": "account",
     "notes": "", "seed_companies": []},
    {"ats_id": "phenom", "display_name": "Phenom", "icon": "⚫",
     "hosts": ["phenompeople.com", "phenom.com"], "recipe": "seed", "auth": "account",
     "notes": "career-site CMS, not a uniform host: employers run it on their OWN careers subdomain "
              "(careers.<co>.com; the Indeed feed carries utm_medium=phenom-feeds). classify_ats "
              "can't catch it by host — record the company→phenom mapping from the applystart feed. "
              "'Apply now' is typically account-gated. Observed: careers.usbank.com (U.S. Bank).",
     "seed_companies": ["U.S. Bank"]},
    {"ats_id": "appvault", "display_name": "AppVault", "icon": "🟩",
     "hosts": ["appvault.com"], "recipe": "apply_recipe.APPVAULT_APPLY_RECIPE", "auth": "account",
     "notes": "Reached via a company careers FRONT (e.g. careerswithus.com → 'APPLY NOW' link) that "
              "hands off to <employer>apply.appvault.com. Account-gated: Material-UI LOGIN (Email + "
              "Password) with a 'Create an Account' form (Email, Password[8-18, upper+lower+non-alpha], "
              "confirm, First/Last Name, Country, Profile Visibility, accept-Terms link → Continue). "
              "MUI inputs carry NO stable name/id EXCEPT the password fields "
              "(#outlined-adornment-password / -re-password) — match the rest by floating-label text / "
              "DOM order. Observed: Ahold Delhaize USA (aholddelhaizeapply.appvault.com).",
     "seed_companies": ["Ahold Delhaize USA"]},
    # Catch-all so an employer's own careers portal (not yet mapped to a known ATS) is a recordable
    # platform. classify_ats returns 'company_site' for unknown external hosts; this makes it a
    # first-class registry entry (so record_company_ats accepts it) until the real backend is known.
    {"ats_id": "company_site", "display_name": "Company careers site (unmapped)", "icon": "⬜",
     "hosts": [], "recipe": "none", "auth": "unknown",
     "notes": "employer's own careers portal not yet mapped to a known ATS backend.",
     "seed_companies": []},
    # The Indeed-native quick-apply flow (smartapply) is NOT an external ATS but is where an apply
    # can also complete without leaving the engine — kept here so classify_ats can name it.
    {"ats_id": "indeed_quick_apply", "display_name": "Indeed Quick Apply (smartapply)", "icon": "🔷",
     "hosts": ["smartapply.indeed.com", "indeed.com"], "recipe": "apply_recipe.INDEED_APPLY_RECIPE",
     "auth": "none", "notes": "On-engine Indeed apply; no external ATS. Not account-gated.",
     "seed_companies": []},
    # LinkedIn's on-engine "Easy Apply" — the same shape as Indeed's smartapply: an apply that
    # completes WITHOUT leaving the engine, so it needs a name here even though it is not a
    # third-party ATS. Its auth is the ENGINE's login (you are already signed in to LinkedIn to
    # see the button at all), which is why auth="none" — there is no separate per-employer account.
    # A LinkedIn posting that is not Easy Apply hands off to a real ATS on that ATS's own host, so
    # the host-matching loop above catches it exactly as it does for an Indeed hand-off.
    {"ats_id": "linkedin_easy_apply", "display_name": "LinkedIn Easy Apply", "icon": "🔗",
     "hosts": ["linkedin.com"], "recipe": "seed", "auth": "none",
     "notes": "On-engine LinkedIn apply; no external ATS. Signed in as the LinkedIn account.",
     "seed_companies": []},
]

# ATS ids that are the ENGINE's own on-page apply rather than a third-party portal. classify_ats
# must not let their broad engine hosts (indeed.com, linkedin.com) shadow a real ATS, so they are
# skipped in the host loop and answered explicitly at the end.
_ON_ENGINE_APPLY = ("indeed_quick_apply", "linkedin_easy_apply")

_BY_ID = {a["ats_id"]: a for a in ATS_PLATFORMS}


# A BRANDED WRAPPER hosts the ATS on the employer's own domain, so the host alone lies: KKR serves a
# Greenhouse job at www.kkr.com/careers/...?gh_jid=<id> (the real form is an embedded
# job-boards.greenhouse.io iframe). Host-matching called that 'company_site' and we'd have grown a
# bespoke KKR path for what is just Greenhouse. These QUERY-PARAM tells are the ATS leaking its own
# identity through the wrapper — cheap, and they generalize across every employer on that ATS.
# (Same lesson as Workday's branded wrappers, which are caught by the APPLY-NOW href instead.)
#: SuccessFactors on a tenant's own domain leaves no host or query tell, so it is recognised by the
#: SHAPE OF ITS PATH: SAP renders `/<Tenant>/job/<Location>-<Title>-<id>/`. Checked only after the
#: host, query and embed passes fail, so a real ATS host always wins.
#:
#: NARROW ON PURPOSE, and narrower than the first attempt. `/<Tenant>/search/` looked like an
#: equally good tell until it claimed `linkedin.com/jobs/search` — a confident wrong answer for a
#: platform we DO know, which is the same trap facebook.com fell into in the facet vocabulary. The
#: job-page shape is the one worth having anyway: the apply flow needs the posting, not the search.
#: The REAL shape, taken from the live url rather than from memory: SAP appends a NUMERIC job id as
#: its own segment. The first version of this pattern required exactly `/<Tenant>/job/<slug>/` and
#: was "verified" against a url I typed myself — so it passed the test and missed the page that was
#: open at the time. The numeric id is also the more distinctive half, which is why the corrected
#: pattern is both righter and narrower.
_SUCCESSFACTORS_PATH_TELLS = (
    re.compile(r"^/[A-Za-z0-9_%-]+/job/[^/]+/\d+/?$"),   # /Teradyne/job/North-Reading-.../1385295400/
)

_QUERY_PARAM_TELLS = {
    "gh_jid": "greenhouse",       # Greenhouse embed / board id
    "gh_src": "greenhouse",
    "lever-origin": "lever",
    "jvi": "jobvite",
}


def classify_ats(url: str, page_hints: Optional[dict[str, Any]] = None) -> str:
    """Map an apply-destination URL to its ATS id. Unknown external host = 'company_site' (still
    handled — an employer's own careers page that isn't a recognized ATS yet). Empty = 'unknown'.
    This is the single source of truth; search_cadence.classify_apply_platform delegates here.

    Checks, in order: the HOST, then the ATS's own QUERY-PARAM tells (which see through a branded
    wrapper on the employer's domain), then optional page_hints — {"embed_hosts": [...]} from a live
    page (e.g. an embedded job-boards.greenhouse.io iframe), for wrappers that hide even the param.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return "unknown"
    for ats in ATS_PLATFORMS:
        if ats["ats_id"] in _ON_ENGINE_APPLY:
            continue  # handled explicitly below so a broad engine host doesn't shadow real ATSs
        if any(needle in host for needle in ats["hosts"]):
            return ats["ats_id"]

    # Branded wrapper: the ATS betrays itself in the query string.
    params = parse_qs(urlparse(url or "").query)
    for param, ats_id in _QUERY_PARAM_TELLS.items():
        if param in params and ats_id in _BY_ID:
            return ats_id

    # Branded wrapper with no param tell — ask where the page POINTS rather than where it IS.
    # Two sources, same idea: an embedded ATS iframe (`embed_hosts`), and the destination of the
    # page's own apply control (`apply_hrefs`).
    #
    # THE APPLY HREF IS OFTEN THE ONLY TELL, and this one cost a halt to find. Ahold Delhaize's
    # careers front is `aholddelhaizeusa.careerswithus.com` — no known host, no param, no iframe —
    # and its "APPLY NOW" points at `aholddelhaizeapply.appvault.com` (measured live 2026-07-30, via
    # a LinkedIn apply). The registry already DESCRIBED that hop in AppVault's notes, in prose,
    # from an Indeed drive; prose does not classify. A careers front is a signpost, and reading the
    # signpost is the whole trick.
    hosts_to_try = list((page_hints or {}).get("embed_hosts") or [])
    hosts_to_try += list((page_hints or {}).get("apply_hrefs") or [])
    for candidate in hosts_to_try:
        cand_host = (urlparse(candidate).hostname or candidate or "").lower()
        for ats in ATS_PLATFORMS:
            if ats["ats_id"] in _ON_ENGINE_APPLY:
                continue
            if any(needle in cand_host for needle in ats["hosts"]):
                return ats["ats_id"]

    # Branded SuccessFactors: no host tell, no param tell — read the path shape.
    path = urlparse(url or "").path or ""
    if any(pat.match(path) for pat in _SUCCESSFACTORS_PATH_TELLS):
        return "successfactors"

    if "smartapply.indeed.com" in host or "indeed.com" in host:
        return "indeed_quick_apply"
    if "linkedin.com" in host:
        return "linkedin_easy_apply"
    return "company_site"


def get_ats(ats_id: str) -> Optional[dict[str, Any]]:
    return _BY_ID.get(ats_id)


# --- company → ATS learning store (the part that grows at runtime) -------------------------------
def _store_path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "company_ats.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_company_ats() -> dict[str, Any]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_company_ats(doc: dict[str, Any]) -> None:
    _store_path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _norm_company(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def record_company_ats(company: str, ats_id: str, url: str = "") -> dict[str, Any]:
    """Learn that <company> applies through <ats_id> (observed live at <url>). This is the
    generalization hook: once recorded, ats_for_company() reuses the ATS recipe + all training
    accumulated on OTHER companies that share it. Idempotent; bumps last_seen_at."""
    key = _norm_company(company)
    if not key or ats_id not in _BY_ID:
        return {"ok": False, "detail": f"bad company/ats ({company!r}, {ats_id!r})"}
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        doc = _load_company_ats()
        rec = doc.get(key) or {"company": company.strip(), "ats_id": ats_id, "first_seen_at": now}
        rec.update({"company": company.strip(), "ats_id": ats_id, "last_seen_at": now})
        if url:
            rec["last_url"] = url[:200]
        doc[key] = rec
        _save_company_ats(doc)
    return {"ok": True, **rec}


def ats_for_company(company: str) -> Optional[str]:
    """The ATS a company is known to use — from the learned store first, then the seed lists. None
    if unknown (drive it fresh, then record_company_ats to generalize next time)."""
    key = _norm_company(company)
    if not key:
        return None
    learned = _load_company_ats().get(key)
    if learned:
        return learned.get("ats_id")
    for ats in ATS_PLATFORMS:
        if any(_norm_company(c) == key for c in ats.get("seed_companies", [])):
            return ats["ats_id"]
    return None


def companies_on_ats(ats_id: str) -> list[str]:
    """Every company known to use this ATS (seed + learned) — the set training generalizes over."""
    out = {c for c in (_BY_ID.get(ats_id, {}).get("seed_companies") or [])}
    for rec in _load_company_ats().values():
        if rec.get("ats_id") == ats_id and rec.get("company"):
            out.add(rec["company"])
    return sorted(out)


def ats_spec() -> dict[str, Any]:
    """The full registry — what GET /api/career_search/ats returns. Merges the constant with the
    learned company→ATS mapping so each ATS shows every company (seed + observed) that uses it."""
    platforms = []
    for a in ATS_PLATFORMS:
        platforms.append({**a, "companies": companies_on_ats(a["ats_id"])})
    return {
        "career_search": CAREER_SEARCH,
        "ats_platforms": platforms,
        "company_ats": _load_company_ats(),
        "note": "Each ATS is domain-like: own recipe + own training bucket (capture domain_id=ats_id). "
                "Training generalizes across companies sharing an ATS via the company→ATS map. "
                "NEVER auto-create an account on an auth='account' ATS — escalate to the operator.",
    }
